import importlib
import sys
import types
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch


def _install_fake_kiteconnect_module():
    fake = types.ModuleType("kiteconnect")

    class _FakeKiteConnect:
        ORDER_TYPE_MARKET = "MARKET"
        ORDER_TYPE_LIMIT = "LIMIT"
        ORDER_TYPE_SL = "SL"
        ORDER_TYPE_SLM = "SLM"
        PRODUCT_CNC = "CNC"
        PRODUCT_MIS = "MIS"
        PRODUCT_NRML = "NRML"
        TRANSACTION_TYPE_BUY = "BUY"
        TRANSACTION_TYPE_SELL = "SELL"

        def __init__(self, *args, **kwargs):
            pass

    class _FakeKiteTicker:
        def __init__(self, *args, **kwargs):
            pass

    fake.KiteConnect = _FakeKiteConnect
    fake.KiteTicker = _FakeKiteTicker
    return fake


def _install_fake_pyotp_module():
    fake = types.ModuleType("pyotp")

    class _FakeTOTP:
        def __init__(self, *args, **kwargs):
            pass

        def now(self):
            return "000000"

    fake.TOTP = _FakeTOTP
    return fake


with patch.dict(
    sys.modules,
    {
        "kiteconnect": _install_fake_kiteconnect_module(),
        "pyotp": _install_fake_pyotp_module(),
    },
):
    zerodha_adapter = importlib.import_module("brokers.zerodha.adapter")

from brokers.base import Exchange, Instrument, InstrumentType


class ZerodhaAdapterQuoteTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.broker = zerodha_adapter.ZerodhaBroker(
            {
                "api_key": "key",
                "api_secret": "secret",
                "user_id": "user",
            }
        )
        self.instruments = [
            Instrument(
                symbol=f"SYM{i}",
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.EQ,
                instrument_token=str(i),
            )
            for i in range(205)
        ]

    async def test_get_quote_batches_large_requests(self):
        seen_batches = []

        def quote(symbols):
            seen_batches.append(list(symbols))
            return {
                symbol: {
                    "last_price": 100 + idx,
                    "ohlc": {"open": 90, "high": 110, "low": 80, "close": 95},
                    "volume": 1000,
                    "oi": 10,
                    "depth": {"buy": [{"price": 99}], "sell": [{"price": 101}]},
                }
                for idx, symbol in enumerate(symbols)
            }

        self.broker.kite = types.SimpleNamespace(quote=quote)

        quotes = await self.broker.get_quote(self.instruments)

        self.assertEqual(len(seen_batches), 3)
        self.assertEqual([len(batch) for batch in seen_batches], [100, 100, 5])
        self.assertEqual(len(quotes), 205)
        self.assertIn("SYM0", quotes)
        self.assertIn("SYM204", quotes)

    async def test_get_quote_returns_partial_quotes_when_edge_blocked(self):
        calls = 0

        def quote(symbols):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise Exception(
                    "Unknown Content-Type (text/html; charset=UTF-8) with response: "
                    "Just a moment... Enable JavaScript and cookies to continue"
                )
            return {
                symbol: {
                    "last_price": 100,
                    "ohlc": {"open": 90, "high": 110, "low": 80, "close": 95},
                    "volume": 1000,
                    "oi": 10,
                    "depth": {"buy": [{"price": 99}], "sell": [{"price": 101}]},
                }
                for symbol in symbols
            }

        self.broker.kite = types.SimpleNamespace(quote=quote)

        with self.assertLogs("broker.zerodha", level="ERROR") as logs:
            quotes = await self.broker.get_quote(self.instruments)

        self.assertEqual(calls, 2)
        self.assertEqual(len(quotes), 100)
        self.assertIn("blocked by upstream edge protection", "\n".join(logs.output))


if __name__ == "__main__":
    import unittest

    unittest.main()
