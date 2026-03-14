from datetime import date
import asyncio
import sys
import types
from unittest import TestCase
from unittest.mock import AsyncMock, patch

import requests

from data.historical_data import NSEHistoricalFetcher


class HistoricalFetcherTests(TestCase):
    def test_retries_403_then_succeeds(self):
        fetcher = NSEHistoricalFetcher(max_attempts=3, base_delay_seconds=0)
        response = requests.Response()
        response.status_code = 403
        http_error = requests.HTTPError("forbidden", response=response)

        with (
            patch.object(fetcher, "_warmup") as warmup,
            patch.object(fetcher, "_rotate_user_agent") as rotate,
            patch.object(fetcher, "_fetch_from_nse", side_effect=[http_error, [{"timestamp": 1}]]) as fetch_nse,
            patch("data.historical_data.random.uniform", return_value=0),
            patch("data.historical_data.time.sleep") as _sleep,
        ):
            candles, attempts = fetcher._fetch_nse_with_retries("RELIANCE", date(2024, 1, 1), date(2024, 1, 31))

        self.assertEqual(candles, [{"timestamp": 1}])
        self.assertEqual(attempts, 2)
        self.assertEqual(fetch_nse.call_count, 2)
        self.assertEqual(warmup.call_count, 2)
        rotate.assert_called_once()

    def test_uses_fallback_when_enabled(self):
        fetcher = NSEHistoricalFetcher(allow_fallback=True)
        yahoo_candles = [{"symbol": "TCS", "timestamp": 1}]

        with (
            patch.object(fetcher, "_fetch_nse_with_retries", side_effect=RuntimeError("nse blocked")),
            patch.object(fetcher, "_fetch_from_yahoo", return_value=yahoo_candles) as fetch_yahoo,
        ):
            candles = fetcher.fetch_daily("TCS", date(2024, 1, 1), date(2024, 1, 31))

        self.assertEqual(candles, yahoo_candles)
        fetch_yahoo.assert_called_once()


    def test_raises_with_yahoo_provider_when_fallback_fails(self):
        fetcher = NSEHistoricalFetcher(allow_fallback=True)

        with (
            patch.object(fetcher, "_fetch_nse_with_retries", side_effect=RuntimeError("nse blocked")),
            patch.object(fetcher, "_fetch_from_yahoo", side_effect=RuntimeError("yahoo blocked")),
        ):
            with self.assertRaises(RuntimeError) as exc:
                fetcher.fetch_daily("SBIN", date(2024, 1, 1), date(2024, 1, 31))

        self.assertIn("provider=yahoo", str(exc.exception))
        self.assertIn("previous_provider=nse", str(exc.exception))

    def test_raises_when_fallback_disabled(self):
        fetcher = NSEHistoricalFetcher(allow_fallback=False)

        with patch.object(fetcher, "_fetch_nse_with_retries", side_effect=RuntimeError("nse blocked")):
            with self.assertRaises(RuntimeError) as exc:
                fetcher.fetch_daily("INFY", date(2024, 1, 1), date(2024, 1, 31))

        self.assertIn("provider=nse", str(exc.exception))

    def test_backfill_metrics_include_fallback_usage(self):
        from data.historical_data import BackfillRequest, FetchMeta, backfill_historical_data

        requests_payload = [BackfillRequest(symbol="INFY")]
        fake_repo_module = types.ModuleType("database.repository")
        upsert_many = AsyncMock()

        class _FakeHistoricalCandleRepository:
            pass

        _FakeHistoricalCandleRepository.upsert_many = upsert_many
        fake_repo_module.HistoricalCandleRepository = _FakeHistoricalCandleRepository

        with (
            patch("data.historical_data.load_config", return_value={"historical": {"max_attempts": 5, "base_delay_seconds": 0.0, "allow_fallback": True}}),
            patch.object(NSEHistoricalFetcher, "fetch_daily_with_meta", return_value=([{"timestamp": 1}], FetchMeta(provider="yahoo", attempts=5, used_fallback=True))),
            patch.dict(sys.modules, {"database.repository": fake_repo_module}),
        ):
            result = asyncio.run(backfill_historical_data(requests_payload))

        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["metrics"]["provider_yahoo_success"], 1)
        self.assertEqual(result["metrics"]["symbols_fallback_used"], 1)
        upsert_many.assert_awaited_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
