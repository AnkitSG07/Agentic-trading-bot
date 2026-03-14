"""Historical NSE/BSE candle ingestion utilities."""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from typing import Iterable

import requests

from config.loader import load_config

logger = logging.getLogger("data.historical")

NSE_ROOT = "https://www.nseindia.com"
NSE_HISTORY_API = NSE_ROOT + "/api/historical/cm/equity"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
NSE_WARMUP_URLS = [
    "https://www.nseindia.com/",
    "https://www.nseindia.com/market-data/live-equity-market",
]
YAHOO_CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS"

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


@dataclass
class BackfillRequest:
    symbol: str
    exchange: str = "NSE"
    timeframe: str = "day"
    start_date: date = date.today() - timedelta(days=365)
    end_date: date = date.today()


@dataclass
class FetchMeta:
    provider: str
    attempts: int
    used_fallback: bool = False


class NSEHistoricalFetcher:
    """Fetch daily candles from NSE historical API with retries and optional fallback."""

    def __init__(self, *, allow_fallback: bool = False, max_attempts: int = 5, base_delay_seconds: float = 1.0) -> None:
        self._allow_fallback = allow_fallback
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._user_agents = list(DEFAULT_USER_AGENTS)
        self._ua_index = 0
        self._session = self._new_session()

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self._headers_for_current_ua())
        return session

    def _headers_for_current_ua(self) -> dict:
        headers = dict(DEFAULT_HEADERS)
        headers["User-Agent"] = self._user_agents[self._ua_index]
        return headers

    def _rotate_user_agent(self) -> None:
        self._ua_index = (self._ua_index + 1) % len(self._user_agents)
        self._session.headers.update(self._headers_for_current_ua())

    def _refresh_session(self) -> None:
        self._session.close()
        self._session = self._new_session()

    def _warmup(self) -> None:
        for url in NSE_WARMUP_URLS:
            resp = self._session.get(url, timeout=20)
            resp.raise_for_status()

    def _parse_nse_payload(self, symbol: str, payload: dict) -> list[dict]:
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

    def _fetch_from_nse(self, symbol: str, start: date, end: date) -> list[dict]:
        params = {
            "symbol": symbol.upper(),
            "series": '["EQ"]',
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
        }
        response = self._session.get(NSE_HISTORY_API, params=params, timeout=30)
        response.raise_for_status()
        return self._parse_nse_payload(symbol=symbol, payload=response.json())

    def _fetch_nse_with_retries(self, symbol: str, start: date, end: date) -> tuple[list[dict], int]:
        retryable = {
            HTTPStatus.FORBIDDEN,
            HTTPStatus.TOO_MANY_REQUESTS,
            HTTPStatus.BAD_GATEWAY,
            HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.GATEWAY_TIMEOUT,
            HTTPStatus.INTERNAL_SERVER_ERROR,
        }
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                if attempt == 1:
                    self._warmup()
                candles = self._fetch_from_nse(symbol=symbol, start=start, end=end)
                logger.info(
                    "Historical backfill succeeded symbol=%s provider=nse attempt=%s candles=%s",
                    symbol.upper(),
                    attempt,
                    len(candles),
                )
                return candles, attempt
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                last_error = exc
                should_retry = status_code in retryable and attempt < self._max_attempts
                logger.warning(
                    "Historical fetch failed symbol=%s provider=nse attempt=%s/%s status=%s retry=%s cause=%s",
                    symbol.upper(),
                    attempt,
                    self._max_attempts,
                    status_code,
                    should_retry,
                    exc,
                )
                if not should_retry:
                    break
                self._rotate_user_agent()
                if attempt >= 2:
                    self._refresh_session()
                self._warmup()
                delay = self._base_delay_seconds * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.info(
                    "Historical retry scheduled symbol=%s provider=nse next_attempt=%s delay_seconds=%.2f ua_index=%s",
                    symbol.upper(),
                    attempt + 1,
                    delay,
                    self._ua_index,
                )
                time_sleep = max(delay, 0)
                if time_sleep:
                    time.sleep(time_sleep)
            except requests.RequestException as exc:
                last_error = exc
                should_retry = attempt < self._max_attempts
                logger.warning(
                    "Historical fetch network error symbol=%s provider=nse attempt=%s/%s retry=%s cause=%s",
                    symbol.upper(),
                    attempt,
                    self._max_attempts,
                    should_retry,
                    exc,
                )
                if not should_retry:
                    break
                self._refresh_session()
                self._warmup()
                delay = self._base_delay_seconds * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(delay)
        raise RuntimeError(f"nse fetch failed after {self._max_attempts} attempts: {last_error}")

    def _fetch_from_yahoo(self, symbol: str, start: date, end: date) -> list[dict]:
        params = {
            "period1": int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()),
            "period2": int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()),
            "interval": "1d",
            "includePrePost": "false",
            "events": "div,splits",
        }
        url = YAHOO_CHART_API.format(symbol=symbol.upper())
        response = self._session.get(url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        chart = (payload.get("chart") or {}).get("result") or []
        if not chart:
            return []
        result = chart[0]
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        candles: list[dict] = []
        for i, ts in enumerate(timestamps):
            if i >= len(opens):
                continue
            o = opens[i]
            h = highs[i] if i < len(highs) else None
            l = lows[i] if i < len(lows) else None
            c = closes[i] if i < len(closes) else None
            v = volumes[i] if i < len(volumes) else 0
            if None in (o, h, l, c):
                continue
            candles.append(
                {
                    "symbol": symbol.upper(),
                    "exchange": "NSE",
                    "timeframe": "day",
                    "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": int(v or 0),
                }
            )
        return sorted(candles, key=lambda x: x["timestamp"])

    def fetch_daily_with_meta(self, symbol: str, start: date, end: date) -> tuple[list[dict], FetchMeta]:
        try:
            candles, attempts = self._fetch_nse_with_retries(symbol=symbol, start=start, end=end)
            return candles, FetchMeta(provider="nse", attempts=attempts, used_fallback=False)
        except Exception as nse_error:
            logger.error("Historical fetch exhausted symbol=%s provider=nse cause=%s", symbol.upper(), nse_error)
            if not self._allow_fallback:
                raise RuntimeError(f"provider=nse error={nse_error}") from nse_error
            try:
                candles = self._fetch_from_yahoo(symbol=symbol, start=start, end=end)
            except Exception as fallback_error:
                logger.error(
                    "Historical fallback failed symbol=%s provider=yahoo cause=%s prior_provider=nse prior_cause=%s",
                    symbol.upper(),
                    fallback_error,
                    nse_error,
                )
                raise RuntimeError(
                    f"provider=yahoo error={fallback_error}; previous_provider=nse previous_error={nse_error}"
                ) from fallback_error
            logger.warning(
                "Historical fetch fallback symbol=%s provider=yahoo candles=%s cause=%s",
                symbol.upper(),
                len(candles),
                nse_error,
            )
            return candles, FetchMeta(provider="yahoo", attempts=self._max_attempts, used_fallback=True)

    def fetch_daily(self, symbol: str, start: date, end: date) -> list[dict]:
        candles, _ = self.fetch_daily_with_meta(symbol=symbol, start=start, end=end)
        return candles


async def backfill_historical_data(requests: Iterable[BackfillRequest]) -> dict:
    from database.repository import HistoricalCandleRepository

    cfg = load_config().get("historical", {})
    fetcher = NSEHistoricalFetcher(
        allow_fallback=bool(cfg.get("allow_fallback", False)),
        max_attempts=int(cfg.get("max_attempts", 5)),
        base_delay_seconds=float(cfg.get("base_delay_seconds", 1.0)),
    )
    max_attempts = int(cfg.get("max_attempts", 5))
    total = 0
    failures: list[dict] = []
    metrics = {
        "symbols_total": 0,
        "symbols_success": 0,
        "symbols_failed": 0,
        "provider_nse_success": 0,
        "provider_yahoo_success": 0,
        "symbols_retried": 0,
        "symbols_fallback_used": 0,
    }
    for req in requests:
        metrics["symbols_total"] += 1
        if req.timeframe != "day":
            metrics["symbols_failed"] += 1
            failures.append({"symbol": req.symbol, "error": "only day timeframe currently supported"})
            continue
        try:
            candles, meta = await asyncio.to_thread(fetcher.fetch_daily_with_meta, req.symbol, req.start_date, req.end_date)
            await HistoricalCandleRepository.upsert_many(candles)
            total += len(candles)
            metrics["symbols_success"] += 1
            if meta.provider == "yahoo":
                metrics["provider_yahoo_success"] += 1
            else:
                metrics["provider_nse_success"] += 1
            if meta.attempts > 1:
                metrics["symbols_retried"] += 1
            if meta.used_fallback:
                metrics["symbols_fallback_used"] += 1
        except Exception as exc:
            logger.warning("Backfill failed symbol=%s error=%s", req.symbol, exc)
            metrics["symbols_failed"] += 1
            failures.append({"symbol": req.symbol, "error": str(exc)})
    logger.info(
        "Historical backfill summary inserted=%s failures=%s symbols_total=%s symbols_success=%s symbols_failed=%s nse_success=%s yahoo_success=%s symbols_retried=%s symbols_fallback_used=%s max_attempts=%s",
        total,
        len(failures),
        metrics["symbols_total"],
        metrics["symbols_success"],
        metrics["symbols_failed"],
        metrics["provider_nse_success"],
        metrics["provider_yahoo_success"],
        metrics["symbols_retried"],
        metrics["symbols_fallback_used"],
        max_attempts,
    )
    return {"inserted": total, "failures": failures, "metrics": metrics}
