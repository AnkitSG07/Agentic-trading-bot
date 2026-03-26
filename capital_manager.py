from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Iterable, Optional

from brokers.base import Funds
from core.pipeline_models import ApprovedCandidate, OrderPlan

logger = logging.getLogger("capital_manager")


@dataclass(slots=True)
class CapitalManagerConfig:
    min_cash_reserve: Decimal = Decimal("50")
    reserve_pct: Decimal = Decimal("0.05")
    max_capital_per_trade_pct: Decimal = Decimal("0.80")
    max_order_value_absolute: Optional[Decimal] = None
    min_risk_reward: float = 1.5
    min_expected_edge_score: float = 0.55
    transaction_cost_pct: Decimal = Decimal("0.0015")
    order_type: str = "LIMIT"
    max_new_entries_per_cycle: int = 2


class CapitalManager:
    """Builds budget-aware executable order plans from approved candidates."""

    def __init__(self, config: dict | CapitalManagerConfig | None = None) -> None:
        if isinstance(config, CapitalManagerConfig):
            self.config = config
            return

        config = config or {}
        max_order_value_absolute = config.get("max_order_value_absolute")
        self.config = CapitalManagerConfig(
            min_cash_reserve=Decimal(str(config.get("min_cash_reserve", 50))),
            reserve_pct=Decimal(str(config.get("reserve_pct", 0.05))),
            max_capital_per_trade_pct=Decimal(str(config.get("max_capital_per_trade_pct", 0.80))),
            max_order_value_absolute=(Decimal(str(max_order_value_absolute)) if max_order_value_absolute is not None else None),
            min_risk_reward=float(config.get("min_risk_reward", 1.5)),
            min_expected_edge_score=float(config.get("min_expected_edge_score", 0.55)),
            transaction_cost_pct=Decimal(str(config.get("transaction_cost_pct", 0.0015))),
            order_type=str(config.get("order_type", "LIMIT") or "LIMIT"),
            max_new_entries_per_cycle=max(0, int(config.get("max_new_entries_per_cycle", 2))),
        )

    def plan_from_candidates(
        self,
        approved_candidates: Iterable[ApprovedCandidate],
        funds: Funds,
        *,
        open_position_symbols: Optional[set[str]] = None,
    ) -> list[OrderPlan]:
        open_position_symbols = {symbol.upper() for symbol in (open_position_symbols or set())}
        spendable_capital = self._spendable_capital(funds)
        if spendable_capital <= 0:
            return []

        plans: list[OrderPlan] = []
        remaining_capital = spendable_capital

        for approved in self._ranked_candidates(approved_candidates):
            if len(plans) >= int(self.config.max_new_entries_per_cycle):
                break
            candidate = approved.candidate
            if candidate.symbol.upper() in open_position_symbols:
                continue
            if candidate.risk_reward < self.config.min_risk_reward:
                continue
            expected_edge_score = float(getattr(candidate, "expected_edge_score", 0.0))
            if expected_edge_score > 0 and expected_edge_score < self.config.min_expected_edge_score:
                continue
            if candidate.max_affordable_qty <= 0:
                continue

            plan = self._plan_candidate(approved, remaining_capital)
            if plan is None:
                continue

            plans.append(plan)
            remaining_capital -= plan.capital_allocated
            if remaining_capital <= 0:
                break

        return plans

    def affordability_summary(self, watchlist_data: list[dict], available_capital: float) -> list[dict]:
        spendable = self._spendable_capital(
            Funds(
                available_cash=Decimal(str(available_capital)),
                used_margin=Decimal("0"),
                total_balance=Decimal(str(available_capital)),
            )
        )
        results: list[dict] = []
        for row in watchlist_data:
            symbol = str(row.get("symbol", ""))
            ltp = Decimal(str(row.get("ltp", 0) or 0))
            if ltp <= 0:
                results.append({"symbol": symbol, "ltp": 0.0, "affordable": False, "max_qty": 0, "est_rupee_profit": 0.0, "cost_per_share": 0.0})
                continue
            cost_per_share = self._cost_per_share(ltp)
            max_qty = int((spendable / cost_per_share).to_integral_value(rounding=ROUND_DOWN)) if spendable >= cost_per_share else 0
            results.append(
                {
                    "symbol": symbol,
                    "ltp": float(ltp),
                    "affordable": max_qty > 0,
                    "max_qty": max_qty,
                    "est_rupee_profit": round(float(ltp * Decimal("0.02") * max_qty), 2),
                    "cost_per_share": round(float(cost_per_share), 2),
                }
            )
        return results

    def _plan_candidate(self, approved: ApprovedCandidate, remaining_capital: Decimal) -> Optional[OrderPlan]:
        candidate = approved.candidate
        entry_price = candidate.entry_price
        cost_per_share = self._cost_per_share(entry_price)
        if cost_per_share <= 0:
            return None

        trade_budget = min(self._per_trade_budget(remaining_capital), remaining_capital)
        if trade_budget < cost_per_share:
            return None

        qty_by_budget = int((trade_budget / cost_per_share).to_integral_value(rounding=ROUND_DOWN))
        quantity = min(qty_by_budget, int(candidate.max_affordable_qty))
        if quantity <= 0:
            return None

        capital_allocated = (entry_price * Decimal(quantity)).quantize(Decimal("0.01"))
        if capital_allocated > remaining_capital:
            return None

        return OrderPlan(
            symbol=candidate.symbol,
            exchange=candidate.exchange,
            side=candidate.side,
            quantity=quantity,
            entry_price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            target=candidate.target,
            product=candidate.product,
            order_type=self.config.order_type,
            strategy_tag=candidate.strategy,
            capital_allocated=capital_allocated,
            risk_reward=candidate.risk_reward,
            confidence=approved.evaluation.confidence,
            source_candidate_id=candidate.candidate_id,
            expected_edge_score=float(getattr(candidate, "expected_edge_score", 0.0)),
        )

    def _ranked_candidates(self, approved_candidates: Iterable[ApprovedCandidate]) -> list[ApprovedCandidate]:
        return sorted(
            approved_candidates,
            key=lambda approved: (
                -int(approved.evaluation.priority),
                -float(approved.evaluation.confidence),
                -float(approved.candidate.signal_strength),
                approved.candidate.symbol,
            ),
        )

    def _spendable_capital(self, funds: Funds) -> Decimal:
        available_cash = max(Decimal(funds.available_cash), Decimal("0"))
        reserve = max(self.config.min_cash_reserve, (available_cash * self.config.reserve_pct).quantize(Decimal("0.01")))
        return max(Decimal("0"), (available_cash - reserve).quantize(Decimal("0.01")))

    def _per_trade_budget(self, spendable_capital: Decimal) -> Decimal:
        per_trade = (spendable_capital * self.config.max_capital_per_trade_pct).quantize(Decimal("0.01"))
        if self.config.max_order_value_absolute is not None:
            per_trade = min(per_trade, self.config.max_order_value_absolute)
        return max(per_trade, Decimal("0"))

    def _cost_per_share(self, entry_price: Decimal) -> Decimal:
        return (Decimal(entry_price) * (Decimal("1") + self.config.transaction_cost_pct)).quantize(Decimal("0.0001"))

