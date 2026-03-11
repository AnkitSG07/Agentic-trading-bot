"""
Dhan Broker Adapter
Implements the BaseBroker interface for Dhan's API.

Prerequisites:
    pip install dhanhq
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from dhanhq import dhanhq, marketfeed

from brokers.base import (
    BaseBroker, Exchange, Funds, Holding, Instrument, InstrumentType,
    OHLCV, Order, OrderSide, OrderStatus, OrderType, Position, ProductType, Quote,
)

logger = logging.getLogger("broker.dhan")


# ─── TYPE MAPS ───────────────────────────────────────────────────────────────

DHAN_EXCHANGE_MAP = {
    Exchange.NSE: "NSE_EQ",
    Exchange.BSE: "BSE_EQ",
    Exchange.NFO: "NSE_FNO",
    Exchange.MCX: "MCX_COMM",
    Exchange.CDS: "NSE_CURRENCY",
}

def _dhan_const(*names: str, default=None):
    """Read constant from dhanhq module across SDK versions."""
    for name in names:
        if hasattr(dhanhq, name):
            return getattr(dhanhq, name)
    if default is not None:
        return default
    raise AttributeError(f"dhanhq has none of constants: {', '.join(names)}")


DHAN_ORDER_TYPE_MAP = {
    OrderType.MARKET: _dhan_const("MARKET", default="MARKET"),
    OrderType.LIMIT: _dhan_const("LIMIT", default="LIMIT"),
    # Different dhanhq versions expose these as STOP_LOSS/STOP_LOSS_MARKET
    # or SL/SLM respectively.
    OrderType.SL: _dhan_const("STOP_LOSS", "SL", default="SL"),
    OrderType.SL_M: _dhan_const("STOP_LOSS_MARKET", "SLM", default="SLM"),
}

DHAN_PRODUCT_MAP = {
    ProductType.CNC: dhanhq.CNC,
    ProductType.MIS: dhanhq.INTRA,
    ProductType.NRML: dhanhq.MARGIN,
}

DHAN_SIDE_MAP = {
    OrderSide.BUY: dhanhq.BUY,
    OrderSide.SELL: dhanhq.SELL,
}

DHAN_INTERVAL_MAP = {
    "minute": "1",
    "5minute": "5",
    "15minute": "15",
    "30minute": "30",
    "60minute": "60",
    "day": "D",
}


class DhanBroker(BaseBroker):
    """
    Full Dhan API implementation.

    Features:
    - Token-based auth (no daily re-login needed if token valid)
    - Real-time WebSocket feed via DhanHQ marketfeed
    - NSE/BSE equity + NSE F&O support
    - Options chain lookups
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.client_id = config["client_id"]
        self.access_token = config["access_token"]
        self.dhan: Optional[dhanhq] = None
        self._ws_feed = None
        self._tick_callbacks: dict[str, list[Callable]] = {}

    # ── Authentication ───────────────────────────────────────────────────────

    async def login(self) -> bool:
        """
        Dhan uses long-lived access tokens (generated from the web portal).
        No TOTP needed. Token is set in .env.
        """
        try:
            self.dhan = dhanhq(self.client_id, self.access_token)
            # Verify token by fetching fund limits
            resp = await asyncio.to_thread(self.dhan.get_fund_limits)
            if resp.get("status") == "success":
                self.is_connected = True
                logger.info(f"✅ Dhan login successful | Client: {self.client_id}")
                return True
            else:
                logger.error(f"Dhan auth failed: {resp}")
                return False
        except Exception as e:
            logger.error(f"❌ Dhan login error: {e}")
            return False

    async def logout(self) -> bool:
        if self._ws_feed:
            await self._ws_feed.disconnect()
        self.is_connected = False
        logger.info("Dhan session closed")
        return True

    async def refresh_session(self) -> bool:
        """Dhan tokens are long-lived; this just re-validates."""
        return await self.login()

    # ── Market Data ──────────────────────────────────────────────────────────

    async def get_quote(self, instruments: list[Instrument]) -> dict[str, Quote]:
        quotes = {}
        try:
            for inst in instruments:
                resp = await asyncio.to_thread(
                    self.dhan.intraday_minute_data,
                    security_id=inst.instrument_token,
                    exchange_segment=DHAN_EXCHANGE_MAP.get(inst.exchange, "NSE_EQ"),
                    instrument_type="EQUITY",
                )
                if resp.get("status") == "success" and resp.get("data"):
                    candles = resp["data"]
                    last = candles[-1] if candles else {}
                    quotes[inst.symbol] = Quote(
                        instrument=inst,
                        ltp=Decimal(str(last.get("close", 0))),
                        open=Decimal(str(last.get("open", 0))),
                        high=Decimal(str(last.get("high", 0))),
                        low=Decimal(str(last.get("low", 0))),
                        close=Decimal(str(last.get("close", 0))),
                        volume=last.get("volume", 0),
                    )
        except Exception as e:
            logger.error(f"get_quote error: {e}")
        return quotes

    async def get_ohlcv(
        self, instrument: Instrument, interval: str,
        from_date: datetime, to_date: datetime
    ) -> list[OHLCV]:
        try:
            dhan_interval = DHAN_INTERVAL_MAP.get(interval, "D")
            if dhan_interval == "D":
                resp = await asyncio.to_thread(
                    self.dhan.historical_daily_data,
                    security_id=instrument.instrument_token,
                    exchange_segment=DHAN_EXCHANGE_MAP.get(instrument.exchange, "NSE_EQ"),
                    instrument_type="EQUITY",
                    expiry_code=0,
                    from_date=from_date.strftime("%Y-%m-%d"),
                    to_date=to_date.strftime("%Y-%m-%d"),
                )
            else:
                resp = await asyncio.to_thread(
                    self.dhan.intraday_minute_data,
                    security_id=instrument.instrument_token,
                    exchange_segment=DHAN_EXCHANGE_MAP.get(instrument.exchange, "NSE_EQ"),
                    instrument_type="EQUITY",
                    interval=dhan_interval,
                    from_date=from_date.strftime("%Y-%m-%d"),
                    to_date=to_date.strftime("%Y-%m-%d"),
                )

            if resp.get("status") != "success":
                return []

            return [
                OHLCV(
                    timestamp=datetime.strptime(c["start_Time"], "%Y-%m-%d %H:%M:%S"),
                    open=Decimal(str(c["open"])),
                    high=Decimal(str(c["high"])),
                    low=Decimal(str(c["low"])),
                    close=Decimal(str(c["close"])),
                    volume=int(c.get("volume", 0)),
                )
                for c in resp.get("data", [])
            ]
        except Exception as e:
            logger.error(f"get_ohlcv error: {e}")
            return []

    async def get_instruments(self, exchange: Exchange) -> list[Instrument]:
        """
        Dhan provides instrument CSVs. Download and parse them.
        https://images.dhan.co/api-data/api-scrip-master.csv
        """
        import csv, io
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("https://images.dhan.co/api-data/api-scrip-master.csv")
                text = resp.text

            reader = csv.DictReader(io.StringIO(text))
            ex_filter = DHAN_EXCHANGE_MAP.get(exchange, "NSE_EQ")

            instruments = []
            for row in reader:
                if row.get("SEM_EXM_EXCH_ID") != ex_filter.split("_")[0]:
                    continue
                try:
                    itype_str = row.get("SEM_INSTRUMENT_NAME", "ES")
                    if "CE" in itype_str:
                        itype = InstrumentType.CE
                    elif "PE" in itype_str:
                        itype = InstrumentType.PE
                    elif "FUT" in itype_str:
                        itype = InstrumentType.FUT
                    else:
                        itype = InstrumentType.EQ

                    instruments.append(Instrument(
                        symbol=row.get("SEM_TRADING_SYMBOL", ""),
                        exchange=exchange,
                        instrument_type=itype,
                        instrument_token=row.get("SEM_SMST_SECURITY_ID", ""),
                        lot_size=int(row.get("SEM_LOT_UNITS", 1) or 1),
                        tick_size=Decimal(str(row.get("SEM_TICK_SIZE", "0.05") or "0.05")),
                    ))
                except Exception:
                    continue

            return instruments
        except Exception as e:
            logger.error(f"get_instruments error: {e}")
            return []

    async def get_option_chain(self, underlying: str, expiry: datetime) -> list[Instrument]:
        instruments = await self.get_instruments(Exchange.NFO)
        return [
            inst for inst in instruments
            if underlying in inst.symbol
            and inst.instrument_type in (InstrumentType.CE, InstrumentType.PE)
        ]

    # ── Order Management ─────────────────────────────────────────────────────

    async def place_order(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        product: ProductType,
        price: Optional[Decimal] = None,
        trigger_price: Optional[Decimal] = None,
        tag: Optional[str] = None,
    ) -> Order:
        try:
            resp = await asyncio.to_thread(
                self.dhan.place_order,
                security_id=instrument.instrument_token,
                exchange_segment=DHAN_EXCHANGE_MAP.get(instrument.exchange, "NSE_EQ"),
                transaction_type=DHAN_SIDE_MAP[side],
                quantity=quantity,
                order_type=DHAN_ORDER_TYPE_MAP[order_type],
                product_type=DHAN_PRODUCT_MAP.get(product, dhanhq.INTRA),
                price=float(price) if price else 0,
                trigger_price=float(trigger_price) if trigger_price else 0,
                tag=tag or "AGENT",
            )

            if resp.get("status") != "success":
                raise Exception(f"Dhan order rejected: {resp}")

            order_id = str(resp["data"]["orderId"])
            order = Order(
                order_id=order_id,
                broker_order_id=order_id,
                instrument=instrument,
                side=side,
                order_type=order_type,
                product=product,
                quantity=quantity,
                price=price,
                trigger_price=trigger_price,
                status=OrderStatus.PENDING,
                tag=tag,
            )
            logger.info(f"📝 Dhan order placed: {side.value} {quantity} {instrument.symbol} [{order_id}]")
            return order

        except Exception as e:
            logger.error(f"place_order error: {e}")
            raise

    async def modify_order(
        self,
        order_id: str,
        quantity: Optional[int] = None,
        price: Optional[Decimal] = None,
        trigger_price: Optional[Decimal] = None,
        order_type: Optional[OrderType] = None,
    ) -> Order:
        try:
            params = {"order_id": order_id}
            if quantity:
                params["quantity"] = quantity
            if price:
                params["price"] = float(price)
            if trigger_price:
                params["trigger_price"] = float(trigger_price)
            if order_type:
                params["order_type"] = DHAN_ORDER_TYPE_MAP[order_type]
            await asyncio.to_thread(self.dhan.modify_order, **params)
            return await self.get_order_status(order_id)
        except Exception as e:
            logger.error(f"modify_order error: {e}")
            raise

    async def cancel_order(self, order_id: str) -> bool:
        try:
            resp = await asyncio.to_thread(self.dhan.cancel_order, order_id)
            return resp.get("status") == "success"
        except Exception as e:
            logger.error(f"cancel_order error: {e}")
            return False

    async def get_order_history(self) -> list[Order]:
        try:
            resp = await asyncio.to_thread(self.dhan.get_order_list)
            if resp.get("status") != "success":
                return []
            return [self._parse_order(o) for o in resp.get("data", [])]
        except Exception as e:
            logger.error(f"get_order_history error: {e}")
            return []

    async def get_order_status(self, order_id: str) -> Order:
        try:
            resp = await asyncio.to_thread(self.dhan.get_order_by_id, order_id)
            if resp.get("status") == "success":
                return self._parse_order(resp["data"])
            raise ValueError(f"Order {order_id} not found")
        except Exception as e:
            logger.error(f"get_order_status error: {e}")
            raise

    # ── Portfolio ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> list[Position]:
        try:
            resp = await asyncio.to_thread(self.dhan.get_positions)
            if resp.get("status") != "success":
                return []
            positions = []
            for p in resp.get("data", []):
                qty = int(p.get("netQty", 0))
                if qty == 0:
                    continue
                inst = Instrument(
                    symbol=p["tradingSymbol"],
                    exchange=Exchange.NSE,
                    instrument_type=InstrumentType.EQ,
                    instrument_token=str(p.get("securityId", "")),
                )
                avg = Decimal(str(p.get("costPrice", 0)))
                ltp = Decimal(str(p.get("lastTradedPrice", 0)))
                pnl = Decimal(str(p.get("unrealizedProfit", 0)))
                positions.append(Position(
                    instrument=inst,
                    side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
                    quantity=abs(qty),
                    average_price=avg,
                    ltp=ltp,
                    pnl=pnl,
                    pnl_pct=float(pnl / (avg * abs(qty)) * 100) if avg else 0,
                    product=ProductType.MIS,
                    broker="dhan",
                ))
            return positions
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return []

    async def get_holdings(self) -> list[Holding]:
        try:
            resp = await asyncio.to_thread(self.dhan.get_holdings)
            if resp.get("status") != "success":
                return []
            holdings = []
            for h in resp.get("data", []):
                inst = Instrument(
                    symbol=h["tradingSymbol"],
                    exchange=Exchange.NSE,
                    instrument_type=InstrumentType.EQ,
                )
                holdings.append(Holding(
                    instrument=inst,
                    quantity=h.get("totalQty", 0),
                    average_price=Decimal(str(h.get("avgCostPrice", 0))),
                    ltp=Decimal(str(h.get("lastTradedPrice", 0))),
                    pnl=Decimal(str(h.get("unrealizedProfit", 0))),
                ))
            return holdings
        except Exception as e:
            logger.error(f"get_holdings error: {e}")
            return []

    async def get_funds(self) -> Funds:
        try:
            resp = await asyncio.to_thread(self.dhan.get_fund_limits)
            if resp.get("status") == "success":
                d = resp["data"]
                return Funds(
                    available_cash=Decimal(str(d.get("availabelBalance", 0))),
                    used_margin=Decimal(str(d.get("utilizedAmount", 0))),
                    total_balance=Decimal(str(d.get("sodLimit", 0))),
                )
            return Funds(Decimal("0"), Decimal("0"), Decimal("0"))
        except Exception as e:
            logger.error(f"get_funds error: {e}")
            return Funds(Decimal("0"), Decimal("0"), Decimal("0"))

    # ── WebSocket ─────────────────────────────────────────────────────────────

    async def subscribe_ticks(self, instruments: list[Instrument], callback: Callable) -> None:
        """Connect Dhan MarketFeed WebSocket."""
        subscription_list = [
            (inst.exchange.value, str(inst.instrument_token), marketfeed.Quote)
            for inst in instruments
            if inst.instrument_token
        ]

        for inst in instruments:
            key = inst.instrument_token
            if key not in self._tick_callbacks:
                self._tick_callbacks[key] = []
            self._tick_callbacks[key].append(callback)

        async def on_message(ws, message):
            if isinstance(message, dict):
                inst_token = str(message.get("security_id", ""))
                cbs = self._tick_callbacks.get(inst_token, [])
                for cb in cbs:
                    await cb(message)

        self._ws_feed = marketfeed.DhanFeed(
            self.client_id,
            self.access_token,
            subscription_list,
            version="v2"
        )
        
        # Explicitly assign the callback instead of using the constructor parameter
        self._ws_feed.on_message = on_message
        
        asyncio.create_task(self._ws_feed.connect())
        logger.info(f"✅ Dhan WebSocket subscribed to {len(instruments)} instruments")

    async def unsubscribe_ticks(self, instruments: list[Instrument]) -> None:
        if self._ws_feed:
            for inst in instruments:
                self._tick_callbacks.pop(inst.instrument_token, None)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_order(self, raw: dict) -> Order:
        inst = Instrument(
            symbol=raw.get("tradingSymbol", ""),
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.EQ,
        )
        status_map = {
            "TRADED": OrderStatus.COMPLETE,
            "PENDING": OrderStatus.OPEN,
            "CANCELLED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "TRANSIT": OrderStatus.PENDING,
        }
        return Order(
            order_id=str(raw.get("orderId", "")),
            broker_order_id=str(raw.get("orderId", "")),
            instrument=inst,
            side=OrderSide.BUY if raw.get("transactionType") == "BUY" else OrderSide.SELL,
            order_type=OrderType.MARKET,
            product=ProductType.MIS,
            quantity=int(raw.get("quantity", 0)),
            price=Decimal(str(raw.get("price", 0))),
            trigger_price=Decimal(str(raw.get("triggerPrice", 0))) if raw.get("triggerPrice") else None,
            status=status_map.get(raw.get("orderStatus", ""), OrderStatus.PENDING),
            filled_quantity=int(raw.get("filledQty", 0)),
            average_price=Decimal(str(raw.get("tradedPrice", 0))) if raw.get("tradedPrice") else None,
            tag=raw.get("correlationId"),
        )
