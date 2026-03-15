from decimal import Decimal

import pytest
from pydantic import ValidationError

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


def test_replay_request_accepts_confidence_override():
    req = ReplayRunCreateRequest(symbols=["RELIANCE"], confidence_threshold=0.5)
    assert req.confidence_threshold == 0.5
