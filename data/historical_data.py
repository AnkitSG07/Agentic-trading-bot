"""Historical NSE/BSE candle ingestion utilities."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

logger = logging.getLogger("data.historical")

NSE_ROOT = "https://www.nseindia.com"
NSE_HISTORY_API = NSE_ROOT + "/api/historical/cm/equity"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


@dataclass
class BackfillRequest:
    symbol: str
    exchange: str = "NSE"
    timeframe: str = "day"
    start_date: date = date.today() - timedelta(days=365)
    end_date: date = date.today()


class NSEHistoricalFetcher:
    """Fetch daily candles from NSE historical API using cookie warmup."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def _warmup(self) -> None:
        req = urllib.request.Request(NSE_ROOT, headers=DEFAULT_HEADERS)
        self._opener.open(req, timeout=20).read(32)

    def fetch_daily(self, symbol: str, start: date, end: date) -> list[dict]:
        self._warmup()
        params = {
            "symbol": symbol.upper(),
            "series": '["EQ"]',
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
        }
        url = f"{NSE_HISTORY_API}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        raw = self._opener.open(req, timeout=30).read().decode("utf-8", errors="ignore")
        payload = json.loads(raw)
        rows = payload.get("data", []) or []
        candles: list[dict] = []
        for row in rows:
            ts = datetime.strptime(row["CH_TIMESTAMP"], "%d-%b-%Y").replace(tzinfo=timezone.utc)
            candles.append(
                {
                    "symbol": symbol.upper(),
                    "exchange": "NSE",
                    "timeframe": "day",
                    "timestamp": ts,
                    "open": float(row.get("CH_OPENING_PRICE") or 0),
                    "high": float(row.get("CH_TRADE_HIGH_PRICE") or 0),
                    "low": float(row.get("CH_TRADE_LOW_PRICE") or 0),
                    "close": float(row.get("CH_CLOSING_PRICE") or 0),
                    "volume": int(float(row.get("CH_TOT_TRADED_QTY") or 0)),
                }
            )
        return sorted(candles, key=lambda x: x["timestamp"])


async def backfill_historical_data(requests: Iterable[BackfillRequest]) -> dict:
    from database.repository import HistoricalCandleRepository

    fetcher = NSEHistoricalFetcher()
    total = 0
    failures: list[dict] = []
    for req in requests:
        if req.timeframe != "day":
            failures.append({"symbol": req.symbol, "error": "only day timeframe currently supported"})
            continue
        try:
            candles = await asyncio.to_thread(fetcher.fetch_daily, req.symbol, req.start_date, req.end_date)
            await HistoricalCandleRepository.upsert_many(candles)
            total += len(candles)
        except Exception as exc:
            logger.warning("Backfill failed for %s: %s", req.symbol, exc)
            failures.append({"symbol": req.symbol, "error": str(exc)})
    return {"inserted": total, "failures": failures}
