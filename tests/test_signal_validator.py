from decimal import Decimal

from core.pipeline_models import OrderPlan
from core.signal_validator import SignalValidator


def _plan(**overrides):
    data = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "side": "BUY",
        "quantity": 10,
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("95"),
        "target": Decimal("110"),
        "product": "MIS",
        "order_type": "LIMIT",
        "strategy_tag": "momentum",
        "capital_allocated": Decimal("1000"),
        "risk_reward": 2.0,
        "confidence": 0.8,
        "source_candidate_id": "cand-1",
    }
    data.update(overrides)
    return OrderPlan(**data)


def test_valid_buy_geometry():
    validator = SignalValidator()
    result = validator.validate(_plan(), current_price_reference=Decimal("101"), available_capital=Decimal("2000"))

    assert result.all_passed is True


def test_invalid_buy_geometry():
    validator = SignalValidator()
    result = validator.validate(
        _plan(stop_loss=Decimal("101"), target=Decimal("99")),
        current_price_reference=Decimal("100"),
        available_capital=Decimal("2000"),
    )

    assert result.all_passed is False
    assert any(check.check_name == "geometry" and not check.passed for check in result.checks)


def test_valid_short_geometry():
    validator = SignalValidator()
    result = validator.validate(
        _plan(side="SHORT", stop_loss=Decimal("105"), target=Decimal("90")),
        current_price_reference=Decimal("99"),
        available_capital=Decimal("2000"),
    )

    assert result.all_passed is True


def test_invalid_risk_reward():
    validator = SignalValidator()
    result = validator.validate(
        _plan(risk_reward=1.0),
        current_price_reference=Decimal("100"),
        available_capital=Decimal("2000"),
    )

    assert any(check.check_name == "risk_reward" and not check.passed for check in result.checks)


def test_price_tolerance_failure():
    validator = SignalValidator()
    result = validator.validate(
        _plan(entry_price=Decimal("100")),
        current_price_reference=Decimal("110"),
        available_capital=Decimal("2000"),
    )

    assert any(check.check_name == "price_tolerance" and not check.passed for check in result.checks)


def test_quantity_and_affordability_failures():
    validator = SignalValidator()

    quantity_result = validator.validate(
        _plan(quantity=0),
        current_price_reference=Decimal("100"),
        available_capital=Decimal("2000"),
    )
    affordability_result = validator.validate(
        _plan(quantity=2, capital_allocated=Decimal("100")),
        current_price_reference=Decimal("100"),
        available_capital=Decimal("50"),
    )

    assert any(check.check_name == "quantity" and not check.passed for check in quantity_result.checks)
    assert any(check.check_name == "affordability" and not check.passed for check in affordability_result.checks)
