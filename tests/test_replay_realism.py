from datetime import datetime, timezone
from decimal import Decimal

from core.replay_engine import ReplayConfig, _estimate_replay_slippage_pct, _simulate_partial_fill


def test_replay_slippage_increases_for_wide_range_and_latency():
    cfg = ReplayConfig(symbols=["AAA"], slippage_pct=0.0005, latency_slippage_bps=5)
    candle = {"open": 100, "high": 110, "low": 95, "close": 108, "volume": 5000}

    slip = _estimate_replay_slippage_pct(candle, cfg)

    assert slip > cfg.slippage_pct


def test_replay_partial_fill_can_reduce_requested_quantity():
    cfg = ReplayConfig(symbols=["AAA"], partial_fill_probability=1.0)

    filled = _simulate_partial_fill(Decimal("10"), 1, datetime(2024, 1, 2, tzinfo=timezone.utc), "AAA", cfg)

    assert filled < Decimal("10")
    assert filled >= Decimal("1")
