"""
Risk Manager - The safety layer for all trades.
Enforces position sizing, daily loss limits, drawdown controls,
and provides kill-switch functionality.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from brokers.base import Funds, Order, Position

logger = logging.getLogger("risk.manager")


@dataclass
class RiskConfig:
    max_capital_per_trade_pct: float = 5.0
    max_open_positions: int = 10
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 8.0
    stop_loss_pct: float = 1.5
    target_pct: float = 3.0
    trailing_stop: bool = True
    trailing_stop_pct: float = 0.8
    position_sizing_method: str = "kelly"  # fixed | kelly | volatility_adjusted
    max_order_value_absolute: Optional[float] = None
    min_cash_buffer: float = 0.0
    tiny_account_mode: bool = False


@dataclass
class RiskCheck:
    approved: bool
    reason: str
    adjusted_quantity: Optional[int] = None
    adjusted_sl: Optional[Decimal] = None


@dataclass
class DailyStats:
    date: date
    starting_capital: Decimal
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    peak_capital: Decimal = Decimal("0")
    kill_switch_triggered: bool = False
    kill_switch_reason: str = ""

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def daily_loss_pct(self) -> float:
        if self.starting_capital <= 0:
            return 0.0
        return float((self.total_pnl / self.starting_capital) * 100)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_capital <= 0:
            return 0.0
        current = self.starting_capital + self.total_pnl
        return float(((self.peak_capital - current) / self.peak_capital) * 100)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100


class RiskManager:
    """
    Production-grade risk management system.

    Responsibilities:
    1. Pre-trade checks (position sizing, daily limits)
    2. Real-time monitoring (unrealized PnL, drawdown)
    3. Kill switch (auto-stop trading on breach)
    4. Post-trade logging
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.today = DailyStats(
            date=date.today(),
            starting_capital=Decimal("0"),
            peak_capital=Decimal("0"),
        )
        self._initialized = False
        self._kill_switch = False
        self._capital_blocked = False

    async def initialize(self, funds: Funds) -> None:
        """Call at market open with current account balance."""
        available_cash = max(funds.available_cash, Decimal("0"))
        total_balance = max(funds.total_balance, Decimal("0"))
        effective_capital = available_cash if available_cash > 0 else total_balance

        self.today = DailyStats(
            date=date.today(),
            starting_capital=effective_capital,
            peak_capital=effective_capital,
        )
        self._capital_blocked = effective_capital <= 0
        if self._capital_blocked:
            self._trigger_kill_switch("No positive tradable capital available at startup")

        self._initialized = True
        logger.info(
            f"💰 Risk Manager initialized | Raw total: ₹{funds.total_balance:,.0f} | "
            f"Raw available: ₹{funds.available_cash:,.0f} | Effective capital: ₹{effective_capital:,.0f} | "
            f"Max daily loss: ₹{effective_capital * Decimal(str(self.config.max_daily_loss_pct / 100)):,.0f}"
        )

    # ── Pre-Trade Checks ─────────────────────────────────────────────────────

    async def check_pre_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: Decimal,
        stop_loss: Optional[Decimal],
        open_positions: list[Position],
        funds: Funds,
    ) -> RiskCheck:
        """
        Run all pre-trade risk checks. Returns RiskCheck with approval status.
        This MUST be called before placing any order.
        """

        # 0. Block fresh trades when no positive baseline capital exists
        if self._capital_blocked:
            return RiskCheck(False, "Trading blocked: no positive capital baseline")

        # 1. Kill switch
        if self._kill_switch:
            return RiskCheck(False, f"Kill switch active: {self.today.kill_switch_reason}")

        # 2. Max positions check
        if len(open_positions) >= self.config.max_open_positions:
            return RiskCheck(
                False,
                f"Max positions reached ({self.config.max_open_positions}). Close some first."
            )

        # 3. Daily loss limit
        if self.today.daily_loss_pct <= -self.config.max_daily_loss_pct:
            self._trigger_kill_switch(f"Daily loss limit hit: {self.today.daily_loss_pct:.2f}%")
            return RiskCheck(False, "Daily loss limit exceeded")

        # 4. Drawdown check
        if self.today.drawdown_pct >= self.config.max_drawdown_pct:
            self._trigger_kill_switch(f"Drawdown limit hit: {self.today.drawdown_pct:.2f}%")
            return RiskCheck(False, "Drawdown limit exceeded")

        # 5. Capital / small-account sizing guardrails
        adjusted_quantity = quantity
        adjusted_reasons: list[str] = []
        available_cash = max(funds.available_cash, Decimal("0"))
        percent_cap = available_cash * Decimal(str(self.config.max_capital_per_trade_pct / 100))
        absolute_cap = None
        if self.config.max_order_value_absolute is not None:
            absolute_cap = Decimal(str(self.config.max_order_value_absolute))
        if self.config.tiny_account_mode and absolute_cap is None:
            absolute_cap = percent_cap

        effective_cap = percent_cap
        if absolute_cap is not None:
            effective_cap = min(effective_cap, absolute_cap)

        cash_buffer = Decimal(str(self.config.min_cash_buffer or 0))
        spendable_cash = max(Decimal("0"), available_cash - cash_buffer)
        if self.config.tiny_account_mode:
            spendable_cash = min(spendable_cash, available_cash * Decimal("0.75"))

        max_trade_value = min(effective_cap, spendable_cash)
        trade_value = entry_price * adjusted_quantity
        if trade_value > max_trade_value and entry_price > 0:
            adjusted_quantity = int((max_trade_value / entry_price).to_integral_value(rounding=ROUND_DOWN))
            if adjusted_quantity <= 0:
                return RiskCheck(False, f"Insufficient capital for even 1 unit of {symbol} after caps/buffer")
            trade_value = entry_price * adjusted_quantity
            adjusted_reasons.append(f"quantity adjusted to {adjusted_quantity} within ₹{max_trade_value:,.0f}")
            logger.warning(
                f"⚠️ Quantity adjusted: {quantity} → {adjusted_quantity} "
                f"(effective cap ₹{max_trade_value:,.0f})"
            )

        # 6. Available funds and cash buffer check
        if trade_value > available_cash:
            return RiskCheck(False, f"Insufficient funds: need ₹{trade_value:,.0f}, have ₹{available_cash:,.0f}")
        remaining_cash = available_cash - trade_value
        if remaining_cash < cash_buffer:
            return RiskCheck(False, f"Cash buffer breach: would leave ₹{remaining_cash:,.0f}, need ₹{cash_buffer:,.0f}")

        # 7. Validate SL is set
        adjusted_sl = None
        if not stop_loss:
            adjusted_sl = self._compute_stop_loss(entry_price, side)
            stop_loss = adjusted_sl
            logger.info(f"Auto SL applied: ₹{adjusted_sl:,.2f}")
            adjusted_reasons.append("auto stop-loss applied")

        # 8. SL sanity check (SL shouldn't be too far or too close)
        sl_distance_pct = abs(float((entry_price - stop_loss) / entry_price * 100))
        if sl_distance_pct > 5.0:
            return RiskCheck(False, f"SL too wide: {sl_distance_pct:.1f}% (max 5%)")
        if sl_distance_pct < 0.1:
            return RiskCheck(False, f"SL too tight: {sl_distance_pct:.2f}% (min 0.1%)")

        reason = "All risk checks passed" if not adjusted_reasons else "; ".join(adjusted_reasons)
        return RiskCheck(True, reason, adjusted_quantity=adjusted_quantity if adjusted_quantity != quantity else None, adjusted_sl=adjusted_sl)

    # ── Position Sizing ──────────────────────────────────────────────────────

    def calculate_position_size(
        self,
        capital: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        win_rate: float = 0.55,
        avg_win_loss_ratio: float = 2.0,
    ) -> int:
        """
        Calculate optimal position size using the configured method.

        Kelly: f = W - (1-W)/R where W=win_rate, R=win/loss_ratio
        Volatility-adjusted: size based on ATR
        Fixed: fixed % of capital
        """
        if self.config.position_sizing_method == "kelly":
            kelly_pct = win_rate - (1 - win_rate) / avg_win_loss_ratio
            # Use half-Kelly for safety
            kelly_pct = max(0.01, min(kelly_pct * 0.5, self.config.max_capital_per_trade_pct / 100))
            risk_amount = capital * Decimal(str(kelly_pct))
        else:
            # Fixed percentage
            risk_amount = capital * Decimal(str(self.config.max_capital_per_trade_pct / 100))

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return 0

        shares = int(risk_amount / sl_distance)
        return max(1, shares)

    # ── Real-Time Monitoring ─────────────────────────────────────────────────

    async def update_pnl(self, positions: list[Position], realized_pnl_delta: Decimal = Decimal("0")) -> None:
        """Update daily PnL with current positions. Call every tick/minute."""
        self.today.realized_pnl += realized_pnl_delta
        self.today.unrealized_pnl = sum(p.pnl for p in positions)

        current_capital = self.today.starting_capital + self.today.total_pnl
        if current_capital > self.today.peak_capital:
            self.today.peak_capital = current_capital

        # Auto kill-switch checks
        if self.today.daily_loss_pct <= -self.config.max_daily_loss_pct:
            self._trigger_kill_switch(f"Daily loss {self.today.daily_loss_pct:.2f}% exceeded limit {self.config.max_daily_loss_pct}%")

        if self.today.drawdown_pct >= self.config.max_drawdown_pct:
            self._trigger_kill_switch(f"Drawdown {self.today.drawdown_pct:.2f}% exceeded limit {self.config.max_drawdown_pct}%")

    async def record_trade(self, order: Order, pnl: Optional[Decimal] = None) -> None:
        """Record a completed trade in daily stats."""
        self.today.total_trades += 1
        if pnl is not None:
            if pnl > 0:
                self.today.winning_trades += 1
                self.today.realized_pnl += pnl
            else:
                self.today.losing_trades += 1
                self.today.realized_pnl += pnl

    # ── Trailing Stop ────────────────────────────────────────────────────────

    def calculate_trailing_stop(
        self,
        entry_price: Decimal,
        current_price: Decimal,
        current_sl: Decimal,
        side: str,
    ) -> Decimal:
        """Compute updated trailing stop loss level."""
        trail_amount = current_price * Decimal(str(self.config.trailing_stop_pct / 100))
        if side == "BUY":
            new_sl = current_price - trail_amount
            return max(new_sl, current_sl)  # Only move SL up
        else:
            new_sl = current_price + trail_amount
            return min(new_sl, current_sl)  # Only move SL down

    # ── Kill Switch ──────────────────────────────────────────────────────────

    def _trigger_kill_switch(self, reason: str) -> None:
        if not self._kill_switch:
            self._kill_switch = True
            self.today.kill_switch_triggered = True
            self.today.kill_switch_reason = reason
            logger.critical(f"🚨 KILL SWITCH TRIGGERED: {reason}")

    def reset_kill_switch(self, override_code: str) -> bool:
        """Manually reset kill switch with admin override."""
        if override_code == "ADMIN_OVERRIDE_2024":
            self._kill_switch = False
            self.today.kill_switch_triggered = False
            logger.warning("⚠️ Kill switch manually reset by admin")
            return True
        return False

    @property
    def is_trading_allowed(self) -> bool:
        return not self._kill_switch and self._initialized

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _compute_stop_loss(self, entry_price: Decimal, side: str) -> Decimal:
        sl_distance = entry_price * Decimal(str(self.config.stop_loss_pct / 100))
        if side == "BUY":
            return entry_price - sl_distance
        return entry_price + sl_distance

    def get_daily_summary(self) -> dict:
        return {
            "date": self.today.date.isoformat(),
            "starting_capital": float(self.today.starting_capital),
            "realized_pnl": float(self.today.realized_pnl),
            "unrealized_pnl": float(self.today.unrealized_pnl),
            "total_pnl": float(self.today.total_pnl),
            "daily_pnl_pct": round(self.today.daily_loss_pct, 2),
            "drawdown_pct": round(self.today.drawdown_pct, 2),
            "total_trades": self.today.total_trades,
            "win_rate": round(self.today.win_rate, 1),
            "kill_switch": self._kill_switch,
            "kill_switch_reason": self.today.kill_switch_reason,
            "trading_allowed": self.is_trading_allowed,
        }
