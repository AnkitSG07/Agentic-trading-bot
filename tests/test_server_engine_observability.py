from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
import sys
import types
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from brokers.base import Funds
from core.pipeline_models import HealthStatus, ReconciliationStatus
from core.preflight import PreflightReport


_ORIGINAL_CORE_ENGINE = sys.modules.get("core.engine")
_ORIGINAL_REPOSITORY = sys.modules.get("database.repository")

fake_engine_mod = types.ModuleType("core.engine")
fake_engine_mod.get_engine = lambda: None
fake_engine_mod.set_engine = lambda engine: None
fake_engine_mod.TradingEngine = object
fake_engine_mod.DEFAULT_WATCHLIST = ["AAA", "BBB"]
sys.modules["core.engine"] = fake_engine_mod

fake_repo_mod = types.ModuleType("database.repository")


class _DummyRepo:
    @staticmethod
    async def get_recent(limit=20):
        return []


fake_repo_mod.AgentDecisionRepository = _DummyRepo
fake_repo_mod.DailySummaryRepository = _DummyRepo
fake_repo_mod.PositionRepository = _DummyRepo
fake_repo_mod.RiskEventRepository = _DummyRepo
fake_repo_mod.TradeRepository = _DummyRepo
fake_repo_mod.ReplayRunRepository = _DummyRepo
sys.modules["database.repository"] = fake_repo_mod

from core.server import app
import core.server as server_module

if _ORIGINAL_CORE_ENGINE is not None:
    sys.modules["core.engine"] = _ORIGINAL_CORE_ENGINE
else:
    sys.modules.pop("core.engine", None)

if _ORIGINAL_REPOSITORY is not None:
    sys.modules["database.repository"] = _ORIGINAL_REPOSITORY
else:
    sys.modules.pop("database.repository", None)


def _report(action: str, reason: str) -> PreflightReport:
    status = HealthStatus(
        broker_ok=action == "continue",
        data_feed_ok=action == "continue",
        ai_ok=action == "continue",
        last_checked=datetime(2026, 3, 23, 10, 0),
        degraded_reason=None if action == "continue" else reason,
        severity="info" if action == "continue" else "warning",
        recommended_action=action,
    )
    return PreflightReport(
        statuses=[status],
        overall_ok=action == "continue",
        recommended_action=action,
        blocking_reasons=[] if action == "continue" else [reason],
    )


def _engine_stub():
    return SimpleNamespace(
        _running=True,
        _primary_broker_name="dhan",
        _replica_broker_name="zerodha",
        _replication_enabled=True,
        _replication_status="ok",
        _last_replication_error="",
        _latest_startup_preflight=_report("continue", ""),
        _latest_runtime_health=_report("block new entries", "AI unavailable"),
        _latest_reconciliation_status=ReconciliationStatus(
            positions_match=True,
            orders_match=False,
            drift_details=["order drift"],
            action_taken="log_only",
        ),
        _pending_execution_reconciliation={"ord-1": {"symbol": "AAA"}},
        _last_known_funds=Funds(
            available_cash=Decimal("1000"),
            used_margin=Decimal("0"),
            total_balance=Decimal("1000"),
        ),
        _agent_status={
            "signals_considered": 4,
            "signals_approved": 1,
            "signals_rejected": 3,
        },
        risk=SimpleNamespace(
            _kill_switch=False,
            is_trading_allowed=True,
            get_daily_summary=lambda: {"kill_switch_reason": ""},
        ),
        agent=SimpleNamespace(decision_history=[{
            "operating_mode": "selective",
            "mode_constraints": {"max_new_entries": 1},
            "approved_candidate_count": 1,
        }]),
        session_guard=SimpleNamespace(
            active_block_reason=lambda _now: "Opening range entry block",
            is_entry_allowed=lambda _now: False,
            is_exit_allowed=lambda _now: True,
        ),
        capital_manager=SimpleNamespace(
            _spendable_capital=lambda funds: Decimal("900")
        ),
        get_broker_health_summary=lambda: {"dhan": {"healthy": True, "score": 100}},
        get_engine_status=lambda: {"selection_mode": "watchlist", "active_symbols": ["AAA"]},
        tracker=SimpleNamespace(get_all=lambda: []),
    )


def test_engine_status_exposes_autonomous_observability(monkeypatch):
    monkeypatch.setattr(server_module, "get_engine", lambda: _engine_stub())
    client = TestClient(app)

    response = client.get("/api/engine/status")

    assert response.status_code == 200
    body = response.json()
    assert body["preflight_state"]["overall_ok"] is True
    assert body["runtime_health_state"]["recommended_action"] == "block new entries"
    assert body["reconciliation_state"]["orders_match"] is False
    assert body["ai_operating_mode"] == "selective"
    assert body["candidate_stats"]["approved"] == 1
    assert body["spendable_capital"] == 900.0
    assert body["session_block"]["active_reason"] == "Opening range entry block"


def test_engine_preflight_and_health_endpoints(monkeypatch):
    monkeypatch.setattr(server_module, "get_engine", lambda: _engine_stub())
    client = TestClient(app)

    preflight = client.get("/api/engine/preflight")
    health = client.get("/api/engine/health")

    assert preflight.status_code == 200
    assert preflight.json()["preflight_state"]["recommended_action"] == "continue"
    assert health.status_code == 200
    assert health.json()["runtime_health_state"]["recommended_action"] == "block new entries"
    assert health.json()["pause_or_block_reason"] == "AI unavailable"


def test_engine_status_uses_timezone_aware_session_clock(monkeypatch):
    seen = {}

    def _capture(name, value):
        seen[name] = value
        return {"active_block_reason": "Opening range entry block", "is_entry_allowed": False, "is_exit_allowed": True}[name]

    guard = SimpleNamespace(
        config=SimpleNamespace(timezone=ZoneInfo("Asia/Kolkata")),
        active_block_reason=lambda now: _capture("active_block_reason", now),
        is_entry_allowed=lambda now: _capture("is_entry_allowed", now),
        is_exit_allowed=lambda now: _capture("is_exit_allowed", now),
    )
    engine = _engine_stub()
    engine.session_guard = guard
    monkeypatch.setattr(server_module, "get_engine", lambda: engine)
    client = TestClient(app)

    response = client.get("/api/engine/status")

    assert response.status_code == 200
    assert all(value.tzinfo is not None for value in seen.values())
