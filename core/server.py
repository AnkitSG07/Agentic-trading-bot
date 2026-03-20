"""
FastAPI Backend Server - Fully Wired
Uses engine singleton (get_engine()) — no global engine variable.
All endpoints pull live data from the running engine and brokers.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
import math
from typing import Literal, Optional

import pandas as pd

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.engine import get_engine, set_engine, TradingEngine
from core.replay_schema import ReplayRunCreateRequest
from data.stock_selector import SelectorConfig, StockSelector
from data.stock_universe import get_cached_nse_equity_symbols, load_nse_equity_symbols
from database.repository import (
    AgentDecisionRepository, DailySummaryRepository,
    PositionRepository, RiskEventRepository, TradeRepository,
    ReplayRunRepository,
)

logger = logging.getLogger("api")
ws_clients: list[WebSocket] = []


def _engine_state_file() -> Path:
    configured = os.getenv("ENGINE_STATE_FILE", "runtime/engine_state.json").strip()
    return Path(configured)


def _persist_engine_state(autostart: bool, mode: str = "paper", overrides: Optional[dict] = None) -> None:
    """Persist desired engine start mode so API restarts can recover gracefully."""
    path = _engine_state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "autostart": bool(autostart),
            "mode": mode,
            "overrides": overrides or {},
            "updated_at": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Unable to persist engine state to {path}: {e}")


def _load_engine_state() -> dict:
    path = _engine_state_file()
    if not path.exists():
        return {"autostart": False, "mode": "paper", "overrides": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "autostart": bool(data.get("autostart", False)),
            "mode": str(data.get("mode", "paper") or "paper"),
            "overrides": data.get("overrides", {}) or {},
        }
    except Exception as e:
        logger.warning(f"Unable to load engine state from {path}: {e}")
        return {"autostart": False, "mode": "paper", "overrides": {}}


def _broker_pref_file() -> Path:
    configured = os.getenv("UI_SETTINGS_FILE", "runtime/ui_settings.json").strip()
    return Path(configured)


def _load_ui_primary_broker_preference() -> str:
    path = _broker_pref_file()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = str(data.get("ui_primary_broker", "") or "").strip().lower()
        return value if value in {"dhan", "zerodha"} else ""
    except Exception as e:
        logger.warning(f"Unable to load broker preference from {path}: {e}")
        return ""


def _persist_ui_primary_broker_preference(ui_primary_broker: str) -> bool:
    path = _broker_pref_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ui_primary_broker": ui_primary_broker,
            "updated_at": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning(f"Unable to persist broker preference to {path}: {e}")
        return False


async def _resolve_ui_primary_status(engine: Optional[TradingEngine]) -> dict:
    selected = _load_ui_primary_broker_preference()
    connected = []
    effective = ""
    fallback_active = False
    reason = ""

    if engine:
        if selected:
            engine.set_ui_primary_broker(selected)
        elif not engine.ui_primary_broker:
            engine.set_ui_primary_broker(engine._primary_broker_name or "")

        connected = await engine.connected_broker_names_live()
        selected = engine.ui_primary_broker or selected
        effective, fallback_active, reason = await engine.resolve_ui_primary_broker_live()
    else:
        reason = "engine not running"

    if selected and selected not in connected and connected:
        fallback_active = True
        reason = reason or f"selected broker '{selected}' is disconnected"
    elif selected and not connected:
        fallback_active = True
        reason = reason or "no healthy broker available"

    return {
        "ui_primary_broker": selected or None,
        "connected_brokers": connected,
        "effective_primary_broker": effective or None,
        "fallback_active": fallback_active,
        "reason": reason,
    }

async def _start_engine_task(mode: str, overrides: Optional[dict] = None) -> None:
    """Shared background launcher used by API start endpoint and startup recovery."""
    # Load config with full env-var expansion (same as main.py)
    from config.loader import load_config
    import copy

    config = copy.deepcopy(load_config())

    if mode == "paper":
        config["app"]["environment"] = "paper"
        for broker_cfg in config.get("brokers", {}).values():
            if isinstance(broker_cfg, dict):
                broker_cfg["sandbox"] = True

    new_engine = TradingEngine(config)
    new_engine.apply_runtime_overrides(overrides or {})
    preferred_ui_broker = _load_ui_primary_broker_preference()
    if preferred_ui_broker:
        new_engine.set_ui_primary_broker(preferred_ui_broker)
    try:
        await new_engine.start()
    except Exception as e:
        logger.error(f"Engine crashed: {e}", exc_info=True)
        set_engine(None)

def _get_allowed_origins() -> list[str]:
    """Build CORS allowlist from env vars with safe local defaults."""
    defaults = {
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }

    configured = {
        o.strip().rstrip("/")
        for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
        if o.strip()
    }

    frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
    if frontend_url:
        configured.add(frontend_url)

    # Explicitly add Render frontend domains
    configured.add("https://agentic-trading-bot-1.onrender.com")
    
    # Add any Render service URL
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_url:
        configured.add(render_url)

    return sorted(defaults | configured)


# ─── LIFESPAN ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🌐 API server online")

    state = _load_engine_state()
    if state.get("autostart"):
        mode = state.get("mode", "paper")
        logger.info(f"♻️ Recovering engine after API restart (mode={mode})")
        asyncio.create_task(_start_engine_task(mode, state.get("overrides", {})))

    yield
    logger.info("🌐 API server shutting down")
    engine = get_engine()
    if engine and engine._running:
        await engine.stop()


app = FastAPI(
    title="AgentTrader India",
    description="Agentic Trading Bot — Zerodha + Dhan + Claude AI",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_allowed_origins(),
    allow_origin_regex=r"https://.*\.onrender\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── MODELS ──────────────────────────────────────────────────────────────────

class ManualOrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    side: str
    quantity: int
    order_type: str = "MARKET"
    price: Optional[float] = None
    product: str = "MIS"
    stop_loss: Optional[float] = None

class KillSwitchResetRequest(BaseModel):
    override_code: str

class EngineStartRequest(BaseModel):
    mode: str = "paper"
    selection_mode: Optional[str] = None
    watchlist_symbols: Optional[list[str]] = None
    min_stock_price: Optional[float] = None
    max_stock_price: Optional[float] = None
    max_auto_pick_symbols: Optional[int] = None
    min_avg_daily_volume: Optional[float] = None
    min_avg_daily_turnover: Optional[float] = None
    max_order_value_absolute: Optional[float] = None
    min_cash_buffer: Optional[float] = None
    tiny_account_mode: Optional[bool] = None

class BrokerPreferenceRequest(BaseModel):
    ui_primary_broker: Literal["dhan", "zerodha"]


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def require_engine():
    engine = get_engine()
    if not engine:
        raise HTTPException(503, "Engine not running. POST /api/engine/start first.")
    return engine

def get_engine_or_none():
    """Return engine if running, None otherwise (don't raise)."""
    return get_engine()

def require_broker():
    engine = require_engine()
    if not engine.primary_broker:
        raise HTTPException(503, "No broker connected.")
    return engine.primary_broker


# ─── HEALTH ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    engine = get_engine()
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "engine_running": engine._running if engine else False,
        "broker": engine._primary_broker_name if engine else None,
        "primary_broker": engine._primary_broker_name if engine else None,
        "replication_enabled": engine._replication_enabled if engine else False,
        "replication_status": engine._replication_status if engine else "disabled",
        "last_replication_error": engine._last_replication_error if engine else "",
        "kill_switch": engine.risk._kill_switch if engine else False,
    }


# ─── ENGINE CONTROL ──────────────────────────────────────────────────────────

@app.post("/api/engine/start")
async def start_engine(req: EngineStartRequest, background_tasks: BackgroundTasks):
    """Start the trading engine in the background."""
    engine = get_engine()
    if engine and engine._running:
        raise HTTPException(400, "Engine already running")

    overrides = req.model_dump(exclude_none=True)
    overrides.pop("mode", None)
    _persist_engine_state(autostart=True, mode=req.mode, overrides=overrides)
    background_tasks.add_task(_start_engine_task, req.mode, overrides)
    return {"status": "starting", "mode": req.mode, "overrides": overrides}


@app.post("/api/engine/stop")
async def stop_engine():
    engine = get_engine()
    if not engine or not engine._running:
        raise HTTPException(400, "Engine not running")
    _persist_engine_state(autostart=False)
    asyncio.create_task(engine.stop())
    return {"status": "stopping"}


@app.get("/api/engine/status")
async def engine_status():
    engine = get_engine()
    if not engine:
        return {"running": False, "broker": None, "positions": 0}
    selection_status = engine.get_engine_status()
    return {
        "running": engine._running,
        "broker": engine._primary_broker_name,
        "primary_broker": engine._primary_broker_name,
        "replica_broker": engine._replica_broker_name or None,
        "positions": len(engine.tracker.get_all()),
        "replication_enabled": engine._replication_enabled,
        "replication_status": engine._replication_status,
        "last_replication_error": engine._last_replication_error,
        "kill_switch": engine.risk._kill_switch,
        "trading_allowed": engine.risk.is_trading_allowed,
        "broker_health": engine.get_broker_health_summary(),
        **selection_status,
    }


# ─── PORTFOLIO ───────────────────────────────────────────────────────────────

@app.get("/api/portfolio/positions")
async def get_positions():
    broker = require_broker()
    try:
        positions = await broker.get_positions()
        return {
            "positions": [
                {
                    "symbol": p.instrument.symbol,
                    "exchange": p.instrument.exchange.value,
                    "side": p.side.value,
                    "quantity": p.quantity,
                    "average_price": float(p.average_price),
                    "ltp": float(p.ltp),
                    "pnl": float(p.pnl),
                    "pnl_pct": round(p.pnl_pct, 2),
                    "product": p.product.value,
                    "broker": p.broker,
                }
                for p in positions
            ],
            "total_unrealized_pnl": sum(float(p.pnl) for p in positions),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/portfolio/holdings")
async def get_holdings():
    broker = require_broker()
    try:
        holdings = await broker.get_holdings()
        return {
            "holdings": [
                {
                    "symbol": h.instrument.symbol,
                    "quantity": h.quantity,
                    "average_price": float(h.average_price),
                    "ltp": float(h.ltp),
                    "pnl": float(h.pnl),
                    "pnl_pct": round(float(h.pnl) / (float(h.average_price) * h.quantity) * 100, 2) if h.average_price and h.quantity else 0,
                }
                for h in holdings
            ],
            "total_pnl": sum(float(h.pnl) for h in holdings),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/portfolio/funds")
async def get_funds():
    broker = require_broker()
    try:
        f = await broker.get_funds()
        return {
            "available_cash": float(f.available_cash),
            "used_margin": float(f.used_margin),
            "total_balance": float(f.total_balance),
            "unrealised_pnl": float(f.unrealised_pnl),
            "realised_pnl": float(f.realised_pnl),
            "utilization_pct": round(float(f.used_margin) / float(f.total_balance) * 100, 1) if f.total_balance else 0,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── ORDERS ──────────────────────────────────────────────────────────────────

@app.get("/api/orders")
async def get_orders(status: Optional[str] = None, broker: str = "all"):
    engine = require_engine()
    try:
        selected_brokers: list[tuple[str, object]] = []
        requested = broker.lower().strip()

        if requested in {"", "all"}:
            selected_brokers = list(engine.brokers.items())
            if not selected_brokers and engine.primary_broker:
                selected_brokers = [(engine._primary_broker_name or "primary", engine.primary_broker)]
        else:
            selected = engine.brokers.get(requested)
            if not selected:
                raise HTTPException(404, f"Broker '{broker}' is not connected")
            selected_brokers = [(requested, selected)]

        orders_with_broker = []
        for broker_name, broker_client in selected_brokers:
            try:
                broker_orders = await broker_client.get_order_history()
                orders_with_broker.extend((broker_name, o) for o in broker_orders)
            except Exception as broker_error:
                logger.warning(f"Failed to fetch orders from {broker_name}: {broker_error}")

        result = [
            {
                "order_id": o.order_id,
                "broker": broker_name,
                "symbol": o.instrument.symbol,
                "exchange": o.instrument.exchange.value,
                "side": o.side.value,
                "quantity": o.quantity,
                "filled_quantity": o.filled_quantity,
                "order_type": o.order_type.value,
                "price": float(o.price) if o.price else None,
                "trigger_price": float(o.trigger_price) if o.trigger_price else None,
                "average_price": float(o.average_price) if o.average_price else None,
                "status": o.status.value,
                "tag": o.tag,
                "placed_at": o.placed_at.isoformat(),
                "rejection_reason": o.rejection_reason,
            }
            for broker_name, o in orders_with_broker
        ]
        if status:
            result = [o for o in result if o["status"] == status.upper()]
        result.sort(key=lambda x: x["placed_at"], reverse=True)
        return {"orders": result, "total": len(result)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/orders/manual")
async def place_manual_order(req: ManualOrderRequest):
    engine = require_engine()
    broker = require_broker()
    try:
        from brokers.base import Exchange as Ex, Instrument, InstrumentType, OrderSide, OrderType, ProductType

        inst = engine._instrument_cache.get(req.symbol) or Instrument(
            req.symbol, Ex(req.exchange), InstrumentType.EQ
        )
        order = await broker.place_order(
            instrument=inst,
            side=OrderSide(req.side),
            quantity=req.quantity,
            order_type=OrderType(req.order_type),
            product=ProductType(req.product),
            price=Decimal(str(req.price)) if req.price else None,
            tag="MANUAL",
        )

        # Place SL if provided
        sl_order_id = None
        if req.stop_loss:
            exit_side = OrderSide.SELL if req.side == "BUY" else OrderSide.BUY
            sl_ord = await broker.place_order(
                instrument=inst, side=exit_side, quantity=req.quantity,
                order_type=OrderType.SL_M, product=ProductType(req.product),
                trigger_price=Decimal(str(req.stop_loss)), tag="MANUAL_SL",
            )
            sl_order_id = sl_ord.order_id

        return {
            "status": "placed",
            "order_id": order.order_id,
            "sl_order_id": sl_order_id,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: str):
    broker = require_broker()
    success = await broker.cancel_order(order_id)
    return {"status": "cancelled" if success else "error"}


@app.post("/api/orders/{order_id}/squareoff")
async def square_off_order(order_id: str):
    """Square off a specific position by symbol."""
    broker = require_broker()
    positions = await broker.get_positions()
    target = next((p for p in positions if p.instrument.symbol == order_id or
                   str(order_id) in str(p.instrument.symbol)), None)
    if not target:
        raise HTTPException(404, f"No open position for {order_id}")
    order = await broker.square_off_position(target)
    return {"status": "squared_off", "order_id": order.order_id}


# ─── RISK ─────────────────────────────────────────────────────────────────────

@app.get("/api/risk/summary")
async def risk_summary():
    engine = get_engine_or_none()
    if not engine:
        return {
            "date": datetime.now().date().isoformat(),
            "starting_capital": 0,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "total_pnl": 0,
            "daily_pnl_pct": 0,
            "drawdown_pct": 0,
            "total_trades": 0,
            "win_rate": 0,
            "kill_switch": False,
            "kill_switch_reason": "",
            "trading_allowed": False,
        }
    return engine.risk.get_daily_summary()


@app.get("/api/risk/events")
async def risk_events(limit: int = 50):
    try:
        events = await RiskEventRepository.get_recent(limit)
        return {
            "events": [
                {
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "type": e.event_type,
                    "severity": e.severity,
                    "symbol": e.symbol,
                    "description": e.description,
                    "pnl": float(e.pnl_at_event) if e.pnl_at_event else None,
                    "drawdown": e.drawdown_at_event,
                }
                for e in events
            ]
        }
    except Exception as e:
        logger.error(f"Risk events error: {e}")
        return {"events": []}


@app.post("/api/risk/kill-switch/reset")
async def reset_kill_switch(req: KillSwitchResetRequest):
    engine = require_engine()
    success = engine.risk.reset_kill_switch(req.override_code)
    if not success:
        raise HTTPException(403, "Invalid override code")
    return {"status": "reset"}


# ─── MARKET DATA ─────────────────────────────────────────────────────────────

@app.get("/api/market/indices")
async def get_indices():
    engine = get_engine_or_none()
    if not engine:
        return {"nifty": 22000.0, "banknifty": 47000.0, "vix": 14.0}
    return await engine.nse_feed.get_index_data()


@app.get("/api/market/quote/{symbol}")
async def get_quote(symbol: str, exchange: str = "NSE"):
    engine = require_engine()
    try:
        inst = await engine._get_instrument(symbol, exchange)
        quotes = await engine.primary_broker.get_quote([inst])
        if symbol not in quotes:
            raise HTTPException(404, f"No quote for {symbol}")
        q = quotes[symbol]
        return {
            "symbol": symbol, "ltp": float(q.ltp),
            "open": float(q.open), "high": float(q.high),
            "low": float(q.low), "close": float(q.close),
            "volume": q.volume, "oi": q.oi,
            "bid": float(q.bid), "ask": float(q.ask),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/market/options/{symbol}")
async def get_option_chain(symbol: str):
    engine = get_engine_or_none()
    if not engine:
        return {}
    chain = await engine.nse_feed.get_option_chain(symbol)
    return chain


@app.get("/api/market/pcr/{symbol}")
async def get_pcr(symbol: str = "NIFTY"):
    engine = get_engine_or_none()
    if not engine:
        return {"symbol": symbol, "pcr": 1.0}
    pcr = await engine.nse_feed.get_pcr(symbol)
    return {"symbol": symbol, "pcr": pcr}


# ─── AGENT ───────────────────────────────────────────────────────────────────

@app.get("/api/agent/decisions")
async def agent_decisions(limit: int = 20):
    try:
        decisions = await AgentDecisionRepository.get_recent(limit)
        return {
            "decisions": [
                {
                    "timestamp": d.timestamp.isoformat(),
                    "market_regime": d.market_regime,
                    "signals_generated": d.signals_generated,
                    "signals_executed": d.signals_executed,
                    "signals_rejected": d.signals_rejected,
                    "nifty": d.nifty_ltp,
                    "vix": d.india_vix,
                    "pcr": d.pcr,
                }
                for d in decisions
            ]
        }
    except Exception as e:
        logger.error(f"Agent decisions error: {e}")
        return {"decisions": []}


@app.get("/api/agent/in-memory-decisions")
async def agent_in_memory(limit: int = 20):
    """Quick access to agent decisions stored in memory (no DB needed)."""
    engine = get_engine_or_none()
    if not engine:
        return {"decisions": [], "agent_status": None, "agent_events": []}
    return {
        "decisions": engine.agent.decision_history[-limit:],
        "agent_status": engine._agent_status,
        "agent_events": engine._agent_events[-50:],
    }


@app.get("/api/agent/status")
async def agent_status():
    engine = get_engine_or_none()
    if not engine:
        return {"agent_status": None, "agent_events": []}
    return {
        "agent_status": engine._agent_status,
        "agent_events": engine._agent_events[-50:],
    }


# ─── ANALYTICS ───────────────────────────────────────────────────────────────

@app.get("/api/analytics/performance")
async def performance(days: int = 30):
    try:
        stats = await PositionRepository.get_performance_stats(days)
        return stats
    except Exception as e:
        logger.error(f"Performance error: {e}")
        return {
            "total_trades": 0,
            "total_pnl": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
        }


@app.get("/api/analytics/daily-history")
async def daily_history(days: int = 30):
    try:
        history = await DailySummaryRepository.get_history(days)
        return {
            "history": [
                {
                    "date": d.date,
                    "net_pnl": float(d.net_pnl or 0),
                    "pnl_pct": d.pnl_pct or 0,
                    "total_trades": d.total_trades,
                    "win_rate": d.win_rate,
                    "drawdown": d.max_drawdown_pct,
                    "kill_switch": d.kill_switch_triggered,
                }
                for d in history
            ]
        }
    except Exception as e:
        logger.error(f"Daily history error: {e}")
        return {"history": []}


@app.get("/api/analytics/trade-history")
async def trade_history(symbol: Optional[str] = None, days: int = 30):
    try:
        positions = await PositionRepository.get_history(days, symbol)
        return {
            "trades": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "quantity": p.quantity,
                    "entry_price": float(p.entry_price),
                    "exit_price": float(p.exit_price) if p.exit_price else None,
                    "realized_pnl": float(p.realized_pnl or 0),
                    "net_pnl": float(p.net_pnl or 0),
                    "strategy": p.strategy,
                    "exit_reason": p.exit_reason,
                    "opened_at": p.opened_at.isoformat(),
                    "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                }
                for p in positions
            ]
        }
    except Exception as e:
        logger.error(f"Trade history error: {e}")
        return {"trades": []}

@app.get("/api/settings/broker-preference")
async def get_broker_preference():
    return await _resolve_ui_primary_status(get_engine_or_none())


@app.post("/api/settings/broker-preference")
async def set_broker_preference(req: BrokerPreferenceRequest):
    selected = req.ui_primary_broker.lower()
    persisted = _persist_ui_primary_broker_preference(selected)
    if not persisted:
        raise HTTPException(500, "failed to persist ui_primary_broker")

    engine = get_engine_or_none()
    if engine:
        engine.set_ui_primary_broker(selected)

    status = await _resolve_ui_primary_status(engine)
    status["pending_connection"] = selected not in status.get("connected_brokers", [])
    return status


def _degraded_live_payload(engine: TradingEngine, ui_status: dict, reason: str) -> dict:
    return {
        "type": "live_update",
        "timestamp": datetime.now().isoformat(),
        "indices": {},
        "funds": {"available": 0.0, "used_margin": 0.0, "total": 0.0},
        "pnl": {"realized": 0, "unrealized": 0, "total": 0, "pct": 0},
        "positions": [],
        "risk": {"kill_switch": False, "drawdown_pct": 0, "daily_pnl_pct": 0, "trading_allowed": False, "trades_today": 0, "win_rate": 0},
        "ticks": {},
        "options_chain": {},
        "watchlist": [],
        "agent_decisions": engine.agent.decision_history[-3:],
        "agent_status": engine._agent_status,
        "agent_events": engine._agent_events[-30:],
        "agent_progress": {
            "stage": engine._agent_status.get("stage"),
            "progress_pct": engine._agent_status.get("progress_pct", 0),
            "selected_strategy": engine._agent_status.get("selected_strategy"),
            "cycle_id": engine._agent_status.get("cycle_id"),
            "last_cycle_duration_ms": engine._agent_status.get("last_cycle_duration_ms"),
        },
        "engine_running": engine._running,
        "primary_broker": engine._primary_broker_name or None,
        "ui_primary_broker": ui_status.get("ui_primary_broker"),
        "effective_primary_broker": None,
        "primary_override_active": True,
        "primary_override_reason": reason,
        "replica_broker": engine._replica_broker_name or None,
        "replication_enabled": engine._replication_enabled,
        "replication_status": engine._replication_status,
        "last_replication_error": engine._last_replication_error,
    }


# ─── WEBSOCKET ───────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.append(websocket)
    logger.info(f"WS client connected ({len(ws_clients)} total)")

    try:
        while True:
            engine = get_engine()
            if engine and engine.primary_broker and engine._running:
                try:
                    ui_status = await _resolve_ui_primary_status(engine)
                    effective_name = ui_status.get("effective_primary_broker")
                    if not effective_name:
                        payload = _degraded_live_payload(engine, ui_status, "no healthy broker available")
                        await websocket.send_text(json.dumps(payload))
                        await asyncio.sleep(1)
                        continue

                    effective_broker = engine.get_broker(str(effective_name))
                    if not effective_broker:
                        payload = _degraded_live_payload(engine, ui_status, f"effective broker '{effective_name}' unavailable")
                        await websocket.send_text(json.dumps(payload))
                        await asyncio.sleep(1)
                        continue

                    try:
                        positions = await effective_broker.get_positions()
                        funds = await effective_broker.get_funds()
                    except Exception as broker_error:
                        payload = _degraded_live_payload(engine, ui_status, f"effective broker error: {broker_error}")
                        await websocket.send_text(json.dumps(payload))
                        await asyncio.sleep(1)
                        continue

                    risk = engine.risk.get_daily_summary()
                    index_data = await engine.nse_feed.get_index_data()

                    payload = {
                        "type": "live_update",
                        "timestamp": datetime.now().isoformat(),
                        "indices": index_data,
                        "funds": {
                            "available": float(funds.available_cash),
                            "used_margin": float(funds.used_margin),
                            "total": float(funds.total_balance),
                        },
                        "pnl": {
                            "realized": risk["realized_pnl"],
                            "unrealized": risk["unrealized_pnl"],
                            "total": risk["total_pnl"],
                            "pct": risk["daily_pnl_pct"],
                        },
                        "positions": [
                            {
                                "symbol": p.instrument.symbol,
                                "side": p.side.value,
                                "qty": p.quantity,
                                "avg": float(p.average_price),
                                "ltp": float(p.ltp),
                                "pnl": float(p.pnl),
                            }
                            for p in positions
                        ],
                        "risk": {
                            "kill_switch": risk["kill_switch"],
                            "drawdown_pct": risk["drawdown_pct"],
                            "daily_pnl_pct": risk["daily_pnl_pct"],
                            "trading_allowed": risk["trading_allowed"],
                            "trades_today": risk["total_trades"],
                            "win_rate": risk["win_rate"],
                        },
                        "ticks": {
                            sym: {
                                "price": data.get("last_price") or data.get("ltp"),
                                "source_broker": ui_status.get("effective_primary_broker"),
                            }
                            for sym, data in engine._tick_data.items()
                            if (data.get("last_price") or data.get("ltp")) is not None
                        },
                        "options_chain": {
                            key: {
                                **value,
                                "source_broker": ui_status.get("effective_primary_broker"),
                            }
                            for key, value in engine._latest_options_chain.items()
                        },
                        "watchlist": [
                            {**item, "source_broker": ui_status.get("effective_primary_broker")}
                            for item in engine._latest_watchlist
                        ],
                        "agent_decisions": engine.agent.decision_history[-3:],
                        "agent_status": engine._agent_status,
                        "agent_events": engine._agent_events[-30:],
                        "agent_progress": {
                            "stage": engine._agent_status.get("stage"),
                            "progress_pct": engine._agent_status.get("progress_pct", 0),
                            "selected_strategy": engine._agent_status.get("selected_strategy"),
                            "cycle_id": engine._agent_status.get("cycle_id"),
                            "last_cycle_duration_ms": engine._agent_status.get("last_cycle_duration_ms"),
                        },
                        "engine_running": engine._running,
                        "primary_broker": engine._primary_broker_name or "dhan",
                        "ui_primary_broker": ui_status.get("ui_primary_broker"),
                        "effective_primary_broker": ui_status.get("effective_primary_broker"),
                        "primary_override_active": ui_status.get("fallback_active"),
                        "primary_override_reason": ui_status.get("reason", ""),
                        "replica_broker": engine._replica_broker_name or "zerodha",
                        "replication_enabled": engine._replication_enabled,
                        "replication_status": engine._replication_status,
                        "last_replication_error": engine._last_replication_error,    
                    }
                    await websocket.send_text(json.dumps(payload))
                except Exception as e:
                    logger.debug(f"WS payload error: {e}")
            else:
                await websocket.send_text(json.dumps({
                    "type": "status",
                    "engine_running": False,
                    "timestamp": datetime.now().isoformat(),
                }))

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        ws_clients.remove(websocket)
        logger.info(f"WS disconnected ({len(ws_clients)} remaining)")


async def broadcast(message: dict) -> None:
    text = json.dumps(message)
    dead = []
    for c in ws_clients:
        try:
            await c.send_text(text)
        except Exception:
            dead.append(c)
    for c in dead:
        ws_clients.remove(c)




class ReplaySelectionRequest(BaseModel):
    symbols: list[str] = []
    exchange: str = "NSE"
    timeframe: str = "day"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget_cap: float
    max_auto_symbols: int = 5
    fee_pct: float = 0.0003
    slippage_pct: float = 0.0005


def _selector_candidate_universe(symbols: list[str] | None) -> list[str]:
    if symbols:
        return [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    engine = get_engine()
    if engine:
        cached_symbols = get_cached_nse_equity_symbols(engine)
        if cached_symbols:
            return cached_symbols

        loaded_symbols = load_nse_equity_symbols(getattr(engine, "_instrument_cache", {}))
        if loaded_symbols:
            return loaded_symbols

    
    from core.engine import DEFAULT_WATCHLIST
    return list(DEFAULT_WATCHLIST)


def _selection_config() -> SelectorConfig:
    engine = get_engine()
    if not engine:
        return SelectorConfig()
    max_stock_price = getattr(engine, "_effective_max_stock_price", None)
    effective_max_price = max_stock_price() if callable(max_stock_price) else getattr(engine, "max_stock_price", 5000.0)
    return SelectorConfig(
        min_stock_price=float(getattr(engine, "min_stock_price", 50.0) or 50.0),
        max_stock_price=float(effective_max_price or 5000.0),
        min_avg_daily_volume=float(getattr(engine, "min_avg_daily_volume", 100000.0) or 100000.0),
        min_avg_daily_turnover=float(getattr(engine, "min_avg_daily_turnover", 5000000.0) or 5000000.0),
        max_auto_pick_symbols=int(getattr(engine, "max_auto_pick_symbols", 10) or 10),
    )


def _validated_budget_cap(raw_budget: float | None) -> float:
    budget = float(raw_budget or 0)
    if not math.isfinite(budget) or budget <= 0:
        raise HTTPException(400, "Invalid budget. Enter a rupee budget greater than zero.")
    return budget


def _bounded_live_quote_symbols(symbols: list[str]) -> list[str]:
    engine = get_engine()
    if not symbols:
        return []

    configured_cap = getattr(engine, "max_live_quote_symbols", None) if engine else None
    default_cap = 250
    cap = int(configured_cap or default_cap)
    if cap <= 0 or len(symbols) <= cap:
        return list(symbols)
    bounded = [str(symbol).strip().upper() for symbol in symbols[:cap] if str(symbol).strip()]
    logger.warning(
        "Bounding replay live-quote universe from %s to %s symbols to reduce memory/request pressure.",
        len(symbols),
        len(bounded),
    )
    return bounded

async def _fetch_universe_quotes(symbols: list[str], exchange: str) -> dict[str, dict]:
    engine = get_engine()
    if not engine or not symbols:
        return {}

    broker = getattr(engine, "primary_broker", None)
    if not broker:
        return {}

    instrument_cache = getattr(engine, "_instrument_cache", {}) or {}
    instruments = []
    instrument_by_symbol = {}
    for symbol in symbols:
        inst = instrument_cache.get(symbol)
        if inst is None:
            get_instrument = getattr(engine, "_get_instrument", None)
            if callable(get_instrument):
                try:
                    inst = await get_instrument(symbol, exchange)
                except Exception as exc:
                    logger.debug("Quote instrument resolution skipped for %s: %s", symbol, exc)
                    inst = None
        if inst is None:
            continue
        instruments.append(inst)
        instrument_by_symbol[symbol] = inst

    if not instruments:
        return {}

    try:
        quotes = await broker.get_quote(instruments)
    except Exception as exc:
        logger.warning("Universe quote fetch failed for replay selection: %s", exc)
        return {}

    latest: dict[str, dict] = {}
    for symbol, quote in (quotes or {}).items():
        ltp = float(getattr(quote, "ltp", 0) or 0)
        if ltp <= 0:
            continue
        latest[str(symbol).strip().upper()] = {
            "symbol": str(symbol).strip().upper(),
            "ltp": ltp,
            "instrument": instrument_by_symbol.get(str(symbol).strip().upper()),
            "quote": quote,
        }
    return latest


def _live_affordable_candidates(
    symbols: list[str],
    latest_quotes: dict[str, dict],
    budget_cap: float,
    fee_pct: float,
    slippage_pct: float,
    config: SelectorConfig,
) -> list[dict]:
    allowance_multiplier = 1.0 + max(float(fee_pct), 0.0) + max(float(slippage_pct), 0.0) + 0.002
    affordable: list[dict] = []
    for position, symbol in enumerate(symbols):
        quote = latest_quotes.get(symbol)
        if not quote:
            continue
        ltp = float(quote["ltp"])
        if ltp < float(config.min_stock_price) or ltp > float(config.max_stock_price):
            continue
        qty = int(float(budget_cap) // (ltp * allowance_multiplier))
        if qty <= 0:
            continue
        estimated_cost = round(qty * ltp * allowance_multiplier, 2)
        affordable.append({
            "symbol": symbol,
            "ltp": round(ltp, 2),
            "estimated_qty": qty,
            "estimated_cost": estimated_cost,
            "budget_cap": round(float(budget_cap), 2),
            "allowance_multiplier": round(allowance_multiplier, 6),
            "reason": f"Affordable from live universe quote @ ₹{ltp:,.2f}",
            "live_quote_available": True,
            "live_universe_rank": position + 1,
        })
    affordable.sort(key=lambda item: (item["ltp"], item["symbol"]))
    return affordable


def _frames_from_candles(candles: list[dict]) -> dict[str, pd.DataFrame]:
    frames: dict[str, list[dict]] = {}
    for candle in candles:
        frames.setdefault(candle["symbol"], []).append(candle)
    return {symbol: pd.DataFrame(rows) for symbol, rows in frames.items()}


def _historical_affordable_candidates(
    frames: dict[str, pd.DataFrame],
    symbols: list[str],
    budget_cap: float,
    fee_pct: float,
    slippage_pct: float,
    config: SelectorConfig,
    max_auto_symbols: int,
) -> tuple[list[dict], list[dict]]:
    selector = StockSelector(SelectorConfig(
        min_stock_price=config.min_stock_price,
        max_stock_price=config.max_stock_price,
        min_avg_daily_volume=config.min_avg_daily_volume,
        min_avg_daily_turnover=config.min_avg_daily_turnover,
        max_auto_pick_symbols=max_auto_symbols,
    ))
    ranked_selection = selector.select_affordable_candidates(
        frames,
        budget_cap=budget_cap,
        max_symbols=max_auto_symbols,
        symbols=symbols,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )
    affordable: list[dict] = []
    for position, symbol in enumerate(symbols):
        frame = frames.get(symbol)
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        close_series = pd.to_numeric(frame["close"], errors="coerce").dropna()
        if close_series.empty:
            continue
        reference_close = float(close_series.iloc[-1])
        allowance_multiplier = 1.0 + max(float(fee_pct), 0.0) + max(float(slippage_pct), 0.0) + 0.002
        qty = int(float(budget_cap) // (reference_close * allowance_multiplier))
        if qty <= 0:
            continue
        estimated_cost = round(qty * reference_close * allowance_multiplier, 2)
        affordable.append({
            "symbol": symbol,
            "ltp": round(reference_close, 2),
            "estimated_qty": qty,
            "estimated_cost": estimated_cost,
            "budget_cap": round(float(budget_cap), 2),
            "allowance_multiplier": round(allowance_multiplier, 6),
            "reason": f"Affordable from historical close in requested replay window @ ₹{reference_close:,.2f}",
            "historical_price_available": True,
            "historical_universe_rank": position + 1,
            "price_source": "historical_close",
        })
    affordable.sort(key=lambda item: (item["ltp"], item["symbol"]))
    return affordable, ranked_selection

async def _fetch_candidate_history(
    candidate_symbols: list[str],
    exchange: str,
    timeframe: str,
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[dict]:
    from database.repository import HistoricalCandleRepository

    if not candidate_symbols:
        return []
    return await HistoricalCandleRepository.fetch_window(
        symbols=candidate_symbols,
        exchange=exchange,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )


async def _resolve_budget_selection(req: ReplaySelectionRequest | ReplayRunCreateRequest) -> dict:
    budget_cap = _validated_budget_cap(getattr(req, "budget_cap", None))
    raw_symbols = getattr(req, "symbols", None)
    symbols = _selector_candidate_universe(raw_symbols)
    if not raw_symbols:
        symbols = _bounded_live_quote_symbols(symbols)
    config = _selection_config()
    max_auto_symbols = int(getattr(req, "max_auto_symbols", config.max_auto_pick_symbols) or config.max_auto_pick_symbols)
    fee_pct = float(getattr(req, "fee_pct", 0.0003) or 0.0003)
    slippage_pct = float(getattr(req, "slippage_pct", 0.0005) or 0.0005)

    candidate_subset = list(symbols)
    candles = await _fetch_candidate_history(
        candidate_subset,
        req.exchange,
        req.timeframe,
        req.start_date,
        req.end_date,
    )
    data_frames = _frames_from_candles(candles)

    affordable_historical, ranked_selection = _historical_affordable_candidates(
        data_frames,
        candidate_subset,
        budget_cap,
        fee_pct,
        slippage_pct,
        config,
        max_auto_symbols,
    )
    if not affordable_historical:
        raise HTTPException(
            400,
            f"No affordable instruments found from historical prices within selected period and budget ₹{budget_cap:,.2f}.",
        )

    selected = ranked_selection or affordable_historical[:max_auto_symbols]
    selected_symbols = [item["symbol"] for item in selected]
    historical_coverage = sorted(data_frames.keys())

    recommendations = []
    historical_by_symbol = {item["symbol"]: item for item in affordable_historical}
    for rank, item in enumerate(selected, start=1):
        merged = {**historical_by_symbol.get(item["symbol"], {}), **item}
        merged["rank"] = rank
        merged["selection_source"] = "historical_ranking" if item["symbol"] in historical_coverage and ranked_selection else "historical_window"
        merged.setdefault("price_source", "historical_close")
        recommendations.append(merged)

    return {
        "selection_mode": "auto",
        "budget_cap": round(budget_cap, 2),
        "max_auto_symbols": max_auto_symbols,
        "candidate_symbols": symbols,
        "candidate_subset": candidate_subset,
        "historical_candidate_symbols": historical_coverage,
        "selected_symbols": selected_symbols,
        "recommendations": recommendations,
    }

class HistoricalBackfillRequest(BaseModel):
    symbols: list[str]
    exchange: str = "NSE"
    timeframe: str = "day"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


@app.post("/api/historical/backfill")
async def backfill_history(req: HistoricalBackfillRequest):
    from datetime import datetime, timedelta
    from data.historical_data import BackfillRequest, backfill_historical_data

    logger.info(
        "Historical backfill API request symbols=%s exchange=%s timeframe=%s start_date=%s end_date=%s request_count=%s",
        req.symbols, req.exchange, req.timeframe, req.start_date, req.end_date, len(req.symbols),
    )

    start = (req.start_date or (datetime.utcnow() - timedelta(days=365))).date()
    end = (req.end_date or datetime.utcnow()).date()
    jobs = [BackfillRequest(symbol=s.upper(), exchange=req.exchange, timeframe=req.timeframe, start_date=start, end_date=end) for s in req.symbols]
    return await backfill_historical_data(jobs)



@app.get("/api/replay/candidate-universe")
async def replay_candidate_universe():
    """Return the candidate universe symbols for auto-pick mode."""
    symbols = _selector_candidate_universe(None)
    symbols = _bounded_live_quote_symbols(symbols)
    return {"symbols": symbols}


@app.post("/api/replay/select-symbols")
async def select_replay_symbols(req: ReplaySelectionRequest):
    return await _resolve_budget_selection(req)


@app.post("/api/replay/runs")
async def create_replay_run(req: ReplayRunCreateRequest):
    from config.loader import load_config
    from core.replay_engine import create_and_start_replay

    payload = req.model_dump()
    if req.selection_mode == "manual":
        if not req.symbols:
            raise HTTPException(400, "symbols are required")
    elif req.symbols:
        payload["symbols"] = [str(symbol).strip().upper() for symbol in req.symbols if str(symbol).strip()]
    else:
        selection = await _resolve_budget_selection(req)
        payload["symbols"] = list(selection["selected_symbols"])
        payload["selection_summary"] = selection

    replay_candles = await _fetch_candidate_history(
        payload["symbols"],
        payload["exchange"],
        payload["timeframe"],
        payload.get("start_date"),
        payload.get("end_date"),
    )
    if not replay_candles:
        raise HTTPException(400, "No historical candles found after backfill for the selected replay symbols.")

    result = await create_and_start_replay(load_config(), payload)
    if req.selection_mode == "auto":
        result["selection_summary"] = payload.get("selection_summary")
    return result


@app.get("/api/replay/runs")
async def list_replay_runs(limit: int = 20):
    rows = await ReplayRunRepository.list_runs(limit=limit)
    return {
        "runs": [
            {
                "id": r.id,
                "status": r.status,
                "config": r.config,
                "metrics": r.metrics,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ]
    }


@app.get("/api/replay/runs/{run_id}")
async def replay_run_status(run_id: str):
    row = await ReplayRunRepository.get(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    return {
        "id": row.id,
        "status": row.status,
        "config": row.config,
        "metrics": row.metrics,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


@app.get("/api/replay/runs/{run_id}/results")
async def replay_run_results(run_id: str):
    row = await ReplayRunRepository.get(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    trades = await ReplayRunRepository.get_trades(run_id)
    return {
        "summary": row.metrics or {},
        "equity_curve": row.equity_curve or [],
        "trades": [
            {
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "symbol": t.symbol,
                "exchange": t.exchange,
                "action": t.action,
                "quantity": t.quantity,
                "price": float(t.price),
                "fees": float(t.fees or 0),
                "pnl": float(t.pnl or 0),
                "rationale": t.rationale,
            }
            for t in trades
        ],
    }

# ─── ENTRYPOINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    )
    uvicorn.run(
        "core.server:app",
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", 8000)),
        reload=False,
    )
