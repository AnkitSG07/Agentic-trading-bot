#!/usr/bin/env python3
"""
AgentTrader India - Main Entry Point
Agentic Trading Bot for Indian Markets (Zerodha + Dhan)

Usage:
    python main.py                     # Start trading engine + API
    python main.py --mode paper        # Paper trading mode
    python main.py --mode backtest     # Run backtest
    python main.py --api-only          # Start API server only (no trading)
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ─── SETUP ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# Inject env vars into config
def _expand_env(value):
    if isinstance(value, str) and value.startswith("${"):
        key = value[2:].split("}")[0]
        default = value.split(":-")[1].rstrip("}") if ":-" in value else None
        return os.getenv(key, default) or value
    elif isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config() -> dict:
    config_path = BASE_DIR / "config" / "config.yaml"
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return _expand_env(raw)


def setup_logging(level: str = "INFO") -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                log_dir / f"trading_{__import__('datetime').date.today()}.log"
            ),
        ],
    )
    # Suppress noisy libraries
    for lib in ("urllib3", "asyncio", "websocket", "kiteconnect"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# ─── MAIN ────────────────────────────────────────────────────────────────────

async def run_trading(config: dict, mode: str = "production") -> None:
    """Start the full trading engine."""
    from core.engine import TradingEngine

    # Override mode if needed
    if mode == "paper":
        config["app"]["environment"] = "paper"
        for broker in config.get("brokers", {}).values():
            broker["sandbox"] = True
        logging.getLogger().info("📋 PAPER TRADING MODE - No real orders will be placed")

    engine = TradingEngine(config)

    # Graceful shutdown handler
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(engine.stop())
        )

    await engine.start()


async def run_api_only(config: dict) -> None:
    """Start just the API server (for development/monitoring)."""
    import uvicorn
    uvicorn.run(
        "core.server:app",
        host="0.0.0.0",
        port=int(config.get("app", {}).get("api_port", 8000)),
        log_level="info",
    )


async def run_backtest(config: dict) -> None:
    """Run backtesting mode."""
    logging.getLogger().info("📊 Backtest mode coming soon...")
    # TODO: Implement vectorbt-based backtester


async def main() -> None:
    parser = argparse.ArgumentParser(description="AgentTrader India")
    parser.add_argument(
        "--mode",
        choices=["production", "paper", "backtest"],
        default="paper",
        help="Trading mode (default: paper)"
    )
    parser.add_argument("--api-only", action="store_true", help="Run API server only")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config.get("app", {}).get("log_level", args.log_level))

    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info("🤖 AgentTrader India v1.0.0")
    logger.info(f"   Mode: {args.mode.upper()}")
    logger.info(f"   Brokers: Zerodha + Dhan")
    logger.info(f"   Strategy: Multi-Strategy AI (Claude)")
    logger.info("=" * 60)

    if args.api_only:
        await run_api_only(config)
    elif args.mode == "backtest":
        await run_backtest(config)
    else:
        # Run trading engine + API server concurrently
        await asyncio.gather(
            run_trading(config, args.mode),
            run_api_only(config),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
