"""
Database Models - SQLAlchemy async ORM
All tables for trades, positions, ticks, agent decisions, and risk events.
Uses TimescaleDB hypertables for time-series tick data.
"""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, Numeric,
    String, Text, ForeignKey, Index, UniqueConstraint,
    func, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
import uuid


class Base(DeclarativeBase):
    pass


# ─── TRADES ──────────────────────────────────────────────────────────────────

class Trade(Base):
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    broker_order_id = Column(String(64), unique=True, nullable=False, index=True)
    broker = Column(String(20), nullable=False)          # zerodha | dhan

    symbol = Column(String(50), nullable=False, index=True)
    exchange = Column(String(10), nullable=False)
    instrument_type = Column(String(10), nullable=False)  # EQ | FUT | CE | PE

    side = Column(String(10), nullable=False)             # BUY | SELL
    order_type = Column(String(10), nullable=False)       # MARKET | LIMIT | SL | SL-M
    product = Column(String(10), nullable=False)          # MIS | CNC | NRML

    quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, default=0)
    price = Column(Numeric(12, 4), nullable=True)
    trigger_price = Column(Numeric(12, 4), nullable=True)
    average_price = Column(Numeric(12, 4), nullable=True)

    status = Column(String(20), nullable=False)           # COMPLETE | CANCELLED | REJECTED
    tag = Column(String(50), nullable=True)               # Strategy tag
    rejection_reason = Column(Text, nullable=True)

    strategy = Column(String(50), nullable=True)
    signal_confidence = Column(Float, nullable=True)
    signal_rationale = Column(Text, nullable=True)

    placed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Link to position for PnL tracking
    position_id = Column(UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True)
    position = relationship("Position", back_populates="trades")

    __table_args__ = (
        Index("idx_trades_symbol_date", "symbol", "placed_at"),
        Index("idx_trades_status", "status"),
    )


# ─── POSITIONS ───────────────────────────────────────────────────────────────

class Position(Base):
    __tablename__ = "positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    broker = Column(String(20), nullable=False)
    symbol = Column(String(50), nullable=False, index=True)
    exchange = Column(String(10), nullable=False)
    product = Column(String(10), nullable=False)
    strategy = Column(String(50), nullable=True)

    side = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Numeric(12, 4), nullable=False)
    exit_price = Column(Numeric(12, 4), nullable=True)
    stop_loss = Column(Numeric(12, 4), nullable=True)
    target = Column(Numeric(12, 4), nullable=True)

    realized_pnl = Column(Numeric(12, 2), nullable=True)
    brokerage = Column(Numeric(10, 2), default=0)
    stt = Column(Numeric(10, 2), default=0)
    net_pnl = Column(Numeric(12, 2), nullable=True)

    status = Column(String(20), default="OPEN")           # OPEN | CLOSED | PARTIALLY_CLOSED
    exit_reason = Column(String(50), nullable=True)       # TARGET | SL | MANUAL | SQUAREOFF | TRAILING_SL

    opened_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    trades = relationship("Trade", back_populates="position")
    sl_orders = relationship("SLOrder", back_populates="position")

    __table_args__ = (
        Index("idx_positions_symbol_status", "symbol", "status"),
        Index("idx_positions_opened_at", "opened_at"),
    )


# ─── STOP LOSS ORDER TRACKING ────────────────────────────────────────────────

class SLOrder(Base):
    __tablename__ = "sl_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position_id = Column(UUID(as_uuid=True), ForeignKey("positions.id"), nullable=False)
    broker_order_id = Column(String(64), nullable=False, index=True)
    broker = Column(String(20), nullable=False)
    symbol = Column(String(50), nullable=False)

    sl_price = Column(Numeric(12, 4), nullable=False)
    sl_type = Column(String(20), default="INITIAL")       # INITIAL | TRAILING | MANUAL
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    position = relationship("Position", back_populates="sl_orders")


# ─── AGENT DECISIONS ─────────────────────────────────────────────────────────

class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)

    market_regime = Column(String(50), nullable=True)
    market_commentary = Column(Text, nullable=True)
    session = Column(String(30), nullable=True)
    nifty_ltp = Column(Float, nullable=True)
    banknifty_ltp = Column(Float, nullable=True)
    india_vix = Column(Float, nullable=True)
    pcr = Column(Float, nullable=True)

    signals_generated = Column(Integer, default=0)
    signals_executed = Column(Integer, default=0)
    signals_rejected = Column(Integer, default=0)
    risk_assessment = Column(String(20), nullable=True)
    session_recommendation = Column(String(30), nullable=True)

    raw_response = Column(JSONB, nullable=True)           # Full AI JSON response
    context_snapshot = Column(JSONB, nullable=True)       # Market context sent to AI


# ─── TICK DATA (TimescaleDB hypertable) ──────────────────────────────────────

class TickData(Base):
    __tablename__ = "tick_data"

    # TimescaleDB requires timestamp as part of PK for hypertable
    timestamp = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    symbol = Column(String(50), primary_key=True, nullable=False)
    exchange = Column(String(10), nullable=False)

    ltp = Column(Numeric(12, 4), nullable=False)
    volume = Column(Integer, default=0)
    oi = Column(Integer, default=0)
    bid = Column(Numeric(12, 4), nullable=True)
    ask = Column(Numeric(12, 4), nullable=True)

    __table_args__ = (
        Index("idx_tick_symbol_time", "symbol", "timestamp"),
    )


# ─── OHLCV CACHE ─────────────────────────────────────────────────────────────

class OHLCVCandle(Base):
    __tablename__ = "ohlcv_candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), nullable=False)
    interval = Column(String(20), nullable=False)         # minute | 5minute | day
    timestamp = Column(DateTime(timezone=True), nullable=False)

    open = Column(Numeric(12, 4), nullable=False)
    high = Column(Numeric(12, 4), nullable=False)
    low = Column(Numeric(12, 4), nullable=False)
    close = Column(Numeric(12, 4), nullable=False)
    volume = Column(Integer, default=0)
    oi = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("symbol", "exchange", "interval", "timestamp", name="uq_ohlcv"),
        Index("idx_ohlcv_symbol_interval_time", "symbol", "interval", "timestamp"),
    )


# ─── DAILY SUMMARY ───────────────────────────────────────────────────────────

class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, unique=True, index=True)  # YYYY-MM-DD

    starting_capital = Column(Numeric(14, 2), nullable=False)
    ending_capital = Column(Numeric(14, 2), nullable=True)
    realized_pnl = Column(Numeric(12, 2), default=0)
    unrealized_pnl = Column(Numeric(12, 2), default=0)
    total_brokerage = Column(Numeric(10, 2), default=0)
    net_pnl = Column(Numeric(12, 2), default=0)
    pnl_pct = Column(Float, default=0)

    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    win_rate = Column(Float, default=0)

    max_drawdown_pct = Column(Float, default=0)
    kill_switch_triggered = Column(Boolean, default=False)
    kill_switch_reason = Column(Text, nullable=True)

    strategies_used = Column(JSONB, nullable=True)        # {"momentum": 3, "options_selling": 2}
    agent_decisions_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─── RISK EVENTS ─────────────────────────────────────────────────────────────

class RiskEvent(Base):
    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    event_type = Column(String(50), nullable=False)       # KILL_SWITCH | SL_HIT | DAILY_LIMIT | DRAWDOWN
    severity = Column(String(20), default="WARNING")      # INFO | WARNING | CRITICAL
    symbol = Column(String(50), nullable=True)
    description = Column(Text, nullable=False)
    pnl_at_event = Column(Numeric(12, 2), nullable=True)
    drawdown_at_event = Column(Float, nullable=True)
    resolved = Column(Boolean, default=False)
