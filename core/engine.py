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
from time import monotonic
from decimal import Decimal
from typing import Optional

import pandas as pd
import pytz

from agents.brain import MarketContext, TradingAgent, SignalAction, TradingSignal
from brokers.base import (
    BaseBroker, Exchange, Instrument, InstrumentType,
    OrderSide, OrderStatus, OrderType, Position, ProductType,
)
from data.indicators import IndicatorsEngine
from data.stock_selector import SelectorConfig, StockSelector
from data.stock_universe import load_nse_equity_symbols
from data.nse_feed import NSEDataFeed, NewsSentimentAnalyzer
from database.repository import (
    AgentDecisionRepository, OHLCVRepository,
    PositionRepository, RiskEventRepository,
    SLOrderRepository, TradeRepository,
)
from risk.manager import RiskConfig, RiskManager

logger = logging.getLogger("engine")
IST = pytz.timezone("Asia/Kolkata")

DEFAULT_WATCHLIST = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "KOTAKBANK", "HINDUNILVR", "WIPRO", "SBIN", "AXISBANK",
    "ADANIENT", "BAJFINANCE", "TITAN", "MARUTI", "NESTLEIND",
    "TATAMOTORS", "TATAPOWER", "ZOMATO", "PAYTM", "LT",
]
VALID_SELECTION_MODES = {"watchlist", "auto_pick"}
DEFAULT_SESSION_PROFILES = {
    "opening": {"selection_multiplier": 0.7, "risk_cap_multiplier": 0.7},
    "mid_session": {"selection_multiplier": 1.0, "risk_cap_multiplier": 1.0},
    "closing": {"selection_multiplier": 0.6, "risk_cap_multiplier": 0.8},
}

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
        agent_config = dict(config.get("agent", {}))
        risk_cfg = config.get("risk", {})
        if "max_order_value_absolute" in risk_cfg and "max_order_value_absolute" not in agent_config:
            agent_config["max_order_value_absolute"] = risk_cfg.get("max_order_value_absolute")
        self.agent = TradingAgent(agent_config)
        self.indicators = IndicatorsEngine()
        self.nse_feed = NSEDataFeed()
        self.sentiment = NewsSentimentAnalyzer()

        risk_fields = RiskConfig.__dataclass_fields__.keys()
        risk_kwargs = {k: v for k, v in config.get("risk", {}).items() if k in risk_fields}
        self.risk = RiskManager(RiskConfig(**risk_kwargs))
        self.tracker = ActivePositionTracker()

        legacy_market_cfg = config.get("market", {})
        engine_cfg = {
            **{
                key: legacy_market_cfg.get(key)
                for key in (
                    "selection_mode",
                    "watchlist_symbols",
                    "min_stock_price",
                    "max_stock_price",
                    "max_auto_pick_symbols",
                    "min_avg_daily_volume",
                )
                if legacy_market_cfg.get(key) is not None
            },
            **config.get("engine", {}),
        }
        self.selection_mode_requested = str(engine_cfg.get("selection_mode", "watchlist") or "watchlist")
        self.selection_mode_warning = ""
        self.selection_mode = self._validated_selection_mode(self.selection_mode_requested)
        self.configured_watchlist_symbols = self._normalize_symbols(engine_cfg.get("watchlist_symbols", DEFAULT_WATCHLIST))
        self.min_stock_price = float(engine_cfg.get("min_stock_price", 50) or 50)
        self.max_stock_price = float(engine_cfg.get("max_stock_price", 5000) or 5000)
        self.max_auto_pick_symbols = int(engine_cfg.get("max_auto_pick_symbols", 10) or 10)
        self.max_live_quote_symbols = int(
            engine_cfg.get("max_live_quote_symbols", max(self.max_auto_pick_symbols * 25, 250))
            or max(self.max_auto_pick_symbols * 25, 250)
        )
        self.max_preload_ohlcv_symbols = int(
            engine_cfg.get("max_preload_ohlcv_symbols", max(self.max_auto_pick_symbols * 10, 50))
            or max(self.max_auto_pick_symbols * 10, 50)
        )
        self.min_avg_daily_volume = float(engine_cfg.get("min_avg_daily_volume", 100000) or 100000)
        self.min_avg_daily_turnover = float(engine_cfg.get("min_avg_daily_turnover", 5000000) or 5000000)
        self.session_profiles = {**DEFAULT_SESSION_PROFILES, **config.get("engine", {}).get("session_profiles", {})}
        self.selector = StockSelector(SelectorConfig(
            min_stock_price=self.min_stock_price,
            max_stock_price=self.max_stock_price,
            min_avg_daily_volume=self.min_avg_daily_volume,
            min_avg_daily_turnover=self.min_avg_daily_turnover,
            max_auto_pick_symbols=self.max_auto_pick_symbols,
        ))
        self._base_risk_caps = {
            "max_order_value_absolute": self.risk.config.max_order_value_absolute,
            "max_open_positions": self.risk.config.max_open_positions,
        }
        self._selected_symbols: list[str] = list(self.configured_watchlist_symbols)
        self._candidate_universe_symbols: list[str] = list(self.configured_watchlist_symbols)
        self._latest_ranked_candidates: list[dict] = []
        self._active_session_profile: dict[str, object] = {"session": "mid_session", "selection_multiplier": 1.0, "risk_cap_multiplier": 1.0}

        self._running = False
        self._tick_data: dict[str, dict] = {}
        self._tick_token_to_symbol: dict[str, str] = {}
        self._latest_options_chain: dict[str, dict] = {}
        self._latest_watchlist: list[dict] = []
        self._instrument_cache: dict[str, Instrument] = {}
        self._nse_equity_symbols_cache: list[str] = []
        self._ohlcv_frames: dict[str, pd.DataFrame] = {}
        self._nifty_history: list[float] = []
        self._primary_broker_name: str = ""
        # Broker used for order placement and execution-side risk management.
        self.execution_primary_broker: str = ""
        # Broker selected by user for dashboard data display (can auto-fallback).
        self.ui_primary_broker: str = ""
        self._agent_status: dict[str, object] = {
            "cycle_id": None,
            "stage": "idle",
            "stage_started_at": None,
            "cycle_started_at": None,
            "last_cycle_duration_ms": None,
            "last_error": None,
            "progress_pct": 0,
            "selected_strategy": None,
            "signals_considered": 0,
            "signals_approved": 0,
            "signals_rejected": 0,
        }
        self._agent_events: list[dict[str, object]] = []
        self._market_data_fallback_state: dict[str, str] = {"ohlcv": "", "ticks": ""}
        self._active_tick_broker_name: str = ""
        self.replica_broker: Optional[BaseBroker] = None
        self._replica_broker_name: str = ""
        self._replication_enabled: bool = False
        self._replication_status: str = "disabled"
        self._last_replication_error: str = ""
        self._reconciliation_task: Optional[asyncio.Task] = None
        self._reconcile_interval_seconds: int = int(
            self.config.get("brokers", {}).get("replication", {}).get("reconcile_interval_seconds", 120)
        )
        self._broker_health_cache: dict[str, tuple[bool, float]] = {}
        self._broker_health_ttl_seconds: float = 5.0
        self._broker_health_scores: dict[str, float] = {}


    @staticmethod
    def _normalize_symbols(symbols) -> list[str]:
        if not isinstance(symbols, list):
            return list(DEFAULT_WATCHLIST)
        normalized: list[str] = []
        for symbol in symbols:
            value = str(symbol or "").strip().upper()
            if value and value not in normalized:
                normalized.append(value)
        return normalized or list(DEFAULT_WATCHLIST)

    def _validated_selection_mode(self, raw_mode: object) -> str:
        mode = str(raw_mode or "watchlist").strip().lower()
        if mode not in VALID_SELECTION_MODES:
            self.selection_mode_warning = f"Invalid selection_mode '{raw_mode}'; falling back to watchlist"
            logger.warning(self.selection_mode_warning)
            return "watchlist"
        self.selection_mode_warning = ""
        return mode

    def apply_runtime_overrides(self, overrides: dict | None = None) -> None:
        overrides = overrides or {}
        if "selection_mode" in overrides:
            self.selection_mode_requested = str(overrides.get("selection_mode") or "watchlist")
            self.selection_mode = self._validated_selection_mode(overrides.get("selection_mode"))
        if "watchlist_symbols" in overrides:
            self.configured_watchlist_symbols = self._normalize_symbols(overrides.get("watchlist_symbols"))
        for attr, key, caster in (
            ("min_stock_price", "min_stock_price", float),
            ("max_stock_price", "max_stock_price", float),
            ("max_auto_pick_symbols", "max_auto_pick_symbols", int),
            ("min_avg_daily_volume", "min_avg_daily_volume", float),
            ("min_avg_daily_turnover", "min_avg_daily_turnover", float),
        ):
            if key in overrides and overrides.get(key) is not None:
                setattr(self, attr, caster(overrides.get(key)))
        if overrides:
            self.selector = StockSelector(SelectorConfig(
                min_stock_price=self.min_stock_price,
                max_stock_price=self._effective_max_stock_price(),
                min_avg_daily_volume=self.min_avg_daily_volume,
                min_avg_daily_turnover=self.min_avg_daily_turnover,
                max_auto_pick_symbols=self.max_auto_pick_symbols,
            ))
            risk_overrides = {
                "max_order_value_absolute": overrides.get("max_order_value_absolute"),
                "min_cash_buffer": overrides.get("min_cash_buffer"),
                "tiny_account_mode": overrides.get("tiny_account_mode"),
            }
            for key, value in risk_overrides.items():
                if value is not None and hasattr(self.risk.config, key):
                    setattr(self.risk.config, key, value)
                    if key == "max_order_value_absolute":
                        self.agent.max_order_value_absolute = value
                        self._base_risk_caps["max_order_value_absolute"] = value
            self._refresh_selection()

    def _effective_max_stock_price(self) -> float:
        ceilings = [float(self.max_stock_price)]
        absolute_cap = self.risk.config.max_order_value_absolute
        if absolute_cap is not None:
            ceilings.append(float(absolute_cap))
        starting_capital = float(self.risk.today.starting_capital or 0)
        if starting_capital > 0:
            percent_budget = starting_capital * (self.risk.config.max_capital_per_trade_pct / 100.0)
            if percent_budget > 0:
                ceilings.append(percent_budget)
        return max(float(self.min_stock_price), min(ceilings))

    def _session_profile_for(self, session_name: str) -> dict[str, float]:
        raw = self.session_profiles.get(session_name, {}) if isinstance(self.session_profiles, dict) else {}
        selection_multiplier = float(raw.get("selection_multiplier", 1.0) or 1.0)
        risk_cap_multiplier = float(raw.get("risk_cap_multiplier", 1.0) or 1.0)
        return {
            "session": session_name,
            "selection_multiplier": max(0.25, min(selection_multiplier, 1.5)),
            "risk_cap_multiplier": max(0.25, min(risk_cap_multiplier, 1.5)),
        }

    def _apply_session_profile(self, now: datetime) -> None:
        session_name = self._get_session(now)
        profile = self._session_profile_for(session_name)
        self._active_session_profile = profile

        base_absolute_cap = self._base_risk_caps.get("max_order_value_absolute")
        if base_absolute_cap is not None:
            adjusted_cap = float(base_absolute_cap) * profile["risk_cap_multiplier"]
            self.risk.config.max_order_value_absolute = round(adjusted_cap, 2)
            self.agent.max_order_value_absolute = round(adjusted_cap, 2)

        base_positions = int(self._base_risk_caps.get("max_open_positions", self.risk.config.max_open_positions))
        self.risk.config.max_open_positions = max(1, int(round(base_positions * profile["risk_cap_multiplier"])))

    def _build_candidate_universe(self) -> list[str]:
        if self.selection_mode == "watchlist":
            return list(self.configured_watchlist_symbols)

        candidate_symbols: list[str] = []
        for source in (
            self._nse_equity_symbols_cache,
            self._ohlcv_frames.keys(),
            self.configured_watchlist_symbols,
        ):
            for symbol in source:
                normalized = str(symbol or "").strip().upper()
                if not normalized or normalized in candidate_symbols:
                    continue
                candidate_symbols.append(normalized)

        return candidate_symbols or list(self.configured_watchlist_symbols)

    def _refresh_selection(self) -> None:
        self._candidate_universe_symbols = self._build_candidate_universe()
        ranked = self.selector.rank_candidates(self._ohlcv_frames, self._candidate_universe_symbols)
        self._latest_ranked_candidates = ranked
        session_limit = max(
            1,
            int(round(self.max_auto_pick_symbols * float(self._active_session_profile.get("selection_multiplier", 1.0)))),
        )
        if self.selection_mode == "auto_pick":
            selected = [item["symbol"] for item in ranked[:session_limit]]
            self._selected_symbols = selected or list(self.configured_watchlist_symbols)
        else:
            self._selected_symbols = list(self.configured_watchlist_symbols)

    def _preload_symbol_subset(self) -> list[str]:
        candidates = self._candidate_universe_symbols or list(self.configured_watchlist_symbols)
        if self.selection_mode != "auto_pick":
            return list(candidates)

        max_symbols = max(1, int(self.max_preload_ohlcv_symbols or 0))
        preload_symbols: list[str] = []
        for source in (
            self.configured_watchlist_symbols,
            self._selected_symbols,
            candidates,
        ):
            for symbol in source:
                normalized = str(symbol or "").strip().upper()
                if not normalized or normalized in preload_symbols:
                    continue
                preload_symbols.append(normalized)
                if len(preload_symbols) >= max_symbols:
                    return preload_symbols
        return preload_symbols

    def get_engine_status(self) -> dict:
        return {
            "selection_mode": self.selection_mode,
            "selection_mode_requested": self.selection_mode_requested,
            "selection_mode_warning": self.selection_mode_warning or None,
            "active_symbols": list(self._selected_symbols),
            "candidate_universe_symbols": list(self._candidate_universe_symbols),
            "ranked_candidates": list(self._latest_ranked_candidates),
            "configured_watchlist_symbols": list(self.configured_watchlist_symbols),
            "session_profile": dict(self._active_session_profile),
            "effective_max_stock_price": self._effective_max_stock_price(),
        }

    async def connected_broker_names_live(self) -> list[str]:
        connected: list[str] = []
        for name, broker in self.brokers.items():
            now = monotonic()
            cached = self._broker_health_cache.get(name)
            if cached and (now - cached[1]) <= self._broker_health_ttl_seconds:
                if cached[0]:
                    connected.append(name)
                continue

            healthy = False
            try:
                await asyncio.wait_for(broker.get_funds(), timeout=2.0)
                healthy = True
            except Exception:
                healthy = False
            self._broker_health_cache[name] = (healthy, now)
            if healthy:
                connected.append(name)

        return connected

    def connected_broker_names(self) -> list[str]:
        now = monotonic()
        connected = [
            name
            for name in self.brokers
            if (cached := self._broker_health_cache.get(name))
            and (now - cached[1]) <= self._broker_health_ttl_seconds
            and cached[0]
        ]
        if connected:
            return connected

        if self._primary_broker_name in self.brokers:
            return [self._primary_broker_name]

        return list(self.brokers.keys())

    def _broker_health_score(self, name: str, broker: BaseBroker) -> float:
        healthy, _ = self._broker_health_cache.get(name, (True, 0.0))
        score = 100.0 if healthy else 40.0
        if getattr(broker, "_ws_blocked", False):
            score -= 30.0
        if getattr(broker, "_historical_data_blocked", False):
            score -= 20.0
        if name == self._primary_broker_name:
            score += 5.0
        return max(0.0, min(score, 100.0))

    def get_broker_health_summary(self) -> dict[str, dict[str, float | bool]]:
        summary: dict[str, dict[str, float | bool]] = {}
        for name, broker in self.brokers.items():
            score = self._broker_health_score(name, broker)
            self._broker_health_scores[name] = score
            healthy, _ = self._broker_health_cache.get(name, (False, 0.0))
            summary[name] = {
                "score": round(score, 1),
                "healthy": bool(healthy),
                "ws_blocked": bool(getattr(broker, "_ws_blocked", False)),
                "historical_data_blocked": bool(getattr(broker, "_historical_data_blocked", False)),
            }
        return summary

    async def resolve_ui_primary_broker_live(self) -> tuple[str, bool, str]:
        connected = await self.connected_broker_names_live()
        selected = self.ui_primary_broker or self._primary_broker_name
        if selected and selected in connected:
            return selected, False, ""
        if connected:
            reason = f"selected broker '{selected}' is disconnected" if selected else "no UI broker selected"
            return connected[0], True, reason
        return "", bool(selected), "no healthy broker available"

    def set_ui_primary_broker(self, broker_name: str) -> None:
        self.ui_primary_broker = broker_name if broker_name in {"dhan", "zerodha"} else ""

    def resolve_ui_primary_broker(self) -> tuple[str, bool, str]:
        connected = self.connected_broker_names()
        selected = self.ui_primary_broker or self._primary_broker_name
        if selected and selected in connected:
            return selected, False, ""
        if connected:
            reason = f"selected broker '{selected}' is disconnected" if selected else "no UI broker selected"
            return connected[0], True, reason
        return "", bool(selected), "no brokers connected"

    def get_broker(self, broker_name: str) -> Optional[BaseBroker]:
        return self.brokers.get(broker_name)

    def get_execution_broker(self) -> Optional[BaseBroker]:
        broker = self.brokers.get(self.execution_primary_broker)
        if broker:
            return broker
        if self.primary_broker:
            if self.execution_primary_broker and self.execution_primary_broker != self._primary_broker_name:
                logger.warning(
                    f"Execution broker '{self.execution_primary_broker}' unavailable; "
                    f"falling back to '{self._primary_broker_name}'"
                )
            self.execution_primary_broker = self._primary_broker_name
            return self.get_execution_broker()
        return None

    def get_ui_data_broker(self) -> Optional[BaseBroker]:
        effective_name, _, _ = self.resolve_ui_primary_broker()
        return self.brokers.get(effective_name) or self.get_execution_broker()

    def _set_agent_stage(self, stage: str, now: Optional[datetime] = None, error: Optional[str] = None) -> None:
        ts = now or datetime.now(IST)
        self._agent_status["stage"] = stage
        self._agent_status["stage_started_at"] = ts.isoformat()
        if error:
            self._agent_status["last_error"] = error

    def _cycle_elapsed_ms(self, now: Optional[datetime] = None) -> int:
        if not self._agent_status.get("cycle_started_at"):
            return 0
        try:
            started = datetime.fromisoformat(str(self._agent_status["cycle_started_at"]))
            current = now or datetime.now(IST)
            return int((current - started).total_seconds() * 1000)
        except Exception:
            return 0

    def _push_agent_event(
        self,
        message: str,
        level: str = "info",
        now: Optional[datetime] = None,
        metadata: Optional[dict[str, object]] = None,
    ) -> None:
        ts = now or datetime.now(IST)
        event: dict[str, object] = {
            "timestamp": ts.isoformat(),
            "level": level,
            "message": message,
            "cycle_id": self._agent_status.get("cycle_id"),
            "stage": self._agent_status.get("stage"),
            "progress_pct": self._agent_status.get("progress_pct", 0),
            "elapsed_ms": self._cycle_elapsed_ms(ts),
        }
        if metadata:
            event.update(metadata)
        self._agent_events.append(event)
        self._agent_events = self._agent_events[-100:]

    # ── Startup / Shutdown ────────────────────────────────────────────────────

    async def start(self) -> None:
        logger.info("🚀 Starting Trading Engine...")
        set_engine(self)

        await self._init_brokers()
        # Give NSEDataFeed the broker references so it can pull
        # index prices from Dhan/Zerodha instead of scraping NSE directly.
        self.nse_feed.set_brokers(
            dhan_broker=self.brokers.get("dhan"),
            zerodha_broker=self.brokers.get("zerodha"),
        )
        
        execution_broker = self.get_execution_broker()
        if not execution_broker:
            raise RuntimeError("No broker connected. Check credentials.")

        funds = await execution_broker.get_funds()
        await self.risk.initialize(funds)

        await self._load_instruments()
        self._candidate_universe_symbols = self._build_candidate_universe()
        await self._preload_ohlcv()
        self._refresh_selection()
        await self._subscribe_market_data()


        

        if self._replication_enabled:
            self._reconciliation_task = asyncio.create_task(self._reconciliation_loop())

        self._running = True
        logger.info("✅ Trading Engine ready")
        await self._main_loop()

    async def stop(self) -> None:
        logger.info("🛑 Stopping...")
        self._running = False
        if self._reconciliation_task:
            self._reconciliation_task.cancel()
            self._reconciliation_task = None
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

                self._apply_session_profile(now)
                await self._ensure_tick_subscription_health()    
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
        self._agent_status["progress_pct"] = 0
        self._agent_status["selected_strategy"] = None
        self._agent_status["signals_considered"] = 0
        self._agent_status["signals_approved"] = 0
        self._agent_status["signals_rejected"] = 0

        if not self.risk.is_trading_allowed:
            self._set_agent_stage("paused", now)
            self._agent_status["progress_pct"] = 100
            self._push_agent_event(
                "Trading paused: kill switch active",
                level="warn",
                now=now,
                metadata={"progress_pct": 100},
            )
            logger.warning("⛔ Kill switch active - no trading")
            return

        self._set_agent_stage("collecting_context", now)
        self._agent_status["progress_pct"] = 10
        self._push_agent_event(
            "Collecting market context",
            now=now,
            metadata={"progress_pct": 10},
        )
        context = await self._build_market_context(now)

        self._set_agent_stage("calling_model")
        self._agent_status["progress_pct"] = 35
        self._push_agent_event(
            "Sending context to AI model",
            now=now,
            metadata={"progress_pct": 35},
        )
        signals = await self.agent.analyze_and_decide(context)
        self._agent_status["signals_considered"] = len(signals)
        if signals:
            self._agent_status["selected_strategy"] = signals[0].strategy
        self._push_agent_event(
            f"AI generated {len(signals)} signal(s) | regime {getattr(context, '_regime', 'unknown')}",
            now=now,
            metadata={
                "progress_pct": 50,
                "selected_strategy": self._agent_status.get("selected_strategy"),
                "signals_considered": len(signals),
            },
        )

        executed, rejected = 0, 0
        rejection_breakdown: dict[str, int] = {}
        self._set_agent_stage("risk_checks")
        self._agent_status["progress_pct"] = 60
        self._push_agent_event(
            "Running risk checks on AI signals",
            now=now,
            metadata={"progress_pct": 60},
        )
        if signals:
            execution_broker = self.get_execution_broker()
            if not execution_broker:
                logger.error("Execution broker unavailable during risk checks")
                return
            funds = await execution_broker.get_funds()
            positions = await execution_broker.get_positions()

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
                        metadata={
                            "signals_rejected": rejected + 1,
                            "signals_approved": executed,
                            "selected_strategy": signal.strategy,
                        },
                    )
                    rejected += 1
                    self._agent_status["signals_rejected"] = rejected
                    continue

                qty = check.adjusted_quantity or signal.quantity
                sl = check.adjusted_sl or signal.stop_loss
                self._set_agent_stage("placing_orders")
                self._agent_status["progress_pct"] = 85
                ok = await self._execute_signal(signal, qty, sl)
                if ok:
                    executed += 1
                    self._agent_status["signals_approved"] = executed
                    self._push_agent_event(
                        f"{signal.symbol} {signal.action.value} executed qty {qty}",
                        level="success",
                        metadata={
                            "signals_approved": executed,
                            "signals_rejected": rejected,
                            "selected_strategy": signal.strategy,
                        },
                    )
                else:
                    rejected += 1
                    self._agent_status["signals_rejected"] = rejected
                    rejection_breakdown["execution_error"] = rejection_breakdown.get("execution_error", 0) + 1
                    self._push_agent_event(
                        f"{signal.symbol} {signal.action.value} execution failed",
                        level="error",
                        metadata={
                            "signals_approved": executed,
                            "signals_rejected": rejected,
                            "selected_strategy": signal.strategy,
                        },
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
                raw_response={
                    "decision": (latest_decision or {}).get("raw_response") or {},
                    "rejection_breakdown": rejection_breakdown,
                    "signals": (latest_decision or {}).get("signals") or [],
                },
                context_snapshot={
                    "market": {
                        "timestamp": context.timestamp.isoformat(),
                        "capital": context.available_capital,
                        "positions": len(context.open_positions),
                        "session": context.session,
                        "regime": getattr(context, "_regime", "unknown"),
                    },
                    "selection": self.get_engine_status(),
                    "risk": {
                        "max_order_value_absolute": self.risk.config.max_order_value_absolute,
                        "min_cash_buffer": self.risk.config.min_cash_buffer,
                        "tiny_account_mode": self.risk.config.tiny_account_mode,
                        "max_open_positions": self.risk.config.max_open_positions,
                        "max_capital_per_trade_pct": self.risk.config.max_capital_per_trade_pct,
                    },
                    "broker_health": self.get_broker_health_summary(),
                    "watchlist": context.watchlist_data,
                    "open_positions": context.open_positions,
                    "options_chain_summary": context.options_chain_summary,
                    "recent_news_sentiment": context.recent_news_sentiment,
                },
            )
        except Exception as e:
            logger.debug(f"Decision persist error: {e}")

        done = datetime.now(IST)
        self._set_agent_stage("decision_complete", done)
        self._agent_status["progress_pct"] = 100
        self._agent_status["signals_approved"] = executed
        self._agent_status["signals_rejected"] = rejected
        if self._agent_status.get("cycle_started_at"):
            cycle_started = datetime.fromisoformat(str(self._agent_status["cycle_started_at"]))
            self._agent_status["last_cycle_duration_ms"] = int((done - cycle_started).total_seconds() * 1000)
        self._push_agent_event(
            "Decision cycle completed",
            level="success",
            now=done,
            metadata={
                "progress_pct": 100,
                "signals_approved": executed,
                "signals_rejected": rejected,
                "signals_considered": len(signals),
                "selected_strategy": self._agent_status.get("selected_strategy"),
            },
        )

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _execute_signal(self, signal: TradingSignal, qty: int, sl: Optional[Decimal]) -> bool:
        try:
            inst = await self._get_instrument(signal.symbol, signal.exchange)
            product = ProductType(signal.product)
            side = OrderSide.BUY if signal.action in (SignalAction.BUY, SignalAction.COVER) else OrderSide.SELL
            order_type = OrderType.LIMIT if signal.entry_price else OrderType.MARKET

            # Entry order
            execution_broker = self.get_execution_broker()
            if not execution_broker:
                raise RuntimeError("Execution broker unavailable")

            entry_order = await execution_broker.place_order(
                instrument=inst, side=side, quantity=qty,
                order_type=order_type, product=product,
                price=signal.entry_price, tag=signal.strategy[:8].upper(),
            )
            logger.info(
                f"✅ Dhan master execution success | {signal.action.value} {qty} {signal.symbol} [{entry_order.order_id}]"
            )

            asyncio.create_task(
                self._copy_trade_to_replica(
                    instrument=inst,
                    side=side,
                    quantity=qty,
                    order_type=order_type,
                    product=product,
                    price=signal.entry_price,
                    trigger_price=None,
                    tag=f"COPY_{signal.strategy[:8].upper()}",
                )
            )

            logger.info(f"✅ {signal.action.value} {qty} {signal.symbol} | {signal.strategy} | {signal.confidence:.0%}")

            # Save trade
            await TradeRepository.save(
                broker_order_id=entry_order.order_id,
                broker=self.execution_primary_broker or self._primary_broker_name,
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
                broker=self.execution_primary_broker or self._primary_broker_name,
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
                    sl_order = await execution_broker.place_order(
                        instrument=inst, side=exit_side, quantity=qty,
                        order_type=OrderType.SL_M, product=product,
                        trigger_price=sl, tag=f"SL_{signal.strategy[:6].upper()}",
                    )
                    sl_order_id = sl_order.order_id
                    await SLOrderRepository.save(
                        position_id=str(db_pos.id),
                        broker_order_id=sl_order.order_id,
                        broker=self.execution_primary_broker or self._primary_broker_name,
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
                broker=self.execution_primary_broker or self._primary_broker_name, strategy=signal.strategy,
            )

            # Telegram alert
            await self._notify_entry(signal, qty, sl)
            return True

        except Exception as e:
            logger.error(f"❌ Dhan master execution failed | {signal.symbol}: {e}")
            logger.error(f"Execution error {signal.symbol}: {e}", exc_info=True)
            return False

    async def _copy_trade_to_replica(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        product: ProductType,
        price: Optional[Decimal],
        trigger_price: Optional[Decimal],
        tag: Optional[str],
    ) -> None:
        if not self._replication_enabled or not self.replica_broker:
            self._replication_status = "disabled"
            return

        try:
            mapped = await self._map_instrument_for_replica(instrument)
            if not mapped:
                raise RuntimeError(f"No Zerodha instrument mapping found for {instrument.symbol}")

            if quantity <= 0:
                raise RuntimeError(f"Invalid quantity {quantity} for replication")

            lot = max(int(mapped.lot_size or 1), 1)
            if quantity % lot != 0:
                raise RuntimeError(
                    f"Quantity {quantity} incompatible with Zerodha lot size {lot} for {mapped.symbol}"
                )

            replica_order = await self.replica_broker.place_order(
                instrument=mapped,
                side=side,
                quantity=quantity,
                order_type=order_type,
                product=product,
                price=price,
                trigger_price=trigger_price,
                tag=tag,
            )
            self._replication_status = "ok"
            self._last_replication_error = ""
            logger.info(
                f"✅ Zerodha copy success | {side.value} {quantity} {mapped.symbol} [{replica_order.order_id}]"
            )
        except Exception as e:
            self._replication_status = "partial_failure"
            self._last_replication_error = str(e)
            logger.warning(f"⚠️ Zerodha copy attempt failed: {e}")
            await RiskEventRepository.log(
                "ZERODHA_COPY_FAILED",
                f"Failed to replicate order for {instrument.symbol}: {e}",
                severity="HIGH",
                symbol=instrument.symbol,
            )

    async def _map_instrument_for_replica(self, master_instrument: Instrument) -> Optional[Instrument]:
        if not self.replica_broker:
            return None

        exchange = master_instrument.exchange
        try:
            replica_instruments = await self.replica_broker.get_instruments(exchange)
        except Exception as e:
            logger.warning(f"Replica instrument fetch failed for {exchange.value}: {e}")
            return None

        for inst in replica_instruments:
            if (
                inst.symbol == master_instrument.symbol
                and inst.instrument_type == master_instrument.instrument_type
                and (not master_instrument.expiry or inst.expiry == master_instrument.expiry)
                and (master_instrument.strike is None or inst.strike == master_instrument.strike)
            ):
                return inst

        return next((i for i in replica_instruments if i.symbol == master_instrument.symbol), None)

    async def _reconciliation_loop(self) -> None:
        while True:
            try:
                await self._reconcile_master_replica_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Reconciliation loop error: {e}")
            await asyncio.sleep(self._reconcile_interval_seconds)

    async def _reconcile_master_replica_state(self) -> None:
        execution_broker = self.get_execution_broker()
        if not execution_broker or not self.replica_broker:
            return

        dhan_positions = await execution_broker.get_positions()
        zerodha_positions = await self.replica_broker.get_positions()
        dhan_orders = await execution_broker.get_order_history()
        zerodha_orders = await self.replica_broker.get_order_history()

        def _position_key(p: Position) -> tuple[str, str, int]:
            return (p.instrument.symbol, p.side.value, p.quantity)

        dhan_pos_keys = {_position_key(p) for p in dhan_positions}
        zerodha_pos_keys = {_position_key(p) for p in zerodha_positions}
        missing_in_replica = sorted(dhan_pos_keys - zerodha_pos_keys)
        extra_in_replica = sorted(zerodha_pos_keys - dhan_pos_keys)

        dhan_open_orders = {
            (o.instrument.symbol, o.side.value, o.quantity)
            for o in dhan_orders
            if o.status in {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING}
        }
        zerodha_open_orders = {
            (o.instrument.symbol, o.side.value, o.quantity)
            for o in zerodha_orders
            if o.status in {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING}
        }

        order_drift = sorted(dhan_open_orders ^ zerodha_open_orders)
        if missing_in_replica or extra_in_replica or order_drift:
            self._replication_status = "partial_failure"
            details = {
                "missing_in_zerodha": missing_in_replica,
                "extra_in_zerodha": extra_in_replica,
                "order_drift": order_drift,
            }
            logger.warning(f"⚠️ Reconciliation drift detected (Dhan source of truth): {details}")
        else:
            if self._replication_enabled:
                self._replication_status = "ok"
            logger.info("✅ Reconciliation in sync: Dhan and Zerodha state aligned")


    # ── Position Monitoring ───────────────────────────────────────────────────

    async def _monitor_positions(self) -> None:
        tracked = self.tracker.get_all()
        if not tracked:
            return

        symbols = list({p["symbol"] for p in tracked})
        instruments = [await self._get_instrument(s) for s in symbols]
        instruments = [i for i in instruments if i]
        execution_broker = self.get_execution_broker()
        quotes = await execution_broker.get_quote(instruments) if (instruments and execution_broker) else {}

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

        execution_broker = self.get_execution_broker()
        if not execution_broker:
            return
        broker_positions = await execution_broker.get_positions()
        await self.risk.update_pnl(broker_positions)

    async def _update_trailing_stop(self, pos_id: str, pos: dict, new_sl: Decimal) -> None:
        try:
            old_id = pos.get("sl_broker_order_id")
            if old_id:
                execution_broker = self.get_execution_broker()
                if not execution_broker:
                    return
                await execution_broker.cancel_order(old_id)
                active = await SLOrderRepository.get_active_for_position(pos_id)
                if active:
                    await SLOrderRepository.deactivate(str(active.id))

            inst = await self._get_instrument(pos["symbol"])
            exit_side = OrderSide.SELL if pos["side"] == "BUY" else OrderSide.BUY
            execution_broker = self.get_execution_broker()
            if not execution_broker:
                return
            new_order = await execution_broker.place_order(
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
            execution_broker = self.get_execution_broker()
            if not execution_broker:
                return
            await execution_broker.place_order(
                instrument=inst, side=exit_side, quantity=pos["quantity"],
                order_type=OrderType.MARKET, product=ProductType.MIS, tag="TARGET_HIT",
            )
            if pos.get("sl_broker_order_id"):
                await execution_broker.cancel_order(pos["sl_broker_order_id"])

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
        execution_broker = self.get_execution_broker()
        if not execution_broker:
            raise RuntimeError("Execution broker unavailable for market context")
        positions = await execution_broker.get_positions()
        funds = await execution_broker.get_funds()
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
        self._latest_options_chain = options_summary
        self._latest_watchlist = watchlist
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
        ctx._session_profile = dict(self._active_session_profile)
        return ctx

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    async def _preload_ohlcv(self) -> None:
        logger.info("📥 Preloading OHLCV...")
        now = datetime.now(IST)
        from_date = now - timedelta(days=120)
        data_broker = self._select_data_broker("ohlcv")
        preload_symbols = self._preload_symbol_subset()
        if self.selection_mode == "auto_pick" and len(preload_symbols) < len(self._candidate_universe_symbols):
            logger.info(
                "Preloading bounded OHLCV subset for auto-pick (%s/%s symbols)",
                len(preload_symbols),
                len(self._candidate_universe_symbols),
            )
        retained_symbols = set(preload_symbols)
        for symbol in list(self._ohlcv_frames.keys()):
            if symbol not in retained_symbols:
                self._ohlcv_frames.pop(symbol, None)
        for symbol in preload_symbols:
            try:
                inst = await self._get_instrument_for_broker(symbol, data_broker)
                candles = await data_broker.get_ohlcv(inst, "day", from_date, now)
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
        self._refresh_selection()
        logger.info(f"✅ OHLCV: {len(self._ohlcv_frames)} symbols | active={len(self._selected_symbols)}")

    async def _refresh_ohlcv(self) -> None:
        previous_selected_symbols = list(self._selected_symbols)
        now = datetime.now(IST)
        from_date = now - timedelta(days=2)
        data_broker = self._select_data_broker("ohlcv")
        for symbol in self._candidate_universe_symbols[:10]:
            try:
                inst = await self._get_instrument_for_broker(symbol, data_broker)
                candles = await data_broker.get_ohlcv(inst, "day", from_date, now)
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
        self._refresh_selection()
        if previous_selected_symbols != self._selected_symbols and self._running:
            logger.info("🔄 Active symbol set changed from %s to %s", previous_selected_symbols, self._selected_symbols)
            await self._subscribe_market_data()

    async def _get_watchlist_indicators(self) -> list[dict]:
        result = []
        ranked_by_symbol = {item["symbol"]: item for item in self._latest_ranked_candidates}
        for symbol in self._selected_symbols:
            df = self._ohlcv_frames.get(symbol)
            if df is None or df.empty:
                continue
            try:
                bundle = self.indicators.compute(df, symbol, "day")
                item = self.indicators.to_dict(bundle)
                meta = ranked_by_symbol.get(symbol, {})
                item["rank"] = meta.get("rank")
                item["score"] = meta.get("score")
                item["selection_reason"] = meta.get("reason", "configured watchlist symbol" if symbol in self.configured_watchlist_symbols else "selected by engine")
                result.append(item)
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
            execution_broker = self.get_execution_broker()
            if not execution_broker:
                return
            positions = await execution_broker.get_positions()
            mis = [p for p in positions if p.product.value == "MIS"]
            for p in mis:
                try:
                    await execution_broker.square_off_position(p)
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
        connected_order: list[str] = []

        if bc.get("zerodha", {}).get("enabled"):
            zb = ZerodhaBroker(bc["zerodha"])
            if await zb.login():
                self.brokers["zerodha"] = zb
                connected_order.append("zerodha")

        if bc.get("dhan", {}).get("enabled"):
            db = DhanBroker(bc["dhan"])
            if await db.login():
                self.brokers["dhan"] = db
                connected_order.append("dhan")

        if not self.brokers:
            return
        if "dhan" in self.brokers:
            self.primary_broker = self.brokers["dhan"]
            self._primary_broker_name = "dhan"
            self.execution_primary_broker = "dhan"
            self.ui_primary_broker = self.ui_primary_broker or "dhan"
            logger.info("🏦 Primary broker forced to Dhan (master source of truth)")
            if "zerodha" in self.brokers:
                self.replica_broker = self.brokers["zerodha"]
                self._replica_broker_name = "zerodha"
                self._replication_enabled = True
                self._replication_status = "ok"
                logger.info("🔁 Zerodha connected as follower for copy trading")
            else:
                self._replication_status = "disabled"
                logger.warning("Zerodha not connected. Copy trading is disabled.")
            return

        logger.warning("Dhan unavailable; fallback mode activated with non-Dhan primary broker")

        best_name: Optional[str] = None
        best_cash = Decimal("0")

        for name in connected_order:
            broker = self.brokers[name]
            try:
                funds = await broker.get_funds()
            except Exception as e:
                logger.warning(f"Could not fetch funds for {name} during primary selection: {e}")
                continue

            available = max(funds.available_cash, Decimal("0"))
            total = max(funds.total_balance, Decimal("0"))
            effective = available if available > 0 else total

            logger.info(
                f"💼 Broker capital check | {name}: available=₹{available:,.0f}, total=₹{total:,.0f}, effective=₹{effective:,.0f}"
            )

            if effective > best_cash:
                best_cash = effective
                best_name = name

        if best_name:
            self.primary_broker = self.brokers[best_name]
            self._primary_broker_name = best_name
            self.execution_primary_broker = best_name
            self.ui_primary_broker = self.ui_primary_broker or best_name
            logger.info(f"🏦 Primary broker selected by tradable cash: {best_name} (₹{best_cash:,.0f})")
            return

        fallback = connected_order[0]
        self.primary_broker = self.brokers[fallback]
        self._primary_broker_name = fallback
        self.execution_primary_broker = fallback
        self.ui_primary_broker = self.ui_primary_broker or fallback
        logger.warning(
            "No broker reported positive tradable capital at startup; "
            f"falling back to first connected broker: {fallback}"
        )


    async def _load_instruments(self) -> None:
        logger.info("📥 Loading instruments...")
        try:
            execution_broker = self.get_execution_broker()
            if not execution_broker:
                raise RuntimeError("Execution broker unavailable for instrument load")
            insts = await execution_broker.get_instruments(Exchange.NSE)
            for i in insts:
                self._instrument_cache[i.symbol] = i
            self._nse_equity_symbols_cache = load_nse_equity_symbols(self._instrument_cache)
            logger.info(
                f"✅ {len(insts)} instruments loaded | NSE cash equities cached={len(self._nse_equity_symbols_cache)}"
            )
        except Exception as e:
            logger.error(f"Instrument load error: {e}")

    def _select_data_broker(self, purpose: str) -> BaseBroker:
        dhan = self.brokers.get("dhan")
        zerodha = self.brokers.get("zerodha")
        selected_name, override_active, _ = self.resolve_ui_primary_broker()
        broker_candidates = [
            (name, broker)
            for name, broker in self.brokers.items()
            if broker is not None
        ]
        if broker_candidates:
            broker_candidates.sort(key=lambda item: self._broker_health_score(item[0], item[1]), reverse=True)
        selected = self.brokers.get(selected_name) or (broker_candidates[0][1] if broker_candidates else None) or dhan or self.primary_broker

        if not selected:
            return self.get_execution_broker()

        if purpose == "ohlcv" and getattr(selected, "_historical_data_blocked", False) and zerodha:
            selected = zerodha
        elif purpose == "ticks" and getattr(selected, "_ws_blocked", False) and zerodha:
            selected = zerodha

        effective_name = next((name for name, obj in self.brokers.items() if obj is selected), "primary")
        if self._market_data_fallback_state.get(purpose) != effective_name:
            msg = "OHLCV" if purpose == "ohlcv" else "live tick"
            if selected is zerodha:
                logger.warning(f"⚠️ Dhan {msg} feed unavailable. Fallback mode activated via Zerodha")
            else:
                logger.info(f"✅ Using Dhan as primary source for {msg} market data")
            self._market_data_fallback_state[purpose] = effective_name
        if override_active and purpose == "ticks":
            logger.warning(f"⚠️ UI primary override active for ticks. Using {effective_name}")

        return selected

    async def _get_instrument(self, symbol: str, exchange: str = "NSE") -> Instrument:
        return self._instrument_cache.get(symbol) or Instrument(symbol, Exchange(exchange), InstrumentType.EQ)

    async def _get_instrument_for_broker(self, symbol: str, broker: BaseBroker) -> Instrument:
        if broker is self.get_execution_broker():
            return await self._get_instrument(symbol)

        try:
            insts = await broker.get_instruments(Exchange.NSE)
            for inst in insts:
                if inst.symbol == symbol:
                    return inst
        except Exception as e:
            logger.debug(f"Fallback instrument lookup failed for {symbol}: {e}")

        return await self._get_instrument(symbol)

    async def _subscribe_market_data(self) -> None:
        data_broker = self._select_data_broker("ticks")
        broker_name = next((name for name, obj in self.brokers.items() if obj is data_broker), "primary")

        if self._active_tick_broker_name and self._active_tick_broker_name != broker_name:
            previous = self.brokers.get(self._active_tick_broker_name)
            if previous:
                try:
                    old_insts = [await self._get_instrument_for_broker(s, previous) for s in self._selected_symbols[:20]]
                    await previous.unsubscribe_ticks(old_insts)
                except Exception as e:
                    logger.debug(f"Tick unsubscribe failed for {self._active_tick_broker_name}: {e}")
            logger.warning(f"Switched live ticks: {self._active_tick_broker_name} -> {broker_name} due to websocket 403")

        insts = [await self._get_instrument_for_broker(s, data_broker) for s in self._selected_symbols[:20]]
        self._tick_token_to_symbol = {
            str(inst.instrument_token): inst.symbol
            for inst in insts
            if inst.instrument_token
        }
        await data_broker.subscribe_ticks(insts, self._on_tick)
        self._active_tick_broker_name = broker_name
        logger.info(f"📡 Subscribed {len(insts)} instruments via {broker_name}")

    async def _ensure_tick_subscription_health(self) -> None:
        if not self._active_tick_broker_name:
            return

        active_broker = self.brokers.get(self._active_tick_broker_name)
        preferred = self._select_data_broker("ticks")
        preferred_name = next((name for name, obj in self.brokers.items() if obj is preferred), "primary")

        if getattr(active_broker, "_ws_blocked", False) or preferred_name != self._active_tick_broker_name:
            await self._subscribe_market_data()

    async def _on_tick(self, tick: dict) -> None:
        sym = (
            tick.get("tradingsymbol")
            or tick.get("trading_symbol")
            or tick.get("symbol")
            or self._tick_token_to_symbol.get(str(tick.get("instrument_token") or tick.get("security_id") or ""))
            or ""
        )
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
