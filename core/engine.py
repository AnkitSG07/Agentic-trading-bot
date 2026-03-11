"""
Trading Engine - Main Orchestrator
Coordinates brokers, AI agent, risk manager, and execution.
Runs as an async event loop during market hours.
"""

import asyncio
import logging
from datetime import datetime, time
from decimal import Decimal
from typing import Optional

import pytz

from agents.brain import MarketContext, TradingAgent, SignalAction
from brokers.base import BaseBroker, Exchange, Instrument, InstrumentType, OrderSide, OrderType, ProductType
from data.indicators import IndicatorsEngine
from risk.manager import RiskConfig, RiskManager

logger = logging.getLogger("engine")

IST = pytz.timezone("Asia/Kolkata")

# ─── WATCHLIST ───────────────────────────────────────────────────────────────

DEFAULT_WATCHLIST = [
    # Large Cap
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "KOTAKBANK", "HINDUNILVR", "WIPRO", "SBIN", "AXISBANK",
    # Mid Cap
    "ADANIENT", "BAJFINANCE", "TITAN", "MARUTI", "NESTLEIND",
    # Indices F&O
    "NIFTY", "BANKNIFTY", "FINNIFTY",
    # Volatile / High Beta
    "TATAMOTORS", "TATAPOWER", "IRFC", "ZOMATO", "PAYTM",
]

INDEX_TOKENS = {
    "NSE_NIFTY50": "256265",
    "NSE_BANKNIFTY": "260105",
    "NSE_INDIAVIX": "264969",
}


class TradingEngine:
    """
    The heart of the trading bot.

    Lifecycle:
    1. Startup: Login brokers, initialize risk, load watchlist
    2. Market Open: Start data feed, begin agent decision loop
    3. Intraday: Process ticks → indicators → AI signals → execute orders
    4. Market Close: Square off intraday, generate daily report
    5. Shutdown: Logout brokers, persist data
    """

    def __init__(self, config: dict):
        self.config = config
        self.brokers: dict[str, BaseBroker] = {}
        self.primary_broker: Optional[BaseBroker] = None
        self.agent = TradingAgent(config.get("agent", {}))
        self.indicators = IndicatorsEngine()
        self.risk = RiskManager(RiskConfig(**config.get("risk", {})))

        self._running = False
        self._tick_data: dict[str, dict] = {}       # symbol → latest tick
        self._ohlcv_cache: dict[str, object] = {}    # symbol → DataFrame
        self._active_signals: list = []
        self._pending_orders: dict[str, object] = {}

    # ── Startup ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize and start the trading engine."""
        logger.info("🚀 Starting Trading Engine...")

        # Initialize brokers
        await self._init_brokers()

        if not self.primary_broker:
            raise RuntimeError("No broker connected. Check credentials.")

        # Initialize risk manager
        funds = await self.primary_broker.get_funds()
        await self.risk.initialize(funds)

        # Load instrument data
        await self._load_instruments()

        # Subscribe to market data
        await self._subscribe_market_data()

        self._running = True
        logger.info("✅ Trading Engine started successfully")

        # Start the main loop
        await self._main_loop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("🛑 Stopping Trading Engine...")
        self._running = False

        # Square off all intraday positions
        await self._square_off_all_intraday()

        # Logout brokers
        for broker in self.brokers.values():
            await broker.logout()

        logger.info("✅ Trading Engine stopped")

    # ── Main Loop ────────────────────────────────────────────────────────────

    async def _main_loop(self) -> None:
        """
        Main event loop. Runs during market hours.
        Decision interval: configurable (default 60s).
        """
        decision_interval = self.config.get("agent", {}).get("decision_interval_seconds", 60)
        strategy_review_interval = self.config.get("agent", {}).get("strategy_review_interval", 3600)
        last_strategy_review = datetime.now(IST)

        logger.info(f"📊 Main loop started (decision every {decision_interval}s)")

        while self._running:
            try:
                now = datetime.now(IST)

                # Check if market is open
                if not self._is_market_open(now):
                    if now.time() >= time(15, 30):
                        logger.info("🔔 Market closed. Running end-of-day tasks...")
                        await self._end_of_day()
                        break
                    await asyncio.sleep(30)
                    continue

                # Periodic strategy review
                if (now - last_strategy_review).seconds >= strategy_review_interval:
                    await self._run_strategy_review()
                    last_strategy_review = now

                # Run agent decision cycle
                await self._decision_cycle(now)

                # Monitor open positions (trailing stops, target hits)
                await self._monitor_positions()

                await asyncio.sleep(decision_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(10)

    # ── Decision Cycle ────────────────────────────────────────────────────────

    async def _decision_cycle(self, now: datetime) -> None:
        """One full cycle: gather data → build context → AI decides → execute."""

        if not self.risk.is_trading_allowed:
            logger.warning("⛔ Trading not allowed (kill switch or uninitialized)")
            return

        # 1. Build market context
        context = await self._build_market_context(now)

        # 2. Get AI signals
        signals = await self.agent.analyze_and_decide(context)

        if not signals:
            logger.info("🤔 No actionable signals this cycle")
            return

        logger.info(f"📡 {len(signals)} signals received from AI agent")

        # 3. Execute signals
        funds = await self.primary_broker.get_funds()
        positions = await self.primary_broker.get_positions()

        for signal in signals:
            if not signal.is_actionable:
                continue

            # Pre-trade risk check
            risk_check = await self.risk.check_pre_trade(
                symbol=signal.symbol,
                side=signal.action.value,
                quantity=signal.quantity,
                entry_price=signal.entry_price or Decimal("0"),
                stop_loss=signal.stop_loss,
                open_positions=positions,
                funds=funds,
            )

            if not risk_check.approved:
                logger.warning(f"❌ Signal rejected [{signal.symbol}]: {risk_check.reason}")
                continue

            # Adjust quantity if needed
            qty = risk_check.adjusted_quantity or signal.quantity
            sl = risk_check.adjusted_sl or signal.stop_loss

            # Execute the trade
            await self._execute_signal(signal, qty, sl)

    # ── Signal Execution ─────────────────────────────────────────────────────

    async def _execute_signal(self, signal, quantity: int, stop_loss: Optional[Decimal]) -> None:
        """Place actual orders for a signal."""
        try:
            # Find instrument
            inst = await self._get_instrument(signal.symbol, signal.exchange)
            if not inst:
                logger.error(f"Instrument not found: {signal.symbol}")
                return

            product = ProductType(signal.product)
            side = OrderSide.BUY if signal.action in (SignalAction.BUY, SignalAction.COVER) else OrderSide.SELL

            if signal.entry_price:
                # Limit order
                order = await self.primary_broker.place_order(
                    instrument=inst,
                    side=side,
                    quantity=quantity,
                    order_type=OrderType.LIMIT,
                    product=product,
                    price=signal.entry_price,
                    tag=f"{signal.strategy[:8].upper()}",
                )
            else:
                # Market order
                order = await self.primary_broker.place_order(
                    instrument=inst,
                    side=side,
                    quantity=quantity,
                    order_type=OrderType.MARKET,
                    product=product,
                    tag=f"{signal.strategy[:8].upper()}",
                )

            logger.info(
                f"✅ Order executed: {signal.action.value} {quantity} {signal.symbol} | "
                f"Strategy: {signal.strategy} | Confidence: {signal.confidence:.0%}"
            )

            # Place SL order if we have a stop loss
            if stop_loss and order.status.value != "REJECTED":
                exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
                await self.primary_broker.place_order(
                    instrument=inst,
                    side=exit_side,
                    quantity=quantity,
                    order_type=OrderType.SL_M,
                    product=product,
                    trigger_price=stop_loss,
                    tag=f"SL_{signal.strategy[:6].upper()}",
                )
                logger.info(f"🛡️ SL order placed: ₹{stop_loss}")

        except Exception as e:
            logger.error(f"Order execution error for {signal.symbol}: {e}")

    # ── Position Monitoring ───────────────────────────────────────────────────

    async def _monitor_positions(self) -> None:
        """Check trailing stops, target hits, and update PnL."""
        try:
            positions = await self.primary_broker.get_positions()
            quotes_map = {}

            if positions:
                instruments = [p.instrument for p in positions]
                quotes_map = await self.primary_broker.get_quote(instruments)

            for pos in positions:
                quote = quotes_map.get(pos.instrument.symbol)
                if not quote:
                    continue

                # Update trailing stop
                if self.risk.config.trailing_stop:
                    # In production: fetch current SL order and compare
                    new_sl = self.risk.calculate_trailing_stop(
                        entry_price=pos.average_price,
                        current_price=quote.ltp,
                        current_sl=pos.average_price * Decimal("0.985"),  # Placeholder
                        side=pos.side.value,
                    )
                    # TODO: Modify SL order if new_sl is better

            # Update risk manager PnL
            await self.risk.update_pnl(positions)

        except Exception as e:
            logger.error(f"Position monitoring error: {e}")

    # ── Market Context Builder ────────────────────────────────────────────────

    async def _build_market_context(self, now: datetime) -> MarketContext:
        """Gather all data needed by the AI agent."""

        # Get index prices
        nifty_ltp = 22000.0
        banknifty_ltp = 47000.0
        vix = 14.5

        try:
            index_instruments = [
                Instrument("NIFTY 50", Exchange.NSE, InstrumentType.EQ, "256265"),
                Instrument("NIFTY BANK", Exchange.NSE, InstrumentType.EQ, "260105"),
                Instrument("INDIA VIX", Exchange.NSE, InstrumentType.EQ, "264969"),
            ]
            quotes = await self.primary_broker.get_quote(index_instruments)
            if "NIFTY 50" in quotes:
                nifty_ltp = float(quotes["NIFTY 50"].ltp)
            if "NIFTY BANK" in quotes:
                banknifty_ltp = float(quotes["NIFTY BANK"].ltp)
            if "INDIA VIX" in quotes:
                vix = float(quotes["INDIA VIX"].ltp)
        except Exception as e:
            logger.warning(f"Index data error: {e}")

        # Get watchlist data with indicators
        watchlist_data = await self._get_watchlist_with_indicators()

        # Get portfolio state
        positions = await self.primary_broker.get_positions()
        funds = await self.primary_broker.get_funds()

        positions_dicts = [
            {
                "symbol": p.instrument.symbol,
                "side": p.side.value,
                "quantity": p.quantity,
                "avg_price": float(p.average_price),
                "ltp": float(p.ltp),
                "pnl": float(p.pnl),
                "pnl_pct": p.pnl_pct,
                "broker": p.broker,
            }
            for p in positions
        ]

        return MarketContext(
            timestamp=now,
            nifty50_ltp=nifty_ltp,
            banknifty_ltp=banknifty_ltp,
            india_vix=vix,
            market_trend=self._detect_market_trend(nifty_ltp),
            session=self._get_session(now),
            day_of_week=now.strftime("%A"),
            available_capital=float(funds.available_cash),
            used_margin=float(funds.used_margin),
            open_positions=positions_dicts,
            watchlist_data=watchlist_data,
            options_chain_summary=None,  # Can be extended
            recent_news_sentiment=None,  # Can be extended with news API
            pcr=None,                    # Can be fetched from NSE data
        )

    async def _get_watchlist_with_indicators(self) -> list[dict]:
        """Fetch OHLCV + compute indicators for all watchlist symbols."""
        import pandas as pd
        from datetime import timedelta

        result = []
        now = datetime.now(IST)
        from_date = now - timedelta(days=60)

        for symbol in DEFAULT_WATCHLIST[:15]:  # Limit to avoid rate limits
            try:
                inst = await self._get_instrument(symbol, "NSE")
                if not inst:
                    continue

                ohlcv_list = await self.primary_broker.get_ohlcv(
                    inst, "day", from_date, now
                )
                if not ohlcv_list:
                    continue

                df = pd.DataFrame([
                    {
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": c.volume,
                    }
                    for c in ohlcv_list
                ])

                bundle = self.indicators.compute(df, symbol, "day")
                result.append(self.indicators.to_dict(bundle))

            except Exception as e:
                logger.debug(f"Indicator error for {symbol}: {e}")

        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _init_brokers(self) -> None:
        from brokers.zerodha.adapter import ZerodhaBroker
        from brokers.dhan.adapter import DhanBroker

        broker_config = self.config.get("brokers", {})

        if broker_config.get("zerodha", {}).get("enabled"):
            zb = ZerodhaBroker(broker_config["zerodha"])
            if await zb.login():
                self.brokers["zerodha"] = zb
                if not self.primary_broker:
                    self.primary_broker = zb
                logger.info("✅ Zerodha connected")

        if broker_config.get("dhan", {}).get("enabled"):
            db = DhanBroker(broker_config["dhan"])
            if await db.login():
                self.brokers["dhan"] = db
                if not self.primary_broker:
                    self.primary_broker = db
                logger.info("✅ Dhan connected")

    async def _load_instruments(self) -> None:
        """Pre-load instrument master data."""
        logger.info("📥 Loading instrument master...")
        self._instrument_cache = {}
        try:
            instruments = await self.primary_broker.get_instruments(Exchange.NSE)
            for inst in instruments:
                self._instrument_cache[inst.symbol] = inst
            logger.info(f"✅ Loaded {len(instruments)} NSE instruments")
        except Exception as e:
            logger.error(f"Instrument load error: {e}")

    async def _get_instrument(self, symbol: str, exchange: str = "NSE") -> Optional[Instrument]:
        cache = getattr(self, "_instrument_cache", {})
        if symbol in cache:
            return cache[symbol]
        return Instrument(symbol, Exchange(exchange), InstrumentType.EQ)

    async def _subscribe_market_data(self) -> None:
        """Subscribe to live tick data for watchlist."""
        instruments = [
            Instrument(s, Exchange.NSE, InstrumentType.EQ)
            for s in DEFAULT_WATCHLIST[:20]
        ]
        await self.primary_broker.subscribe_ticks(instruments, self._on_tick)
        logger.info(f"📡 Subscribed to {len(instruments)} instruments")

    async def _on_tick(self, tick: dict) -> None:
        """Process incoming tick data."""
        symbol = tick.get("tradingsymbol") or tick.get("symbol", "")
        if symbol:
            self._tick_data[symbol] = tick

    async def _square_off_all_intraday(self) -> None:
        """Square off all MIS positions at market close."""
        try:
            positions = await self.primary_broker.get_positions()
            mis_positions = [p for p in positions if p.product.value == "MIS"]
            if mis_positions:
                logger.info(f"📤 Squaring off {len(mis_positions)} intraday positions...")
                for pos in mis_positions:
                    await self.primary_broker.square_off_position(pos)
        except Exception as e:
            logger.error(f"Square off error: {e}")

    async def _run_strategy_review(self) -> None:
        """Periodic AI strategy review."""
        logger.info("🔍 Running periodic strategy review...")
        summary = self.risk.get_daily_summary()
        review = await self.agent.review_strategy(summary)
        if review:
            logger.info(f"📋 Strategy review: {review.get('overall_assessment', 'N/A')}")

    async def _end_of_day(self) -> None:
        """End of day tasks: square off, report, persist."""
        await self._square_off_all_intraday()
        summary = self.risk.get_daily_summary()
        logger.info(
            f"📊 DAY SUMMARY | P&L: ₹{summary['total_pnl']:+,.0f} "
            f"({summary['daily_pnl_pct']:+.2f}%) | "
            f"Trades: {summary['total_trades']} | Win Rate: {summary['win_rate']:.1f}%"
        )

    def _is_market_open(self, now: datetime) -> bool:
        if now.weekday() >= 5:  # Saturday, Sunday
            return False
        market_open = time(9, 15)
        market_close = time(15, 30)
        return market_open <= now.time() <= market_close

    def _get_session(self, now: datetime) -> str:
        t = now.time()
        if t < time(9, 15):
            return "pre_open"
        elif t < time(10, 0):
            return "opening"
        elif t < time(14, 30):
            return "mid_session"
        else:
            return "closing"

    def _detect_market_trend(self, nifty_ltp: float) -> str:
        # Simplified - In production, compare to 20-day EMA
        cache = getattr(self, "_nifty_history", [])
        cache.append(nifty_ltp)
        self._nifty_history = cache[-20:]
        if len(cache) < 5:
            return "sideways"
        avg = sum(cache[-5:]) / 5
        if nifty_ltp > avg * 1.005:
            return "bullish"
        elif nifty_ltp < avg * 0.995:
            return "bearish"
        return "sideways"
