from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from core.replay_engine import ReplayConfig, ReplayFillModel


def _plan(**overrides):
    data = {
        "side": "BUY",
        "entry_price": Decimal("100"),
        "order_type": "MARKET",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_market_order_fill_rule_uses_candle_and_slippage():
    model = ReplayFillModel(ReplayConfig(symbols=["AAA"], slippage_pct=0.001, latency_slippage_bps=0.0))
    candle = {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 100000}

    fill = model.resolve_entry(candle, _plan(order_type="MARKET"))

    assert fill.filled is True
    assert fill.fill_price > Decimal("100")
    assert fill.trigger_reason == "market"


def test_limit_order_fill_rule_requires_price_range_touch():
    model = ReplayFillModel(ReplayConfig(symbols=["AAA"]))
    candle = {"open": 103, "high": 105, "low": 101, "close": 104, "volume": 100000}

    no_fill = model.resolve_entry(candle, _plan(order_type="LIMIT", entry_price=Decimal("100")))
    yes_fill = model.resolve_entry(candle, _plan(order_type="LIMIT", entry_price=Decimal("102")))

    assert no_fill.filled is False
    assert yes_fill.filled is True
    assert yes_fill.fill_price == Decimal("102")


def test_buy_limit_fills_when_candle_trades_entirely_below_limit():
    model = ReplayFillModel(ReplayConfig(symbols=["AAA"]))
    candle = {"open": 99, "high": 99, "low": 95, "close": 96, "volume": 100000}

    fill = model.resolve_entry(candle, _plan(order_type="LIMIT", entry_price=Decimal("100")))

    assert fill.filled is True
    assert fill.fill_price == Decimal("99")
    assert fill.trigger_reason == "limit_improved"


def test_short_limit_fills_when_candle_trades_entirely_above_limit():
    model = ReplayFillModel(ReplayConfig(symbols=["AAA"]))
    candle = {"open": 105, "high": 108, "low": 105, "close": 107, "volume": 100000}

    fill = model.resolve_entry(candle, _plan(side="SHORT", order_type="LIMIT", entry_price=Decimal("100")))

    assert fill.filled is True
    assert fill.fill_price == Decimal("105")
    assert fill.trigger_reason == "limit_improved"


def test_stop_loss_trigger_rule_is_deterministic():
    model = ReplayFillModel(ReplayConfig(symbols=["AAA"]))
    candle = {"open": 100, "high": 101, "low": 94, "close": 95, "volume": 100000}
    position = {"qty": Decimal("5"), "stop_loss": Decimal("95"), "target": Decimal("110")}

    fill = model.resolve_protective_exit(candle, position)

    assert fill.filled is True
    assert fill.fill_price == Decimal("95")
    assert fill.trigger_reason == "stop_loss"


def test_slippage_changes_market_fill_price():
    low_slip = ReplayFillModel(ReplayConfig(symbols=["AAA"], slippage_pct=0.0001, latency_slippage_bps=0.0))
    high_slip = ReplayFillModel(ReplayConfig(symbols=["AAA"], slippage_pct=0.003, latency_slippage_bps=0.0))
    candle = {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 100000}

    low_fill = low_slip.resolve_entry(candle, _plan(order_type="MARKET"))
    high_fill = high_slip.resolve_entry(candle, _plan(order_type="MARKET"))

    assert high_fill.fill_price > low_fill.fill_price


def test_stop_vs_target_precedence_defaults_to_stop_first():
    model = ReplayFillModel(ReplayConfig(symbols=["AAA"], ambiguity_rule="stop_first"))
    candle = {"open": 100, "high": 111, "low": 94, "close": 108, "volume": 100000}
    position = {"qty": Decimal("5"), "stop_loss": Decimal("95"), "target": Decimal("110")}

    fill = model.resolve_protective_exit(candle, position)

    assert fill.filled is True
    assert fill.fill_price == Decimal("95")
    assert fill.trigger_reason == "stop_loss"
