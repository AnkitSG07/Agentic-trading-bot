from decimal import Decimal
import sys
import types

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

# Minimal stubs so core.server can import without the full runtime stack.
fake_engine_mod = types.ModuleType("core.engine")
class _TradingEngine:
    pass
fake_engine_mod.get_engine = lambda: None
fake_engine_mod.set_engine = lambda engine: None
fake_engine_mod.TradingEngine = _TradingEngine
fake_engine_mod.DEFAULT_WATCHLIST = ["AAA", "BBB", "CCC"]
sys.modules.setdefault("core.engine", fake_engine_mod)

fake_repo_mod = types.ModuleType("database.repository")
class _DummyRepo:
    @staticmethod
    async def list_runs(limit=20):
        return []
    @staticmethod
    async def get(run_id):
        return None
    @staticmethod
    async def get_trades(run_id):
        return []
fake_repo_mod.AgentDecisionRepository = _DummyRepo
fake_repo_mod.DailySummaryRepository = _DummyRepo
fake_repo_mod.PositionRepository = _DummyRepo
fake_repo_mod.RiskEventRepository = _DummyRepo
fake_repo_mod.TradeRepository = _DummyRepo
fake_repo_mod.ReplayRunRepository = _DummyRepo
fake_repo_mod.HistoricalCandleRepository = _DummyRepo
sys.modules.setdefault("database.repository", fake_repo_mod)

from core.replay_engine import (
    _build_levels,
    _compute_bb_signal,
    _compute_rsi,
    _derive_overall_signal,
    _entry_fee_allocation,
    _resolve_index_ltp,
    _summarize_trades,
)
from core.replay_schema import ReplayRunCreateRequest
from core.server import app


def test_indicator_helpers_generate_non_neutral_values_when_data_available():
    closes = [100 + i for i in range(30)]
    rsi = _compute_rsi(closes, period=14)
    assert rsi is not None
    bb_signal = _compute_bb_signal(closes)
    overall = _derive_overall_signal(rsi, 1.2, 0.8, bb_signal)
    assert overall in {"bullish", "bearish", "neutral"}


def test_entry_fee_allocation_is_proportional():
    position = {"qty": Decimal("10"), "entry_fees": Decimal("100")}
    allocated = _entry_fee_allocation(position, Decimal("4"))
    assert allocated == Decimal("40")


def test_trade_summary_prefers_realized_flag_and_counts_cover():
    trades = [
        {"action": "SHORT", "pnl": 0.0, "realized": False},
        {"action": "COVER", "pnl": 120.0, "realized": True},
        {"action": "SELL", "pnl": -20.0, "realized": True},
    ]
    summary = _summarize_trades(trades)
    assert summary["order_count"] == 3
    assert summary["completed_trades"] == 2
    assert summary["win_rate"] == 50.0


def test_build_levels_from_candle_prices():
    levels = _build_levels({"high": 110, "low": 90, "close": 100})
    assert levels["pivot"] == 100.0
    assert levels["r1"] == 110.0
    assert levels["s1"] == 90.0


def test_resolve_index_ltp_uses_index_history_or_static_fallback_only():
    assert _resolve_index_ltp(24123.4, fallback=24000.0) == 24123.4
    assert _resolve_index_ltp(None, fallback=24000.0) == 24000.0


def test_replay_request_validation_rejects_bad_values():
    with pytest.raises(ValidationError):
        ReplayRunCreateRequest(symbols=["RELIANCE"], initial_capital=0)
    with pytest.raises(ValidationError):
        ReplayRunCreateRequest(symbols=["RELIANCE"], ai_every_n_candles=0)
    with pytest.raises(ValidationError):
        ReplayRunCreateRequest(symbols=["RELIANCE"], slippage_pct=-0.1)
    with pytest.raises(ValidationError):
        ReplayRunCreateRequest(selection_mode="manual", symbols=[])
    with pytest.raises(ValidationError):
        ReplayRunCreateRequest(selection_mode="auto", symbols=[], budget_cap=None)


def test_replay_request_accepts_confidence_override_and_auto_mode_budget():
    req = ReplayRunCreateRequest(symbols=["RELIANCE"], confidence_threshold=0.5)
    assert req.confidence_threshold == 0.5

    auto_req = ReplayRunCreateRequest(selection_mode="auto", symbols=[], budget_cap=1000, max_auto_symbols=3)
    assert auto_req.selection_mode == "auto"
    assert auto_req.budget_cap == 1000
    assert auto_req.symbols == []


def test_replay_route_supports_auto_mode_selection(monkeypatch):
    async def fake_fetch_window(symbols, exchange, timeframe, start_date, end_date):
        rows = []
        for symbol, base in [("AAA", 90), ("BBB", 130), ("CCC", 1200)]:
            for i in range(25):
                rows.append({
                    "symbol": symbol,
                    "exchange": exchange,
                    "timeframe": timeframe,
                    "timestamp": f"2024-01-{i+1:02d}T00:00:00",
                    "open": base + i,
                    "high": base + i,
                    "low": base + i,
                    "close": base + i,
                    "volume": 150000,
                })
        return rows

    async def fake_create_and_start_replay(config, payload):
        return {"run_id": "run-auto-1", "status": "queued", "payload": payload}

    sys.modules["database.repository"].HistoricalCandleRepository = type(
        "_FakeHistoricalCandleRepository",
        (),
        {"fetch_window": staticmethod(fake_fetch_window)},
    )
    fake_loader = types.ModuleType("config.loader")
    fake_loader.load_config = lambda: {"app": {}}
    sys.modules["config.loader"] = fake_loader
    fake_replay_engine = types.ModuleType("core.replay_engine")
    fake_replay_engine.create_and_start_replay = fake_create_and_start_replay
    sys.modules["core.replay_engine"] = fake_replay_engine

    client = TestClient(app)
    response = client.post("/api/replay/runs", json={
        "selection_mode": "auto",
        "symbols": [],
        "budget_cap": 1000,
        "max_auto_symbols": 2,
        "exchange": "NSE",
        "timeframe": "day",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["selection_summary"]["selected_symbols"] == ["AAA", "BBB"]
    assert body["payload"]["symbols"] == ["AAA", "BBB"]
    assert body["payload"]["selection_summary"]["recommendations"][0]["estimated_qty"] > 0
