from decimal import Decimal

import pytest

from brokers.base import Funds
from risk.manager import RiskConfig, RiskManager


@pytest.mark.asyncio
async def test_check_pre_trade_adjusts_quantity_then_still_applies_auto_sl():
    mgr = RiskManager(RiskConfig(max_capital_per_trade_pct=50.0, max_order_value_absolute=1200.0, min_cash_buffer=300.0))
    await mgr.initialize(Funds(available_cash=Decimal("5000"), used_margin=Decimal("0"), total_balance=Decimal("5000")))

    result = await mgr.check_pre_trade(
        symbol="RELIANCE",
        side="BUY",
        quantity=10,
        entry_price=Decimal("500"),
        stop_loss=None,
        open_positions=[],
        funds=Funds(available_cash=Decimal("5000"), used_margin=Decimal("0"), total_balance=Decimal("5000")),
    )

    assert result.approved is True
    assert result.adjusted_quantity == 2
    assert result.adjusted_sl is not None
    assert "auto stop-loss applied" in result.reason


@pytest.mark.asyncio
async def test_tiny_account_mode_enforces_stricter_spend_limit():
    mgr = RiskManager(RiskConfig(max_capital_per_trade_pct=90.0, min_cash_buffer=100.0, tiny_account_mode=True))
    await mgr.initialize(Funds(available_cash=Decimal("1000"), used_margin=Decimal("0"), total_balance=Decimal("1000")))

    result = await mgr.check_pre_trade(
        symbol="SBIN",
        side="BUY",
        quantity=9,
        entry_price=Decimal("100"),
        stop_loss=Decimal("97"),
        open_positions=[],
        funds=Funds(available_cash=Decimal("1000"), used_margin=Decimal("0"), total_balance=Decimal("1000")),
    )

    assert result.approved is True
    assert result.adjusted_quantity == 7
    assert "quantity adjusted" in result.reason
