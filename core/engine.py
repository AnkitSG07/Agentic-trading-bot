"""
Trading Engine v2 - Fully Implemented Orchestrator
- Real SL order tracking with trailing stop
- Live index data from NSE (not hardcoded)
- Options chain wired into AI context
- News sentiment from NSE announcements
- Full DB persistence on every trade/position
- Module-level singleton for API access
"""

import asyncio
import logging
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Optional

import pandas as pd
import pytz

from agents.brain import MarketContext, TradingAgent, SignalAction, TradingSignal
from brokers.base import (
    BaseBroker, Exchange, Instrument, InstrumentType,
    OrderSide, OrderType, Position, ProductType,
)
from data.indicators import IndicatorsEngine
from data.nse_feed import NSEDataFeed, NewsSentimentAnalyzer
from database.repository import (
    AgentDecisionRepository, OHLCVRepository,
    PositionRepository, RiskEventRepository,
    SLOrderRepository, TradeRepository,
)
from risk.manager import RiskConfig, RiskManager

logger = logging.getLogger("engine")
IST = pytz.timezone("Asia/Kolkata")

WATCHLIST = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "KOTAKBANK", "HINDUNILVR", "WIPRO", "SBIN", "AXISBANK",
    "ADANIENT", "BAJFINANCE", "TITAN", "MARUTI", "NESTLEIND",
    "TATAMOTORS", "TATAPOWER", "ZOMATO", "PAYTM", "LT",
]

# ─── MODULE-LEVEL SINGLETON ───────────────────────────────────────────────────

_engine_instance: Optional["TradingEngine"] = None


def get_engine() -> Optional["TradingEngine"]:
    return _engine_instance


def set_engine(engine: Optional["TradingEngine"]) -> None:
    global _engine_instance
    _engine_instance = engine


# ─── POSITION TRACKER ─────────────────────────────────────────────────────────

class ActivePositionTracker:
    """Tracks open positions with their SL order IDs for trailing stop management."""

    def __init__(self):
        self._positions: dict[str, dict] = {}

    def add(self, position_db_id: str, symbol: str, side: str, quantity: int,
            entry_price: Decimal, stop_loss: Optional[Decimal], target: Optional[Decimal],
            sl_broker_order_id: Optional[str], broker: str, strategy: str) -> None:
        self._positions[position_db_id] = {
            "symbol": symbol, "side": side, "quantity": quantity,
            "entry_price": entry_price, "current_sl": stop_loss, "target": target,
            "sl_broker_order_id": sl_broker_order_id, "broker": broker,
            "strategy": strategy, "peak_price": entry_price,
        }

    def update_peak(self, pos_id: str, ltp: Decimal) -> None:
        pos = self._positions.get(pos_id)
        if not pos:
            return
        if pos["side"] == "BUY" and ltp > pos["peak_price"]:
            pos["peak_price"] = ltp
        elif pos["side"] == "SELL" and ltp < pos["peak_price"]:
            pos["peak_price"] = ltp

    def get_all(self) -> list[dict]:
        return [{"id": k, **v} for k, v in self._positions.items()]

    def get(self, pos_id: str) -> Optional[dict]:
        return self._positions.get(pos_id)

    def update_sl(self, pos_id: str, new_sl: Decimal, new_order_id: str) -> None:
        if pos_id in self._positions:
            self._positions[pos_id]["current_sl"] = new_sl
            self._positions[pos_id]["sl_broker_order_id"] = new_order_id

    def remove(self, pos_id: str) -> None:
        self._positions.pop(pos_id, None)


# ─── TRADING ENGINE ────────────────────────────────────────────────────────────

class TradingEngine:

    def __init__(self, config: dict):
        self.config = config
        self.brokers: dict[str, BaseBroker] = {}
        self.primary_broker: Optional[BaseBroker] = None
        self.agent = TradingAgent(config.get("agent", {}))
        self.indicators = IndicatorsEngine()
        self.nse_feed = NSEDataFeed()
        self.sentiment = NewsSentimentAnalyzer()

        risk_fields = RiskConfig.__dataclass_fields__.keys()
        risk_kwargs = {k: v for k, v in config.get("risk", {}).items() if k in risk_fields}
        self.risk = RiskManager(RiskConfig(**risk_kwargs))
        self.tracker = ActivePositionTracker()

        self._running = False
        self._tick_data: dict[str, dict] = {}
        self._instrument_cache: dict[str, Instrument] = {}
        self._ohlcv_frames: dict[str, pd.DataFrame] = {}
        self._nifty_history: list[float] = []
        self._primary_broker_name: str = ""
        self._agent_status: dict[str, object] = {
            "cycle_id": None,
            "stage": "idle",
            "stage_started_at": None,
            "cycle_started_at": None,
            "last_cycle_duration_ms": None,
            "last_error": None,
        }
        self._agent_events: list[dict[str, str]] = []

    def _set_agent_stage(self, stage: str, now: Optional[datetime] = None, error: Optional[str] = None) -> None:
        ts = now or datetime.now(IST)
        self._agent_status["stage"] = stage
        self._agent_status["stage_started_at"] = ts.isoformat()
        if error:
            self._agent_status["last_error"] = error

    def _push_agent_event(self, message: str, level: str = "info", now: Optional[datetime] = None) -> None:
        ts = now or datetime.now(IST)
        self._agent_events.append({
            "timestamp": ts.isoformat(),
            "level": level,
            "message": message,
        })
        self._agent_events = self._agent_events[-100:]

    # ── Startup / Shutdown ────────────────────────────────────────────────────

    async def start(self) -> None:
        logger.info("🚀 Starting Trading Engine...")
        set_engine(self)

        await self._init_brokers()
        if not self.primary_broker:
            raise RuntimeError("No broker connected. Check credentials.")

        funds = await self.primary_broker.get_funds()
        await self.risk.initialize(funds)

        await self._load_instruments()
        await self._preload_ohlcv()
        await self._subscribe_market_data()

        self._running = True
        logger.info("✅ Trading Engine ready")
        await self._main_loop()

    async def stop(self) -> None:
        logger.info("🛑 Stopping...")
        self._running = False
        await self._square_off_all_intraday()
        await self._save_daily_summary()
        await self.nse_feed.close()
        for broker in self.brokers.values():
            await broker.logout()
        set_engine(None)

    # ── Main Loop ─────────────────────────────────────────────────────────────

    async def _main_loop(self) -> None:
        interval = self.config.get("agent", {}).get("decision_interval_seconds", 60)
        review_interval = self.config.get("agent", {}).get("strategy_review_interval", 3600)
        last_review = datetime.now(IST)

        while self._running:
            try:
                now = datetime.now(IST)
                if not self._is_market_open(now):
                    if now.time() >= time(15, 30):
                        await self._end_of_day()
                        break
                    await asyncio.sleep(30)
                    continue

                if (now - last_review).seconds >= review_interval:
                    await self._run_strategy_review()
                    last_review = now

                await self._refresh_ohlcv()
                await self._decision_cycle(now)
                await self._monitor_positions()
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(10)

    # ── Decision Cycle ────────────────────────────────────────────────────────

    async def _decision_cycle(self, now: datetime) -> None:
        self._agent_status["cycle_id"] = uuid.uuid4().hex[:8]
        self._agent_status["cycle_started_at"] = now.isoformat()
        self._agent_status["last_error"] = None

        if not self.risk.is_trading_allowed:
            self._set_agent_stage("paused", now)
            self._push_agent_event("Trading paused: kill switch active", level="warn", now=now)
            logger.warning("⛔ Kill switch active - no trading")
            return

        self._set_agent_stage("collecting_context", now)
        context = await self._build_market_context(now)

        self._set_agent_stage("calling_model")
        signals = await self.agent.analyze_and_decide(context)
        self._push_agent_event(
            f"AI generated {len(signals)} signal(s) | regime {getattr(context, '_regime', 'unknown')}",
            now=now,
        )

        executed, rejected = 0, 0
        rejection_breakdown: dict[str, int] = {}
        self._set_agent_stage("risk_checks")
        if signals:
            funds = await self.primary_broker.get_funds()
            positions = await self.primary_broker.get_positions()

            for signal in signals:
                if not signal.is_actionable:
                    continue
                check = await self.risk.check_pre_trade(
                    symbol=signal.symbol,
                    side=signal.action.value,
                    quantity=signal.quantity,
                    entry_price=signal.entry_price or Decimal("1"),
                    stop_loss=signal.stop_loss,
                    open_positions=positions,
                    funds=funds,
                )
                if not check.approved:
                    logger.warning(f"❌ {signal.symbol}: {check.reason}")
                    reason = (check.reason or "risk_check_failed").strip().lower().replace(" ", "_")
                    rejection_breakdown[reason] = rejection_breakdown.get(reason, 0) + 1
                    self._push_agent_event(
                        f"{signal.symbol} {signal.action.value} rejected: {check.reason}",
                        level="error",
                    )
                    rejected += 1
                    continue

                qty = check.adjusted_quantity or signal.quantity
                sl = check.adjusted_sl or signal.stop_loss
                self._set_agent_stage("placing_orders")
                ok = await self._execute_signal(signal, qty, sl)
                if ok:
                    executed += 1
                    self._push_agent_event(f"{signal.symbol} {signal.action.value} executed qty {qty}", level="success")
                else:
                    rejected += 1
                    rejection_breakdown["execution_error"] = rejection_breakdown.get("execution_error", 0) + 1
                    self._push_agent_event(
                        f"{signal.symbol} {signal.action.value} execution failed",
                        level="error",
                    )

        latest_decision = self.agent.decision_history[-1] if self.agent.decision_history else None
        if latest_decision and latest_decision.get("timestamp") == context.timestamp.isoformat():
            latest_decision["signals_generated"] = len(signals)
            latest_decision["signals_executed"] = executed
            latest_decision["signals_rejected"] = rejected
            latest_decision["rejection_breakdown"] = rejection_breakdown
            latest_decision["market_commentary"] = latest_decision.get("market_commentary") or latest_decision.get("commentary")

        # Persist to DB
        try:
            await AgentDecisionRepository.save(
                timestamp=now,
                market_regime=getattr(context, "_regime", "unknown"),
                market_commentary=(latest_decision or {}).get("market_commentary") or "",
                session_name=context.session,
                nifty_ltp=context.nifty50_ltp,
                banknifty_ltp=context.banknifty_ltp,
                india_vix=context.india_vix,
                pcr=context.pcr,
                signals_generated=len(signals),
                signals_executed=executed,
                signals_rejected=rejected,
                risk_assessment=(latest_decision or {}).get("risk_assessment") or "",
                session_recommendation=(latest_decision or {}).get("session_recommendation") or "",
                raw_response={"rejection_breakdown": rejection_breakdown},
                context_snapshot={
                    "capital": context.available_capital,
                    "positions": len(context.open_positions),
                    "session": context.session,
                },
            )
        except Exception as e:
            logger.debug(f"Decision persist error: {e}")

        done = datetime.now(IST)
        self._set_agent_stage("decision_complete", done)
        if self._agent_status.get("cycle_started_at"):
            cycle_started = datetime.fromisoformat(str(self._agent_status["cycle_started_at"]))
            self._agent_status["last_cycle_duration_ms"] = int((done - cycle_started).total_seconds() * 1000)

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _execute_signal(self, signal: TradingSignal, qty: int, sl: Optional[Decimal]) -> bool:
        try:
            inst = await self._get_instrument(signal.symbol, signal.exchange)
            product = ProductType(signal.product)
            side = OrderSide.BUY if signal.action in (SignalAction.BUY, SignalAction.COVER) else OrderSide.SELL
            order_type = OrderType.LIMIT if signal.entry_price else OrderType.MARKET

            # Entry order
            entry_order = await self.primary_broker.place_order(
                instrument=inst, side=side, quantity=qty,
                order_type=order_type, product=product,
                price=signal.entry_price, tag=signal.strategy[:8].upper(),
            )

            logger.info(f"✅ {signal.action.value} {qty} {signal.symbol} | {signal.strategy} | {signal.confidence:.0%}")

            # Save trade
            await TradeRepository.save(
                broker_order_id=entry_order.order_id,
                broker=self._primary_broker_name,
                symbol=signal.symbol, exchange=signal.exchange,
                instrument_type=inst.instrument_type.value,
                side=side.value, order_type=order_type.value, product=product.value,
                quantity=qty, price=signal.entry_price,
                status=entry_order.status.value, tag=signal.strategy[:8].upper(),
                strategy=signal.strategy, confidence=signal.confidence,
                rationale=signal.rationale,
            )

            # Open DB position
            entry_price = signal.entry_price or Decimal("0")
            db_pos = await PositionRepository.open_position(
                broker=self._primary_broker_name,
                symbol=signal.symbol, exchange=signal.exchange,
                product=product.value, side=side.value, quantity=qty,
                entry_price=entry_price, stop_loss=sl,
                target=signal.target, strategy=signal.strategy,
            )

            # SL order
            sl_order_id = None
            if sl:
                exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
                try:
                    sl_order = await self.primary_broker.place_order(
                        instrument=inst, side=exit_side, quantity=qty,
                        order_type=OrderType.SL_M, product=product,
                        trigger_price=sl, tag=f"SL_{signal.strategy[:6].upper()}",
                    )
                    sl_order_id = sl_order.order_id
                    await SLOrderRepository.save(
                        position_id=str(db_pos.id),
                        broker_order_id=sl_order.order_id,
                        broker=self._primary_broker_name,
                        symbol=signal.symbol, sl_price=sl, sl_type="INITIAL",
                    )
                    logger.info(f"🛡️ SL @ ₹{sl} [{sl_order.order_id}]")
                except Exception as e:
                    logger.error(f"SL placement failed {signal.symbol}: {e}")
                    await RiskEventRepository.log(
                        "SL_ORDER_FAILED", f"SL failed for {signal.symbol}: {e}",
                        severity="CRITICAL", symbol=signal.symbol,
                    )

            self.tracker.add(
                position_db_id=str(db_pos.id), symbol=signal.symbol,
                side=side.value, quantity=qty, entry_price=entry_price,
                stop_loss=sl, target=signal.target,
                sl_broker_order_id=sl_order_id,
                broker=self._primary_broker_name, strategy=signal.strategy,
            )

            # Telegram alert
            await self._notify_entry(signal, qty, sl)
            return True

        except Exception as e:
            logger.error(f"Execution error {signal.symbol}: {e}", exc_info=True)
            return False

    # ── Position Monitoring ───────────────────────────────────────────────────

    async def _monitor_positions(self) -> None:
        tracked = self.tracker.get_all()
        if not tracked:
            return

        symbols = list({p["symbol"] for p in tracked})
        instruments = [await self._get_instrument(s) for s in symbols]
        instruments = [i for i in instruments if i]
        quotes = await self.primary_broker.get_quote(instruments) if instruments else {}

        for pos in tracked:
            pos_id = pos["id"]
            quote = quotes.get(pos["symbol"])
            if not quote:
                continue

            ltp = quote.ltp
            self.tracker.update_peak(pos_id, ltp)

            # ── Trailing Stop Update ─────────────────────────────────────────
            if self.risk.config.trailing_stop and pos["current_sl"]:
                new_sl = self.risk.calculate_trailing_stop(
                    entry_price=pos["entry_price"],
                    current_price=ltp,
                    current_sl=pos["current_sl"],
                    side=pos["side"],
                )
                sl_improved = (
                    (pos["side"] == "BUY" and new_sl > pos["current_sl"] + Decimal("0.5"))
                    or
                    (pos["side"] == "SELL" and new_sl < pos["current_sl"] - Decimal("0.5"))
                )
                if sl_improved:
                    await self._update_trailing_stop(pos_id, pos, new_sl)

            # ── Target Check ─────────────────────────────────────────────────
            if pos.get("target"):
                hit = (
                    (pos["side"] == "BUY" and ltp >= pos["target"])
                    or (pos["side"] == "SELL" and ltp <= pos["target"])
                )
                if hit:
                    await self._close_at_target(pos_id, pos, ltp)

        broker_positions = await self.primary_broker.get_positions()
        await self.risk.update_pnl(broker_positions)

    async def _update_trailing_stop(self, pos_id: str, pos: dict, new_sl: Decimal) -> None:
        try:
            old_id = pos.get("sl_broker_order_id")
            if old_id:
                await self.primary_broker.cancel_order(old_id)
                active = await SLOrderRepository.get_active_for_position(pos_id)
                if active:
                    await SLOrderRepository.deactivate(str(active.id))

            inst = await self._get_instrument(pos["symbol"])
            exit_side = OrderSide.SELL if pos["side"] == "BUY" else OrderSide.BUY
            new_order = await self.primary_broker.place_order(
                instrument=inst, side=exit_side, quantity=pos["quantity"],
                order_type=OrderType.SL_M, product=ProductType.MIS,
                trigger_price=new_sl, tag="TRAIL_SL",
            )
            self.tracker.update_sl(pos_id, new_sl, new_order.order_id)
            await SLOrderRepository.save(
                position_id=pos_id,
                broker_order_id=new_order.order_id,
                broker=pos["broker"],
                symbol=pos["symbol"], sl_price=new_sl, sl_type="TRAILING",
            )
            await PositionRepository.update_stop_loss(pos_id, new_sl)
            logger.info(f"📈 Trail SL {pos['symbol']}: ₹{pos['current_sl']} → ₹{new_sl}")
        except Exception as e:
            logger.error(f"Trail SL error {pos['symbol']}: {e}")

    async def _close_at_target(self, pos_id: str, pos: dict, ltp: Decimal) -> None:
        try:
            inst = await self._get_instrument(pos["symbol"])
            exit_side = OrderSide.SELL if pos["side"] == "BUY" else OrderSide.BUY
            await self.primary_broker.place_order(
                instrument=inst, side=exit_side, quantity=pos["quantity"],
                order_type=OrderType.MARKET, product=ProductType.MIS, tag="TARGET_HIT",
            )
            if pos.get("sl_broker_order_id"):
                await self.primary_broker.cancel_order(pos["sl_broker_order_id"])

            entry = pos["entry_price"]
            gross = (ltp - entry) * pos["quantity"] if pos["side"] == "BUY" else (entry - ltp) * pos["quantity"]
            brok = Decimal("40")
            stt = ltp * pos["quantity"] * Decimal("0.00025")
            net = gross - brok - stt

            await PositionRepository.close_position(
                position_id=pos_id, exit_price=ltp,
                realized_pnl=gross, exit_reason="TARGET",
                brokerage=brok, stt=stt,
            )
            self.tracker.remove(pos_id)
            logger.info(f"🎯 TARGET {pos['symbol']} | P&L: ₹{net:+,.0f}")
            await self._notify_exit(pos["symbol"], pos["side"], pos["quantity"], entry, ltp, net, "TARGET")
        except Exception as e:
            logger.error(f"Target close error {pos['symbol']}: {e}")

    # ── Market Context ────────────────────────────────────────────────────────

    async def _build_market_context(self, now: datetime) -> MarketContext:
        # Live index data from NSE (real)
        index_data = await self.nse_feed.get_index_data()
        nifty = index_data.get("nifty", 22000.0)
        banknifty = index_data.get("banknifty", 47000.0)
        vix = index_data.get("vix", 14.0)

        # Real options chain + PCR
        nifty_chain = await self.nse_feed.get_option_chain("NIFTY")
        bnk_chain = await self.nse_feed.get_option_chain("BANKNIFTY")
        pcr = nifty_chain.get("pcr", 1.0)

        options_summary = {
            "NIFTY": {
                "pcr": nifty_chain.get("pcr"),
                "pcr_view": nifty_chain.get("pcr_interpretation"),
                "atm_strike": nifty_chain.get("atm_strike"),
                "atm_straddle": nifty_chain.get("atm_straddle_price"),
                "expected_move_pct": nifty_chain.get("expected_move_pct"),
                "max_pain": nifty_chain.get("max_pain_strike"),
                "key_resistance": nifty_chain.get("key_resistance"),
                "key_support": nifty_chain.get("key_support"),
                "top_call_oi": [x["strike"] for x in nifty_chain.get("top_5_ce_oi", [])[:3]],
                "top_put_oi": [x["strike"] for x in nifty_chain.get("top_5_pe_oi", [])[:3]],
            },
            "BANKNIFTY": {
                "pcr": bnk_chain.get("pcr"),
                "atm_strike": bnk_chain.get("atm_strike"),
                "max_pain": bnk_chain.get("max_pain_strike"),
                "key_resistance": bnk_chain.get("key_resistance"),
                "key_support": bnk_chain.get("key_support"),
            },
        }

        # Real news sentiment from NSE announcements
        news = await self.sentiment.get_market_sentiment()

        # Portfolio
        positions = await self.primary_broker.get_positions()
        funds = await self.primary_broker.get_funds()
        pos_dicts = [
            {
                "symbol": p.instrument.symbol, "side": p.side.value,
                "quantity": p.quantity, "avg_price": float(p.average_price),
                "ltp": float(p.ltp), "pnl": float(p.pnl),
                "pnl_pct": p.pnl_pct, "broker": p.broker,
            }
            for p in positions
        ]

        watchlist = await self._get_watchlist_indicators()
        self._nifty_history.append(nifty)
        self._nifty_history = self._nifty_history[-50:]
        trend = self._detect_trend(nifty, vix)

        ctx = MarketContext(
            timestamp=now, nifty50_ltp=nifty, banknifty_ltp=banknifty,
            india_vix=vix, market_trend=trend,
            session=self._get_session(now), day_of_week=now.strftime("%A"),
            available_capital=float(funds.available_cash),
            used_margin=float(funds.used_margin),
            open_positions=pos_dicts, watchlist_data=watchlist,
            options_chain_summary=options_summary,
            recent_news_sentiment=news, pcr=pcr,
        )
        ctx._regime = trend
        return ctx

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    async def _preload_ohlcv(self) -> None:
        logger.info("📥 Preloading OHLCV...")
        now = datetime.now(IST)
        from_date = now - timedelta(days=120)
        for symbol in WATCHLIST:
            try:
                inst = await self._get_instrument(symbol)
                candles = await self.primary_broker.get_ohlcv(inst, "day", from_date, now)
                if candles:
                    self._ohlcv_frames[symbol] = pd.DataFrame([
                        {"open": float(c.open), "high": float(c.high),
                         "low": float(c.low), "close": float(c.close), "volume": c.volume}
                        for c in candles
                    ])
                    await OHLCVRepository.upsert_candles([
                        {"symbol": symbol, "exchange": "NSE", "interval": "day",
                         "timestamp": c.timestamp, "open": c.open, "high": c.high,
                         "low": c.low, "close": c.close, "volume": c.volume, "oi": c.oi}
                        for c in candles
                    ])
                await asyncio.sleep(0.25)
            except Exception as e:
                logger.debug(f"Skip {symbol}: {e}")
        logger.info(f"✅ OHLCV: {len(self._ohlcv_frames)} symbols")

    async def _refresh_ohlcv(self) -> None:
        now = datetime.now(IST)
        from_date = now - timedelta(days=2)
        for symbol in WATCHLIST[:10]:
            try:
                inst = await self._get_instrument(symbol)
                candles = await self.primary_broker.get_ohlcv(inst, "day", from_date, now)
                if candles and symbol in self._ohlcv_frames:
                    new = pd.DataFrame([{
                        "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close), "volume": c.volume,
                    } for c in candles])
                    self._ohlcv_frames[symbol] = pd.concat(
                        [self._ohlcv_frames[symbol], new]
                    ).drop_duplicates().tail(250)
            except Exception:
                pass

    async def _get_watchlist_indicators(self) -> list[dict]:
        result = []
        for symbol in WATCHLIST:
            df = self._ohlcv_frames.get(symbol)
            if df is None or df.empty:
                continue
            try:
                bundle = self.indicators.compute(df, symbol, "day")
                result.append(self.indicators.to_dict(bundle))
            except Exception:
                pass
        return result

    # ── Strategy Review ───────────────────────────────────────────────────────

    async def _run_strategy_review(self) -> None:
        logger.info("🔍 Strategy review...")
        try:
            perf = await PositionRepository.get_performance_stats(days=30)
            review = await self.agent.review_strategy({**perf, **self.risk.get_daily_summary()})
            if review:
                logger.info(f"📋 {review.get('overall_assessment', '')}")
                ct = review.get("parameter_adjustments", {}).get("confidence_threshold")
                if ct:
                    self.agent.confidence_threshold = float(ct)
                    logger.info(f"🔧 Confidence threshold → {ct}")
        except Exception as e:
            logger.error(f"Strategy review error: {e}")

    # ── EOD ───────────────────────────────────────────────────────────────────

    async def _square_off_all_intraday(self) -> None:
        try:
            positions = await self.primary_broker.get_positions()
            mis = [p for p in positions if p.product.value == "MIS"]
            for p in mis:
                try:
                    await self.primary_broker.square_off_position(p)
                    logger.info(f"📤 Squared off {p.instrument.symbol}")
                except Exception as e:
                    logger.error(f"Square off error {p.instrument.symbol}: {e}")
        except Exception as e:
            logger.error(f"Square off all error: {e}")

    async def _save_daily_summary(self) -> None:
        from database.repository import DailySummaryRepository
        from datetime import date
        s = self.risk.get_daily_summary()
        try:
            await DailySummaryRepository.upsert({
                "date": date.today().isoformat(),
                "starting_capital": s["starting_capital"],
                "realized_pnl": s["realized_pnl"],
                "unrealized_pnl": s["unrealized_pnl"],
                "net_pnl": s["total_pnl"],
                "pnl_pct": s["daily_pnl_pct"],
                "total_trades": s["total_trades"],
                "winning_trades": int(s["total_trades"] * s["win_rate"] / 100),
                "losing_trades": s["total_trades"] - int(s["total_trades"] * s["win_rate"] / 100),
                "win_rate": s["win_rate"],
                "max_drawdown_pct": s["drawdown_pct"],
                "kill_switch_triggered": s["kill_switch"],
                "kill_switch_reason": s.get("kill_switch_reason", ""),
            })
        except Exception as e:
            logger.error(f"Daily summary save error: {e}")

    async def _end_of_day(self) -> None:
        await self._square_off_all_intraday()
        await self._save_daily_summary()
        s = self.risk.get_daily_summary()
        logger.info(f"📊 EOD | ₹{s['total_pnl']:+,.0f} ({s['daily_pnl_pct']:+.2f}%) | {s['total_trades']} trades")

    # ── Notifications ─────────────────────────────────────────────────────────

    async def _notify_entry(self, signal: TradingSignal, qty: int, sl: Optional[Decimal]) -> None:
        try:
            import os
            from core.notifier import TelegramNotifier
            n = TelegramNotifier(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""))
            await n.trade_entry(signal.symbol, signal.action.value, qty,
                signal.entry_price or Decimal("0"), signal.strategy, signal.confidence,
                sl, signal.target)
        except Exception:
            pass

    async def _notify_exit(self, symbol, side, qty, entry, ltp, pnl, reason) -> None:
        try:
            import os
            from core.notifier import TelegramNotifier
            n = TelegramNotifier(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""))
            await n.trade_exit(symbol, side, qty, entry, ltp, pnl, reason)
        except Exception:
            pass

    # ── Init Helpers ──────────────────────────────────────────────────────────

    async def _init_brokers(self) -> None:
        from brokers.zerodha.adapter import ZerodhaBroker
        from brokers.dhan.adapter import DhanBroker
        bc = self.config.get("brokers", {})
        if bc.get("zerodha", {}).get("enabled"):
            zb = ZerodhaBroker(bc["zerodha"])
            if await zb.login():
                self.brokers["zerodha"] = zb
                if not self.primary_broker:
                    self.primary_broker = zb
                    self._primary_broker_name = "zerodha"
        if bc.get("dhan", {}).get("enabled"):
            db = DhanBroker(bc["dhan"])
            if await db.login():
                self.brokers["dhan"] = db
                if not self.primary_broker:
                    self.primary_broker = db
                    self._primary_broker_name = "dhan"

    async def _load_instruments(self) -> None:
        logger.info("📥 Loading instruments...")
        try:
            insts = await self.primary_broker.get_instruments(Exchange.NSE)
            for i in insts:
                self._instrument_cache[i.symbol] = i
            logger.info(f"✅ {len(insts)} instruments loaded")
        except Exception as e:
            logger.error(f"Instrument load error: {e}")

    async def _get_instrument(self, symbol: str, exchange: str = "NSE") -> Instrument:
        return self._instrument_cache.get(symbol) or Instrument(symbol, Exchange(exchange), InstrumentType.EQ)

    async def _subscribe_market_data(self) -> None:
        insts = [await self._get_instrument(s) for s in WATCHLIST[:20]]
        await self.primary_broker.subscribe_ticks(insts, self._on_tick)
        logger.info(f"📡 Subscribed {len(insts)} instruments")

    async def _on_tick(self, tick: dict) -> None:
        sym = tick.get("tradingsymbol") or tick.get("trading_symbol", "")
        if sym:
            self._tick_data[sym] = tick

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _is_market_open(self, now: datetime) -> bool:
        return now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 30)

    def _get_session(self, now: datetime) -> str:
        t = now.time()
        if t < time(9, 15): return "pre_open"
        if t < time(10, 0): return "opening"
        if t < time(14, 30): return "mid_session"
        return "closing"

    def _detect_trend(self, nifty: float, vix: float) -> str:
        h = self._nifty_history
        if len(h) < 10: return "sideways"
        recent = sum(h[-5:]) / 5
        older = sum(h[-20:-10]) / 10 if len(h) >= 20 else recent
        m = (recent - older) / older * 100
        if vix > 20: return "high_volatility"
        if m > 0.5: return "trending_up"
        if m < -0.5: return "trending_down"
        return "ranging"
