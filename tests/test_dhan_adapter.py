import importlib
import sys
import types
from datetime import datetime
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch


def _install_fake_dhanhq_module():
    fake = types.ModuleType("dhanhq")

    class _FakeDhanHQClient:
        CNC = "CNC"
        INTRA = "INTRA"
        MARGIN = "MARGIN"
        BUY = "BUY"
        SELL = "SELL"
        MARKET = "MARKET"
        LIMIT = "LIMIT"
        STOP_LOSS = "SL"
        STOP_LOSS_MARKET = "SLM"

        def __init__(self, *args, **kwargs):
            pass

    fake.dhanhq = _FakeDhanHQClient
    fake.marketfeed = object()
    return fake


with patch.dict(sys.modules, {"dhanhq": _install_fake_dhanhq_module()}):
    dhan_adapter = importlib.import_module("brokers.dhan.adapter")

from brokers.base import Exchange, Instrument, InstrumentType


class DhanAdapterOHLCVTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.broker = dhan_adapter.DhanBroker({"client_id": "cid", "access_token": "token"})
        self.instrument = Instrument(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.EQ,
            instrument_token="1234",
        )
        self.from_date = datetime(2024, 1, 1)
        self.to_date = datetime(2024, 1, 1, 23, 59)

    async def test_get_ohlcv_aggregates_30minute_from_15minute_without_calling_30(self):
        intraday = MagicMock(return_value={
            "status": "success",
            "data": [
                {
                    "start_Time": "2024-01-01 09:15:00",
                    "open": 100,
                    "high": 105,
                    "low": 99,
                    "close": 104,
                    "volume": 1000,
                },
                {
                    "start_Time": "2024-01-01 09:30:00",
                    "open": 104,
                    "high": 108,
                    "low": 103,
                    "close": 107,
                    "volume": 1500,
                },
                {
                    "start_Time": "2024-01-01 09:45:00",
                    "open": 107,
                    "high": 110,
                    "low": 106,
                    "close": 109,
                    "volume": 1200,
                },
                {
                    "start_Time": "2024-01-01 10:00:00",
                    "open": 109,
                    "high": 111,
                    "low": 108,
                    "close": 110,
                    "volume": 800,
                },
            ],
        })
        self.broker.dhan = types.SimpleNamespace(
            intraday_minute_data=intraday,
            historical_daily_data=MagicMock(),
        )

        candles = await self.broker.get_ohlcv(self.instrument, "30minute", self.from_date, self.to_date)

        self.assertEqual([call.kwargs["interval"] for call in intraday.call_args_list], ["15"])
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].timestamp, datetime(2024, 1, 1, 9, 15))
        self.assertEqual(candles[0].open, Decimal("100"))
        self.assertEqual(candles[0].high, Decimal("108"))
        self.assertEqual(candles[0].low, Decimal("99"))
        self.assertEqual(candles[0].close, Decimal("107"))
        self.assertEqual(candles[0].volume, 2500)
        self.assertEqual(candles[1].timestamp, datetime(2024, 1, 1, 9, 45))
        self.assertEqual(candles[1].open, Decimal("107"))
        self.assertEqual(candles[1].close, Decimal("110"))
        self.assertNotIn("30", [call.kwargs["interval"] for call in intraday.call_args_list])

    async def test_get_ohlcv_logs_requested_and_translated_interval(self):
        intraday = MagicMock(return_value={"status": "success", "data": []})
        self.broker.dhan = types.SimpleNamespace(
            intraday_minute_data=intraday,
            historical_daily_data=MagicMock(),
        )

        with self.assertLogs("broker.dhan", level="INFO") as logs:
            await self.broker.get_ohlcv(self.instrument, "30minute", self.from_date, self.to_date)

        joined = "\n".join(logs.output)
        self.assertIn("requested_interval=30minute", joined)
        self.assertIn("translated_interval=15", joined)
        self.assertEqual(intraday.call_args.kwargs["interval"], "15")

    async def test_get_ohlcv_rejects_unknown_interval_before_calling_dhan(self):
        intraday = MagicMock(return_value={"status": "success", "data": []})
        self.broker.dhan = types.SimpleNamespace(
            intraday_minute_data=intraday,
            historical_daily_data=MagicMock(),
        )

        with self.assertLogs("broker.dhan", level="ERROR") as logs:
            candles = await self.broker.get_ohlcv(self.instrument, "3minute", self.from_date, self.to_date)

        self.assertEqual(candles, [])
        intraday.assert_not_called()
        self.assertIn("requested_interval=3minute", "\n".join(logs.output))
        self.assertIn("unsupported", "\n".join(logs.output))


if __name__ == "__main__":
    import unittest

    unittest.main()
