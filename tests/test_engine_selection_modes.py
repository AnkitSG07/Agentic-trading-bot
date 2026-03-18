import pandas as pd

from core.engine import TradingEngine


def _config(selection_mode="watchlist"):
    return {
        "agent": {"api_key_env": "MISSING", "groq_api_key_env": "MISSING", "openrouter_api_key_env": "MISSING"},
        "brokers": {"replication": {"reconcile_interval_seconds": 120}},
        "engine": {
            "selection_mode": selection_mode,
            "watchlist_symbols": ["AAA", "BBB"],
            "min_stock_price": 10,
            "max_stock_price": 1000,
            "max_auto_pick_symbols": 2,
            "min_avg_daily_volume": 1000,
        },
        "risk": {"max_order_value_absolute": 5000, "min_cash_buffer": 100, "tiny_account_mode": False},
    }


def _frame(start, volume=5000):
    closes = [start + i for i in range(30)]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [volume] * 30})


def test_runtime_overrides_update_engine_status_and_risk_config():
    engine = TradingEngine(_config())
    engine.apply_runtime_overrides({
        "selection_mode": "auto_pick",
        "watchlist_symbols": ["ZZZ"],
        "max_auto_pick_symbols": 1,
        "max_order_value_absolute": 999,
        "min_cash_buffer": 55,
        "tiny_account_mode": True,
    })

    status = engine.get_engine_status()

    assert status["selection_mode"] == "auto_pick"
    assert status["configured_watchlist_symbols"] == ["ZZZ"]
    assert engine.risk.config.max_order_value_absolute == 999
    assert engine.risk.config.min_cash_buffer == 55
    assert engine.risk.config.tiny_account_mode is True


def test_watchlist_mode_keeps_configured_symbols_active():
    engine = TradingEngine(_config("watchlist"))
    engine._ohlcv_frames = {"AAA": _frame(100), "BBB": _frame(90), "CCC": _frame(200)}
    engine._refresh_selection()

    assert engine.get_engine_status()["active_symbols"] == ["AAA", "BBB"]


def test_auto_pick_mode_uses_broader_universe_not_only_watchlist():
    engine = TradingEngine(_config("auto_pick"))
    engine._instrument_cache = {symbol: object() for symbol in ["AAA", "BBB", "CCC", "DDD"]}
    engine._ohlcv_frames = {
        "AAA": _frame(100, volume=7000),
        "BBB": pd.DataFrame({"open": [95] * 30, "high": [95] * 30, "low": [95] * 30, "close": [95] * 29 + [94], "volume": [5000] * 30}),
        "CCC": _frame(200, volume=9000),
        "DDD": pd.DataFrame({"open": [50] * 30, "high": [50] * 30, "low": [50] * 30, "close": [50] * 30, "volume": [2000] * 30}),
    }
    engine._refresh_selection()

    status = engine.get_engine_status()
    assert status["selection_mode"] == "auto_pick"
    assert set(status["active_symbols"]) == {"CCC", "AAA"}
    assert {item["symbol"] for item in status["ranked_candidates"]} >= {"CCC", "AAA"}
    assert "CCC" not in engine.configured_watchlist_symbols


def test_invalid_selection_mode_falls_back_to_watchlist():
    engine = TradingEngine(_config("bad_mode"))
    assert engine.selection_mode == "watchlist"
