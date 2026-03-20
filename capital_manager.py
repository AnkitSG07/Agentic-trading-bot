"""
CapitalManager — Budget-aware order sizing layer.

Fix 15: This entire module is new. There was no dedicated budget verification
layer between brain.py signals and core/engine.py order placement.

The old flow:
  AI generates signal → risk manager checks it → if rejected, nothing happens
  Problem: AI suggested RELIANCE @ ₹2,800 when account had ₹1,000.
  The rejection wasted an API call and placed zero orders.

The new flow:
  AI generates signals → CapitalManager.prepare_orders() →
    1. Fetches LIVE Dhan balance (not cached)
    2. Computes spendable = available - max(₹50, available * 5%)
    3. Checks each signal for affordability with 0.15% transaction buffer
    4. Computes max affordable quantity per signal
    5. Scores signals by confidence × R/R × rupee profit
    6. Enforces minimum 1.5:1 R/R
    7. Returns ranked OrderPlan list → engine places them directly

Place this file at: trading-bot/capital_manager.py
(same level as main.py and docker-compose.yml)

Import in core/engine.py:
    from capital_manager import CapitalManager, OrderPlan
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

logger = logging.getLogger("capital_manager")


# ─── ORDER PLAN ──────────────────────────────────────────────────────────────

@dataclass
class OrderPlan:
    """
    A fully validated, budget-verified order ready for execution.
    engine._execute_from_plan() accepts ONLY OrderPlan objects —
    never raw quantities from the AI or risk manager.
    """
    symbol:        str
    exchange:      str
    action:        str           # "BUY" | "SELL" | "SHORT" | "COVER"
    strategy:      str
    quantity:      int           # exact affordable quantity (already verified)
    entry_price:   Optional[Decimal]
    stop_loss:     Optional[Decimal]
    target:        Optional[Decimal]
    confidence:    float
    rationale:     str
    risk_reward:   Optional[float]
    timeframe:     str
    product:       str           # "MIS" | "CNC" | "NRML"
    priority:      int
    tags:          list[str] = field(default_factory=list)

    # Computed fields (set by CapitalManager)
    cost_estimate:  float = 0.0   # entry_price × quantity × 1.0015
    rupee_profit:   float = 0.0   # (target - entry) × quantity (gross)
    score:          float = 0.0   # confidence × risk_reward × rupee_profit


# ─── CAPITAL MANAGER ─────────────────────────────────────────────────────────

class CapitalManager:
    """
    Budget-aware order sizing and prioritization layer.

    Sits between brain.py (AI signals) and engine.py (order placement).
    Ensures every order that reaches the broker is:
      - Affordable given current live balance
      - Sized to exact max quantity the account can buy
      - Filtered for minimum 1.5:1 R/R
      - Ranked by expected rupee profit (not just confidence)

    Usage in engine._decision_cycle():
        live_prices = {sym: tick.get("ltp") for sym, tick in self._tick_data.items()}
        order_plans = await self.capital_manager.prepare_orders(
            signals=signals,
            broker=execution_broker,
            live_prices=live_prices,
            open_position_symbols={p["symbol"] for p in ctx.open_positions},
        )
        for plan in order_plans:
            await self._execute_from_plan(plan)
    """

    def __init__(self, config: dict):
        # Minimum cash to keep untouched (₹50 default for tiny accounts)
        self.min_cash_reserve: float = float(config.get("min_cash_reserve", 50.0))

        # Maximum % of spendable capital per trade
        self.max_capital_per_trade_pct: float = float(
            config.get("max_capital_per_trade_pct", 80.0)
        )

        # Hard max order value in rupees (None = no cap beyond capital)
        self.max_order_value_absolute: Optional[float] = config.get(
            "max_order_value_absolute"
        )

        # Minimum R/R to even consider a signal
        self.min_risk_reward: float = float(config.get("min_risk_reward", 1.5))

        # Transaction cost buffer (brokerage + STT + slippage estimate)
        self.transaction_cost_pct: float = float(
            config.get("transaction_cost_pct", 0.0015)  # 0.15%
        )

        logger.info(
            "CapitalManager ready | reserve=₹%.0f | max_pct=%.0f%% | "
            "min_rr=%.1f | cost_buffer=%.3f%%",
            self.min_cash_reserve,
            self.max_capital_per_trade_pct,
            self.min_risk_reward,
            self.transaction_cost_pct * 100,
        )

    # ── Core public method ────────────────────────────────────────────────────

    async def prepare_orders(
        self,
        signals: list,                      # list[TradingSignal] from brain.py
        broker,                             # BaseBroker — for live balance fetch
        live_prices: dict[str, float],      # {symbol: ltp} from tick data
        open_position_symbols: set[str],    # symbols already in positions
    ) -> list[OrderPlan]:
        """
        Convert AI signals into verified, budget-sized OrderPlan objects.

        Steps:
          1. Fetch live balance from broker (never cached)
          2. Compute spendable capital
          3. For each signal: check affordability, size quantity, compute score
          4. Filter by min R/R
          5. Sort by score descending
          6. Return ranked OrderPlan list

        Returns empty list if balance fetch fails or no signals pass checks.
        """
        # Step 1: live balance fetch
        try:
            funds = await broker.get_funds()
            available = float(funds.available_cash)
        except Exception as e:
            logger.error("CapitalManager: failed to fetch live balance — %s", e)
            return []

        # Step 2: spendable capital
        reserve   = max(self.min_cash_reserve, available * 0.05)
        spendable = max(0.0, available - reserve)

        logger.info(
            "CapitalManager | available=₹%.0f reserve=₹%.0f spendable=₹%.0f | "
            "signals=%d",
            available, reserve, spendable, len(signals),
        )

        if spendable <= 0:
            logger.warning("CapitalManager: spendable=₹0 — no orders possible")
            return []

        order_plans: list[OrderPlan] = []

        for signal in signals:
            if not getattr(signal, "is_actionable", False):
                continue
            if signal.symbol in open_position_symbols:
                logger.debug(
                    "CapitalManager: skipping %s — open position exists",
                    signal.symbol,
                )
                continue

            plan = self._size_signal(signal, spendable, live_prices)
            if plan is None:
                continue

            order_plans.append(plan)

        # Sort by score descending (best rupee opportunity first)
        order_plans.sort(key=lambda p: p.score, reverse=True)

        logger.info(
            "CapitalManager: %d/%d signals passed → order plans: %s",
            len(order_plans),
            len(signals),
            [(p.symbol, p.action, p.quantity, f"₹{p.cost_estimate:,.0f}") for p in order_plans],
        )

        return order_plans

    # ── Signal sizing ─────────────────────────────────────────────────────────

    def _size_signal(
        self,
        signal,                        # TradingSignal
        spendable: float,
        live_prices: dict[str, float],
    ) -> Optional[OrderPlan]:
        """
        Size a single signal against current spendable capital.
        Returns None if the signal fails any hard check.
        """
        symbol = signal.symbol

        # Resolve best available price
        entry_price = (
            float(signal.entry_price) if signal.entry_price else None
        ) or live_prices.get(symbol) or 0.0

        if entry_price <= 0:
            logger.warning(
                "CapitalManager: no price for %s — skipping", symbol
            )
            return None

        # Cost per share including transaction buffer
        cost_per_share = entry_price * (1 + self.transaction_cost_pct)

        # Can we afford even 1 share?
        if spendable < cost_per_share:
            logger.info(
                "CapitalManager: %s TOO EXPENSIVE | cost=₹%.2f > spendable=₹%.2f",
                symbol, cost_per_share, spendable,
            )
            return None

        # Per-trade budget
        per_trade_budget = spendable * (self.max_capital_per_trade_pct / 100.0)
        if self.max_order_value_absolute is not None:
            per_trade_budget = min(per_trade_budget, self.max_order_value_absolute)

        # Max affordable quantity
        quantity = int(per_trade_budget / cost_per_share)
        if quantity <= 0:
            logger.info(
                "CapitalManager: %s quantity=0 after budget sizing", symbol
            )
            return None

        # Compute R/R and rupee profit
        stop_loss = float(signal.stop_loss) if signal.stop_loss else None
        target    = float(signal.target)    if signal.target    else None

        risk_reward = signal.risk_reward
        if risk_reward is None and stop_loss and target and entry_price > 0:
            reward = abs(target - entry_price)
            risk   = abs(entry_price - stop_loss)
            risk_reward = round(reward / risk, 2) if risk > 0 else None

        # Enforce minimum R/R
        if risk_reward is not None and risk_reward < self.min_risk_reward:
            logger.info(
                "CapitalManager: %s rejected — R/R=%.2f < min %.1f",
                symbol, risk_reward, self.min_risk_reward,
            )
            return None

        # Rupee profit estimate
        rupee_profit = 0.0
        if target and entry_price > 0:
            rupee_profit = abs(target - entry_price) * quantity

        cost_estimate = cost_per_share * quantity

        # Score = confidence × R/R × rupee_profit
        # This ranks signals that give best actual ₹ return first,
        # not just highest confidence
        rr_for_score    = risk_reward if risk_reward is not None else 1.0
        score = signal.confidence * rr_for_score * rupee_profit

        plan = OrderPlan(
            symbol       = symbol,
            exchange     = getattr(signal, "exchange", "NSE"),
            action       = signal.action.value,
            strategy     = getattr(signal, "strategy", "unknown"),
            quantity     = quantity,
            entry_price  = signal.entry_price,
            stop_loss    = signal.stop_loss,
            target       = signal.target,
            confidence   = signal.confidence,
            rationale    = getattr(signal, "rationale", ""),
            risk_reward  = risk_reward,
            timeframe    = getattr(signal, "timeframe", "intraday"),
            product      = getattr(signal, "product", "MIS"),
            priority     = getattr(signal, "priority", 5),
            tags         = list(getattr(signal, "tags", [])),
            cost_estimate= cost_estimate,
            rupee_profit = rupee_profit,
            score        = score,
        )

        logger.info(
            "CapitalManager: %s %s | qty=%d | cost=₹%.0f | "
            "profit~₹%.0f | R/R=%.2f | score=%.2f",
            plan.action, symbol, quantity, cost_estimate,
            rupee_profit, risk_reward or 0, score,
        )

        return plan

    # ── Utility ───────────────────────────────────────────────────────────────

    def affordability_summary(
        self,
        watchlist_data: list[dict],
        available_capital: float,
    ) -> list[dict]:
        """
        Generate affordability labels for all watchlist symbols.
        Used by brain._build_prompt() to inject [AFFORDABLE] / [TOO EXPENSIVE]
        labels into the AI prompt before calling the model.

        Returns list of dicts:
          {symbol, ltp, affordable, max_qty, est_rupee_profit, cost_per_share}
        """
        reserve   = max(self.min_cash_reserve, available_capital * 0.05)
        spendable = max(0.0, available_capital - reserve)
        result    = []

        for w in watchlist_data:
            symbol = w.get("symbol", "")
            ltp    = float(w.get("ltp", 0) or 0)
            if ltp <= 0:
                result.append({"symbol": symbol, "affordable": False,
                               "max_qty": 0, "est_rupee_profit": 0,
                               "cost_per_share": 0})
                continue

            cost_per_share = ltp * (1 + self.transaction_cost_pct)
            affordable     = spendable >= cost_per_share
            max_qty        = int(spendable / cost_per_share) if affordable else 0
            est_profit     = (ltp * 0.02) * max_qty  # rough 2% move estimate

            result.append({
                "symbol":         symbol,
                "ltp":            ltp,
                "affordable":     affordable,
                "max_qty":        max_qty,
                "est_rupee_profit": round(est_profit, 2),
                "cost_per_share": round(cost_per_share, 2),
            })

        return result
