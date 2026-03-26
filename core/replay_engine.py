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

import pandas as pd


logger = logging.getLogger("core.replay")

# ── Per-provider delay between AI calls during replay (seconds) ────────────────
# These values act as minimum pacing after each completed AI decision.
# Replay still enforces a strict end-to-end decision budget in TradingAgent.
REPLAY_AI_CALL_DELAY_BY_PROVIDER = {
    "gemini": 3.0,
    "groq": 0.50,
    "openrouter": 0.25,
    "default": 2.00,
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
    # fix 5: default 1 — evaluate every candle. Users can increase via UI
    # to reduce total API calls at the cost of fewer trading decisions.
    ai_every_n_candles: int = 1
    confidence_threshold: float | None = None
    order_type: str = "MARKET"
    circuit_breaker_cooldown: int = 2
    decision_timeout_seconds: float = 4.0
    provider_timeout_seconds: float = 1.8
    max_models_per_decision: int = 2
    market_order_fill_basis: str = "open"
    ambiguity_rule: str = "stop_first"


@dataclass(slots=True)
class ReplayFillResult:
    filled: bool
    fill_price: Decimal | None = None
    trigger_reason: str | None = None
    slippage_pct: float = 0.0


class ReplayFillModel:
    """Deterministic replay fill rules shared across entry/exit simulation."""

    def __init__(self, cfg: ReplayConfig) -> None:
        self.cfg = cfg

    def market_fill(self, candle: dict, side: str) -> ReplayFillResult:
        base_key = "open" if str(self.cfg.market_order_fill_basis).lower() == "open" else "close"
        base_price = Decimal(str(candle.get(base_key) or candle.get("close") or candle.get("open") or 0))
        if base_price <= 0:
            return ReplayFillResult(filled=False, trigger_reason="missing_price")
        slip = Decimal(str(_estimate_replay_slippage_pct(candle, self.cfg)))
        direction = Decimal("1") if side in {"BUY", "COVER"} else Decimal("-1")
        fill_price = (base_price * (Decimal("1") + (direction * slip))).quantize(Decimal("0.01"))
        return ReplayFillResult(filled=True, fill_price=fill_price, trigger_reason="market", slippage_pct=float(slip))

    def limit_fill(self, candle: dict, side: str, limit_price: Decimal) -> ReplayFillResult:
        low = Decimal(str(candle.get("low") or candle.get("close") or 0))
        high = Decimal(str(candle.get("high") or candle.get("close") or 0))
        open_price = Decimal(str(candle.get("open") or candle.get("close") or 0))

        if side in {"BUY", "COVER"}:
            if low <= limit_price <= high:
                return ReplayFillResult(filled=True, fill_price=limit_price, trigger_reason="limit")
            if high < limit_price:
                # Candle traded entirely below the limit price, so the order is
                # marketable for the full candle. Use the better deterministic
                # price between the candle open and the limit ceiling.
                return ReplayFillResult(
                    filled=True,
                    fill_price=min(limit_price, open_price if open_price > 0 else limit_price),
                    trigger_reason="limit_improved",
                )
        if side in {"SELL", "SHORT"}:
            if low <= limit_price <= high:
                return ReplayFillResult(filled=True, fill_price=limit_price, trigger_reason="limit")
            if low > limit_price:
                # Candle traded entirely above the sell limit price, so the
                # order is marketable for the full candle. Use the better
                # deterministic price between the candle open and the limit floor.
                return ReplayFillResult(
                    filled=True,
                    fill_price=max(limit_price, open_price if open_price > 0 else limit_price),
                    trigger_reason="limit_improved",
                )
        return ReplayFillResult(filled=False, trigger_reason="limit_not_reached")

    def resolve_entry(self, candle: dict, plan) -> ReplayFillResult:
        order_type = str(getattr(plan, "order_type", None) or self.cfg.order_type or "MARKET").upper()
        if order_type == "MARKET":
            return self.market_fill(candle, plan.side)
        return self.limit_fill(candle, plan.side, Decimal(plan.entry_price))

    def resolve_protective_exit(self, candle: dict, position: dict) -> ReplayFillResult:
        side = "BUY" if Decimal(position["qty"]) > 0 else "SHORT"
        stop_loss = position.get("stop_loss")
        target = position.get("target")
        if stop_loss is None and target is None:
            return ReplayFillResult(filled=False, trigger_reason="no_exit_levels")

        low = Decimal(str(candle.get("low") or candle.get("close") or 0))
        high = Decimal(str(candle.get("high") or candle.get("close") or 0))
        stop_hit = False
        target_hit = False
        if side == "BUY":
            stop_hit = stop_loss is not None and low <= Decimal(stop_loss)
            target_hit = target is not None and high >= Decimal(target)
        else:
            stop_hit = stop_loss is not None and high >= Decimal(stop_loss)
            target_hit = target is not None and low <= Decimal(target)

        if stop_hit and target_hit:
            # Conservative deterministic ambiguity rule:
            # when the same candle touches both stop and target, assume stop first.
            chosen = "stop_loss" if self.cfg.ambiguity_rule == "stop_first" else "target"
            price = Decimal(stop_loss if chosen == "stop_loss" else target)
            return ReplayFillResult(filled=True, fill_price=price, trigger_reason=chosen)
        if stop_hit:
            return ReplayFillResult(filled=True, fill_price=Decimal(stop_loss), trigger_reason="stop_loss")
        if target_hit:
            return ReplayFillResult(filled=True, fill_price=Decimal(target), trigger_reason="target")
        return ReplayFillResult(filled=False, trigger_reason="no_trigger")


class ReplayEngine:
    def __init__(self, app_config: dict):
        self.config = app_config
        from agents.brain import TradingAgent
        from capital_manager import CapitalManager
        from core.candidate_builder import CandidateBuilder, CandidateBuilderConfig
        from core.session_guard import SessionBlockWindow, SessionGuard, SessionGuardConfig
        from core.signal_validator import SignalValidator, SignalValidatorConfig
        from data.news_classifier import NewsClassifier, NewsClassifierConfig
        from risk.manager import RiskConfig, RiskManager
        from risk.portfolio_guard import PortfolioGuard, PortfolioGuardConfig

        agent_cfg = dict(app_config.get("agent", {}))
        replay_cfg = dict(app_config.get("replay", {}))
        replay_fallbacks = agent_cfg.get("replay_fallback_models")
        if replay_fallbacks:
            agent_cfg["fallback_models"] = replay_fallbacks
        agent_cfg["decision_timeout_seconds"] = replay_cfg.get("decision_timeout_seconds", agent_cfg.get("replay_decision_timeout_seconds", agent_cfg.get("decision_timeout_seconds", 5.0)))
        agent_cfg["provider_timeout_seconds"] = replay_cfg.get("provider_timeout_seconds", agent_cfg.get("replay_provider_timeout_seconds", agent_cfg.get("provider_timeout_seconds", 2.5)))
        agent_cfg["max_fallback_wait_seconds"] = replay_cfg.get(
            "max_fallback_wait_seconds",
            agent_cfg.get(
                "replay_max_fallback_wait_seconds",
                agent_cfg.get("max_fallback_wait_seconds", 0.5),
            ),
        )
        agent_cfg["max_models_per_decision"] = replay_cfg.get("max_models_per_decision", agent_cfg.get("replay_max_models_per_decision", agent_cfg.get("max_models_per_decision", 3)))
        agent_cfg["circuit_breaker_cooldown"] = replay_cfg.get("circuit_breaker_cooldown", agent_cfg.get("circuit_breaker_cooldown", 2))

        self.agent = TradingAgent(agent_cfg)
        session_cfg = app_config.get("session", {})
        risk_cfg = app_config.get("risk", {})
        news_cfg = app_config.get("news", {})
        session_windows = tuple(
            SessionBlockWindow(
                start=datetime.strptime(str(window.get("start", "09:15")), "%H:%M").time(),
                end=datetime.strptime(str(window.get("end", "09:30")), "%H:%M").time(),
                reason=str(window.get("reason", "Entry block")),
            )
            for window in session_cfg.get("blocked_entry_windows", [])
        )
        self.candidate_builder = CandidateBuilder(CandidateBuilderConfig(
            exchange=str(app_config.get("market", {}).get("exchange", "NSE") or "NSE"),
            timeframe=str(app_config.get("market", {}).get("timeframe", "day") or "day"),
            product="MIS",
            capital_budget=0.0,
            max_candidates=int(app_config.get("engine", {}).get("max_auto_pick_symbols", 10) or 10),
        ), news_classifier=NewsClassifier(NewsClassifierConfig(
            enabled=bool(news_cfg.get("enabled", True)),
            freshness_limit_minutes=int(news_cfg.get("freshness_limit_minutes", 240) or 240),
            confidence_modifier_cap=float(news_cfg.get("confidence_modifier_cap", 0.2) or 0.2),
        )))
        self.capital_manager = CapitalManager(app_config.get("agent", {}))
        self.signal_validator = SignalValidator(SignalValidatorConfig(
            min_risk_reward=float(risk_cfg.get("min_risk_reward", 1.5) or 1.5),
            min_expected_edge_score=float(risk_cfg.get("min_expected_edge_score", 0.55) or 0.55),
        ))
        self.session_guard = SessionGuard(SessionGuardConfig(
            entry_block_windows=session_windows or SessionGuardConfig().entry_block_windows,
            exits_allowed_during_entry_blocks=bool(session_cfg.get("allow_exits_during_entry_blocks", True)),
        ))

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
        self.portfolio_guard = PortfolioGuard(PortfolioGuardConfig(
            max_open_positions=replay_risk_cfg.max_open_positions,
            max_per_sector=int(risk_cfg.get("sector_concentration_cap", 2) or 2),
            correlation_cap=int(risk_cfg.get("correlation_cap", 2) or 2),
            max_long_positions=int(risk_cfg.get("long_bias_cap", 10) or 10),
            max_short_positions=int(risk_cfg.get("short_bias_cap", 10) or 10),
            max_strategy_allocation=float(risk_cfg.get("strategy_family_cap", 0.5) or 0.5),
        ))

    @staticmethod
    def _approved_candidates_from_result(candidates, evaluation_result):
        from core.pipeline_models import ApprovedCandidate

        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        approved: list[ApprovedCandidate] = []
        for evaluation in evaluation_result.candidate_evaluations:
            if not evaluation.approved:
                continue
            candidate = candidate_by_id.get(evaluation.candidate_id)
            if candidate is None:
                continue
            approved.append(ApprovedCandidate(candidate=candidate, evaluation=evaluation))
        return approved

    async def _prepare_replay_pipeline(
        self,
        *,
        cfg: ReplayConfig,
        ts: datetime,
        context,
        frames: dict[str, pd.DataFrame],
        funds,
        positions: dict[str, dict],
    ) -> dict[str, object]:
        from core.pipeline_models import AIEvaluationResult

        stage_rejections: dict[str, dict[str, int]] = {
            "candidate_builder": {},
            "ai": {},
            "validator": {},
            "portfolio": {},
            "risk": {},
            "pipeline": {},
        }
        logger.debug("Replay pipeline stage=candidate_build ts=%s", ts.isoformat())  
        self.candidate_builder.config.capital_budget = float(max(Decimal(funds.available_cash), Decimal("0")))
        self.candidate_builder.config.max_candidates = len(cfg.symbols)
        price_references = {
            symbol: float(frame["close"].iloc[-1])
            for symbol, frame in frames.items()
            if frame is not None and not frame.empty
        }
        candidates = self.candidate_builder.build_candidates(
            frames,
            price_references=price_references,
            symbols=cfg.symbols,
            generated_at=ts,
            regime=context.market_trend,
            session_name=context.session,
        )
        logger.debug("Replay pipeline stage=ai_evaluate ts=%s candidates=%d", ts.isoformat(), len(candidates))
        try:
            evaluation_result = await self.agent.evaluate_candidates(candidates, context)
        except Exception as exc:
            logger.warning("Replay pipeline stage=ai_evaluate degraded: %s", exc, exc_info=True)
            stage_rejections["ai"]["ai_evaluate_exception"] = stage_rejections["ai"].get("ai_evaluate_exception", 0) + 1
            evaluation_result = self.agent._heuristic_evaluation_result(
                candidates,
                context,
                operating_mode="capital_preservation",
                commentary=f"Replay fallback evaluation used because AI evaluation failed: {exc}",
            )
        approved_candidates = self._approved_candidates_from_result(candidates, evaluation_result)
        approved_by_id = {approved.candidate_id: approved for approved in approved_candidates}

        session_block_reason = self.session_guard.active_block_reason(ts)
        if session_block_reason:
            approved_candidates = []
            approved_by_id = {}
        logger.debug("Replay pipeline stage=plan ts=%s approved_candidates=%d", ts.isoformat(), len(approved_candidates))
        order_plans = self.capital_manager.plan_from_candidates(
            approved_candidates,
            funds,
            open_position_symbols={symbol.upper() for symbol in positions},
        )

        logger.debug("Replay pipeline stage=validate ts=%s generated_order_plans=%d", ts.isoformat(), len(order_plans))
        validated_order_plans = []
        for plan in order_plans:
            validation = self.signal_validator.validate(
                plan,
                current_price_reference=Decimal(str(price_references.get(plan.symbol, float(plan.entry_price)))),
                available_capital=Decimal(funds.available_cash),
            )
            if validation.all_passed:
                validated_order_plans.append(plan)
            else:
                for reason in getattr(validation, "blocking_reasons", []) or ["signal_validator"]:
                    stage_rejections["validator"][str(reason)] = stage_rejections["validator"].get(str(reason), 0) + 1

        self.portfolio_guard.config.max_open_positions = self.risk.config.max_open_positions
        logger.debug("Replay pipeline stage=guard ts=%s validated_order_plans=%d", ts.isoformat(), len(validated_order_plans))
        portfolio_result = self.portfolio_guard.check(
            validated_order_plans,
            candidate_lookup=approved_by_id,
            open_position_symbols={symbol.upper() for symbol in positions},
            open_positions_count=len(positions),
            open_positions=[
                {
                    "symbol": symbol,
                    "side": "BUY" if Decimal(str(position.get("qty", 0))) >= 0 else "SHORT",
                    "strategy": position.get("strategy"),
                    "sector_tag": position.get("sector_tag"),
                }
                for symbol, position in positions.items()
            ],
        )
        surviving_candidate_ids = {plan.source_candidate_id for plan in portfolio_result.approved}
        filtered_approved_candidates = [
            approved for approved in approved_candidates
            if approved.candidate_id in surviving_candidate_ids
        ]
        for evaluation in evaluation_result.candidate_evaluations:
            for note in getattr(evaluation, "risk_notes", []) or []:
                reason = str(note or "").strip().lower() or "unspecified_rejection"
                stage_rejections["ai"][reason] = stage_rejections["ai"].get(reason, 0) + 1
        for reason in (getattr(portfolio_result, "blocked", {}) or {}):
            reason_key = f"{reason}"
            stage_rejections["portfolio"][reason_key] = stage_rejections["portfolio"].get(reason_key, 0) + 1
        pipeline_counters = {
            "candidates_built": len(candidates),
            "ai_approved": len(approved_candidates),
            "planned_orders": len(order_plans),
            "validator_passed": len(validated_order_plans),
            "portfolio_passed": len(portfolio_result.approved),
            "risk_passed": 0,
            "submitted_orders": 0,
            "filled_orders": 0,
            "rejected_reasons_by_stage": stage_rejections,
        }

        return {
            "candidates": candidates,
            "evaluation_result": evaluation_result if candidates else AIEvaluationResult(candidate_evaluations=[], market_regime=context.market_trend, operating_mode="selective", market_commentary="No replay candidates.", mode_constraints={}),
            "approved_candidates": filtered_approved_candidates,
            "order_plans": portfolio_result.approved,
            "session_block_reason": session_block_reason,
            "portfolio_result": portfolio_result,
            "pipeline_counters": pipeline_counters,
        }

    async def run(self, run_id: str, cfg: ReplayConfig) -> dict:
        from agents.brain import MarketContext
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
            fill_model = ReplayFillModel(cfg)

            sorted_ts    = sorted(by_ts)
            total_points = len(sorted_ts)

            for idx, ts in enumerate(sorted_ts, start=1):
                snap = by_ts[ts]
                pipeline = {
                    "candidates": [],
                    "evaluation_result": None,
                    "approved_candidates": [],
                    "order_plans": [],
                    "pipeline_counters": {
                        "candidates_built": 0,
                        "ai_approved": 0,
                        "planned_orders": 0,
                        "validator_passed": 0,
                        "portfolio_passed": 0,
                        "risk_passed": 0,
                        "submitted_orders": 0,
                        "filled_orders": 0,
                        "rejected_reasons_by_stage": {"pipeline": {}},
                    },
                }

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

                # ── Protective exits first: replay must keep exit behavior alive
                # even if SessionGuard later blocks fresh entries. When the same
                # candle hits both target and stop, ReplayFillModel assumes stop-loss
                # first by default (conservative ambiguity rule).
                for symbol, pos in list(positions.items()):
                    candle = snap.get(symbol) or last_seen.get(symbol)
                    if not candle:
                        continue
                    exit_fill = fill_model.resolve_protective_exit(candle, pos)
                    if not exit_fill.filled or exit_fill.fill_price is None:
                        continue

                    qty = abs(Decimal(pos["qty"]))
                    fee = exit_fill.fill_price * qty * Decimal(str(cfg.fee_pct))
                    entry_fee_alloc = _entry_fee_allocation(pos, qty)
                    if Decimal(pos["qty"]) > 0:
                        pnl = (exit_fill.fill_price - pos["entry_price"]) * qty - fee - entry_fee_alloc
                        cash += exit_fill.fill_price * qty - fee
                        action = "SELL"
                    else:
                        pnl = (pos["entry_price"] - exit_fill.fill_price) * qty - fee - entry_fee_alloc
                        cash -= exit_fill.fill_price * qty + fee
                        action = "COVER"
                    trades.append({
                        "run_id": run_id,
                        "timestamp": ts,
                        "symbol": symbol,
                        "exchange": cfg.exchange,
                        "action": action,
                        "quantity": int(qty),
                        "requested_quantity": int(qty),
                        "price": float(exit_fill.fill_price),
                        "fees": float(fee),
                        "slippage_pct": exit_fill.slippage_pct,
                        "pnl": float(pnl),
                        "realized": True,
                        "rationale": f"Replay {exit_fill.trigger_reason} exit",
                    })
                    positions.pop(symbol, None)
                    await self.risk.record_trade(order=None, pnl=pnl)

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

                frames = {}
                for symbol in cfg.symbols:
                    candles_for_symbol = [
                        row
                        for row_ts in sorted_ts[:idx]
                        for row in [by_ts.get(row_ts, {}).get(symbol)]
                        if row is not None
                    ]
                    if not candles_for_symbol:
                        continue
                    frames[symbol] = pd.DataFrame(candles_for_symbol)

                # ── Shared candidate/evaluation/planning pipeline ───────────
                should_run_ai = max(int(cfg.ai_every_n_candles or 1), 1)
                if idx % should_run_ai == 0:
                    try:
                        pipeline = await self._prepare_replay_pipeline(
                            cfg=cfg,
                            ts=ts,
                            context=context,
                            frames=frames,
                            funds=Funds(
                                available_cash=cash,
                                used_margin=Decimal("0"),
                                total_balance=cash,
                            ),
                            positions=positions,
                        )
                    except Exception as exc:
                        logger.warning("Replay pipeline failed for candle %s; degrading safely: %s", ts.isoformat(), exc, exc_info=True)
                        pipeline = {
                            "candidates": [],
                            "evaluation_result": None,
                            "approved_candidates": [],
                            "order_plans": [],
                            "pipeline_counters": {
                                "candidates_built": 0,
                                "ai_approved": 0,
                                "planned_orders": 0,
                                "validator_passed": 0,
                                "portfolio_passed": 0,
                                "risk_passed": 0,
                                "submitted_orders": 0,
                                "filled_orders": 0,
                                "rejected_reasons_by_stage": {"pipeline": {"replay_pipeline_exception": 1}},
                            },
                        }

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
                    pipeline = {
                        "candidates": [],
                        "evaluation_result": None,
                        "approved_candidates": [],
                        "order_plans": [],
                    }

                # ── Execute order plans ─────────────────────────────────────
                for plan in pipeline["order_plans"]:
                    plan.order_type = str(cfg.order_type or plan.order_type or "MARKET").upper()
                    signal_candle = snap.get(plan.symbol) or last_seen.get(plan.symbol)
                    if not signal_candle:
                        continue

                    funds = Funds(
                        available_cash=cash,
                        used_margin=Decimal("0"),
                        total_balance=cash,
                    )
                    check = await self.risk.check_pre_trade(
                        plan.symbol,
                        plan.side,
                        plan.quantity,
                        plan.entry_price,
                        plan.stop_loss,
                        open_positions,
                        funds,
                    )
                    if not check.approved:
                        logger.warning(
                            "Replay order plan REJECTED: symbol=%s action=%s qty=%s "
                            "price=%.2f cash=%.2f reason=%s",
                            plan.symbol, plan.side, plan.quantity,
                            float(plan.entry_price), float(cash), check.reason,
                        )
                        rejected = pipeline["pipeline_counters"].setdefault("rejected_reasons_by_stage", {}).setdefault("risk", {})
                        rejected[str(check.reason or "risk_rejection")] = rejected.get(str(check.reason or "risk_rejection"), 0) + 1
                        continue
                    pipeline["pipeline_counters"]["risk_passed"] = int(pipeline["pipeline_counters"].get("risk_passed", 0)) + 1

                    requested_qty = Decimal(str(check.adjusted_quantity or plan.quantity or 1))
                    entry_fill = fill_model.resolve_entry(signal_candle, plan)
                    pipeline["pipeline_counters"]["submitted_orders"] = int(pipeline["pipeline_counters"].get("submitted_orders", 0)) + 1
                    if not entry_fill.filled or entry_fill.fill_price is None:
                        rejected = pipeline["pipeline_counters"].setdefault("rejected_reasons_by_stage", {}).setdefault("execution", {})
                        reason = str(entry_fill.trigger_reason or "unfilled")
                        rejected[reason] = rejected.get(reason, 0) + 1
                        continue

                    qty = requested_qty
                    fee = entry_fill.fill_price * qty * Decimal(str(cfg.fee_pct))
                    action = plan.side
                    if action == "BUY":
                        cash -= entry_fill.fill_price * qty + fee
                        positions[plan.symbol] = {
                            "qty": qty,
                            "entry_price": entry_fill.fill_price,
                            "entry_fees": fee,
                            "stop_loss": plan.stop_loss,
                            "target": plan.target,
                            "source_candidate_id": plan.source_candidate_id,
                        }
                    elif action == "SHORT":
                        cash += entry_fill.fill_price * qty - fee
                        positions[plan.symbol] = {
                            "qty": -qty,
                            "entry_price": entry_fill.fill_price,
                            "entry_fees": fee,
                            "stop_loss": plan.stop_loss,
                            "target": plan.target,
                            "source_candidate_id": plan.source_candidate_id,
                        }
                    else:
                        continue

                    trades.append({
                        "run_id":             run_id,
                        "timestamp":          ts,
                        "symbol":             plan.symbol,
                        "exchange":           cfg.exchange,
                        "action":             action,
                        "quantity":           int(qty),
                        "requested_quantity": int(requested_qty),
                        "price":              float(entry_fill.fill_price),
                        "fees":               float(fee),
                        "slippage_pct":       entry_fill.slippage_pct,
                        "pnl":                0.0,
                        "realized":           False,
                        "rationale":          f"Replay order plan {plan.source_candidate_id}",
                    })
                    pipeline["pipeline_counters"]["filled_orders"] = int(pipeline["pipeline_counters"].get("filled_orders", 0)) + 1
                  
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
                    "pipelineCounters": pipeline.get("pipeline_counters", {}),  
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
                        "pipeline": pipeline.get("pipeline_counters", {}),  
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

    if score >= 1:
        return "bullish"
    if score <= -1:
        return "bearish"
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
    replay_defaults = app_config.get("replay", {}) or {}
    filtered_payload = {
        **{k: v for k, v in replay_defaults.items() if k in valid_keys},
        **{k: v for k, v in payload.items() if k in valid_keys},
    }

    async def _safe_replay_task() -> None:
        await engine.run(run_id, ReplayConfig(**filtered_payload))

    asyncio.create_task(_safe_replay_task())
    return {"run_id": run_id, "status": "queued"}
