"""
Base Broker Adapter - Abstract interface for all Indian broker integrations.
All broker implementations must inherit from this class.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


# ─── ENUMS ──────────────────────────────────────────────────────────────────

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"              # Stop Loss
    SL_M = "SL-M"          # Stop Loss Market


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ProductType(str, Enum):
    CNC = "CNC"            # Cash and Carry (Delivery)
    MIS = "MIS"            # Margin Intraday Squareoff
    NRML = "NRML"          # Normal (F&O overnight)
    BO = "BO"              # Bracket Order
    CO = "CO"              # Cover Order


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TRIGGER_PENDING = "TRIGGER_PENDING"


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"            # NSE F&O
    MCX = "MCX"
    BFO = "BFO"            # BSE F&O
    CDS = "CDS"            # Currency


class InstrumentType(str, Enum):
    EQ = "EQ"
    FUT = "FUT"
    CE = "CE"              # Call Option
    PE = "PE"              # Put Option


# ─── DATA MODELS ────────────────────────────────────────────────────────────

@dataclass
class Instrument:
    symbol: str
    exchange: Exchange
    instrument_type: InstrumentType
    instrument_token: Optional[str] = None
    lot_size: int = 1
    tick_size: Decimal = Decimal("0.05")
    expiry: Optional[datetime] = None
    strike: Optional[Decimal] = None
    option_type: Optional[str] = None  # CE | PE


@dataclass
class Quote:
    instrument: Instrument
    ltp: Decimal                        # Last Traded Price
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    oi: int = 0                         # Open Interest
    bid: Decimal = Decimal("0")
    ask: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=datetime.now)
    iv: Optional[float] = None          # Implied Volatility (options)
    delta: Optional[float] = None
    theta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None


@dataclass
class Order:
    order_id: str
    broker_order_id: Optional[str]
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    product: ProductType
    quantity: int
    price: Optional[Decimal]
    trigger_price: Optional[Decimal]
    status: OrderStatus
    filled_quantity: int = 0
    average_price: Optional[Decimal] = None
    tag: Optional[str] = None          # Strategy tag
    placed_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    rejection_reason: Optional[str] = None


@dataclass
class Position:
    instrument: Instrument
    side: OrderSide
    quantity: int
    average_price: Decimal
    ltp: Decimal
    pnl: Decimal
    pnl_pct: float
    product: ProductType
    broker: str


@dataclass
class Holding:
    instrument: Instrument
    quantity: int
    average_price: Decimal
    ltp: Decimal
    pnl: Decimal
    collateral_quantity: int = 0


@dataclass
class Funds:
    available_cash: Decimal
    used_margin: Decimal
    total_balance: Decimal
    collateral: Decimal = Decimal("0")
    unrealised_pnl: Decimal = Decimal("0")
    realised_pnl: Decimal = Decimal("0")


@dataclass
class OHLCV:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    oi: int = 0


# ─── BASE BROKER ────────────────────────────────────────────────────────────

class BaseBroker(ABC):
    """Abstract base class for all Indian broker integrations."""

    def __init__(self, config: dict):
        self.config = config
        self.name = self.__class__.__name__
        self.is_connected = False
        self.logger = logging.getLogger(f"broker.{self.name.lower()}")

    # ── Authentication ───────────────────────────────────────

    @abstractmethod
    async def login(self) -> bool:
        """Authenticate with the broker. Returns True on success."""
        ...

    @abstractmethod
    async def logout(self) -> bool:
        """Logout and invalidate session."""
        ...

    @abstractmethod
    async def refresh_session(self) -> bool:
        """Refresh access token if needed."""
        ...

    # ── Market Data ──────────────────────────────────────────

    @abstractmethod
    async def get_quote(self, instruments: list[Instrument]) -> dict[str, Quote]:
        """Get real-time quotes for instruments."""
        ...

    @abstractmethod
    async def get_ohlcv(
        self,
        instrument: Instrument,
        interval: str,         # minute | 3minute | 5minute | 15minute | 60minute | day
        from_date: datetime,
        to_date: datetime,
    ) -> list[OHLCV]:
        """Fetch historical OHLCV data."""
        ...

    @abstractmethod
    async def get_instruments(self, exchange: Exchange) -> list[Instrument]:
        """Get full instrument list for an exchange."""
        ...

    @abstractmethod
    async def get_option_chain(
        self,
        underlying: str,
        expiry: datetime,
    ) -> list[Instrument]:
        """Get options chain for an underlying."""
        ...

    # ── Order Management ─────────────────────────────────────

    @abstractmethod
    async def place_order(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        product: ProductType,
        price: Optional[Decimal] = None,
        trigger_price: Optional[Decimal] = None,
        tag: Optional[str] = None,
    ) -> Order:
        """Place a new order."""
        ...

    @abstractmethod
    async def modify_order(
        self,
        order_id: str,
        quantity: Optional[int] = None,
        price: Optional[Decimal] = None,
        trigger_price: Optional[Decimal] = None,
        order_type: Optional[OrderType] = None,
    ) -> Order:
        """Modify an existing open order."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending/open order."""
        ...

    @abstractmethod
    async def get_order_history(self) -> list[Order]:
        """Get all orders for today."""
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Order:
        """Get status of a specific order."""
        ...

    # ── Portfolio ────────────────────────────────────────────

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get current intraday + overnight positions."""
        ...

    @abstractmethod
    async def get_holdings(self) -> list[Holding]:
        """Get CNC holdings (delivery portfolio)."""
        ...

    @abstractmethod
    async def get_funds(self) -> Funds:
        """Get available funds and margins."""
        ...

    # ── WebSocket ────────────────────────────────────────────

    @abstractmethod
    async def subscribe_ticks(
        self,
        instruments: list[Instrument],
        callback,
    ) -> None:
        """Subscribe to real-time tick data via WebSocket."""
        ...

    @abstractmethod
    async def unsubscribe_ticks(self, instruments: list[Instrument]) -> None:
        """Unsubscribe from tick data."""
        ...

    # ── Utility ──────────────────────────────────────────────

    async def place_bracket_order(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: int,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        target_price: Decimal,
        product: ProductType = ProductType.MIS,
        tag: Optional[str] = None,
    ) -> tuple[Order, Order, Order]:
        """
        Place a full bracket (entry + SL + target) as separate orders.
        Returns (entry_order, sl_order, target_order).
        Broker-specific BO support can override this.
        """
        entry = await self.place_order(
            instrument, side, quantity,
            OrderType.LIMIT, product, entry_price, tag=tag
        )
        exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        sl = await self.place_order(
            instrument, exit_side, quantity,
            OrderType.SL_M, product, trigger_price=stop_loss_price, tag=f"{tag}_SL"
        )
        target = await self.place_order(
            instrument, exit_side, quantity,
            OrderType.LIMIT, product, target_price, tag=f"{tag}_TGT"
        )
        return entry, sl, target

    async def square_off_position(self, position: Position) -> Order:
        """Market order to close a position immediately."""
        exit_side = OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY
        return await self.place_order(
            position.instrument,
            exit_side,
            position.quantity,
            OrderType.MARKET,
            position.product,
            tag="SQUAREOFF",
        )

    def __repr__(self) -> str:
        status = "connected" if self.is_connected else "disconnected"
        return f"<{self.name} [{status}]>"
