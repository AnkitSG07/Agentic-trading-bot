from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from brokers.base import Funds
from core.pipeline_models import AICandidateEvaluation, AIEvaluationResult, OrderPlan, TradeCandidate
from core.replay_engine import ReplayConfig, ReplayEngine
from risk.portfolio_guard import PortfolioGuardResult


class StubCandidateBuilder:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0
        self.config = SimpleNamespace(capital_budget=0.0, max_candidates=0)

    def build_candidates(self, *args, **kwargs):
        self.calls += 1
        return list(self.candidates)


class StubAgent:
    def __init__(self, evaluation_result):
        self.evaluation_result = evaluation_result
        self.evaluate_calls = 0
        self.decision_history = []

    async def evaluate_candidates(self, candidates, context):
        self.evaluate_calls += 1
        return self.evaluation_result

    async def analyze_and_decide(self, context):  # pragma: no cover - must not be used
        raise AssertionError("legacy raw AI signal path must not be used in replay")


class StubCapitalManager:
    def __init__(self, plans):
        self.calls = 0
        self.plans = plans

    def plan_from_candidates(self, approved_candidates, funds, **kwargs):
        self.calls += 1
        return list(self.plans)


class StubSignalValidator:
    def __init__(self):
        self.calls = 0

    def validate(self, order_plan, **kwargs):
        self.calls += 1
        return SimpleNamespace(all_passed=True)


class StubPortfolioGuard:
    def __init__(self, approved_plans):
        self.calls = 0
        self.config = SimpleNamespace(max_open_positions=10)
        self._approved = approved_plans

    def filter_candidates(self, approved_candidates, **kwargs):
        self.calls += 1
        return PortfolioGuardResult(approved=list(self._approved), blocked={})

    def check(self, plans, **kwargs):
        self.calls += 1
        return SimpleNamespace(approved=list(self._approved), blocked={})


class StubSessionGuard:
    def active_block_reason(self, now):
        return None


def _candidate() -> TradeCandidate:
    return TradeCandidate(
        candidate_id="AAA:BUY:2026-03-23T10:00:00",
        symbol="AAA",
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
        sector_tag="symbol:AAA",
        ltp_reference=Decimal("100"),
        max_affordable_qty=10,
        generated_at=datetime(2026, 3, 23, 10, 0),
        priority=5,
    )


@pytest.mark.anyio
async def test_replay_uses_same_core_pipeline_components():
    candidate = _candidate()
    evaluation = AICandidateEvaluation(
        candidate_id=candidate.candidate_id,
        approved=True,
        confidence=0.82,
        rationale="deterministic replay approval",
        priority=1,
    )
    plan = OrderPlan(
        symbol="AAA",
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
        risk_reward=2.0,
        confidence=0.82,
        source_candidate_id=candidate.candidate_id,
    )

    engine = ReplayEngine.__new__(ReplayEngine)
    engine.candidate_builder = StubCandidateBuilder([candidate])
    engine.agent = StubAgent(AIEvaluationResult(
        candidate_evaluations=[evaluation],
        market_regime="trending_up",
        operating_mode="active_trading",
        market_commentary="Replay parity",
        mode_constraints={},
    ))
    engine.capital_manager = StubCapitalManager([plan])
    engine.signal_validator = StubSignalValidator()
    engine.portfolio_guard = StubPortfolioGuard([plan])
    engine.session_guard = StubSessionGuard()
    engine.risk = SimpleNamespace(config=SimpleNamespace(max_open_positions=10))

    context = SimpleNamespace(market_trend="trending_up", session="mid_session")
    frames = {
        "AAA": pd.DataFrame([
            {"timestamp": datetime(2026, 3, 23, 9, 55), "open": 99, "high": 101, "low": 98, "close": 100, "volume": 100000}
        ])
    }
    bundle = await engine._prepare_replay_pipeline(
        cfg=ReplayConfig(symbols=["AAA"]),
        ts=datetime(2026, 3, 23, 10, 0),
        context=context,
        frames=frames,
        funds=Funds(available_cash=Decimal("10000"), used_margin=Decimal("0"), total_balance=Decimal("10000")),
        positions={},
    )

    assert engine.candidate_builder.calls == 1
    assert engine.agent.evaluate_calls == 1
    assert engine.portfolio_guard.calls == 1
    assert engine.capital_manager.calls == 1
    assert engine.signal_validator.calls == 1
    assert bundle["order_plans"][0].symbol == "AAA"


def test_replay_is_not_using_legacy_ai_raw_signal_path():
    src = Path("core/replay_engine.py").read_text()
    assert "evaluate_candidates(" in src
    assert "analyze_and_decide(" not in src


def test_replay_engine_uses_phase7_config_backed_controls():
    src = Path("core/replay_engine.py").read_text()
    assert 'session_cfg = app_config.get("session", {})' in src
    assert 'risk_cfg = app_config.get("risk", {})' in src
    assert 'news_cfg = app_config.get("news", {})' in src
    assert "allow_exits_during_entry_blocks" in src
    assert "confidence_modifier_cap" in src
    assert "min_risk_reward" in src
