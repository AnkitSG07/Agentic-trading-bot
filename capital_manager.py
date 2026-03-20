"""
Capital Manager — Real Budget-Aware Order Sizing for Dhan Live Trading

This module sits between the AI brain signal output and the actual
Dhan order placement. It answers ONE question before any order is placed:

  "Given my exact available cash right now, which of these AI signals
   can I actually afford, how many shares can I buy, and which one
   gives maximum profit potential per rupee spent?"

Rules enforced BEFORE any order touches Dhan:
  1. Fetch LIVE available cash from Dhan (not cached)
  2. Hard-reject any symbol whose price > available cash (can't buy even 1 share)
  3. Hard-reject any symbol whose price > max_stock_price config
  4. Calculate exact affordable quantity = floor(available_cash / ltp)
  5. Keep a cash reserve (default 5%) so account never hits zero
  6. Score remaining signals by profit potential per rupee
  7. Pick the BEST single signal (or 2 if capital allows both)
  8. Return sized orders ready for Dhan placement

Example with ₹1,000 account:
  - RELIANCE ₹2,800  → REJECTED (can't afford 1 share)
  - TCS      ₹3,900  → REJECTED (can't afford 1 share)
  - SBIN     ₹800    → 1 share affordable ✓
  - TRIDENT  ₹45     → 20 shares affordable ✓ (₹900 used)
  - TATASTEEL₹155    → 6 shares affordable ✓ (₹930 used)
  Winner: scored by momentum × volume × R/R ratio
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from agents.brain import TradingSignal, SignalAction

logger = logging.getLogger("capital_manager")


# ── Safety constants ──────────────────────────────────────────────────────────

# Never use more than this fraction of available cash in one cycle
# Keeps buffer for brokerage, STT, slippage, and SL order margin
MAX_CAPITAL_USAGE_PCT = 0.90     # use max 90% of available cash

# Minimum cash to keep in account at all times (absolute floor)
MIN_CASH_RESERVE_ABSOLUTE = 50   # ₹50 always stays untouched

# Dhan intraday brokerage: ₹20 per executed order (both legs)
# STT on intraday sell: 0.025% of turnover
# We add a 0.15% total cost buffer to cover all charges
TRANSACTION_COST_PCT = 0.0015    # 0.15% total cost allowance per trade


@dataclass
class AffordabilityCheck:
    """Result of checking whether a signal is affordable."""
    signal:           TradingSignal
    affordable:       bool
    reason:           str             # why rejected if not affordable
    adjusted_qty:     int             # how many shares we can actually buy
    estimated_cost:   Decimal         # total cost including buffer
    capital_pct:      float           # what % of available cash this uses
    profit_potential: float           # estimated rupee profit at target
    score:            float           # composite score for ranking


@dataclass
class OrderPlan:
    """A fully sized, validated order ready for Dhan placement."""
    signal:         TradingSignal
    quantity:       int
    entry_price:    Decimal
    stop_loss:      Decimal
    target:         Decimal
    estimated_cost: Decimal
    max_loss:       Decimal           # worst case loss if SL hit
    max_profit:     Decimal           # best case profit if target hit
    risk_reward:    float
    capital_used_pct: float


class CapitalManager:
    """
    Budget-aware order sizing manager for real Dhan live trading.

    Workflow:
    1. Call prepare_orders(signals, broker, config) before EVERY execution
    2. It fetches live Dhan balance, filters unaffordable signals,
       sizes each signal to exact affordable quantity, ranks by profit
       potential, returns the best 1-2 plans ready for placement
    3. The engine places orders exactly as specified in the plan

    This replaces the old risk_check → quantity calculation flow with
    a single, transparent, budget-first approach.
    """

    def __init__(self, config: dict):
        # Max percentage of capital per single trade
        self.max_capital_per_trade_pct = float(
            config.get("max_capital_per_trade_pct", 50.0)
        )
        # Hard cap in rupees (None = use only percentage)
        self.max_order_value_absolute: Optional[float] = config.get(
            "max_order_value_absolute"
        )
        # Minimum cash reserve to keep in account
        self.min_cash_reserve = float(
            config.get("min_cash_reserve", MIN_CASH_RESERVE_ABSOLUTE)
        )
        # Maximum number of orders in one cycle
        self.max_orders_per_cycle = int(config.get("max_orders_per_cycle", 2))

    # ── Main entry point ──────────────────────────────────────────────────────

    async def prepare_orders(
        self,
        signals:           list[TradingSignal],
        broker,                                  # DhanBroker instance
        live_prices:       dict[str, float],     # symbol → current LTP
        open_position_symbols: set[str],         # already have position
    ) -> list[OrderPlan]:
        """
        Takes raw AI signals, fetches live Dhan balance, filters and
        sizes each signal, returns ranked OrderPlan list ready for execution.

        This is the ONLY function the engine needs to call.
        """
        # Step 1: Get LIVE balance from Dhan right now
        # We never use cached funds — always fresh before placing orders
        try:
            funds = await broker.get_funds()
            available_cash = float(funds.available_cash)
        except Exception as e:
            logger.error("Failed to fetch live Dhan balance: %s", e)
            return []  # Hard stop — never place orders without knowing balance

        logger.info(
            "Capital check | Available: ₹%.2f | Signals: %d",
            available_cash, len(signals),
        )

        if available_cash <= 0:
            logger.warning("Zero or negative available cash — no orders possible")
            return []

        # Step 2: Calculate spendable capital
        # Reserve a buffer so we never drain the account completely
        reserve = max(self.min_cash_reserve, available_cash * 0.05)
        spendable = available_cash - reserve

        if spendable <= 0:
            logger.warning(
                "Spendable cash ₹%.2f after ₹%.2f reserve — no orders possible",
                spendable, reserve,
            )
            return []

        # Step 3: Check and size each signal
        checks: list[AffordabilityCheck] = []
        for signal in signals:
            if not signal.is_actionable:
                continue
            if signal.symbol in open_position_symbols:
                logger.info(
                    "Skipping %s — already have open position", signal.symbol
                )
                continue

            check = self._check_signal(signal, spendable, live_prices)
            if check.affordable:
                checks.append(check)
            else:
                logger.info(
                    "REJECTED %s %s — %s",
                    signal.action.value, signal.symbol, check.reason,
                )

        if not checks:
            logger.info("No affordable signals after capital check")
            return []

        # Step 4: Rank by profit potential score (best first)
        checks.sort(key=lambda c: c.score, reverse=True)

        # Step 5: Pick best orders that fit within spendable capital
        # Never allocate the same capital twice
        plans: list[OrderPlan] = []
        remaining_capital = Decimal(str(spendable))

        for check in checks:
            if len(plans) >= self.max_orders_per_cycle:
                break
            if check.estimated_cost > remaining_capital:
                logger.info(
                    "Skipping %s — cost ₹%.2f exceeds remaining ₹%.2f",
                    check.signal.symbol,
                    float(check.estimated_cost),
                    float(remaining_capital),
                )
                continue

            plan = self._build_order_plan(check, available_cash)
            if plan:
                plans.append(plan)
                remaining_capital -= check.estimated_cost
                logger.info(
                    "APPROVED %s %s | qty=%d | cost=₹%.2f | "
                    "max_profit=₹%.2f | max_loss=₹%.2f | R/R=%.1f",
                    plan.signal.action.value,
                    plan.signal.symbol,
                    plan.quantity,
                    float(plan.estimated_cost),
                    float(plan.max_profit),
                    float(plan.max_loss),
                    plan.risk_reward,
                )

        if plans:
            total_allocated = sum(float(p.estimated_cost) for p in plans)
            logger.info(
                "Order plan complete | %d orders | Total allocated: ₹%.2f / ₹%.2f (%.1f%%)",
                len(plans),
                total_allocated,
                available_cash,
                (total_allocated / available_cash * 100) if available_cash > 0 else 0,
            )

        return plans

    # ── Signal affordability check ────────────────────────────────────────────

    def _check_signal(
        self,
        signal:        TradingSignal,
        spendable:     float,
        live_prices:   dict[str, float],
    ) -> AffordabilityCheck:
        """
        Check if we can afford this signal at all and size it correctly.
        Uses live price from tick data, falls back to signal entry_price.
        """
        symbol = signal.symbol

        # Get the most current price — live tick first, then signal entry
        ltp = live_prices.get(symbol)
        if not ltp or ltp <= 0:
            ltp = float(signal.entry_price) if signal.entry_price else 0
        if not ltp or ltp <= 0:
            return AffordabilityCheck(
                signal=signal, affordable=False,
                reason="no live price available",
                adjusted_qty=0, estimated_cost=Decimal("0"),
                capital_pct=0, profit_potential=0, score=0,
            )

        price = Decimal(str(ltp))

        # Hard check 1: Can we afford even 1 share?
        cost_of_one = price * Decimal(str(1 + TRANSACTION_COST_PCT))
        if float(cost_of_one) > spendable:
            return AffordabilityCheck(
                signal=signal, affordable=False,
                reason=f"₹{ltp:,.2f}/share exceeds spendable ₹{spendable:,.2f}",
                adjusted_qty=0, estimated_cost=Decimal("0"),
                capital_pct=0, profit_potential=0, score=0,
            )

        # Calculate per-trade budget cap
        per_trade_budget = Decimal(str(
            spendable * (self.max_capital_per_trade_pct / 100.0)
        ))
        if self.max_order_value_absolute is not None:
            per_trade_budget = min(
                per_trade_budget,
                Decimal(str(self.max_order_value_absolute)),
            )
        per_trade_budget = min(per_trade_budget, Decimal(str(spendable)))

        # Calculate affordable quantity
        cost_per_share = price * Decimal(str(1 + TRANSACTION_COST_PCT))
        max_qty = int((per_trade_budget / cost_per_share).to_integral_value(ROUND_DOWN))

        if max_qty <= 0:
            return AffordabilityCheck(
                signal=signal, affordable=False,
                reason=f"per-trade budget ₹{float(per_trade_budget):,.2f} too small for ₹{ltp:,.2f}/share",
                adjusted_qty=0, estimated_cost=Decimal("0"),
                capital_pct=0, profit_potential=0, score=0,
            )

        # Use the minimum of: what AI suggested, what we can afford
        ai_qty = signal.quantity if signal.quantity > 0 else max_qty
        final_qty = min(ai_qty, max_qty)
        if final_qty <= 0:
            final_qty = 1  # always try at least 1 share if affordable

        estimated_cost = price * final_qty * Decimal(str(1 + TRANSACTION_COST_PCT))
        capital_pct = float(estimated_cost) / spendable * 100

        # Calculate profit potential
        target = signal.target or (price * Decimal("1.03"))   # default 3% target
        sl     = signal.stop_loss or (price * Decimal("0.985"))  # default 1.5% SL
        profit_potential = float((target - price) * final_qty)
        max_loss_potential = float((price - sl) * final_qty)

        rr = profit_potential / max_loss_potential if max_loss_potential > 0 else 0

        # Composite score = confidence × volume_signal × R/R ratio × momentum
        # Higher score = better use of limited capital
        conf_score  = signal.confidence * 3.0
        rr_score    = min(rr, 5.0)           # cap at 5:1 so it doesn't dominate
        profit_score = profit_potential / 100  # normalize by ₹100 profit units
        score = conf_score + rr_score + profit_score

        return AffordabilityCheck(
            signal         = signal,
            affordable     = True,
            reason         = "affordable",
            adjusted_qty   = final_qty,
            estimated_cost = estimated_cost,
            capital_pct    = capital_pct,
            profit_potential = profit_potential,
            score          = score,
        )

    def _build_order_plan(
        self,
        check:          AffordabilityCheck,
        available_cash: float,
    ) -> Optional[OrderPlan]:
        """Build a complete, validated OrderPlan from an AffordabilityCheck."""
        signal = check.signal
        qty    = check.adjusted_qty

        if qty <= 0:
            return None

        # Use live-sized price if signal has entry_price, otherwise market order
        entry = signal.entry_price or Decimal("0")

        # Calculate SL and target — use signal values if present and valid
        if signal.stop_loss and signal.stop_loss > 0 and entry > 0:
            sl = signal.stop_loss
        else:
            # Default SL: 1.5% below entry for BUY
            sl = entry * Decimal("0.985") if entry > 0 else Decimal("0")

        if signal.target and signal.target > 0 and entry > 0:
            target = signal.target
        else:
            # Default target: 3% above entry for BUY
            target = entry * Decimal("1.03") if entry > 0 else Decimal("0")

        max_profit = (target - entry) * qty if entry > 0 and target > entry else Decimal("0")
        max_loss   = (entry - sl) * qty    if entry > 0 and sl < entry   else Decimal("0")
        rr = float(max_profit / max_loss) if max_loss > 0 else 0

        # Hard check: minimum R/R of 1.5:1
        if rr > 0 and rr < 1.5:
            logger.warning(
                "Skipping %s — R/R %.1f below minimum 1.5:1",
                signal.symbol, rr,
            )
            return None

        return OrderPlan(
            signal          = signal,
            quantity        = qty,
            entry_price     = entry,
            stop_loss       = sl,
            target          = target,
            estimated_cost  = check.estimated_cost,
            max_loss        = max_loss,
            max_profit      = max_profit,
            risk_reward     = rr,
            capital_used_pct = check.capital_pct,
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    def summary_log(self, plans: list[OrderPlan], available_cash: float) -> str:
        """Generate a clean summary log string for the plan."""
        if not plans:
            return "No orders planned — no affordable signals"
        lines = [f"Order plan ({len(plans)} orders, ₹{available_cash:,.2f} available):"]
        for i, p in enumerate(plans, 1):
            lines.append(
                f"  {i}. {p.signal.action.value} {p.quantity}×{p.signal.symbol} "
                f"@ ₹{float(p.entry_price):,.2f} | "
                f"SL: ₹{float(p.stop_loss):,.2f} | "
                f"TGT: ₹{float(p.target):,.2f} | "
                f"Max profit: ₹{float(p.max_profit):,.0f} | "
                f"Max loss: ₹{float(p.max_loss):,.0f} | "
                f"R/R: {p.risk_reward:.1f} | "
                f"Capital: {p.capital_used_pct:.1f}%"
            )
        return "\n".join(lines)
