from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
import sys
import types

import pytest

from agents.brain import MarketContext
from brokers.base import Funds
from core.pipeline_models import AICandidateEvaluation, AIEvaluationResult, ApprovedCandidate, OrderPlan, TradeCandidate
from core.preflight import EnginePreflight, PreflightConfig, PreflightReport
from risk.portfolio_guard import PortfolioGuardResult


repository_stub = types.ModuleType("database.repository")


class _Repo:
    @staticmethod
    async def save(*args, **kwargs):
        return None

    @staticmethod
    async def open_position(*args, **kwargs):
        return SimpleNamespace(id="pos-1")

    @staticmethod
    async def log(*args, **kwargs):
        return None


repository_stub.AgentDecisionRepository = _Repo
repository_stub.OHLCVRepository = _Repo
repository_stub.PositionRepository = _Repo
repository_stub.RiskEventRepository = _Repo
repository_stub.SLOrderRepository = _Repo
repository_stub.TradeRepository = _Repo
sys.modules.setdefault("database.repository", repository_stub)

from core.engine import TradingEngine


class StubCandidateBuilder:
    def __init__(self, candidates):
        self.candidates = candidates
        self.config = SimpleNamespace(capital_budget=0.0, max_candidates=0)
        self.calls = 0

    def build_candidates(self, *args, **kwargs):
        self.calls += 1
        return list(self.candidates)


class StubAgent:
    def __init__(self, evaluation_result):
        self.evaluation_result = evaluation_result
        self.calls = 0
        self.decision_history = []

    async def evaluate_candidates(self, candidates, context):
        self.calls += 1
        return self.evaluation_result


class StubSessionGuard:
    def __init__(self, reason=None):
        self.reason = reason

    def active_block_reason(self, now):
        return self.reason


class StubPortfolioGuard:
    def __init__(self, result):
        self.result = result
        self.config = SimpleNamespace(max_open_positions=10)
        self.calls = 0

    def filter_candidates(self, approved_candidates, **kwargs):
        self.calls += 1
        self.last_candidates = approved_candidates
        return self.result

    def check(self, plans, **kwargs):
        self.calls += 1
        self.last_plans = plans
        return self.result


class StubCapitalManager:
    def __init__(self, plans):
        self.plans = plans
        self.calls = 0

    def plan_from_candidates(self, approved_candidates, funds, **kwargs):
        self.calls += 1
        self.last_candidates = approved_candidates
        return list(self.plans)


class StubSignalValidator:
    def __init__(self):
        self.calls = 0

    def validate(self, order_plan, **kwargs):
        self.calls += 1
        return SimpleNamespace(all_passed=order_plan.symbol != "BLOCKED")


def _candidate(symbol: str) -> TradeCandidate:
    return TradeCandidate(
        candidate_id=f"{symbol}:BUY:2026-03-23T10:00:00",
        symbol=symbol,
        exchange="NSE",
        side="BUY",
        setup_type="breakout",
        strategy="momentum",
        timeframe="5m",
        product="MIS",
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        target=Decimal("110"),
        risk_reward=2.0,
        signal_strength=0.9,
        trend_score=0.8,
        liquidity_score=0.7,
        volatility_regime="normal",
        sector_tag="energy",
        ltp_reference=Decimal("100"),
        max_affordable_qty=10,
        generated_at=datetime(2026, 3, 23, 10, 0),
        priority=5,
    )


def _context() -> MarketContext:
    return MarketContext(
        timestamp=datetime(2026, 3, 23, 10, 0),
        nifty50_ltp=22500.0,
        banknifty_ltp=48500.0,
        india_vix=14.0,
        market_trend="trending_up",
        session="mid_session",
        day_of_week="Monday",
        available_capital=10000.0,
        used_margin=0.0,
        open_positions=[],
        watchlist_data=[{"symbol": "AAA", "ltp": 100.0}],
        options_chain_summary=None,
        recent_news_sentiment="Neutral",
        pcr=1.0,
    )


@pytest.mark.anyio
async def test_prepare_phase4_execution_uses_candidate_guard_plan_and_validator():
    candidate = _candidate("AAA")
    evaluation_result = AIEvaluationResult(
        candidate_evaluations=[AICandidateEvaluation(
            candidate_id=candidate.candidate_id,
            approved=True,
            confidence=0.88,
            rationale="Looks good",
            priority=1,
        )],
        market_regime="trending_up",
        operating_mode="active_trading",
        market_commentary="Constructive tape.",
        mode_constraints={"confidence_floor": 0.65, "max_new_entries": 2},
    )
    approved_candidate = ApprovedCandidate(candidate=candidate, evaluation=evaluation_result.candidate_evaluations[0])
    plans = [
        OrderPlan(
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
            confidence=0.88,
            source_candidate_id=candidate.candidate_id,
        ),
        OrderPlan(
            symbol="BLOCKED",
            exchange="NSE",
            side="BUY",
            quantity=1,
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            target=Decimal("110"),
            product="MIS",
            order_type="LIMIT",
            strategy_tag="momentum",
            capital_allocated=Decimal("100"),
            risk_reward=2.0,
            confidence=0.80,
            source_candidate_id="blocked-id",
        ),
    ]

    engine = TradingEngine.__new__(TradingEngine)
    engine.candidate_builder = StubCandidateBuilder([candidate])
    engine.agent = StubAgent(evaluation_result)
    engine.session_guard = StubSessionGuard(reason=None)
    engine.portfolio_guard = StubPortfolioGuard(SimpleNamespace(approved=[plans[0]], blocked={}))
    engine.capital_manager = StubCapitalManager(plans)
    engine.signal_validator = StubSignalValidator()
    engine.max_auto_pick_symbols = 10
    engine._ohlcv_frames = {"AAA": object()}
    engine._selected_symbols = ["AAA"]

    bundle = await engine._prepare_phase4_execution(
        _context(),
        Funds(available_cash=Decimal("10000"), used_margin=Decimal("0"), total_balance=Decimal("10000")),
        positions=[],
    )

    assert engine.candidate_builder.calls == 1
    assert engine.agent.calls == 1
    assert engine.portfolio_guard.calls == 1
    assert engine.capital_manager.calls == 1
    assert engine.signal_validator.calls == 2
    assert [candidate.candidate.symbol for candidate in bundle["approved_candidates"]] == ["AAA"]
    assert [plan.symbol for plan in bundle["order_plans"]] == ["AAA"]
    assert engine.agent.decision_history[-1]["approved_candidate_count"] == 1


@pytest.mark.anyio
async def test_prepare_phase4_execution_respects_session_block():
    candidate = _candidate("AAA")
    evaluation_result = AIEvaluationResult(
        candidate_evaluations=[AICandidateEvaluation(
            candidate_id=candidate.candidate_id,
            approved=True,
            confidence=0.88,
            rationale="Looks good",
            priority=1,
        )],
        market_regime="trending_up",
        operating_mode="active_trading",
        market_commentary="Constructive tape.",
        mode_constraints={"confidence_floor": 0.65, "max_new_entries": 2},
    )

    engine = TradingEngine.__new__(TradingEngine)
    engine.candidate_builder = StubCandidateBuilder([candidate])
    engine.agent = StubAgent(evaluation_result)
    engine.session_guard = StubSessionGuard(reason="Opening range entry block")
    engine.portfolio_guard = StubPortfolioGuard(PortfolioGuardResult(approved=[], blocked={}))
    engine.capital_manager = StubCapitalManager([])
    engine.signal_validator = StubSignalValidator()
    engine.max_auto_pick_symbols = 10
    engine._ohlcv_frames = {"AAA": object()}
    engine._selected_symbols = ["AAA"]

    bundle = await engine._prepare_phase4_execution(
        _context(),
        Funds(available_cash=Decimal("10000"), used_margin=Decimal("0"), total_balance=Decimal("10000")),
        positions=[],
    )

    assert bundle["approved_candidates"] == []
    assert bundle["order_plans"] == []
    assert engine.agent.decision_history[-1]["session_block_reason"] == "Opening range entry block"


class _StartupBrokerStub:
    is_connected = True

    async def get_funds(self):
        return {"ok": True}

    async def get_positions(self):
        return []

    async def get_order_history(self):
        return []


@pytest.mark.anyio
async def test_startup_preflight_uses_live_ai_and_repository_probes(monkeypatch):
    engine = TradingEngine.__new__(TradingEngine)
    engine.preflight = EnginePreflight(PreflightConfig())
    engine.agent = SimpleNamespace(check_provider_health=lambda: _false_probe())
    engine.risk = SimpleNamespace(is_trading_allowed=True)
    engine._tick_data = {"AAA": {"ltp": 100}}
    engine._last_tick_at = None
    engine._ai_health_cache = None
    engine._ai_health_ttl_seconds = 0.0
    engine._is_market_open = lambda _now: True

    async def _repo_down(limit=1):
        raise RuntimeError("db down")

    monkeypatch.setattr("core.engine.AgentDecisionRepository", SimpleNamespace(get_recent=_repo_down))

    report = await engine._run_startup_preflight(_StartupBrokerStub())

    assert report.overall_ok is False
    assert "market data is stale" in report.blocking_reasons
    assert "AI provider unavailable" in report.blocking_reasons
    assert "repository unavailable" in report.blocking_reasons


async def _false_probe():
    return False


@pytest.mark.anyio
async def test_decision_cycle_short_circuits_when_runtime_blocks_entries():
    engine = TradingEngine.__new__(TradingEngine)
    engine.risk = SimpleNamespace(is_trading_allowed=True)
    engine.agent = SimpleNamespace(decision_history=[])
    engine._agent_status = {
        "cycle_id": None,
        "cycle_started_at": None,
        "last_error": None,
        "progress_pct": 0,
        "selected_strategy": None,
        "signals_considered": 0,
        "signals_approved": 0,
        "signals_rejected": 0,
    }
    engine._set_agent_stage = lambda *args, **kwargs: None
    engine._push_agent_event = lambda *args, **kwargs: None
    async def _build_market_context(_now):
        return _context()
    engine._build_market_context = _build_market_context
    async def _runtime_report(_now):
        return PreflightReport(statuses=[], overall_ok=False, recommended_action="block new entries", blocking_reasons=["AI unavailable"])
    engine._run_runtime_preflight = _runtime_report
    builder = StubCandidateBuilder([_candidate("AAA")])
    engine.candidate_builder = builder
    engine._record_phase4_decision = TradingEngine._record_phase4_decision.__get__(engine, TradingEngine)

    await engine._decision_cycle(datetime(2026, 3, 23, 10, 0))

    assert builder.calls == 0
    assert engine.agent.decision_history[-1]["session_block_reason"] == "block new entries"
    assert engine.agent.decision_history[-1]["portfolio_blocked"]["runtime_health"] == ["AI unavailable"]


@pytest.mark.anyio
async def test_runtime_preflight_respects_configured_health_check_interval(monkeypatch):
    engine = TradingEngine.__new__(TradingEngine)
    engine._latest_runtime_health = None
    engine._last_runtime_health_check_at = None
    engine._health_check_interval_seconds = 60
    engine.risk = SimpleNamespace(is_trading_allowed=True)
    engine.get_broker_health_summary = lambda: {"dhan": {"healthy": True}}
    engine._market_data_is_fresh = lambda _now: True

    calls = {"ai": 0, "runtime": 0}

    async def _ai_ok():
        calls["ai"] += 1
        return True

    async def _runtime(*, broker_ok, market_data_fresh, ai_ok, risk_ok, now):
        calls["runtime"] += 1
        return PreflightReport(statuses=[], overall_ok=True, recommended_action="continue", blocking_reasons=[])

    engine._probe_ai_reachability = _ai_ok
    engine.preflight = SimpleNamespace(run_runtime=_runtime)

    first = await engine._run_runtime_preflight(datetime(2026, 3, 23, 10, 0))
    second = await engine._run_runtime_preflight(datetime(2026, 3, 23, 10, 0, 30))
    third = await engine._run_runtime_preflight(datetime(2026, 3, 23, 10, 1, 1))

    assert first is second
    assert third is not second
    assert calls == {"ai": 2, "runtime": 2}
