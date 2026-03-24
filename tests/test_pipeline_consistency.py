from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from brokers.base import Funds
from core.pipeline_models import AICandidateEvaluation, AIEvaluationResult, OrderPlan, TradeCandidate
from core.replay_engine import ReplayConfig, ReplayEngine
from risk.portfolio_guard import PortfolioPlanGuardResult


class StubCandidateBuilder:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0
        self.config = SimpleNamespace(capital_budget=0.0, max_candidates=0)

    def build_candidates(self, *args, **kwargs):
        self.calls += 1
        return list(self.candidates)


class StubAgent:
    def __init__(self, confidence_modifier_cap: float):
        self.confidence_modifier_cap = confidence_modifier_cap
        self.evaluate_calls = 0
        self.decision_history = []

    async def evaluate_candidates(self, candidates, context):
        self.evaluate_calls += 1
        evaluations = []
        for candidate in candidates:
            base_confidence = 0.45 if candidate.symbol == "BBB" else 0.82
            adjusted_confidence = min(0.99, base_confidence + self.confidence_modifier_cap)
            evaluations.append(AICandidateEvaluation(
                candidate_id=candidate.candidate_id,
                approved=True,
                confidence=adjusted_confidence,
                rationale=f"confidence adjusted by cap {self.confidence_modifier_cap}",
                priority=1,
            ))
        return AIEvaluationResult(
            candidate_evaluations=evaluations,
            market_regime="trending_up",
            operating_mode="active_trading",
            market_commentary="Replay parity",
            mode_constraints={},
        )

    async def analyze_and_decide(self, context):  # pragma: no cover - must not be used
        raise AssertionError("legacy raw AI signal path must not be used in replay")


class StubCapitalManager:
    def __init__(self):
        self.calls = 0

    def plan_from_candidates(self, approved_candidates, funds, **kwargs):
        self.calls += 1
        plans = []
        for approved in approved_candidates:
            candidate = approved.candidate
            risk_reward = 1.2 if candidate.symbol == "BBB" else 2.0
            plans.append(OrderPlan(
                symbol=candidate.symbol,
                exchange="NSE",
                side="BUY",
                quantity=5,
                entry_price=Decimal("100"),
                stop_loss=Decimal("95"),
                target=Decimal("110"),
                product="MIS",
                order_type="LIMIT",
                strategy_tag="momentum",
                capital_allocated=Decimal("500"),
                risk_reward=risk_reward,
                confidence=approved.evaluation.confidence,
                source_candidate_id=candidate.candidate_id,
            ))
        return plans


class StubSignalValidator:
    def __init__(self, min_risk_reward: float):
        self.calls = 0
        self.min_risk_reward = min_risk_reward

    def validate(self, order_plan, **kwargs):
        self.calls += 1
        return SimpleNamespace(all_passed=order_plan.risk_reward >= self.min_risk_reward)


class StubPortfolioGuard:
    def __init__(self, min_confidence: float = 0.7):
        self.calls = 0
        self.config = SimpleNamespace(max_open_positions=10)
        self.min_confidence = min_confidence

    def check(self, plans, **kwargs):
        self.calls += 1
        approved = []
        blocked = {}
        for plan in plans:
            if plan.confidence >= self.min_confidence:
                approved.append(plan)
            else:
                blocked[plan.source_candidate_id] = "confidence below cap-adjusted threshold"
        return PortfolioPlanGuardResult(approved=approved, blocked=blocked)


class StubSessionGuard:
    def __init__(self, block_reason: str | None):
        self.block_reason = block_reason

    def active_block_reason(self, now):
        return self.block_reason


def _candidate(symbol: str) -> TradeCandidate:
    return TradeCandidate(
        candidate_id=f"{symbol}:BUY:2026-03-23T10:00:00",
        symbol=symbol,
        exchange="NSE",
        side="BUY",
        setup_type="breakout",
        strategy="momentum",
        timeframe="day",
        product="MIS",
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        target=Decimal("110"),
        risk_reward=2.0,
        signal_strength=0.8,
        trend_score=0.6,
        liquidity_score=0.9,
        volatility_regime="normal",
        sector_tag=f"symbol:{symbol}",
        ltp_reference=Decimal("100"),
        max_affordable_qty=10,
        generated_at=datetime(2026, 3, 23, 10, 0),
        priority=5,
    )


def _build_engine_fixture(
    *,
    session_block_reason: str | None,
    confidence_modifier_cap: float,
    min_risk_reward: float,
) -> tuple[ReplayEngine, ReplayConfig, SimpleNamespace, dict[str, pd.DataFrame], Funds]:
    engine = ReplayEngine.__new__(ReplayEngine)
    engine.candidate_builder = StubCandidateBuilder([_candidate("AAA"), _candidate("BBB")])
    engine.agent = StubAgent(confidence_modifier_cap=confidence_modifier_cap)
    engine.capital_manager = StubCapitalManager()
    engine.signal_validator = StubSignalValidator(min_risk_reward=min_risk_reward)
    engine.portfolio_guard = StubPortfolioGuard(min_confidence=0.7)
    engine.session_guard = StubSessionGuard(block_reason=session_block_reason)
    engine.risk = SimpleNamespace(config=SimpleNamespace(max_open_positions=10))

    context = SimpleNamespace(market_trend="trending_up", session="mid_session")
    frames = {
        "AAA": pd.DataFrame([
            {"timestamp": datetime(2026, 3, 23, 9, 55), "open": 99, "high": 101, "low": 98, "close": 100, "volume": 100000}
        ]),
        "BBB": pd.DataFrame([
            {"timestamp": datetime(2026, 3, 23, 9, 55), "open": 49, "high": 51, "low": 48, "close": 50, "volume": 90000}
        ]),
    }
    funds = Funds(available_cash=Decimal("10000"), used_margin=Decimal("0"), total_balance=Decimal("10000"))
    return engine, ReplayConfig(symbols=["AAA", "BBB"]), context, frames, funds


@pytest.mark.anyio
async def test_replay_uses_same_core_pipeline_components():
    engine, cfg, context, frames, funds = _build_engine_fixture(
        session_block_reason=None,
        confidence_modifier_cap=0.30,
        min_risk_reward=1.0,
    )

    bundle = await engine._prepare_replay_pipeline(
        cfg=cfg,
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=funds,
        positions={},
    )

    assert engine.candidate_builder.calls == 1
    assert engine.agent.evaluate_calls == 1
    assert engine.capital_manager.calls == 1
    assert engine.signal_validator.calls == 2
    assert engine.portfolio_guard.calls == 1
    assert {plan.symbol for plan in bundle["order_plans"]} == {"AAA", "BBB"}


@pytest.mark.anyio
async def test_replay_session_block_gates_candidates_and_sets_block_reason():
    open_engine, cfg, context, frames, funds = _build_engine_fixture(
        session_block_reason=None,
        confidence_modifier_cap=0.30,
        min_risk_reward=1.0,
    )
    blocked_engine, _, _, _, _ = _build_engine_fixture(
        session_block_reason="Opening range entry block",
        confidence_modifier_cap=0.30,
        min_risk_reward=1.0,
    )

    open_bundle = await open_engine._prepare_replay_pipeline(
        cfg=cfg,
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=funds,
        positions={},
    )
    blocked_bundle = await blocked_engine._prepare_replay_pipeline(
        cfg=cfg,
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=funds,
        positions={},
    )

    assert open_bundle["session_block_reason"] is None
    assert len(open_bundle["approved_candidates"]) == 2
    assert len(open_bundle["order_plans"]) == 2

    assert blocked_bundle["session_block_reason"] == "Opening range entry block"
    assert blocked_bundle["approved_candidates"] == []
    assert blocked_bundle["order_plans"] == []


@pytest.mark.anyio
async def test_replay_min_risk_reward_controls_validation_pass_fail():
    permissive_engine, cfg, context, frames, funds = _build_engine_fixture(
        session_block_reason=None,
        confidence_modifier_cap=0.10,
        min_risk_reward=1.0,
    )
    strict_engine, _, _, _, _ = _build_engine_fixture(
        session_block_reason=None,
        confidence_modifier_cap=0.10,
        min_risk_reward=1.5,
    )

    permissive_bundle = await permissive_engine._prepare_replay_pipeline(
        cfg=cfg,
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=funds,
        positions={},
    )
    strict_bundle = await strict_engine._prepare_replay_pipeline(
        cfg=cfg,
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=funds,
        positions={},
    )

    assert {plan.symbol for plan in permissive_bundle["order_plans"]} == {"AAA"}
    assert strict_bundle["order_plans"] == []
    assert strict_bundle["portfolio_result"].blocked == {}


@pytest.mark.anyio
async def test_replay_confidence_modifier_cap_changes_plan_filtering_and_block_reasons():
    low_cap_engine, cfg, context, frames, funds = _build_engine_fixture(
        session_block_reason=None,
        confidence_modifier_cap=0.05,
        min_risk_reward=1.0,
    )
    high_cap_engine, _, _, _, _ = _build_engine_fixture(
        session_block_reason=None,
        confidence_modifier_cap=0.30,
        min_risk_reward=1.0,
    )

    low_cap_bundle = await low_cap_engine._prepare_replay_pipeline(
        cfg=cfg,
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=funds,
        positions={},
    )
    high_cap_bundle = await high_cap_engine._prepare_replay_pipeline(
        cfg=cfg,
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=funds,
        positions={},
    )

    assert {plan.symbol for plan in low_cap_bundle["order_plans"]} == {"AAA"}
    assert low_cap_bundle["portfolio_result"].blocked == {
        "BBB:BUY:2026-03-23T10:00:00": "confidence below cap-adjusted threshold"
    }
    assert {plan.symbol for plan in high_cap_bundle["order_plans"]} == {"AAA", "BBB"}
    assert high_cap_bundle["portfolio_result"].blocked == {}
