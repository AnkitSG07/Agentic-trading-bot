"""
Celery Tasks - Scheduled Background Jobs
Handles: daily reports, data cleanup, instrument refresh, health checks.
"""

import asyncio
import logging
import os
from datetime import datetime, date, timedelta
from decimal import Decimal

from celery import Celery
from celery.schedules import crontab

logger = logging.getLogger("tasks")

# ─── CELERY APP ───────────────────────────────────────────────────────────────

redis_url = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:6379/1"

celery_app = Celery(
    "agenttrader",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        # Daily EOD report - 3:35 PM IST (after market close)
        "daily-eod-report": {
            "task": "core.tasks.celery_tasks.generate_daily_report",
            "schedule": crontab(hour=15, minute=35, day_of_week="1-5"),
        },
        # Refresh instrument master - 8:30 AM IST (before market open)
        "refresh-instruments": {
            "task": "core.tasks.celery_tasks.refresh_instrument_master",
            "schedule": crontab(hour=8, minute=30, day_of_week="1-5"),
        },
        # Health check every 5 minutes during market hours
        "health-check": {
            "task": "core.tasks.celery_tasks.health_check",
            "schedule": crontab(minute="*/5", hour="9-15", day_of_week="1-5"),
        },
        # OHLCV data sync - every 15 minutes
        "sync-ohlcv": {
            "task": "core.tasks.celery_tasks.sync_ohlcv_data",
            "schedule": crontab(minute="*/15", hour="9-15", day_of_week="1-5"),
        },
        # Database cleanup - 2 AM daily
        "db-cleanup": {
            "task": "core.tasks.celery_tasks.cleanup_old_data",
            "schedule": crontab(hour=2, minute=0),
        },
        # Weekly performance report - Sunday 9 PM
        "weekly-report": {
            "task": "core.tasks.celery_tasks.generate_weekly_report",
            "schedule": crontab(hour=21, minute=0, day_of_week=0),
        },
    },
)


def run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── TASKS ───────────────────────────────────────────────────────────────────

@celery_app.task(name="core.tasks.celery_tasks.generate_daily_report", bind=True, max_retries=3)
def generate_daily_report(self):
    """Generate and send end-of-day P&L report via Telegram."""
    try:
        return run_async(_generate_daily_report_async())
    except Exception as exc:
        logger.error(f"Daily report task error: {exc}")
        raise self.retry(exc=exc, countdown=60)


async def _generate_daily_report_async():
    from database.repository import PositionRepository, DailySummaryRepository, TradeRepository
    from core.notifier import TelegramNotifier
    import os

    today = date.today().isoformat()

    # Get today's closed positions
    positions = await PositionRepository.get_history(days=1)
    today_positions = [p for p in positions if p.closed_at and p.closed_at.date() == date.today()]

    total_pnl = sum(float(p.net_pnl or 0) for p in today_positions)
    winners = [p for p in today_positions if (p.net_pnl or 0) > 0]
    losers = [p for p in today_positions if (p.net_pnl or 0) <= 0]
    win_rate = len(winners) / len(today_positions) * 100 if today_positions else 0

    # Get trade count
    trades = await TradeRepository.get_today()
    brokerage = len([t for t in trades if t.status == "COMPLETE"]) * 40  # ~₹40 per trade

    # Save daily summary
    stats = await PositionRepository.get_performance_stats(days=1)
    summary_data = {
        "date": today,
        "starting_capital": 0,  # Will be filled from risk manager
        "realized_pnl": total_pnl,
        "total_brokerage": brokerage,
        "net_pnl": total_pnl - brokerage,
        "pnl_pct": 0,
        "total_trades": len(today_positions),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": win_rate,
        "strategies_used": {},
        "agent_decisions_count": 0,
    }
    await DailySummaryRepository.upsert(summary_data)

    # Send Telegram notification
    notifier = TelegramNotifier(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    await notifier.daily_summary(
        total_pnl=total_pnl,
        pnl_pct=0,
        total_trades=len(today_positions),
        win_rate=win_rate,
        drawdown=0,
    )

    logger.info(f"Daily report generated: ₹{total_pnl:+,.0f} | {len(today_positions)} trades")
    return {"date": today, "pnl": total_pnl, "trades": len(today_positions)}


@celery_app.task(name="core.tasks.celery_tasks.refresh_instrument_master", bind=True, max_retries=2)
def refresh_instrument_master(self):
    """Download fresh instrument master from brokers before market open."""
    try:
        return run_async(_refresh_instruments_async())
    except Exception as exc:
        logger.error(f"Instrument refresh error: {exc}")
        raise self.retry(exc=exc, countdown=300)


async def _refresh_instruments_async():
    """Download and cache instrument CSV from Dhan and Zerodha."""
    import httpx
    import os

    # Dhan instrument master
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get("https://images.dhan.co/api-data/api-scrip-master.csv")
            # Save to local cache
            cache_path = "/tmp/dhan_instruments.csv"
            with open(cache_path, "w") as f:
                f.write(resp.text)
            logger.info(f"Dhan instruments refreshed: {len(resp.text.splitlines())} rows")
    except Exception as e:
        logger.error(f"Dhan instrument refresh error: {e}")

    return {"status": "refreshed", "timestamp": datetime.now().isoformat()}


@celery_app.task(name="core.tasks.celery_tasks.health_check", bind=True)
def health_check(self):
    """Check system health and alert on issues."""
    return run_async(_health_check_async())


async def _health_check_async():
    import httpx
    import os

    issues = []

    # Check API server
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:8000/health")
            if resp.status_code != 200:
                issues.append("API server not responding")
    except Exception:
        issues.append("API server unreachable")

    if issues:
        from core.notifier import TelegramNotifier
        notifier = TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
        await notifier.system_alert("WARNING", f"Health check issues: {', '.join(issues)}")
        logger.warning(f"Health check failed: {issues}")
    else:
        logger.debug("Health check passed")

    return {"status": "ok" if not issues else "degraded", "issues": issues}


@celery_app.task(name="core.tasks.celery_tasks.sync_ohlcv_data", bind=True)
def sync_ohlcv_data(self):
    """Sync latest OHLCV candles to database for all watchlist symbols."""
    return run_async(_sync_ohlcv_async())


async def _sync_ohlcv_async():
    from database.repository import OHLCVRepository

    WATCHLIST = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
        "KOTAKBANK", "WIPRO", "SBIN", "AXISBANK", "BAJFINANCE",
        "TATAMOTORS", "TITAN", "MARUTI", "NESTLEIND", "ZOMATO",
    ]

    synced = 0
    for symbol in WATCHLIST:
        try:
            # In production: fetch from active broker adapter
            # For now, log intent
            logger.debug(f"OHLCV sync: {symbol}")
            synced += 1
            await asyncio.sleep(0.1)  # Rate limit
        except Exception as e:
            logger.warning(f"OHLCV sync error for {symbol}: {e}")

    logger.info(f"OHLCV sync complete: {synced} symbols")
    return {"synced": synced}


@celery_app.task(name="core.tasks.celery_tasks.cleanup_old_data", bind=True)
def cleanup_old_data(self):
    """Remove old tick data and compress historical records."""
    return run_async(_cleanup_async())


async def _cleanup_async():
    from database.repository import get_session
    from sqlalchemy import text

    async with get_session() as session:
        # Delete tick data older than 30 days
        await session.execute(text("""
            DELETE FROM tick_data
            WHERE timestamp < NOW() - INTERVAL '30 days'
        """))
        # Delete OHLCV candles older than 1 year
        await session.execute(text("""
            DELETE FROM ohlcv_candles
            WHERE timestamp < NOW() - INTERVAL '365 days'
              AND interval = 'minute'
        """))
        logger.info("Database cleanup complete")

    return {"status": "cleaned"}


@celery_app.task(name="core.tasks.celery_tasks.generate_weekly_report", bind=True)
def generate_weekly_report(self):
    """Weekly performance analysis sent to Telegram."""
    return run_async(_weekly_report_async())


async def _weekly_report_async():
    from database.repository import PositionRepository
    import os

    stats = await PositionRepository.get_performance_stats(days=7)
    perf_30 = await PositionRepository.get_performance_stats(days=30)

    from core.notifier import TelegramNotifier
    notifier = TelegramNotifier(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    msg = (
        f"📊 <b>WEEKLY PERFORMANCE REPORT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Last 7 Days:</b>\n"
        f"  P&L: ₹{stats['total_pnl']:+,.0f}\n"
        f"  Trades: {stats['total_trades']} | Win Rate: {stats['win_rate']:.1f}%\n"
        f"  Avg Win: ₹{stats['avg_win']:+,.0f} | Avg Loss: ₹{stats['avg_loss']:+,.0f}\n"
        f"  Profit Factor: {stats['profit_factor']:.2f}\n\n"
        f"<b>Last 30 Days:</b>\n"
        f"  P&L: ₹{perf_30['total_pnl']:+,.0f}\n"
        f"  Trades: {perf_30['total_trades']} | Win Rate: {perf_30['win_rate']:.1f}%\n"
        f"📅 Week ending {date.today().strftime('%d %b %Y')}"
    )
    await notifier.send(msg)
    logger.info("Weekly report sent")
    return stats
