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




def test_selector_candidate_universe_prefers_loaded_instrument_cache(monkeypatch):
    class _Instrument:
        def __init__(self, symbol, exchange="NSE", instrument_type="EQ"):
            self.symbol = symbol
            self.exchange = exchange
            self.instrument_type = instrument_type

    engine = types.SimpleNamespace(
        _nse_equity_symbols_cache=["CHEAP", "MIDCAP", "RELIANCE"],
        _instrument_cache={
            "CHEAP": _Instrument("CHEAP"),
            "MIDCAP": _Instrument("MIDCAP"),
            "RELIANCE": _Instrument("RELIANCE"),
        },
    )
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

    from core.server import _selector_candidate_universe

    assert _selector_candidate_universe([]) == ["CHEAP", "MIDCAP", "RELIANCE"]


def test_selector_candidate_universe_falls_back_to_default_watchlist(monkeypatch):
    monkeypatch.setattr("core.server.get_engine", lambda: None)

    from core.server import _selector_candidate_universe

    assert _selector_candidate_universe([]) == ["AAA", "BBB", "CCC"]


def test_bounded_live_quote_symbols_caps_auto_derived_universe(monkeypatch):
    engine = types.SimpleNamespace(max_live_quote_symbols=3)
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

    from core.server import _bounded_live_quote_symbols

    assert _bounded_live_quote_symbols(["AAA", "BBB", "CCC", "DDD", "EEE"]) == ["AAA", "BBB", "CCC"]

@pytest.mark.asyncio
async def test_budget_selection_can_pick_low_priced_symbols_from_broader_dhan_universe(monkeypatch):
    async def fake_fetch_window(symbols, exchange, timeframe, start_date, end_date):
        rows = []
        for symbol, base in [("CHEAP", 48), ("MIDCAP", 96), ("EXPENSIVE", 2500)]:
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
                    "volume": 200000,
                })
        return rows

    sys.modules["database.repository"].HistoricalCandleRepository = type(
        "_FakeHistoricalCandleRepository",
        (),
        {"fetch_window": staticmethod(fake_fetch_window)},
    )

    class _Quote:
        def __init__(self, ltp):
            self.ltp = ltp

    class _Broker:
        async def get_quote(self, instruments):
            prices = {"CHEAP": 48, "MIDCAP": 96, "EXPENSIVE": 2500, "NOHISTORY": 105}
            return {inst.symbol: _Quote(ltp=prices[inst.symbol]) for inst in instruments if inst.symbol in prices}

    class _Instrument:
        def __init__(self, symbol, exchange="NSE", instrument_type="EQ"):
            self.symbol = symbol
            self.exchange = exchange
            self.instrument_type = instrument_type

    engine = types.SimpleNamespace(
        primary_broker=_Broker(),
        _nse_equity_symbols_cache=["CHEAP", "MIDCAP", "EXPENSIVE", "NOHISTORY"],
        _instrument_cache={symbol: _Instrument(symbol) for symbol in ["CHEAP", "MIDCAP", "EXPENSIVE", "NOHISTORY"]},
        min_stock_price=10,
        max_stock_price=5000,
        min_avg_daily_volume=1000,
        min_avg_daily_turnover=100000,
        max_auto_pick_symbols=2,
    )
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

    from core.server import ReplaySelectionRequest, _resolve_budget_selection

    result = await _resolve_budget_selection(ReplaySelectionRequest(
        symbols=[],
        budget_cap=1000,
        max_auto_symbols=2,
        exchange="NSE",
        timeframe="day",
    ))

    assert result["candidate_symbols"] == ["CHEAP", "MIDCAP", "EXPENSIVE", "NOHISTORY"]
    assert result["selected_symbols"] == ["CHEAP", "MIDCAP"]
    assert all(item["symbol"] != "NOHISTORY" for item in result["recommendations"])
    assert all(item["estimated_cost"] <= 1000 for item in result["recommendations"])



@pytest.mark.asyncio
async def test_budget_selection_rejects_invalid_budget(monkeypatch):
    monkeypatch.setattr("core.server.get_engine", lambda: None)

    from core.server import ReplaySelectionRequest, _resolve_budget_selection

    with pytest.raises(Exception) as exc_info:
        await _resolve_budget_selection(ReplaySelectionRequest(
            symbols=["AAA"],
            budget_cap=0,
            max_auto_symbols=2,
            exchange="NSE",
            timeframe="day",
        ))

    assert exc_info.value.status_code == 400
    assert "Invalid budget" in exc_info.value.detail


@pytest.mark.asyncio
async def test_budget_selection_distinguishes_no_affordable_live_universe(monkeypatch):
    class _Quote:
        def __init__(self, ltp):
            self.ltp = ltp

    class _Broker:
        async def get_quote(self, instruments):
            return {inst.symbol: _Quote(ltp=5000) for inst in instruments}

    class _Instrument:
        def __init__(self, symbol, exchange="NSE", instrument_type="EQ"):
            self.symbol = symbol
            self.exchange = exchange
            self.instrument_type = instrument_type

    engine = types.SimpleNamespace(
        primary_broker=_Broker(),
        _instrument_cache={"EXPENSIVE": _Instrument("EXPENSIVE")},
        _nse_equity_symbols_cache=["EXPENSIVE"],
        min_stock_price=10,
        max_stock_price=10000,
        min_avg_daily_volume=1000,
        min_avg_daily_turnover=100000,
        max_auto_pick_symbols=5,
    )
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

    from core.server import ReplaySelectionRequest, _resolve_budget_selection

    with pytest.raises(Exception) as exc_info:
        await _resolve_budget_selection(ReplaySelectionRequest(
            symbols=[],
            budget_cap=100,
            max_auto_symbols=1,
            exchange="NSE",
            timeframe="day",
        ))

    assert exc_info.value.status_code == 400
    assert "No affordable instruments found from live universe data" in exc_info.value.detail


@pytest.mark.asyncio
async def test_budget_selection_can_pick_from_live_universe_before_backfill(monkeypatch):
    fetch_calls = []

    async def fake_fetch_window(symbols, exchange, timeframe, start_date, end_date):
        fetch_calls.append(list(symbols))
        return []

    class _Quote:
        def __init__(self, ltp):
            self.ltp = ltp

    class _Broker:
        async def get_quote(self, instruments):
            prices = {"AFFORD": 95, "MID": 210, "EXPENSIVE": 5000}
            return {inst.symbol: _Quote(ltp=prices[inst.symbol]) for inst in instruments if inst.symbol in prices}

    class _Instrument:
        def __init__(self, symbol, exchange="NSE", instrument_type="EQ"):
            self.symbol = symbol
            self.exchange = exchange
            self.instrument_type = instrument_type

    sys.modules["database.repository"].HistoricalCandleRepository = type(
        "_FreshSelectionHistoricalRepo",
        (),
        {"fetch_window": staticmethod(fake_fetch_window)},
    )

    engine = types.SimpleNamespace(
        primary_broker=_Broker(),
        _instrument_cache={
            "AFFORD": _Instrument("AFFORD"),
            "MID": _Instrument("MID"),
            "EXPENSIVE": _Instrument("EXPENSIVE"),
        },
        _nse_equity_symbols_cache=["AFFORD", "MID", "EXPENSIVE"],
        min_stock_price=10,
        max_stock_price=10000,
        min_avg_daily_volume=1000,
        min_avg_daily_turnover=100000,
        max_auto_pick_symbols=2,
    )
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

    from core.server import ReplaySelectionRequest, _resolve_budget_selection

    result = await _resolve_budget_selection(ReplaySelectionRequest(
        symbols=[],
        budget_cap=500,
        max_auto_symbols=2,
        exchange="NSE",
        timeframe="day",
    ))

    assert fetch_calls == [["AFFORD", "MID"]]
    assert result["selected_symbols"] == ["AFFORD", "MID"]
    assert result["historical_candidate_symbols"] == []
    assert all(item["selection_source"] == "live_universe" for item in result["recommendations"])


def test_backfill_and_run_flow_succeeds_when_symbols_are_selected_before_replay_backfill(monkeypatch):
    historical_calls = []

    async def fake_fetch_window(symbols, exchange, timeframe, start_date, end_date):
        historical_calls.append(list(symbols))
        if symbols == ["AFFORD", "MID"]:
            rows = []
            for symbol, base in [("AFFORD", 95), ("MID", 210)]:
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
                        "volume": 250000,
                    })
            return rows
        return []

    async def fake_create_and_start_replay(config, payload):
        return {"run_id": "run-fresh-1", "status": "queued", "payload": payload}

    class _Quote:
        def __init__(self, ltp):
            self.ltp = ltp

    class _Broker:
        async def get_quote(self, instruments):
            prices = {"AFFORD": 95, "MID": 210, "EXPENSIVE": 5000}
            return {inst.symbol: _Quote(ltp=prices[inst.symbol]) for inst in instruments if inst.symbol in prices}

    class _Instrument:
        def __init__(self, symbol, exchange="NSE", instrument_type="EQ"):
            self.symbol = symbol
            self.exchange = exchange
            self.instrument_type = instrument_type

    sys.modules["database.repository"].HistoricalCandleRepository = type(
        "_FreshRunHistoricalRepo",
        (),
        {"fetch_window": staticmethod(fake_fetch_window)},
    )
    fake_loader = types.ModuleType("config.loader")
    fake_loader.load_config = lambda: {"app": {}}
    sys.modules["config.loader"] = fake_loader
    fake_replay_engine = types.ModuleType("core.replay_engine")
    fake_replay_engine.create_and_start_replay = fake_create_and_start_replay
    sys.modules["core.replay_engine"] = fake_replay_engine

    engine = types.SimpleNamespace(
        primary_broker=_Broker(),
        _instrument_cache={
            "AFFORD": _Instrument("AFFORD"),
            "MID": _Instrument("MID"),
            "EXPENSIVE": _Instrument("EXPENSIVE"),
        },
        _nse_equity_symbols_cache=["AFFORD", "MID", "EXPENSIVE"],
        min_stock_price=10,
        max_stock_price=10000,
        min_avg_daily_volume=1000,
        min_avg_daily_turnover=100000,
        max_auto_pick_symbols=2,
    )
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

    client = TestClient(app)
    selection_response = client.post("/api/replay/select-symbols", json={
        "selection_mode": "auto",
        "symbols": [],
        "budget_cap": 500,
        "max_auto_symbols": 2,
        "exchange": "NSE",
        "timeframe": "day",
    })
    assert selection_response.status_code == 200
    selected_symbols = selection_response.json()["selected_symbols"]
    assert selected_symbols == ["AFFORD", "MID"]

    run_response = client.post("/api/replay/runs", json={
        "selection_mode": "auto",
        "symbols": selected_symbols,
        "budget_cap": 500,
        "max_auto_symbols": 2,
        "exchange": "NSE",
        "timeframe": "day",
    })

    assert run_response.status_code == 200
    body = run_response.json()
    assert body["payload"]["symbols"] == ["AFFORD", "MID"]
    assert historical_calls[0] == ["AFFORD", "MID"]
    assert historical_calls[-1] == ["AFFORD", "MID"]


def test_replay_run_reports_missing_history_after_backfill(monkeypatch):
    async def fake_fetch_window(symbols, exchange, timeframe, start_date, end_date):
        return []

    async def fake_create_and_start_replay(config, payload):
        return {"run_id": "run-fresh-2", "status": "queued", "payload": payload}

    sys.modules["database.repository"].HistoricalCandleRepository = type(
        "_NoHistoryRepo",
        (),
        {"fetch_window": staticmethod(fake_fetch_window)},
    )
    fake_loader = types.ModuleType("config.loader")
    fake_loader.load_config = lambda: {"app": {}}
    sys.modules["config.loader"] = fake_loader
    fake_replay_engine = types.ModuleType("core.replay_engine")
    fake_replay_engine.create_and_start_replay = fake_create_and_start_replay
    sys.modules["core.replay_engine"] = fake_replay_engine

    class _Quote:
        def __init__(self, ltp):
            self.ltp = ltp

    class _Broker:
        async def get_quote(self, instruments):
            prices = {"AAA": 90, "BBB": 130, "CCC": 1200}
            return {inst.symbol: _Quote(ltp=prices[inst.symbol]) for inst in instruments if inst.symbol in prices}

    class _Instrument:
        def __init__(self, symbol, exchange="NSE", instrument_type="EQ"):
            self.symbol = symbol
            self.exchange = exchange
            self.instrument_type = instrument_type

    engine = types.SimpleNamespace(
        primary_broker=_Broker(),
        _instrument_cache={symbol: _Instrument(symbol) for symbol in ["AAA", "BBB", "CCC"]},
        _nse_equity_symbols_cache=["AAA", "BBB", "CCC"],
        min_stock_price=10,
        max_stock_price=5000,
        min_avg_daily_volume=1000,
        min_avg_daily_turnover=100000,
        max_auto_pick_symbols=2,
    )
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

    client = TestClient(app)
    response = client.post("/api/replay/runs", json={
        "selection_mode": "manual",
        "symbols": ["AFFORD"],
        "exchange": "NSE",
        "timeframe": "day",
    })

    assert response.status_code == 400
    assert "No historical candles found after backfill" in response.json()["detail"]


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

    class _Quote:
        def __init__(self, ltp):
            self.ltp = ltp

    class _Broker:
        async def get_quote(self, instruments):
            prices = {"AAA": 90, "BBB": 130, "CCC": 1200}
            return {inst.symbol: _Quote(ltp=prices[inst.symbol]) for inst in instruments if inst.symbol in prices}

    class _Instrument:
        def __init__(self, symbol, exchange="NSE", instrument_type="EQ"):
            self.symbol = symbol
            self.exchange = exchange
            self.instrument_type = instrument_type

    engine = types.SimpleNamespace(
        primary_broker=_Broker(),
        _instrument_cache={symbol: _Instrument(symbol) for symbol in ["AAA", "BBB", "CCC"]},
        _nse_equity_symbols_cache=["AAA", "BBB", "CCC"],
        min_stock_price=10,
        max_stock_price=5000,
        min_avg_daily_volume=1000,
        min_avg_daily_turnover=100000,
        max_auto_pick_symbols=2,
    )
    monkeypatch.setattr("core.server.get_engine", lambda: engine)

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
