"""Historical replay engine that reuses agent + risk pipeline.

Bugs fixed in this version:
  1.  _compute_signals (indicators.py) always returned "neutral" — fixed inline
      by computing overall_signal directly from RSI/MACD/BB with correct thresholds.
  2.  india_vix hardcoded to 14.0 — now computed from realised volatility.
  3.  market_trend hardcoded to "sideways" — now computed from NIFTY history.
  4.  _derive_overall_signal used RSI ≤35/≥65 — fixed to <30/>70 matching AI prompt.
  5.  ai_every_n_candles defaulted to 5 — now 1 (evaluate every candle).
  6.  No sleep between AI calls — rate-limits all models within first minute.
      Fixed: 5-second sleep after every AI call (12 calls/min < 15 RPM limit).
  7.  Circuit breaker never reset between candles — once tripped, all models
      stayed locked for 30 calls (= entire replay with ai_every_n_candles=5).
      Fixed: clear() circuit breaker state before each AI call.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal


logger = logging.getLogger("core.replay")

# ── Per-provider delay between AI calls during replay (seconds) ────────────────
# Gemini free tier: 15 RPM → 4s minimum gap
# Groq free tier:   30 RPM → 2s minimum gap
# OpenRouter:       generous limits, 1s is enough
# Unknown provider: stay conservative at 5s
REPLAY_AI_CALL_DELAY_BY_PROVIDER = {
    "gemini":     4.0,
    "groq":       2.0,
    "openrouter": 1.0,
    "default":    5.0,
}


@dataclass
class ReplayConfig:
    symbols: list[str]
    exchange: str = "NSE"
    timeframe: str = "day"
    start_date: datetime | None = None
    end_date: datetime | None = None
    initial_capital: float = 100000
    fee_pct: float = 0.0003
    slippage_pct: float = 0.0005
    latency_slippage_bps: float = 2.0
    partial_fill_probability: float = 0.15
    # fix 5: default 1 — evaluate every candle. Users can increase via UI
    # to reduce total API calls at the cost of fewer trading decisions.
    ai_every_n_candles: int = 1
    confidence_threshold: float | None = None


class ReplayEngine:
    def __init__(self, app_config: dict):
        self.config = app_config
        from agents.brain import TradingAgent
        from risk.manager import RiskConfig, RiskManager

        self.agent = TradingAgent(app_config.get("agent", {}))

        replay_risk_cfg = RiskConfig(
            max_capital_per_trade_pct=95.0,
            max_open_positions=50,
            max_daily_loss_pct=100.0,
            max_drawdown_pct=100.0,
            stop_loss_pct=3.0,
            min_cash_buffer=0.0,
            tiny_account_mode=False,
        )
        self.risk = RiskManager(replay_risk_cfg)
        logger.info(
            "Replay risk config: max_capital_per_trade_pct=%.0f%%, max_positions=%d",
            replay_risk_cfg.max_capital_per_trade_pct,
            replay_risk_cfg.max_open_positions,
        )

    async def run(self, run_id: str, cfg: ReplayConfig) -> dict:
        from agents.brain import MarketContext, SignalAction
        from brokers.base import (
            Exchange, Funds, Instrument, InstrumentType,
            OrderSide, Position, ProductType,
        )
        from database.repository import HistoricalCandleRepository, ReplayRunRepository

        try:
            await ReplayRunRepository.mark_running(run_id)
            candles = await HistoricalCandleRepository.fetch_window(
                cfg.symbols, cfg.exchange, cfg.timeframe,
                cfg.start_date, cfg.end_date,
            )
            if not candles:
                symbols  = ", ".join(cfg.symbols) if cfg.symbols else "(none)"
                start    = cfg.start_date.date().isoformat() if cfg.start_date else "(open)"
                end      = cfg.end_date.date().isoformat()   if cfg.end_date   else "(open)"
                error_msg = (
                    "No historical candles available for the selected window. "
                    f"symbols={symbols}, exchange={cfg.exchange}, timeframe={cfg.timeframe}, "
                    f"start={start}, end={end}. Backfill candles first and rerun."
                )
                await ReplayRunRepository.mark_failed(run_id, error_msg)
                return {"status": "failed", "error": "No historical candles available"}

            by_ts: dict[datetime, dict[str, dict]] = {}
            for c in candles:
                by_ts.setdefault(c["timestamp"], {})[c["symbol"]] = c

            cash = Decimal(str(cfg.initial_capital))
            positions: dict[str, dict] = {}
            trades: list[dict] = []
            equity_curve: list[dict] = []
            price_history:  dict[str, list[float]] = {s: [] for s in cfg.symbols}
            volume_history: dict[str, list[float]] = {s: [] for s in cfg.symbols}
            last_seen: dict[str, dict] = {}
            last_index_prices = {"NIFTY 50": None, "NIFTY BANK": None}
            nifty_history: list[float] = []

            if cfg.confidence_threshold is not None:
                self.agent.confidence_threshold = max(
                    0.30, min(0.95, float(cfg.confidence_threshold))
                )

            await self.risk.initialize(
                Funds(
                    available_cash=cash,
                    used_margin=Decimal("0"),
                    total_balance=cash,
                )
            )

            sorted_ts    = sorted(by_ts)
            total_points = len(sorted_ts)

            for idx, ts in enumerate(sorted_ts, start=1):
                snap = by_ts[ts]

                # ── Update price / volume history ────────────────────────────
                for symbol in cfg.symbols:
                    candle_data = snap.get(symbol)
                    if candle_data:
                        last_seen[symbol] = candle_data
                        price_history.setdefault(symbol, []).append(
                            float(candle_data["close"])
                        )
                        volume_history.setdefault(symbol, []).append(
                            float(candle_data.get("volume") or 0)
                        )
                        if len(price_history[symbol]) > 240:
                            price_history[symbol] = price_history[symbol][-240:]
                        if len(volume_history[symbol]) > 240:
                            volume_history[symbol] = volume_history[symbol][-240:]

                for idx_sym in ("NIFTY 50", "NIFTY BANK"):
                    idx_candle = snap.get(idx_sym)
                    if idx_candle:
                        last_index_prices[idx_sym] = float(idx_candle["close"])

                # fix 2: live VIX estimate from realised volatility
                india_vix = _estimate_vix(price_history)

                # fix 3: live market trend from NIFTY history
                nifty_ltp     = _resolve_index_ltp(last_index_prices["NIFTY 50"],  24000.0)
                banknifty_ltp = _resolve_index_ltp(last_index_prices["NIFTY BANK"], 50000.0)
                nifty_history.append(nifty_ltp)
                if len(nifty_history) > 50:
                    nifty_history = nifty_history[-50:]
                market_trend = _detect_trend(nifty_history, india_vix)

                # ── Build watchlist ──────────────────────────────────────────
                watch = []
                for symbol in cfg.symbols:
                    candle = snap.get(symbol) or last_seen.get(symbol)
                    if not candle:
                        continue
                    closes  = price_history.get(symbol, [])
                    volumes = volume_history.get(symbol, [])
                    change_pct = 0.0
                    if len(closes) >= 2 and closes[-2] > 0:
                        change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100

                    rsi          = _compute_rsi(closes, 14)
                    macd, macd_s = _compute_macd(closes)
                    bb_signal    = _compute_bb_signal(closes)
                    vol_ratio    = _compute_volume_ratio(volumes)
                    # fix 1 + 4: correct signal derivation
                    overall      = _derive_overall_signal(rsi, macd, macd_s, bb_signal)

                    watch.append({
                        "symbol":     symbol,
                        "ltp":        float(candle["close"]),
                        "change_pct": float(change_pct),
                        "indicators": {
                            "rsi": round(rsi, 2) if rsi is not None else "N/A",
                            "macd_signal": (
                                round(macd - macd_s, 4)
                                if macd is not None and macd_s is not None
                                else "N/A"
                            ),
                            "bb_signal":      bb_signal,
                            "supertrend":     "bullish" if (rsi or 50.0) >= 50 else "bearish",
                            "volume_ratio":   round(vol_ratio, 2),
                            "overall_signal": overall,
                        },
                        "levels":   _build_levels(candle),
                        "is_stale": symbol not in snap,
                    })

                # ── Open positions list ──────────────────────────────────────
                open_positions = []
                for symbol, p in positions.items():
                    src  = (
                        snap.get(symbol)
                        or last_seen.get(symbol)
                        or {"close": float(p["entry_price"])}
                    )
                    ltp  = Decimal(str(src["close"]))
                    qty  = p["qty"]
                    pnl  = (ltp - p["entry_price"]) * qty
                    side = OrderSide.BUY if qty > 0 else OrderSide.SELL
                    open_positions.append(
                        Position(
                            instrument=Instrument(
                                symbol=symbol,
                                exchange=Exchange[cfg.exchange],
                                instrument_type=InstrumentType.EQ,
                            ),
                            side=side,
                            quantity=abs(int(qty)),
                            average_price=p["entry_price"],
                            ltp=ltp,
                            pnl=pnl,
                            pnl_pct=(
                                float((pnl / (p["entry_price"] * abs(qty))) * 100)
                                if qty else 0.0
                            ),
                            product=ProductType.CNC,
                            broker="replay",
                        )
                    )

                context = MarketContext(
                    timestamp=ts,
                    nifty50_ltp=nifty_ltp,
                    banknifty_ltp=banknifty_ltp,
                    india_vix=india_vix,       # fix 2
                    market_trend=market_trend, # fix 3
                    session="mid_session",
                    day_of_week=ts.strftime("%A"),
                    available_capital=float(cash),
                    used_margin=0.0,
                    open_positions=[
                        {
                            "symbol":    p.instrument.symbol,
                            "side":      p.side.value,
                            "quantity":  p.quantity,
                            "avg_price": float(p.average_price),
                            "ltp":       float(p.ltp),
                            "pnl":       float(p.pnl),
                        }
                        for p in open_positions
                    ],
                    watchlist_data=watch,
                    options_chain_summary=None,
                    recent_news_sentiment=None,
                    pcr=1.0,
                )

                # ── AI decision ──────────────────────────────────────────────
                should_run_ai = max(int(cfg.ai_every_n_candles or 1), 1)
                if idx % should_run_ai == 0:
                    # fix 7: reset circuit breaker before each AI call so a
                    # rate-limit spike on one candle doesn't black-list all
                    # models for the rest of the replay run.
                    self.agent._model_consecutive_failures.clear()
                    self.agent._model_skip_until.clear()

                    try:
                        signals = await self.agent.analyze_and_decide(context)
                    except Exception as exc:
                        logger.warning(
                            "AI analyze failed in replay, skipping candle: %s", exc
                        )
                        signals = []

                    # Per-provider adaptive throttle between AI calls.
                    # Extract provider from the model that was actually used.
                    model_used = None
                    if self.agent.decision_history:
                        model_used = self.agent.decision_history[-1].get("model_used")
                    provider = (
                        model_used.split("/")[0]
                        if model_used and "/" in model_used
                        else "default"
                    )
                    delay = REPLAY_AI_CALL_DELAY_BY_PROVIDER.get(
                        provider,
                        REPLAY_AI_CALL_DELAY_BY_PROVIDER["default"],
                    )
                    await asyncio.sleep(delay)
                else:
                    signals = []

                # ── Execute signals ──────────────────────────────────────────
                for s in signals:
                    signal_candle = snap.get(s.symbol) or last_seen.get(s.symbol)
                    if not s.is_actionable or not signal_candle:
                        continue

                    price = Decimal(str(signal_candle["close"]))
                    dyn_slip = _estimate_replay_slippage_pct(signal_candle, cfg)
                    exec_price = price * (
                        Decimal("1") + Decimal(str(
                            dyn_slip if s.action in (
                                SignalAction.BUY, SignalAction.COVER
                            ) else -dyn_slip
                        ))
                    )

                    funds = Funds(
                        available_cash=cash,
                        used_margin=Decimal("0"),
                        total_balance=cash,
                    )
                    check = await self.risk.check_pre_trade(
                        s.symbol, s.action.value, s.quantity,
                        exec_price, s.stop_loss, open_positions, funds,
                    )
                    if not check.approved:
                        logger.warning(
                            "Replay trade REJECTED: symbol=%s action=%s qty=%s "
                            "price=%.2f cash=%.2f reason=%s",
                            s.symbol, s.action.value, s.quantity,
                            float(exec_price), float(cash), check.reason,
                        )
                        continue

                    requested_qty = Decimal(str(check.adjusted_quantity or s.quantity or 1))
                    qty = _simulate_partial_fill(requested_qty, idx, ts, s.symbol, cfg)
                    if qty <= 0:
                        continue

                    fee           = exec_price * qty * Decimal(str(cfg.fee_pct))
                    action        = s.action.value
                    fee_remaining = fee
                    qty_remaining = qty
                    trade_pnl     = Decimal("0")
                    realized      = False

                    if action in ("BUY", "COVER"):
                        pos = positions.get(s.symbol)
                        if pos and pos["qty"] < 0 and qty_remaining > 0:
                            short_abs   = abs(pos["qty"])
                            close_qty   = min(short_abs, qty_remaining)
                            fee_alloc   = fee * (close_qty / qty) if qty > 0 else Decimal("0")
                            ef_alloc    = _entry_fee_allocation(pos, close_qty)
                            pnl         = (pos["entry_price"] - exec_price) * close_qty - fee_alloc - ef_alloc
                            cash       -= exec_price * close_qty + fee_alloc
                            pos["qty"] += close_qty
                            pos["entry_fees"] = max(Decimal("0"), pos.get("entry_fees", Decimal("0")) - ef_alloc)
                            qty_remaining -= close_qty
                            fee_remaining -= fee_alloc
                            trade_pnl += pnl
                            realized   = True
                            if pos["qty"] == 0:
                                positions.pop(s.symbol, None)
                            await self.risk.record_trade(order=None, pnl=pnl)

                        if qty_remaining > 0:
                            cash -= exec_price * qty_remaining + fee_remaining
                            pos   = positions.get(s.symbol)
                            if pos and pos["qty"] > 0:
                                pos["qty"], pos["entry_price"] = _merge_position(
                                    pos["qty"], pos["entry_price"],
                                    qty_remaining, exec_price,
                                )
                                pos["entry_fees"] = pos.get("entry_fees", Decimal("0")) + fee_remaining
                            else:
                                positions[s.symbol] = {
                                    "qty":        qty_remaining,
                                    "entry_price": exec_price,
                                    "entry_fees":  fee_remaining,
                                }

                    elif action in ("SELL", "SHORT"):
                        pos = positions.get(s.symbol)
                        if pos and pos["qty"] > 0 and qty_remaining > 0:
                            close_qty   = min(pos["qty"], qty_remaining)
                            fee_alloc   = fee * (close_qty / qty) if qty > 0 else Decimal("0")
                            ef_alloc    = _entry_fee_allocation(pos, close_qty)
                            pnl         = (exec_price - pos["entry_price"]) * close_qty - fee_alloc - ef_alloc
                            cash       += exec_price * close_qty - fee_alloc
                            pos["qty"] -= close_qty
                            pos["entry_fees"] = max(Decimal("0"), pos.get("entry_fees", Decimal("0")) - ef_alloc)
                            qty_remaining -= close_qty
                            fee_remaining -= fee_alloc
                            trade_pnl += pnl
                            realized   = True
                            if pos["qty"] == 0:
                                positions.pop(s.symbol, None)
                            await self.risk.record_trade(order=None, pnl=pnl)

                        if action == "SHORT" and qty_remaining > 0:
                            cash += exec_price * qty_remaining - fee_remaining
                            pos   = positions.get(s.symbol)
                            if pos and pos["qty"] < 0:
                                ea    = abs(pos["qty"])
                                na    = ea + qty_remaining
                                pos["entry_price"] = ((pos["entry_price"] * ea) + (exec_price * qty_remaining)) / na
                                pos["qty"]         = -na
                                pos["entry_fees"]  = pos.get("entry_fees", Decimal("0")) + fee_remaining
                            else:
                                positions[s.symbol] = {
                                    "qty":        -qty_remaining,
                                    "entry_price": exec_price,
                                    "entry_fees":  fee_remaining,
                                }
                        elif action == "SELL" and qty_remaining > 0:
                            continue

                    trades.append({
                        "run_id":             run_id,
                        "timestamp":          ts,
                        "symbol":             s.symbol,
                        "exchange":           cfg.exchange,
                        "action":             action,
                        "quantity":           int(qty),
                        "requested_quantity": int(requested_qty),
                        "price":              float(exec_price),
                        "fees":               float(fee),
                        "slippage_pct":       dyn_slip,
                        "pnl":                float(trade_pnl),
                        "realized":           realized,
                        "rationale":          s.rationale,
                    })

                # ── Equity snapshot ──────────────────────────────────────────
                equity = cash
                for symbol, p in positions.items():
                    src = (
                        snap.get(symbol)
                        or last_seen.get(symbol)
                        or {"close": float(p["entry_price"])}
                    )
                    equity += Decimal(str(src["close"])) * p["qty"]
                equity_curve.append({"timestamp": ts.isoformat(), "equity": float(equity)})

                # ── Live progress snapshot ────────────────────────────────────
                real_trades  = [t for t in trades if bool(t.get("realized"))]
                live_wins    = sum(1 for t in real_trades if (t.get("pnl") or 0) > 0)
                live_losses  = sum(1 for t in real_trades if (t.get("pnl") or 0) < 0)

                live_snapshot = {
                    "candle":        idx,
                    "totalCandles":  total_points,
                    "equity":        float(equity),
                    "equityHistory": [float(p.get("equity") or 0) for p in equity_curve[-180:]],
                    "date":          ts.isoformat(),
                    "tradeLog": [
                        {
                            "symbol":   t.get("symbol"),
                            "action":   t.get("action"),
                            "price":    float(t.get("price") or 0),
                            "quantity": int(t.get("quantity") or 0),
                            "pnl":      float(t.get("pnl") or 0) if t.get("pnl") is not None else None,
                            "time": (
                                t.get("timestamp").isoformat()
                                if hasattr(t.get("timestamp"), "isoformat")
                                else t.get("timestamp")
                            ),
                        }
                        for t in trades[-60:]
                    ][::-1],
                    "positions": {
                        sym: {
                            "side":  "BUY" if pos["qty"] > 0 else "SELL",
                            "entry": float(pos["entry_price"]),
                            "qty":   int(pos["qty"]),
                        }
                        for sym, pos in positions.items()
                    },
                    "openSignals":  [],
                    "decisions":    idx,
                    "signalCount":  len(trades),
                    "wins":         live_wins,
                    "losses":       live_losses,
                    "maxEquity":    max(
                        (p.get("equity") or 0) for p in equity_curve
                    ) if equity_curve else float(cfg.initial_capital),
                    "maxDrawdown":  _max_drawdown(equity_curve),
                    "stage":        "placing_orders",
                    "progressPct":  round((idx / total_points) * 100, 2) if total_points else 0,
                    "regime":       "replay_backtest",
                    "commentary":   (
                        f"Replay {idx}/{total_points} | "
                        f"VIX≈{india_vix:.1f} | {market_trend}"
                    ),
                    "thoughts": [
                        {
                            "timestamp": (
                                t.get("timestamp").isoformat()
                                if hasattr(t.get("timestamp"), "isoformat")
                                else t.get("timestamp")
                            ),
                            "level":   "success" if str(t.get("action", "")).upper() == "BUY" else "info",
                            "message": (
                                f"{str(t.get('action', '')).upper()} "
                                f"<strong>{t.get('symbol') or ''}</strong> "
                                f"@ ₹{round(float(t.get('price') or 0))}"
                            ),
                        }
                        for t in trades[-25:]
                    ],
                    "strategyWeights": {
                        "momentum": 0.25, "mean_reversion": 0.25,
                        "options_selling": 0.20, "breakout": 0.20, "scalping": 0.10,
                    },
                    "priceData": price_history,
                }

                await ReplayRunRepository.mark_progress(
                    run_id,
                    metrics={
                        "progress": {
                            "processed":         idx,
                            "total":             total_points,
                            "pct":               round((idx / total_points) * 100, 2) if total_points else 0,
                            "current_timestamp": ts.isoformat(),
                        },
                        "live": live_snapshot,
                    },
                )

            # ── Final summary ────────────────────────────────────────────────
            final_value  = equity_curve[-1]["equity"] if equity_curve else float(cfg.initial_capital)
            total_return = (
                (final_value - cfg.initial_capital) / cfg.initial_capital * 100
            ) if cfg.initial_capital else 0.0
            summary = _summarize_trades(trades)
            metrics = {
                "final_value":          final_value,
                "net_pnl":              final_value - cfg.initial_capital,
                "return_pct":           total_return,
                "trade_count":          summary["order_count"],
                "order_count":          summary["order_count"],
                "completed_trades":     summary["completed_trades"],
                "open_positions_count": len(positions),
                "win_rate":             summary["win_rate"],
                "drawdown_pct":         _max_drawdown(equity_curve),
                "profit_factor":        summary["profit_factor"],
            }

            await ReplayRunRepository.save_results(
                run_id,
                metrics=metrics,
                equity_curve=equity_curve,
                trades=trades,
            )
            return {
                "status":       "completed",
                "metrics":      metrics,
                "equity_curve": equity_curve,
                "trades":       trades,
            }

        except Exception as exc:
            logger.exception("Replay run %s failed unexpectedly", run_id)
            await ReplayRunRepository.mark_failed(run_id, str(exc))
            return {"status": "failed", "error": str(exc)}


# ─── VIX estimation (fix 2) ───────────────────────────────────────────────────

def _estimate_vix(price_history: dict[str, list[float]], lookback: int = 20) -> float:
    vols = []
    for prices in price_history.values():
        if len(prices) < 5:
            continue
        window  = prices[-lookback:]
        returns = [
            (window[i] - window[i - 1]) / window[i - 1]
            for i in range(1, len(window))
            if window[i - 1] > 0
        ]
        if len(returns) < 2:
            continue
        mean     = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        ann      = math.sqrt(variance) * math.sqrt(252) * 100
        vols.append(ann)

    if not vols:
        return 14.0

    vols_sorted = sorted(vols)
    p75_idx     = max(0, int(len(vols_sorted) * 0.75) - 1)
    index_vol   = vols_sorted[p75_idx] * 0.7
    return round(max(8.0, min(40.0, index_vol)), 2)


# ─── Trend detection (fix 3) ─────────────────────────────────────────────────

def _detect_trend(nifty_history: list[float], vix: float) -> str:
    h = nifty_history
    if len(h) < 10:
        return "sideways"
    recent   = sum(h[-5:])  / 5
    older    = sum(h[-20:-10]) / 10 if len(h) >= 20 else recent
    mom_pct  = (recent - older) / older * 100 if older > 0 else 0.0

    if vix > 20:
        return "high_volatility"
    if mom_pct > 0.5:
        return "trending_up"
    if mom_pct < -0.5:
        return "trending_down"
    return "ranging"


# ─── Overall signal (fix 1 + 4) ──────────────────────────────────────────────

def _derive_overall_signal(
    rsi: float | None,
    macd: float | None,
    macd_signal: float | None,
    bb_signal: str,
) -> str:
    """
    fix 1: replaces the broken _compute_signals() in indicators.py.
    fix 4: uses RSI <30/>70 (not ≤35/≥65) to match AI system prompt anchors.
    """
    score = 0

    if rsi is not None:
        if rsi < 30:
            score += 2
        elif rsi < 40:
            score += 1
        elif rsi > 70:
            score -= 2
        elif rsi > 60:
            score -= 1

    if macd is not None and macd_signal is not None:
        score += 1 if (macd - macd_signal) > 0 else -1

    if bb_signal == "below_lower":
        score += 1
    elif bb_signal == "above_upper":
        score -= 1

    if score >= 3:
        return "strong_buy"
    if score >= 1:
        return "buy"
    if score <= -3:
        return "strong_sell"
    if score <= -1:
        return "sell"
    return "neutral"


# ─── Unchanged helpers ────────────────────────────────────────────────────────

def _entry_fee_allocation(position: dict, close_qty: Decimal) -> Decimal:
    qty        = abs(position.get("qty", Decimal("0")))
    entry_fees = position.get("entry_fees", Decimal("0"))
    if qty <= 0 or entry_fees <= 0:
        return Decimal("0")
    return entry_fees * min(Decimal("1"), close_qty / qty)


def _estimate_replay_slippage_pct(candle: dict, cfg: ReplayConfig) -> float:
    base     = float(cfg.slippage_pct)
    open_px  = float(candle.get("open")  or candle.get("close") or 0.0)
    high_px  = float(candle.get("high")  or candle.get("close") or open_px)
    low_px   = float(candle.get("low")   or candle.get("close") or open_px)
    close_px = float(candle.get("close") or open_px or 1.0)
    volume   = max(float(candle.get("volume") or 0.0), 1.0)
    return round(
        base
        + (abs(high_px - low_px) / max(close_px, 1.0) * 0.10)
        + min(0.003, 25000.0 / volume)
        + float(cfg.latency_slippage_bps) / 10000.0
        + abs(close_px - open_px) / max(open_px, 1.0) * 0.05,
        6,
    )


def _simulate_partial_fill(
    requested_qty: Decimal, idx: int, ts: datetime, symbol: str, cfg: ReplayConfig
) -> Decimal:
    qty_int = int(requested_qty)
    if qty_int <= 1:
        return Decimal(str(max(qty_int, 0)))
    seed      = (idx + int(ts.timestamp()) + sum(ord(ch) for ch in symbol)) % 100
    threshold = int(float(cfg.partial_fill_probability) * 100)
    if seed >= threshold:
        return Decimal(str(qty_int))
    return Decimal(str(max(1, math.floor(qty_int * 0.6))))


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    out   = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _compute_rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(abs(min(d, 0.0)))
    avg_gain = sum(gains)  / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _compute_macd(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 26:
        return None, None
    macd_line = [a - b for a, b in zip(_ema(values, 12), _ema(values, 26))]
    signal    = _ema(macd_line, 9)
    return macd_line[-1], signal[-1] if signal else None


def _compute_bb_signal(values: list[float], period: int = 20) -> str:
    if len(values) < period:
        return "neutral"
    window   = values[-period:]
    mean     = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std      = math.sqrt(variance)
    last     = values[-1]
    if last > mean + 2 * std:
        return "above_upper"
    if last < mean - 2 * std:
        return "below_lower"
    return "inside_bands"


def _compute_volume_ratio(volumes: list[float], period: int = 20) -> float:
    if not volumes:
        return 1.0
    recent = volumes[-period:]
    avg    = sum(recent) / len(recent)
    return volumes[-1] / avg if avg > 0 else 1.0


def _build_levels(candle: dict) -> dict:
    high  = float(candle.get("high")  or candle.get("close") or 0)
    low   = float(candle.get("low")   or candle.get("close") or 0)
    close = float(candle.get("close") or 0)
    pivot = (high + low + close) / 3 if close else 0.0
    return {
        "pivot": round(pivot, 2),
        "r1":    round((2 * pivot) - low,  2),
        "s1":    round((2 * pivot) - high, 2),
    }


def _resolve_index_ltp(last_value: float | None, fallback: float) -> float:
    return float(last_value) if last_value is not None else float(fallback)


def _max_drawdown(equity_curve: list[dict]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]["equity"]
    dd   = 0.0
    for point in equity_curve:
        v    = point["equity"]
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak * 100)
    return dd


def _merge_position(
    old_qty: Decimal, old_entry: Decimal,
    add_qty: Decimal, add_entry: Decimal,
) -> tuple[Decimal, Decimal]:
    total = old_qty + add_qty
    if total <= 0:
        return total, add_entry
    return total, ((old_entry * old_qty) + (add_entry * add_qty)) / total


def _summarize_trades(trades: list[dict]) -> dict:
    realized = [t for t in trades if t.get("realized") is True]
    if not realized:
        realized = [t for t in trades if t.get("action") in ("SELL", "SHORT", "COVER")]
    wins   = [t for t in realized if (t.get("pnl") or 0) > 0]
    losses = [t for t in realized if (t.get("pnl") or 0) < 0]
    pf     = (
        sum((t.get("pnl") or 0) for t in wins)
        / abs(sum((t.get("pnl") or 0) for t in losses))
    ) if losses else None
    return {
        "order_count":      len(trades),
        "completed_trades": len(realized),
        "win_rate":         (len(wins) / len(realized) * 100) if realized else 0.0,
        "profit_factor":    pf,
    }


async def create_and_start_replay(app_config: dict, payload: dict) -> dict:
    from dataclasses import fields as dc_fields
    from database.repository import ReplayRunRepository

    run_id = str(uuid.uuid4())
    await ReplayRunRepository.create(run_id, payload)
    engine = ReplayEngine(app_config)

    valid_keys       = {f.name for f in dc_fields(ReplayConfig)}
    filtered_payload = {k: v for k, v in payload.items() if k in valid_keys}

    async def _safe_replay_task() -> None:
        await engine.run(run_id, ReplayConfig(**filtered_payload))

    asyncio.create_task(_safe_replay_task())
    return {"run_id": run_id, "status": "queued"}
