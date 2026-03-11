"""
FastAPI Backend Server
Provides REST API + WebSocket endpoints for the React dashboard.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("api")

# ─── GLOBAL STATE ────────────────────────────────────────────────────────────

engine = None                         # TradingEngine instance
ws_clients: list[WebSocket] = []      # Connected dashboard clients


# ─── LIFESPAN ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🌐 API server starting...")
    yield
    logger.info("🌐 API server shutting down...")


# ─── APP ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AgentTrader India API",
    description="Agentic Trading Bot for Indian Markets - Zerodha + Dhan",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── MODELS ──────────────────────────────────────────────────────────────────

class EngineControlRequest(BaseModel):
    action: str                       # start | stop | pause


class ManualOrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    side: str                         # BUY | SELL
    quantity: int
    order_type: str = "MARKET"        # MARKET | LIMIT
    price: Optional[float] = None
    product: str = "MIS"
    stop_loss: Optional[float] = None
    target: Optional[float] = None


class KillSwitchRequest(BaseModel):
    override_code: str


# ─── HEALTH ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "engine_running": engine._running if engine else False,
    }


# ─── ENGINE CONTROL ──────────────────────────────────────────────────────────

@app.post("/api/engine/control")
async def control_engine(req: EngineControlRequest):
    global engine
    if req.action == "start":
        if engine and engine._running:
            raise HTTPException(400, "Engine already running")
        # In production: start engine in background task
        return {"status": "starting", "message": "Engine start initiated"}
    elif req.action == "stop":
        if engine:
            asyncio.create_task(engine.stop())
        return {"status": "stopping"}
    return {"status": "unknown_action"}


# ─── PORTFOLIO ───────────────────────────────────────────────────────────────

@app.get("/api/portfolio/positions")
async def get_positions():
    if not engine or not engine.primary_broker:
        return {"positions": [], "error": "Engine not running"}
    try:
        positions = await engine.primary_broker.get_positions()
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
            ]
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/portfolio/holdings")
async def get_holdings():
    if not engine or not engine.primary_broker:
        return {"holdings": []}
    try:
        holdings = await engine.primary_broker.get_holdings()
        return {
            "holdings": [
                {
                    "symbol": h.instrument.symbol,
                    "quantity": h.quantity,
                    "average_price": float(h.average_price),
                    "ltp": float(h.ltp),
                    "pnl": float(h.pnl),
                }
                for h in holdings
            ]
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/portfolio/funds")
async def get_funds():
    if not engine or not engine.primary_broker:
        return {"available": 0, "used": 0, "total": 0}
    try:
        funds = await engine.primary_broker.get_funds()
        return {
            "available_cash": float(funds.available_cash),
            "used_margin": float(funds.used_margin),
            "total_balance": float(funds.total_balance),
            "unrealised_pnl": float(funds.unrealised_pnl),
            "realised_pnl": float(funds.realised_pnl),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── ORDERS ──────────────────────────────────────────────────────────────────

@app.get("/api/orders")
async def get_orders():
    if not engine or not engine.primary_broker:
        return {"orders": []}
    try:
        orders = await engine.primary_broker.get_order_history()
        return {
            "orders": [
                {
                    "order_id": o.order_id,
                    "symbol": o.instrument.symbol,
                    "side": o.side.value,
                    "quantity": o.quantity,
                    "order_type": o.order_type.value,
                    "price": float(o.price) if o.price else None,
                    "status": o.status.value,
                    "filled_quantity": o.filled_quantity,
                    "average_price": float(o.average_price) if o.average_price else None,
                    "tag": o.tag,
                    "placed_at": o.placed_at.isoformat(),
                }
                for o in orders
            ]
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/orders/manual")
async def place_manual_order(req: ManualOrderRequest):
    """Place a manual order from the dashboard."""
    if not engine or not engine.primary_broker:
        raise HTTPException(503, "Engine not running")
    try:
        from brokers.base import Exchange, Instrument, InstrumentType, OrderSide, OrderType, ProductType
        from decimal import Decimal

        inst = Instrument(
            symbol=req.symbol,
            exchange=Exchange(req.exchange),
            instrument_type=InstrumentType.EQ,
        )
        order = await engine.primary_broker.place_order(
            instrument=inst,
            side=OrderSide(req.side),
            quantity=req.quantity,
            order_type=OrderType(req.order_type),
            product=ProductType(req.product),
            price=Decimal(str(req.price)) if req.price else None,
            tag="MANUAL",
        )
        return {"status": "success", "order_id": order.order_id}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: str):
    if not engine or not engine.primary_broker:
        raise HTTPException(503, "Engine not running")
    success = await engine.primary_broker.cancel_order(order_id)
    return {"status": "cancelled" if success else "error"}


# ─── RISK ────────────────────────────────────────────────────────────────────

@app.get("/api/risk/summary")
async def get_risk_summary():
    if not engine:
        return {"error": "Engine not initialized"}
    return engine.risk.get_daily_summary()


@app.post("/api/risk/kill-switch/reset")
async def reset_kill_switch(req: KillSwitchRequest):
    if not engine:
        raise HTTPException(503, "Engine not running")
    success = engine.risk.reset_kill_switch(req.override_code)
    return {"status": "reset" if success else "invalid_code"}


# ─── MARKET DATA ─────────────────────────────────────────────────────────────

@app.get("/api/market/quote/{symbol}")
async def get_quote(symbol: str, exchange: str = "NSE"):
    if not engine or not engine.primary_broker:
        raise HTTPException(503, "Engine not running")
    try:
        from brokers.base import Exchange as Ex, Instrument, InstrumentType
        inst = Instrument(symbol, Ex(exchange), InstrumentType.EQ)
        quotes = await engine.primary_broker.get_quote([inst])
        if symbol in quotes:
            q = quotes[symbol]
            return {
                "symbol": symbol,
                "ltp": float(q.ltp),
                "open": float(q.open),
                "high": float(q.high),
                "low": float(q.low),
                "close": float(q.close),
                "volume": q.volume,
                "oi": q.oi,
            }
        raise HTTPException(404, f"No quote for {symbol}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/agent/decisions")
async def get_agent_decisions():
    if not engine:
        return {"decisions": []}
    return {"decisions": engine.agent.decision_history[-20:]}


# ─── WEBSOCKET ───────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time WebSocket for the dashboard.
    Pushes tick data, PnL updates, and agent decisions.
    """
    await websocket.accept()
    ws_clients.append(websocket)
    logger.info(f"Dashboard connected. Total clients: {len(ws_clients)}")

    try:
        while True:
            # Send live data every second
            if engine and engine.primary_broker:
                try:
                    positions = await engine.primary_broker.get_positions()
                    risk_summary = engine.risk.get_daily_summary()

                    payload = {
                        "type": "update",
                        "timestamp": datetime.now().isoformat(),
                        "pnl": {
                            "realized": risk_summary.get("realized_pnl", 0),
                            "unrealized": risk_summary.get("unrealized_pnl", 0),
                            "total": risk_summary.get("total_pnl", 0),
                            "pct": risk_summary.get("daily_pnl_pct", 0),
                        },
                        "positions_count": len(positions),
                        "risk": risk_summary,
                        "tick_data": {
                            k: v.get("last_price") for k, v in
                            list(engine._tick_data.items())[:10]
                        },
                    }
                    await websocket.send_text(json.dumps(payload))
                except Exception:
                    pass

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        ws_clients.remove(websocket)
        logger.info(f"Dashboard disconnected. Clients: {len(ws_clients)}")


async def broadcast_to_dashboard(message: dict) -> None:
    """Send a message to all connected dashboard clients."""
    if not ws_clients:
        return
    text = json.dumps(message)
    disconnected = []
    for client in ws_clients:
        try:
            await client.send_text(text)
        except Exception:
            disconnected.append(client)
    for c in disconnected:
        ws_clients.remove(c)


# ─── ENTRYPOINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", 8000)),
        reload=False,
        log_level="info",
    )
