from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.pipeline_models import OrderPlan, PreflightCheck, PreflightResult


@dataclass(slots=True)
class SignalValidatorConfig:
    min_risk_reward: float = 1.5
    price_tolerance_pct: float = 0.02
    valid_exchanges: tuple[str, ...] = ("NSE", "BSE", "NFO", "MCX")
    valid_products: tuple[str, ...] = ("MIS", "CNC", "NRML")


class SignalValidator:
    def __init__(self, config: SignalValidatorConfig | None = None) -> None:
        self.config = config or SignalValidatorConfig()

    def validate(
        self,
        order_plan: OrderPlan,
        *,
        current_price_reference: Decimal,
        available_capital: Decimal,
    ) -> PreflightResult:
        checks = [
            self._geometry_check(order_plan),
            self._risk_reward_check(order_plan),
            self._price_tolerance_check(order_plan, current_price_reference),
            self._quantity_check(order_plan),
            self._affordability_check(order_plan, available_capital),
            self._product_exchange_check(order_plan),
        ]
        blocking_reasons = [check.message for check in checks if not check.passed and check.severity == "blocking"]
        return PreflightResult(checks=checks, all_passed=not blocking_reasons, blocking_reasons=blocking_reasons)

    def _geometry_check(self, order_plan: OrderPlan) -> PreflightCheck:
        side = order_plan.side.upper()
        entry = order_plan.entry_price
        stop = order_plan.stop_loss
        target = order_plan.target
        passed = False
        if side in {"BUY", "COVER"}:
            passed = stop < entry < target
        elif side in {"SELL", "SHORT"}:
            passed = target < entry < stop
        return PreflightCheck(
            check_name="geometry",
            passed=passed,
            severity="blocking",
            message="Order geometry is valid" if passed else f"Invalid price geometry for {side}",
            recommended_action="Adjust entry, stop loss, and target levels",
        )

    def _risk_reward_check(self, order_plan: OrderPlan) -> PreflightCheck:
        passed = float(order_plan.risk_reward) >= float(self.config.min_risk_reward)
        return PreflightCheck(
            check_name="risk_reward",
            passed=passed,
            severity="blocking",
            message="Risk/reward meets threshold" if passed else f"Risk/reward {order_plan.risk_reward:.2f} below minimum {self.config.min_risk_reward:.2f}",
            recommended_action="Raise target or tighten stop loss",
        )

    def _price_tolerance_check(self, order_plan: OrderPlan, current_price_reference: Decimal) -> PreflightCheck:
        reference = float(current_price_reference)
        entry = float(order_plan.entry_price)
        if reference <= 0:
            passed = False
        else:
            passed = abs(entry - reference) / reference <= float(self.config.price_tolerance_pct)
        return PreflightCheck(
            check_name="price_tolerance",
            passed=passed,
            severity="blocking",
            message="Entry is within live price tolerance" if passed else "Entry price is outside live price tolerance",
            recommended_action="Refresh quote and rebuild order plan",
        )

    @staticmethod
    def _quantity_check(order_plan: OrderPlan) -> PreflightCheck:
        passed = int(order_plan.quantity) > 0
        return PreflightCheck(
            check_name="quantity",
            passed=passed,
            severity="blocking",
            message="Quantity is valid" if passed else "Quantity must be greater than zero",
            recommended_action="Increase quantity to a positive integer",
        )

    @staticmethod
    def _affordability_check(order_plan: OrderPlan, available_capital: Decimal) -> PreflightCheck:
        required = order_plan.entry_price * Decimal(order_plan.quantity)
        allocated = order_plan.capital_allocated
        limit = min(required, allocated) if allocated > 0 else required
        passed = required <= available_capital and required <= allocated
        return PreflightCheck(
            check_name="affordability",
            passed=passed,
            severity="blocking",
            message="Capital is sufficient" if passed else f"Required capital {required} exceeds allowed capital {max(available_capital, limit)}",
            recommended_action="Reduce size or allocate more capital",
        )

    def _product_exchange_check(self, order_plan: OrderPlan) -> PreflightCheck:
        exchange = order_plan.exchange.upper()
        product = order_plan.product.upper()
        side = order_plan.side.upper()
        valid = exchange in self.config.valid_exchanges and product in self.config.valid_products
        if valid and product == "CNC" and side in {"SHORT", "COVER"}:
            valid = False
        return PreflightCheck(
            check_name="exchange_product_action",
            passed=valid,
            severity="blocking",
            message="Exchange/product/action combination is valid" if valid else "Invalid exchange/product/action combination",
            recommended_action="Use a supported exchange/product/action combination",
        )
