"""Historical replay engine that reuses agent + risk pipeline."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


logger = logging.getLogger("core.replay")


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
    ai_every_n_candles: int = 1
    

class ReplayEngine:
    def __init__(self, app_config: dict):
        self.config = app_config
        from agents.brain import TradingAgent
        from risk.manager import RiskConfig, RiskManager

        self.agent = TradingAgent(app_config.get("agent", {}))
        self.risk = RiskManager(RiskConfig())

    async def run(self, run_id: str, cfg: ReplayConfig) -> dict:
        from agents.brain import MarketContext, SignalAction
        from brokers.base import Exchange, Funds, Instrument, InstrumentType, OrderSide, Position, ProductType

        from database.repository import HistoricalCandleRepository, ReplayRunRepository

        await ReplayRunRepository.mark_running(run_id)
        candles = await HistoricalCandleRepository.fetch_window(cfg.symbols, cfg.exchange, cfg.timeframe, cfg.start_date, cfg.end_date)
        if not candles:
            symbols = ", ".join(cfg.symbols) if cfg.symbols else "(none)"
            start = cfg.start_date.date().isoformat() if cfg.start_date else "(open)"
            end = cfg.end_date.date().isoformat() if cfg.end_date else "(open)"
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
        price_history: dict[str, list[float]] = {symbol: [] for symbol in cfg.symbols}

        await self.risk.initialize(Funds(available_cash=cash, used_margin=Decimal("0"), total_balance=cash))

        sorted_ts = sorted(by_ts)
        total_points = len(sorted_ts)

        for idx, ts in enumerate(sorted_ts, start=1):
            snap = by_ts[ts]
            for symbol in cfg.symbols:
                candle_data = snap.get(symbol)
                if candle_data:
                    price_history.setdefault(symbol, []).append(float(candle_data["close"]))
                    if len(price_history[symbol]) > 240:
                        price_history[symbol] = price_history[symbol][-240:]
            watch = []
            for symbol, candle in snap.items():
                watch.append({"symbol": symbol, "ltp": candle["close"], "change_pct": 0.0, "indicators": {"overall_signal": "neutral"}, "levels": {}})

            open_positions = []
            for symbol, p in positions.items():
                ltp = Decimal(str(snap.get(symbol, {"close": float(p["entry_price"])})["close"]))
                qty = p["qty"]
                pnl = (ltp - p["entry_price"]) * qty
                side = OrderSide.BUY if qty > 0 else OrderSide.SELL
                open_positions.append(
                    Position(
                        instrument=Instrument(symbol=symbol, exchange=Exchange[cfg.exchange], instrument_type=InstrumentType.EQ),
                        side=side,
                        quantity=abs(int(qty)),
                        average_price=p["entry_price"],
                        ltp=ltp,
                        pnl=pnl,
                        pnl_pct=float((pnl / (p["entry_price"] * abs(qty))) * 100) if qty else 0.0,
                        product=ProductType.CNC,
                        broker="replay",
                    )
                )

            context = MarketContext(
                timestamp=ts,
                nifty50_ltp=float(snap.get("NIFTY 50", next(iter(snap.values()))["close"])),
                banknifty_ltp=float(snap.get("NIFTY BANK", next(iter(snap.values()))["close"])),
                india_vix=14.0,
                market_trend="sideways",
                session="mid_session",
                day_of_week=ts.strftime("%A"),
                available_capital=float(cash),
                used_margin=0.0,
                open_positions=[{"symbol": p.instrument.symbol, "side": p.side.value, "quantity": p.quantity, "avg_price": float(p.average_price), "ltp": float(p.ltp), "pnl": float(p.pnl)} for p in open_positions],
                watchlist_data=watch,
                options_chain_summary=None,
                recent_news_sentiment=None,
                pcr=1.0,
            )

            should_run_ai = max(int(cfg.ai_every_n_candles or 1), 1)
            if idx % should_run_ai == 0:
                try:
                    signals = await self.agent.analyze_and_decide(context)
                except Exception as exc:
                    logger.warning("AI analyze failed in replay, skipping candle: %s", exc)
                    signals = []
            else:
                signals = []

            for s in signals:
                if not s.is_actionable or s.symbol not in snap:
                    continue
                price = Decimal(str(snap[s.symbol]["close"]))
                exec_price = price * (Decimal("1") + Decimal(str(cfg.slippage_pct if s.action in (SignalAction.BUY, SignalAction.COVER) else -cfg.slippage_pct)))
                funds = Funds(available_cash=cash, used_margin=Decimal("0"), total_balance=cash)
                check = await self.risk.check_pre_trade(s.symbol, s.action.value, s.quantity, exec_price, s.stop_loss, open_positions, funds)
                if not check.approved:
                    continue
                qty = Decimal(str(check.adjusted_quantity or s.quantity or 1))
                fee = exec_price * qty * Decimal(str(cfg.fee_pct))
                action = s.action.value

                if action in ("BUY", "COVER"):
                    cash -= exec_price * qty + fee
                    pos = positions.get(s.symbol)
                    if pos:
                        pos["qty"], pos["entry_price"] = _merge_position(pos["qty"], pos["entry_price"], qty, exec_price)
                    else:
                        positions[s.symbol] = {"qty": qty, "entry_price": exec_price}
                    trade_pnl = Decimal("0")
                elif action in ("SELL", "SHORT"):
                    if s.symbol in positions:
                        pos = positions[s.symbol]
                        close_qty = min(pos["qty"], qty)
                        pnl = (exec_price - pos["entry_price"]) * close_qty - fee
                        cash += exec_price * close_qty - fee
                        pos["qty"] -= close_qty
                        if pos["qty"] <= 0:
                            positions.pop(s.symbol, None)
                        await self.risk.record_trade(order=None, pnl=pnl)
                        trade_pnl = pnl
                    else:
                        continue

                trades.append({"run_id": run_id, "timestamp": ts, "symbol": s.symbol, "exchange": cfg.exchange, "action": action, "quantity": int(qty), "price": float(exec_price), "fees": float(fee), "slippage_pct": cfg.slippage_pct, "pnl": float(trade_pnl), "rationale": s.rationale})

            equity = cash
            for symbol, p in positions.items():
                equity += Decimal(str(snap.get(symbol, {"close": float(p["entry_price"])})["close"])) * p["qty"]
            equity_curve.append({"timestamp": ts.isoformat(), "equity": float(equity)})

            realized = [t for t in trades if t.get("action") in ("SELL", "SHORT")]
            live_wins = sum(1 for t in realized if (t.get("pnl") or 0) > 0)
            live_losses = sum(1 for t in realized if (t.get("pnl") or 0) < 0)
            live_snapshot = {
                "candle": idx,
                "totalCandles": total_points,
                "equity": float(equity),
                "equityHistory": [float(point.get("equity") or 0) for point in equity_curve[-180:]],
                "date": ts.isoformat(),
                "tradeLog": [
                    {
                        "symbol": t.get("symbol"),
                        "action": t.get("action"),
                        "price": float(t.get("price") or 0),
                        "quantity": int(t.get("quantity") or 0),
                        "pnl": float(t.get("pnl") or 0) if t.get("pnl") is not None else None,
                        "time": t.get("timestamp").isoformat() if hasattr(t.get("timestamp"), "isoformat") else t.get("timestamp"),
                    }
                    for t in trades[-60:]
                ][::-1],
                "positions": {
                    symbol: {"side": "BUY", "entry": float(pos["entry_price"]), "qty": int(pos["qty"])}
                    for symbol, pos in positions.items()
                },
                "openSignals": [],
                "decisions": idx,
                "signalCount": len(trades),
                "wins": live_wins,
                "losses": live_losses,
                "maxEquity": max((point.get("equity") or 0) for point in equity_curve) if equity_curve else float(cfg.initial_capital),
                "maxDrawdown": _max_drawdown(equity_curve),
                "stage": "placing_orders",
                "progressPct": round((idx / total_points) * 100, 2) if total_points else 0,
                "regime": "replay_backtest",
                "commentary": f"Replay running {idx}/{total_points}",
                "thoughts": [
                    {
                        "timestamp": t.get("timestamp").isoformat() if hasattr(t.get("timestamp"), "isoformat") else t.get("timestamp"),
                        "level": "success" if str(t.get("action", "")).upper() == "BUY" else "info",
                        "message": f"{str(t.get('action', '')).upper()} <strong>{t.get('symbol') or ''}</strong> @ ₹{round(float(t.get('price') or 0))}",
                    }
                    for t in trades[-25:]
                ],
                "strategyWeights": {"momentum": 0.25, "mean_reversion": 0.25, "options_selling": 0.2, "breakout": 0.2, "scalping": 0.1},
                "priceData": price_history,
            }

            await ReplayRunRepository.mark_progress(
                run_id,
                metrics={
                    "progress": {
                        "processed": idx,
                        "total": total_points,
                        "pct": round((idx / total_points) * 100, 2) if total_points else 0,
                        "current_timestamp": ts.isoformat(),
                    },
                    "live": live_snapshot,
                },
            )
    
        final_value = equity_curve[-1]["equity"] if equity_curve else float(cfg.initial_capital)
        total_return = ((final_value - cfg.initial_capital) / cfg.initial_capital * 100) if cfg.initial_capital else 0.0
        summary = _summarize_trades(trades)
        metrics = {
            "final_value": final_value,
            "net_pnl": final_value - cfg.initial_capital,
            "return_pct": total_return,
            "trade_count": summary["completed_trades"],
            "order_count": summary["order_count"],
            "completed_trades": summary["completed_trades"],
            "open_positions_count": len(positions),
            "win_rate": summary["win_rate"],
            "drawdown_pct": _max_drawdown(equity_curve),
            "profit_factor": summary["profit_factor"],
        }

        await ReplayRunRepository.save_results(run_id, metrics=metrics, equity_curve=equity_curve, trades=trades)
        return {"status": "completed", "metrics": metrics, "equity_curve": equity_curve, "trades": trades}


def _max_drawdown(equity_curve: list[dict]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]["equity"]
    dd = 0.0
    for point in equity_curve:
        v = point["equity"]
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak * 100)
    return dd


def _merge_position(old_qty: Decimal, old_entry: Decimal, add_qty: Decimal, add_entry: Decimal) -> tuple[Decimal, Decimal]:
    total_qty = old_qty + add_qty
    if total_qty <= 0:
        return total_qty, add_entry
    weighted_entry = ((old_entry * old_qty) + (add_entry * add_qty)) / total_qty
    return total_qty, weighted_entry


def _summarize_trades(trades: list[dict]) -> dict:
    realized_trades = [t for t in trades if t.get("action") in ("SELL", "SHORT")]
    wins = [t for t in realized_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in realized_trades if (t.get("pnl") or 0) < 0]
    profit_factor = (sum((t.get("pnl") or 0) for t in wins) / abs(sum((t.get("pnl") or 0) for t in losses))) if losses else None
    return {
        "order_count": len(trades),
        "completed_trades": len(realized_trades),
        "win_rate": (len(wins) / len(realized_trades) * 100) if realized_trades else 0.0,
        "profit_factor": profit_factor,
    }

async def create_and_start_replay(app_config: dict, payload: dict) -> dict:
    from database.repository import ReplayRunRepository

    run_id = str(uuid.uuid4())
    await ReplayRunRepository.create(run_id, payload)
    engine = ReplayEngine(app_config)
    asyncio.create_task(engine.run(run_id, ReplayConfig(**payload)))
    return {"run_id": run_id, "status": "queued"}
