"""
Zerodha Kite Connect Broker Adapter
Implements the BaseBroker interface for Zerodha's Kite API.

Prerequisites:
    pip install kiteconnect pyotp
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import parse_qs, urlparse

import pyotp
from kiteconnect import KiteConnect, KiteTicker

from brokers.base import (
    BaseBroker, Exchange, Funds, Holding, Instrument, InstrumentType,
    OHLCV, Order, OrderSide, OrderStatus, OrderType, Position, ProductType, Quote,
)

logger = logging.getLogger("broker.zerodha")

QUOTE_BATCH_SIZE = 100
T = TypeVar("T")

# ─── TYPE MAPS ──────────────────────────────────────────────────────────────

KITE_EXCHANGE_MAP = {
    Exchange.NSE: "NSE",
    Exchange.BSE: "BSE",
    Exchange.NFO: "NFO",
    Exchange.MCX: "MCX",
    Exchange.BFO: "BFO",
    Exchange.CDS: "CDS",
}

KITE_ORDER_TYPE_MAP = {
    OrderType.MARKET: KiteConnect.ORDER_TYPE_MARKET,
    OrderType.LIMIT: KiteConnect.ORDER_TYPE_LIMIT,
    OrderType.SL: KiteConnect.ORDER_TYPE_SL,
    OrderType.SL_M: KiteConnect.ORDER_TYPE_SLM,
}

KITE_PRODUCT_MAP = {
    ProductType.CNC: KiteConnect.PRODUCT_CNC,
    ProductType.MIS: KiteConnect.PRODUCT_MIS,
    ProductType.NRML: KiteConnect.PRODUCT_NRML,
}

KITE_SIDE_MAP = {
    OrderSide.BUY: KiteConnect.TRANSACTION_TYPE_BUY,
    OrderSide.SELL: KiteConnect.TRANSACTION_TYPE_SELL,
}

INTERVAL_MAP = {
    "minute": "minute",
    "3minute": "3minute",
    "5minute": "5minute",
    "15minute": "15minute",
    "30minute": "30minute",
    "60minute": "60minute",
    "day": "day",
}


class ZerodhaBroker(BaseBroker):
    """
    Full Zerodha Kite Connect implementation.

    Features:
    - Auto-login with TOTP (no manual intervention)
    - Real-time WebSocket tick subscription
    - Full order lifecycle management
    - Options chain support
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config["api_key"]
        self.api_secret = config["api_secret"]
        self.user_id = config["user_id"]
        self.totp_secret = config.get("totp_secret")

        self.kite: Optional[KiteConnect] = None
        self.ticker: Optional[KiteTicker] = None
        self.access_token: Optional[str] = None
        self._tick_callbacks: dict[int, list[Callable]] = {}
        self._instrument_cache: dict[str, list[dict]] = {}
        self._historical_data_blocked: bool = False
        self._historical_warned: bool = False
        self._ws_blocked: bool = False
        self._ws_warned: bool = False

    # ── Authentication ───────────────────────────────────────────────────────

    async def login(self) -> bool:
        """
        Auto-login using TOTP. Saves access_token for the session.
        Zerodha tokens are valid for the trading day only.
        """
        try:
            self.kite = KiteConnect(api_key=self.api_key)
            login_url = self.kite.login_url()

            if self.totp_secret:
                # Automated TOTP login via requests
                import requests
                session = requests.Session()

                # Step 1: Get login page
                password = self.config.get("password")
                if not password:
                    raise ValueError(
                        "Zerodha password missing. Set ZERODHA_PASSWORD in .env"
                    )

                resp = session.post(
                    "https://kite.zerodha.com/api/login",
                    data={"user_id": self.user_id, "password": password},
                )
                resp.raise_for_status()
                request_id = resp.json()["data"]["request_id"]

                # Step 2: Submit TOTP
                totp = pyotp.TOTP(self.totp_secret)
                resp = session.post(
                    "https://kite.zerodha.com/api/twofa",
                    data={
                        "user_id": self.user_id,
                        "request_id": request_id,
                        "twofa_value": totp.now(),
                        "twofa_type": "totp",
                    },
                )
                resp.raise_for_status()

                # Step 3: Follow authorize redirect and extract request token
                auth_resp = session.get(login_url, allow_redirects=True)
                redirect_url = auth_resp.url or resp.url
                request_token = parse_qs(urlparse(redirect_url).query).get("request_token", [None])[0]
                if not request_token:
                    raise ValueError(
                        "Unable to extract request_token from Zerodha redirect URL. "
                        f"Final URL: {redirect_url}"
                    )
            else:
                raise ValueError(
                    "TOTP secret required for automated login. "
                    "Set ZERODHA_TOTP_SECRET in .env"
                )

            # Step 4: Generate access token
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            self.access_token = data["access_token"]
            self.kite.set_access_token(self.access_token)

            self.is_connected = True
            logger.info(f"✅ Zerodha login successful | User: {self.user_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Zerodha login failed: {e}")
            self.is_connected = False
            return False

    async def logout(self) -> bool:
        try:
            if self.kite and self.access_token:
                self.kite.invalidate_access_token()
            if self.ticker:
                self.ticker.close()
            self.is_connected = False
            logger.info("Zerodha session closed")
            return True
        except Exception as e:
            logger.error(f"Zerodha logout error: {e}")
            return False

    async def refresh_session(self) -> bool:
        """Zerodha tokens don't support refresh - full re-login needed."""
        return await self.login()

    # ── Market Data ──────────────────────────────────────────────────────────

    @staticmethod
    def _chunked(items: list[T], size: int) -> list[list[T]]:
        if size <= 0:
            return [items]
        return [items[i:i + size] for i in range(0, len(items), size)]

    @staticmethod
    def _is_edge_block_response(error: Exception) -> bool:
        message = str(error or "").lower()
        return (
            "unknown content-type" in message
            and "text/html" in message
        ) or "just a moment" in message or "enable javascript and cookies to continue" in message

    async def get_quote(self, instruments: list[Instrument]) -> dict[str, Quote]:
        """Fetch LTP + full market depth for instruments."""
        try:
            quotes = {}
            instrument_batches = self._chunked(instruments, QUOTE_BATCH_SIZE)
            if len(instrument_batches) > 1:
                logger.info(
                    "Fetching Zerodha quotes in %s batches (batch_size=%s total=%s)",
                    len(instrument_batches),
                    QUOTE_BATCH_SIZE,
                    len(instruments),
                )

            for batch_index, batch in enumerate(instrument_batches, start=1):
                kite_symbols = [
                    f"{KITE_EXCHANGE_MAP[inst.exchange]}:{inst.symbol}"
                    for inst in batch
                ]
                try:
                    raw = await asyncio.to_thread(self.kite.quote, kite_symbols)
                except Exception as batch_error:
                    if self._is_edge_block_response(batch_error):
                        logger.error(
                            "Zerodha quote request blocked by upstream edge protection "
                            "(batch=%s/%s symbols=%s). Returning partial quotes. Error: %s",
                            batch_index,
                            len(instrument_batches),
                            len(batch),
                            batch_error,
                        )
                        break
                    raise

                for inst in batch:
                    key = f"{KITE_EXCHANGE_MAP[inst.exchange]}:{inst.symbol}"
                    if key in raw:
                        d = raw[key]
                        ohlc = d.get("ohlc", {})
                        quotes[inst.symbol] = Quote(
                            instrument=inst,
                            ltp=Decimal(str(d["last_price"])),
                            open=Decimal(str(ohlc.get("open", 0))),
                            high=Decimal(str(ohlc.get("high", 0))),
                            low=Decimal(str(ohlc.get("low", 0))),
                            close=Decimal(str(ohlc.get("close", 0))),
                            volume=d.get("volume", 0),
                            oi=d.get("oi", 0),
                            bid=Decimal(str(d.get("depth", {}).get("buy", [{}])[0].get("price", 0))),
                            ask=Decimal(str(d.get("depth", {}).get("sell", [{}])[0].get("price", 0))),
                        )
            return quotes
        except Exception as e:
            logger.error(f"get_quote error: {e}")
            return {}

    async def get_ohlcv(
        self, instrument: Instrument, interval: str,
        from_date: datetime, to_date: datetime
    ) -> list[OHLCV]:
        if self._historical_data_blocked:
            return []

        try:
            token = instrument.instrument_token
            raw = await asyncio.to_thread(
                self.kite.historical_data,
                token, from_date, to_date,
                INTERVAL_MAP.get(interval, "day"),
                oi=True,
            )
            return [
                OHLCV(
                    timestamp=r["date"],
                    open=Decimal(str(r["open"])),
                    high=Decimal(str(r["high"])),
                    low=Decimal(str(r["low"])),
                    close=Decimal(str(r["close"])),
                    volume=r["volume"],
                    oi=r.get("oi", 0),
                )
                for r in raw
            ]
        except Exception as e:
            err = str(e)
            if "insufficient permission" in err.lower():
                self._historical_data_blocked = True
                if not self._historical_warned:
                    logger.warning(
                        "Historical data permission missing for Zerodha API key. "
                        "Disabling Zerodha OHLCV fetch for this session."
                    )
                    self._historical_warned = True
                return []
            logger.error(f"get_ohlcv error: {e}")
            return []

    async def get_instruments(self, exchange: Exchange) -> list[Instrument]:
        """Download and cache the full instrument list."""
        ex = KITE_EXCHANGE_MAP[exchange]
        if ex not in self._instrument_cache:
            raw = await asyncio.to_thread(self.kite.instruments, ex)
            self._instrument_cache[ex] = raw

        instruments = []
        for r in self._instrument_cache.get(ex, []):
            itype_str = r.get("instrument_type", "EQ")
            try:
                itype = InstrumentType(itype_str)
            except ValueError:
                itype = InstrumentType.EQ

            instruments.append(Instrument(
                symbol=r["tradingsymbol"],
                exchange=exchange,
                instrument_type=itype,
                instrument_token=str(r["instrument_token"]),
                lot_size=r.get("lot_size", 1),
                tick_size=Decimal(str(r.get("tick_size", 0.05))),
                expiry=r.get("expiry"),
                strike=Decimal(str(r["strike"])) if r.get("strike") else None,
                option_type=r.get("instrument_type") if itype_str in ("CE", "PE") else None,
            ))
        return instruments

    async def get_option_chain(self, underlying: str, expiry: datetime) -> list[Instrument]:
        instruments = await self.get_instruments(Exchange.NFO)
        return [
            inst for inst in instruments
            if underlying in inst.symbol
            and inst.expiry
            and inst.expiry.date() == expiry.date()
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
            params = dict(
                tradingsymbol=instrument.symbol,
                exchange=KITE_EXCHANGE_MAP[instrument.exchange],
                transaction_type=KITE_SIDE_MAP[side],
                quantity=quantity,
                order_type=KITE_ORDER_TYPE_MAP[order_type],
                product=KITE_PRODUCT_MAP.get(product, KiteConnect.PRODUCT_MIS),
                variety=KiteConnect.VARIETY_REGULAR,
                tag=tag or "AGENT",
            )
            if price:
                params["price"] = float(price)
            if trigger_price:
                params["trigger_price"] = float(trigger_price)

            order_id = await asyncio.to_thread(self.kite.place_order, **params)

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
            logger.info(f"📝 Order placed: {side.value} {quantity} {instrument.symbol} [{order_id}]")
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
            params = {"order_id": order_id, "variety": KiteConnect.VARIETY_REGULAR}
            if quantity:
                params["quantity"] = quantity
            if price:
                params["price"] = float(price)
            if trigger_price:
                params["trigger_price"] = float(trigger_price)
            if order_type:
                params["order_type"] = KITE_ORDER_TYPE_MAP[order_type]

            await asyncio.to_thread(self.kite.modify_order, **params)
            return await self.get_order_status(order_id)
        except Exception as e:
            logger.error(f"modify_order error: {e}")
            raise

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await asyncio.to_thread(
                self.kite.cancel_order,
                variety=KiteConnect.VARIETY_REGULAR,
                order_id=order_id,
            )
            logger.info(f"🚫 Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"cancel_order error: {e}")
            return False

    async def get_order_history(self) -> list[Order]:
        try:
            raw = await asyncio.to_thread(self.kite.orders)
            return [self._parse_order(o) for o in raw]
        except Exception as e:
            logger.error(f"get_order_history error: {e}")
            return []

    async def get_order_status(self, order_id: str) -> Order:
        orders = await self.get_order_history()
        for o in orders:
            if o.order_id == order_id:
                return o
        raise ValueError(f"Order {order_id} not found")

    # ── Portfolio ────────────────────────────────────────────────────────────

    async def get_positions(self) -> list[Position]:
        try:
            raw = await asyncio.to_thread(self.kite.positions)
            positions = []
            for p in raw.get("net", []):
                if p["quantity"] == 0:
                    continue
                inst = Instrument(
                    symbol=p["tradingsymbol"],
                    exchange=Exchange(p["exchange"]),
                    instrument_type=InstrumentType.EQ,
                )
                qty = p["quantity"]
                avg = Decimal(str(p["average_price"]))
                ltp = Decimal(str(p["last_price"]))
                pnl = Decimal(str(p["pnl"]))
                positions.append(Position(
                    instrument=inst,
                    side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
                    quantity=abs(qty),
                    average_price=avg,
                    ltp=ltp,
                    pnl=pnl,
                    pnl_pct=float(pnl / (avg * abs(qty)) * 100) if avg else 0,
                    product=ProductType(p.get("product", "MIS")),
                    broker="zerodha",
                ))
            return positions
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return []

    async def get_holdings(self) -> list[Holding]:
        try:
            raw = await asyncio.to_thread(self.kite.holdings)
            holdings = []
            for h in raw:
                inst = Instrument(
                    symbol=h["tradingsymbol"],
                    exchange=Exchange.NSE,
                    instrument_type=InstrumentType.EQ,
                )
                holdings.append(Holding(
                    instrument=inst,
                    quantity=h["quantity"],
                    average_price=Decimal(str(h["average_price"])),
                    ltp=Decimal(str(h["last_price"])),
                    pnl=Decimal(str(h["pnl"])),
                ))
            return holdings
        except Exception as e:
            logger.error(f"get_holdings error: {e}")
            return []

    async def get_funds(self) -> Funds:
        try:
            raw = await asyncio.to_thread(self.kite.margins)
            equity = raw.get("equity", {})
            return Funds(
                available_cash=Decimal(str(equity.get("available", {}).get("cash", 0))),
                used_margin=Decimal(str(equity.get("utilised", {}).get("debits", 0))),
                total_balance=Decimal(str(equity.get("net", 0))),
            )
        except Exception as e:
            logger.error(f"get_funds error: {e}")
            return Funds(Decimal("0"), Decimal("0"), Decimal("0"))

    # ── WebSocket ────────────────────────────────────────────────────────────

    async def subscribe_ticks(self, instruments: list[Instrument], callback: Callable) -> None:
        """Connect KiteTicker and subscribe to real-time tick feed."""
        if self._ws_blocked:
            if not self._ws_warned:
                logger.warning("Skipping Zerodha WebSocket subscription (permission blocked)")
                self._ws_warned = True
            return
            
        if not self.is_connected or not self.access_token:
            logger.warning("Zerodha WebSocket requested without active session. Refreshing session once.")
            if not await self.refresh_session():
                self._mark_ws_blocked("Session refresh failed for WebSocket subscription")
                return
                
        tokens = [int(inst.instrument_token) for inst in instruments if inst.instrument_token]

        for token in tokens:
            if token not in self._tick_callbacks:
                self._tick_callbacks[token] = []
            self._tick_callbacks[token].append(callback)

        def on_ticks(ws, ticks):
            for tick in ticks:
                cbs = self._tick_callbacks.get(tick["instrument_token"], [])
                for cb in cbs:
                    asyncio.run_coroutine_threadsafe(cb(tick), asyncio.get_event_loop())

        def on_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)
            logger.info(f"✅ WebSocket connected, subscribed to {len(tokens)} instruments")

        def on_error(ws, code, reason):
            logger.error(f"WebSocket error [{code}]: {reason}")
            reason_str = str(reason).lower()
            if "403" in reason_str or "forbidden" in reason_str:
                self._mark_ws_blocked(str(reason), ws)

        def on_close(ws, code, reason):
            logger.warning(f"WebSocket closed [{code}]: {reason}")
            reason_str = str(reason).lower()
            if "403" in reason_str or "forbidden" in reason_str:
                self._mark_ws_blocked(str(reason), ws)

        if not self.ticker:
            self.ticker = KiteTicker(self.api_key, self.access_token, reconnect=False)
            self.ticker.on_ticks = on_ticks
            self.ticker.on_connect = on_connect
            self.ticker.on_error = on_error
            self.ticker.on_close = on_close
            self.ticker.connect(threaded=True)
        else:
            self.ticker.subscribe(tokens)
            self.ticker.set_mode(self.ticker.MODE_FULL, tokens)

    def _mark_ws_blocked(self, reason: str, ws=None) -> None:
        """Disable Zerodha WS usage for this session after auth/permission failures."""
        self._ws_blocked = True
        if not self._ws_warned:
            logger.warning(
                "Zerodha market-data websocket disabled for this session: %s. "
                "Check Kite WebSocket permissions and access token validity.",
                reason,
            )
            self._ws_warned = True

        for obj in (ws, self.ticker):
            if not obj:
                continue
            for method in ("stop", "close"):
                fn = getattr(obj, method, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass

        self.ticker = None
        
    async def unsubscribe_ticks(self, instruments: list[Instrument]) -> None:
        tokens = [int(inst.instrument_token) for inst in instruments if inst.instrument_token]
        if self.ticker:
            self.ticker.unsubscribe(tokens)
        for token in tokens:
            self._tick_callbacks.pop(token, None)

    # ── Private Helpers ──────────────────────────────────────────────────────

    def _parse_order(self, raw: dict) -> Order:
        inst = Instrument(
            symbol=raw["tradingsymbol"],
            exchange=Exchange(raw["exchange"]),
            instrument_type=InstrumentType.EQ,
        )
        status_map = {
            "COMPLETE": OrderStatus.COMPLETE,
            "OPEN": OrderStatus.OPEN,
            "CANCELLED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "TRIGGER PENDING": OrderStatus.TRIGGER_PENDING,
        }
        return Order(
            order_id=raw["order_id"],
            broker_order_id=raw["order_id"],
            instrument=inst,
            side=OrderSide.BUY if raw["transaction_type"] == "BUY" else OrderSide.SELL,
            order_type=OrderType(raw["order_type"].replace("-", "_")),
            product=ProductType(raw["product"]),
            quantity=raw["quantity"],
            price=Decimal(str(raw["price"])) if raw["price"] else None,
            trigger_price=Decimal(str(raw["trigger_price"])) if raw.get("trigger_price") else None,
            status=status_map.get(raw["status"], OrderStatus.PENDING),
            filled_quantity=raw.get("filled_quantity", 0),
            average_price=Decimal(str(raw["average_price"])) if raw.get("average_price") else None,
            tag=raw.get("tag"),
            rejection_reason=raw.get("status_message"),
        )
