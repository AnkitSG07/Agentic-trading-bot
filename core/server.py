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
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.engine import get_engine, set_engine, TradingEngine
from database.repository import (
    AgentDecisionRepository, DailySummaryRepository,
    PositionRepository, RiskEventRepository, TradeRepository,
)

logger = logging.getLogger("api")
ws_clients: list[WebSocket] = []


def _engine_state_file() -> Path:
    configured = os.getenv("ENGINE_STATE_FILE", "runtime/engine_state.json").strip()
    return Path(configured)


def _persist_engine_state(autostart: bool, mode: str = "paper") -> None:
    """Persist desired engine start mode so API restarts can recover gracefully."""
    path = _engine_state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "autostart": bool(autostart),
            "mode": mode,
            "updated_at": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Unable to persist engine state to {path}: {e}")


def _load_engine_state() -> dict:
    path = _engine_state_file()
    if not path.exists():
        return {"autostart": False, "mode": "paper"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "autostart": bool(data.get("autostart", False)),
            "mode": str(data.get("mode", "paper") or "paper"),
        }
    except Exception as e:
        logger.warning(f"Unable to load engine state from {path}: {e}")
        return {"autostart": False, "mode": "paper"}


async def _start_engine_task(mode: str) -> None:
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
        asyncio.create_task(_start_engine_task(mode))

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
        "kill_switch": engine.risk._kill_switch if engine else False,
    }


# ─── ENGINE CONTROL ──────────────────────────────────────────────────────────

@app.post("/api/engine/start")
async def start_engine(req: EngineStartRequest, background_tasks: BackgroundTasks):
    """Start the trading engine in the background."""
    engine = get_engine()
    if engine and engine._running:
        raise HTTPException(400, "Engine already running")

    _persist_engine_state(autostart=True, mode=req.mode)
    background_tasks.add_task(_start_engine_task, req.mode)
    return {"status": "starting", "mode": req.mode}


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
    return {
        "running": engine._running,
        "broker": engine._primary_broker_name,
        "positions": len(engine.tracker.get_all()),
        "kill_switch": engine.risk._kill_switch,
        "trading_allowed": engine.risk.is_trading_allowed,
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
                    positions = await engine.primary_broker.get_positions()
                    funds = await engine.primary_broker.get_funds()
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
                            sym: data.get("last_price") or data.get("ltp")
                            for sym, data in engine._tick_data.items()
                            if (data.get("last_price") or data.get("ltp")) is not None
                        },
                        "options_chain": engine._latest_options_chain,
                        "watchlist": engine._latest_watchlist,
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
