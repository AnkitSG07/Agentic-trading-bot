"""
Database Connection Manager + Repository Layer
Async SQLAlchemy with TimescaleDB support.
"""

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import AsyncGenerator, Optional
import uuid

import asyncpg
from urllib.parse import urlparse

from sqlalchemy import text, select, update, and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import (
    Base, Trade, Position, SLOrder, AgentDecision,
    TickData, OHLCVCandle, DailySummary, RiskEvent,
)

logger = logging.getLogger("database")


# ─── ENGINE ──────────────────────────────────────────────────────────────────

_engine = None
_session_factory = None


async def init_db(database_url: str) -> None:
    """Initialize async database engine and create all tables."""
    global _engine, _session_factory

    # Convert sync postgres URLs to asyncpg (support postgres:// and postgresql://)
    if database_url.startswith("postgres://"):
        async_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://"):
        async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        async_url = database_url

    # ─── AUTO-CREATE DATABASE LOGIC ──────────────────────────────────────────
    try:
        # Parse the URL to get the target database name
        parsed = urlparse(async_url.replace("postgresql+asyncpg://", "postgresql://"))
        db_name = parsed.path.lstrip('/')
        
        # Connect to the default 'postgres' database to check/create
        default_url = async_url.replace(f"/{db_name}", "/postgres").replace("postgresql+asyncpg://", "postgresql://")
        
        conn = await asyncpg.connect(default_url)
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        
        if not exists:
            logger.info(f"🛠️ Database '{db_name}' not found. Creating it automatically...")
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            logger.info(f"✅ Database '{db_name}' created successfully!")
        
        await conn.close()
    except Exception as e:
        logger.warning(f"⚠️ Auto-create DB check skipped (might already exist or lack permissions): {e}")
    # ─────────────────────────────────────────────────────────────────────────

    _engine = create_async_engine(
        async_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )

    _session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )

    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Create TimescaleDB hypertable for tick_data if not exists
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM timescaledb_information.hypertables
                        WHERE hypertable_name = 'tick_data'
                    ) THEN
                        PERFORM create_hypertable('tick_data', 'timestamp',
                            if_not_exists => TRUE,
                            chunk_time_interval => INTERVAL '1 day'
                        );
                    END IF;
                EXCEPTION WHEN others THEN
                    -- TimescaleDB not available, continue without hypertable
                    NULL;
                END $$;
            """))

        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        _engine = None
        _session_factory = None
        raise e


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions."""
    if not _session_factory:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ─── TRADE REPOSITORY ────────────────────────────────────────────────────────

class TradeRepository:

    @staticmethod
    async def save(
        broker_order_id: str,
        broker: str,
        symbol: str,
        exchange: str,
        instrument_type: str,
        side: str,
        order_type: str,
        product: str,
        quantity: int,
        price: Optional[Decimal],
        status: str,
        tag: Optional[str] = None,
        strategy: Optional[str] = None,
        confidence: Optional[float] = None,
        rationale: Optional[str] = None,
        average_price: Optional[Decimal] = None,
        position_id: Optional[str] = None,
    ) -> Trade:
        async with get_session() as session:
            trade = Trade(
                broker_order_id=broker_order_id,
                broker=broker,
                symbol=symbol,
                exchange=exchange,
                instrument_type=instrument_type,
                side=side,
                order_type=order_type,
                product=product,
                quantity=quantity,
                price=price,
                average_price=average_price,
                status=status,
                tag=tag,
                strategy=strategy,
                signal_confidence=confidence,
                signal_rationale=rationale,
                position_id=uuid.UUID(position_id) if position_id else None,
            )
            session.add(trade)
            await session.flush()
            return trade

    @staticmethod
    async def update_status(
        broker_order_id: str,
        status: str,
        filled_qty: int = 0,
        avg_price: Optional[Decimal] = None,
    ) -> None:
        async with get_session() as session:
            await session.execute(
                update(Trade)
                .where(Trade.broker_order_id == broker_order_id)
                .values(
                    status=status,
                    filled_quantity=filled_qty,
                    average_price=avg_price,
                    completed_at=datetime.utcnow() if status == "COMPLETE" else None,
                )
            )

    @staticmethod
    async def get_today(symbol: Optional[str] = None) -> list[Trade]:
        async with get_session() as session:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
            q = select(Trade).where(Trade.placed_at >= today_start)
            if symbol:
                q = q.where(Trade.symbol == symbol)
            result = await session.execute(q.order_by(desc(Trade.placed_at)))
            return result.scalars().all()

    @staticmethod
    async def get_pnl_stats(days: int = 30) -> dict:
        async with get_session() as session:
            since = datetime.utcnow() - timedelta(days=days)
            result = await session.execute(
                select(
                    func.count(Trade.id).label("total"),
                    func.sum(Trade.quantity * Trade.average_price).label("total_value"),
                ).where(
                    and_(Trade.placed_at >= since, Trade.status == "COMPLETE")
                )
            )
            row = result.one()
            return {"total_trades": row.total or 0, "total_value": float(row.total_value or 0)}


# ─── POSITION REPOSITORY ─────────────────────────────────────────────────────

class PositionRepository:

    @staticmethod
    async def open_position(
        broker: str,
        symbol: str,
        exchange: str,
        product: str,
        side: str,
        quantity: int,
        entry_price: Decimal,
        stop_loss: Optional[Decimal] = None,
        target: Optional[Decimal] = None,
        strategy: Optional[str] = None,
    ) -> Position:
        async with get_session() as session:
            pos = Position(
                broker=broker,
                symbol=symbol,
                exchange=exchange,
                product=product,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target=target,
                strategy=strategy,
                instrument_type="EQ",
                status="OPEN",
            )
            session.add(pos)
            await session.flush()
            return pos

    @staticmethod
    async def close_position(
        position_id: str,
        exit_price: Decimal,
        realized_pnl: Decimal,
        exit_reason: str,
        brokerage: Decimal = Decimal("40"),
        stt: Decimal = Decimal("0"),
    ) -> None:
        net = realized_pnl - brokerage - stt
        async with get_session() as session:
            await session.execute(
                update(Position)
                .where(Position.id == uuid.UUID(position_id))
                .values(
                    exit_price=exit_price,
                    realized_pnl=realized_pnl,
                    brokerage=brokerage,
                    stt=stt,
                    net_pnl=net,
                    status="CLOSED",
                    exit_reason=exit_reason,
                    closed_at=datetime.utcnow(),
                )
            )

    @staticmethod
    async def update_stop_loss(position_id: str, new_sl: Decimal) -> None:
        async with get_session() as session:
            await session.execute(
                update(Position)
                .where(Position.id == uuid.UUID(position_id))
                .values(stop_loss=new_sl)
            )

    @staticmethod
    async def get_open_positions() -> list[Position]:
        async with get_session() as session:
            result = await session.execute(
                select(Position).where(Position.status == "OPEN")
                .order_by(desc(Position.opened_at))
            )
            return result.scalars().all()

    @staticmethod
    async def get_history(days: int = 30, symbol: Optional[str] = None) -> list[Position]:
        async with get_session() as session:
            since = datetime.utcnow() - timedelta(days=days)
            q = select(Position).where(
                and_(Position.opened_at >= since, Position.status == "CLOSED")
            )
            if symbol:
                q = q.where(Position.symbol == symbol)
            result = await session.execute(q.order_by(desc(Position.closed_at)))
            return result.scalars().all()

    @staticmethod
    async def get_performance_stats(days: int = 30) -> dict:
        async with get_session() as session:
            since = datetime.utcnow() - timedelta(days=days)
            result = await session.execute(
                select(
                    func.count(Position.id).label("total"),
                    func.sum(Position.net_pnl).label("total_pnl"),
                    func.count(Position.id).filter(Position.net_pnl > 0).label("winners"),
                    func.avg(Position.net_pnl).filter(Position.net_pnl > 0).label("avg_win"),
                    func.avg(Position.net_pnl).filter(Position.net_pnl < 0).label("avg_loss"),
                ).where(
                    and_(Position.opened_at >= since, Position.status == "CLOSED")
                )
            )
            row = result.one()
            total = row.total or 0
            winners = row.winners or 0
            return {
                "total_trades": total,
                "total_pnl": float(row.total_pnl or 0),
                "win_rate": round(winners / total * 100, 1) if total > 0 else 0,
                "avg_win": float(row.avg_win or 0),
                "avg_loss": float(row.avg_loss or 0),
                "profit_factor": abs(float(row.avg_win or 0) / float(row.avg_loss or 1)),
            }


# ─── SL ORDER REPOSITORY ─────────────────────────────────────────────────────

class SLOrderRepository:

    @staticmethod
    async def save(
        position_id: str,
        broker_order_id: str,
        broker: str,
        symbol: str,
        sl_price: Decimal,
        sl_type: str = "INITIAL",
    ) -> SLOrder:
        async with get_session() as session:
            sl = SLOrder(
                position_id=uuid.UUID(position_id),
                broker_order_id=broker_order_id,
                broker=broker,
                symbol=symbol,
                sl_price=sl_price,
                sl_type=sl_type,
            )
            session.add(sl)
            await session.flush()
            return sl

    @staticmethod
    async def get_active_for_position(position_id: str) -> Optional[SLOrder]:
        async with get_session() as session:
            result = await session.execute(
                select(SLOrder).where(
                    and_(
                        SLOrder.position_id == uuid.UUID(position_id),
                        SLOrder.is_active == True,
                    )
                ).order_by(desc(SLOrder.created_at))
            )
            return result.scalars().first()

    @staticmethod
    async def deactivate(sl_order_id: str) -> None:
        async with get_session() as session:
            await session.execute(
                update(SLOrder)
                .where(SLOrder.id == uuid.UUID(sl_order_id))
                .values(is_active=False)
            )


# ─── AGENT DECISION REPOSITORY ───────────────────────────────────────────────

class AgentDecisionRepository:

    @staticmethod
    async def save(
        timestamp: datetime,
        market_regime: str,
        market_commentary: str,
        session_name: str,
        nifty_ltp: float,
        banknifty_ltp: float,
        india_vix: float,
        pcr: Optional[float],
        signals_generated: int,
        signals_executed: int,
        signals_rejected: int,
        risk_assessment: str,
        session_recommendation: str,
        raw_response: dict,
        context_snapshot: dict,
    ) -> AgentDecision:
        async with get_session() as session:
            decision = AgentDecision(
                timestamp=timestamp,
                market_regime=market_regime,
                market_commentary=market_commentary,
                session=session_name,
                nifty_ltp=nifty_ltp,
                banknifty_ltp=banknifty_ltp,
                india_vix=india_vix,
                pcr=pcr,
                signals_generated=signals_generated,
                signals_executed=signals_executed,
                signals_rejected=signals_rejected,
                risk_assessment=risk_assessment,
                session_recommendation=session_recommendation,
                raw_response=raw_response,
                context_snapshot=context_snapshot,
            )
            session.add(decision)
            await session.flush()
            return decision

    @staticmethod
    async def get_recent(limit: int = 20) -> list[AgentDecision]:
        async with get_session() as session:
            result = await session.execute(
                select(AgentDecision)
                .order_by(desc(AgentDecision.timestamp))
                .limit(limit)
            )
            return result.scalars().all()


# ─── TICK REPOSITORY ─────────────────────────────────────────────────────────

class TickRepository:

    @staticmethod
    async def save_batch(ticks: list[dict]) -> None:
        """Bulk insert ticks - optimized for high frequency."""
        if not ticks:
            return
        async with get_session() as session:
            # Use upsert for deduplication
            stmt = pg_insert(TickData).values(ticks)
            stmt = stmt.on_conflict_do_nothing(index_elements=["timestamp", "symbol"])
            await session.execute(stmt)

    @staticmethod
    async def get_recent(symbol: str, minutes: int = 60) -> list[TickData]:
        async with get_session() as session:
            since = datetime.utcnow() - timedelta(minutes=minutes)
            result = await session.execute(
                select(TickData)
                .where(and_(TickData.symbol == symbol, TickData.timestamp >= since))
                .order_by(TickData.timestamp)
            )
            return result.scalars().all()


# ─── OHLCV REPOSITORY ────────────────────────────────────────────────────────

class OHLCVRepository:

    @staticmethod
    async def upsert_candles(candles: list[dict]) -> None:
        if not candles:
            return
        async with get_session() as session:
            stmt = pg_insert(OHLCVCandle).values(candles)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_ohlcv",
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "oi": stmt.excluded.oi,
                },
            )
            await session.execute(stmt)

    @staticmethod
    async def get_candles(
        symbol: str, interval: str, limit: int = 200
    ) -> list[OHLCVCandle]:
        async with get_session() as session:
            result = await session.execute(
                select(OHLCVCandle)
                .where(
                    and_(OHLCVCandle.symbol == symbol, OHLCVCandle.interval == interval)
                )
                .order_by(desc(OHLCVCandle.timestamp))
                .limit(limit)
            )
            candles = result.scalars().all()
            return list(reversed(candles))  # Chronological order


# ─── DAILY SUMMARY REPOSITORY ────────────────────────────────────────────────

class DailySummaryRepository:

    @staticmethod
    async def upsert(summary_data: dict) -> None:
        async with get_session() as session:
            stmt = pg_insert(DailySummary).values(**summary_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["date"],
                set_={k: stmt.excluded[k] for k in summary_data if k != "date"},
            )
            await session.execute(stmt)

    @staticmethod
    async def get_history(days: int = 30) -> list[DailySummary]:
        async with get_session() as session:
            result = await session.execute(
                select(DailySummary)
                .order_by(desc(DailySummary.date))
                .limit(days)
            )
            return result.scalars().all()


# ─── RISK EVENT REPOSITORY ───────────────────────────────────────────────────

class RiskEventRepository:

    @staticmethod
    async def log(
        event_type: str,
        description: str,
        severity: str = "WARNING",
        symbol: Optional[str] = None,
        pnl: Optional[Decimal] = None,
        drawdown: Optional[float] = None,
    ) -> None:
        async with get_session() as session:
            event = RiskEvent(
                event_type=event_type,
                severity=severity,
                symbol=symbol,
                description=description,
                pnl_at_event=pnl,
                drawdown_at_event=drawdown,
            )
            session.add(event)

    @staticmethod
    async def get_recent(limit: int = 50) -> list[RiskEvent]:
        async with get_session() as session:
            result = await session.execute(
                select(RiskEvent).order_by(desc(RiskEvent.timestamp)).limit(limit)
            )
            return result.scalars().all()
