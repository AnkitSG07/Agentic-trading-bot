#!/usr/bin/env python3
"""
AgentTrader India v2.0 - Main Entry Point
Fully wired: DB init, engine singleton, API server, Celery.

Usage:
    python main.py                  # API-only mode (safe default)
    python main.py --mode paper     # Paper trading
    python main.py --mode production  # Live trading
    python main.py --mode backtest    # Backtest
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# config.loader handles dotenv + yaml + env expansion
from config.loader import load_config

BASE_DIR = Path(__file__).parent


# ─── LOGGING ─────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    from datetime import date
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / f"bot_{date.today()}.log"),
        ],
    )
    for lib in ("urllib3", "asyncio", "websocket", "kiteconnect", "httpx", "httpcore"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# ─── DATABASE INIT ────────────────────────────────────────────────────────────

async def init_database(config: dict) -> None:
    # Prefer managed database URL in cloud deployments (e.g., Render).
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")

    if not url:
        db_cfg = config.get("data", {}).get("postgres", {})
        url = (
            f"postgresql://{db_cfg.get('user', 'trader')}:"
            f"{db_cfg.get('password', 'password')}@"
            f"{db_cfg.get('host', 'localhost')}:"
            f"{db_cfg.get('port', 5432)}/"
            f"{db_cfg.get('database', 'trading_bot')}"
        )

    from database.repository import init_db
    await init_db(url)
    logging.getLogger("main").info("✅ Database connected")


# ─── RUNNERS ─────────────────────────────────────────────────────────────────

async def run_trading(config: dict, mode: str) -> None:
    from core.engine import TradingEngine, set_engine

    logger = logging.getLogger("main")

    if mode == "paper":
        config["app"]["environment"] = "paper"
        for broker_cfg in config.get("brokers", {}).values():
            if isinstance(broker_cfg, dict):
                broker_cfg["sandbox"] = True
        logger.info("📋 PAPER TRADING MODE — No real orders")

    engine = TradingEngine(config)
    loop = asyncio.get_event_loop()

    def _shutdown():
        logger.info("Signal received — shutting down...")
        asyncio.create_task(engine.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass  # Windows

    await engine.start()


async def run_api_server(config: dict) -> None:
    import uvicorn
    port = int(os.getenv("API_PORT", config.get("app", {}).get("api_port", 8000)))
    server = uvicorn.Server(
        uvicorn.Config(
            "core.server:app",
            host="0.0.0.0",
            port=port,
            log_level="warning",
            loop="asyncio",
        )
    )
    await server.serve()


async def run_backtest(config: dict) -> None:
    """
    Vectorbt-based backtester.
    Runs all strategies on historical data and prints performance report.
    """
    import pandas as pd
    logger = logging.getLogger("backtest")
    logger.info("📊 Starting backtester...")

    try:
        import vectorbt as vbt
    except ImportError:
        logger.error("vectorbt not installed. Run: pip install vectorbt")
        return

    # Default backtest: NIFTY Momentum strategy on Nifty 50 components
    from data.indicators import IndicatorsEngine
    from datetime import datetime, timedelta

    ind_engine = IndicatorsEngine()
    symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
    end = datetime.now()
    start = end - timedelta(days=365)

    logger.info(f"Backtesting {len(symbols)} symbols from {start.date()} to {end.date()}")

    results = {}
    for symbol in symbols:
        try:
            # In production: fetch from broker or local DB
            # For demo, generate synthetic data
            import numpy as np
            n = 252
            prices = pd.Series(
                10000 * np.cumprod(1 + np.random.normal(0.0005, 0.015, n)),
                name=symbol,
            )

            # RSI-based strategy
            df = pd.DataFrame({"close": prices})
            bundle = ind_engine.compute(df, symbol, "day")

            # Vectorbt backtest
            close = vbt.Data.from_data(prices)
            rsi = vbt.RSI.run(prices, window=14)

            entries = rsi.rsi_crossed_above(30)
            exits = rsi.rsi_crossed_below(70)

            pf = vbt.Portfolio.from_signals(
                close=prices,
                entries=entries.rsi_crossed_above,
                exits=exits.rsi_crossed_below,
                init_cash=100000,
                fees=0.0002,
                slippage=0.0005,
            )

            results[symbol] = {
                "total_return": round(pf.total_return * 100, 2),
                "sharpe": round(pf.sharpe_ratio, 2),
                "max_drawdown": round(pf.max_drawdown * 100, 2),
                "total_trades": int(pf.trades.count()),
                "win_rate": round(pf.trades.win_rate * 100, 1),
            }
            logger.info(f"{symbol}: Return={results[symbol]['total_return']}% | "
                       f"Sharpe={results[symbol]['sharpe']} | "
                       f"MaxDD={results[symbol]['max_drawdown']}%")

        except Exception as e:
            logger.error(f"Backtest error {symbol}: {e}")

    # Summary
    if results:
        avg_return = sum(r["total_return"] for r in results.values()) / len(results)
        logger.info(f"\n{'='*50}")
        logger.info(f"BACKTEST COMPLETE | Avg Return: {avg_return:.2f}%")
        logger.info(f"{'='*50}")
        for sym, r in results.items():
            logger.info(f"  {sym:15s} | {r['total_return']:+6.2f}% | "
                       f"Sharpe: {r['sharpe']:.2f} | "
                       f"WinRate: {r['win_rate']:.1f}% | "
                       f"Trades: {r['total_trades']}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="AgentTrader India v2.0")
    parser.add_argument("--mode", choices=["production", "paper", "backtest"], default=None)
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--no-db", action="store_true", help="Skip DB init (for testing)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    # Default to API-only mode if no mode specified
    if args.mode is None and not args.api_only:
        args.api_only = True

    config = load_config()
    setup_logging(config.get("app", {}).get("log_level", args.log_level))
    logger = logging.getLogger("main")

    logger.info("=" * 60)
    logger.info("🤖  AgentTrader India v2.0")
    if args.api_only:
        logger.info("    Mode    : API-ONLY (engine starts via API)")
    else:
        logger.info(f"    Mode    : {args.mode.upper()}")
    logger.info(f"    Brokers : Zerodha + Dhan")
    logger.info(f"    AI      : Claude (Multi-Strategy)")
    logger.info("=" * 60)

    # Initialize database (unless skipped)
    db_initialized = False
    if not args.no_db:
        try:
            await init_database(config)
            db_initialized = True
        except Exception as e:
            logger.error(f"❌ DB init failed: {e}")
            logger.warning("⚠️  Running without database - limited functionality")

    if args.api_only:
        logger.info("🌐 API-only mode — engine must be started via POST /api/engine/start")
        await run_api_server(config)

    elif args.mode == "backtest":
        if not db_initialized:
            logger.error("Backtest requires database. Remove --no-db flag.")
            return
        await run_backtest(config)

    else:
        if not db_initialized:
            logger.error("Trading mode requires database. Remove --no-db flag.")
            return
        # Run trading engine + API server concurrently
        logger.info(f"🚀 Starting engine ({args.mode}) + API server together...")
        await asyncio.gather(
            run_trading(config, args.mode),
            run_api_server(config),
            return_exceptions=True,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Shutdown complete")
