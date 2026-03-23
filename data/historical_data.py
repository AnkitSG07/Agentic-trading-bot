"""Historical NSE/BSE candle ingestion utilities.

Root cause of 403/429 errors:
  - NSE blocks all non-browser requests from cloud/datacenter IPs (403)
  - Yahoo Finance v8 API rate-limits cloud IPs heavily (429)

Provider waterfall (cloud-safe order):
  1. NSE historical API  — works from home/office IPs; fails on cloud
  2. yfinance            — uses crumb auth; also blocked on datacenter IPs
  3. Yahoo Finance raw   — direct HTTP; same block as yfinance
  4. Stooq CSV           — free, no auth, works on most cloud IPs
  5. Alpha Vantage       — most reliable on cloud (set ALPHAVANTAGE_API_KEY env var)

For Render/Railway/cloud deployments the recommended path is:
  Option A — Set ALPHAVANTAGE_API_KEY env var (free at alphavantage.co, 25 req/day)
  Option B — Pre-seed DB by running backfill from your LOCAL machine first
  Option C — Set HTTPS_PROXY env var pointing to a residential proxy
"""

import asyncio
import csv
import io
import logging
import os
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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
STOOQ_API = "https://stooq.com/q/d/l/?s={symbol}.NS&d1={d1}&d2={d2}&i=d"
ALPHAVANTAGE_API = "https://www.alphavantage.co/query"

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
    start_date: date = None
    end_date: date = None

    def __post_init__(self):
        if self.start_date is None:
            self.start_date = date.today() - timedelta(days=365)
        if self.end_date is None:
            self.end_date = date.today()


@dataclass
class FetchMeta:
    provider: str
    attempts: int
    used_fallback: bool = False


def _count_weekdays(start: date, end: date) -> int:
    if start > end:
        return 0
    total_days = (end - start).days + 1
    full_weeks, remainder = divmod(total_days, 7)
    weekdays = full_weeks * 5
    for offset in range(remainder):
        if (start.weekday() + offset) % 7 < 5:
            weekdays += 1
    return weekdays


def _is_requested_range_fully_cached(
    existing: list[dict],
    start_date: date | None,
    end_date: date | None,
    min_cached_candles: int,
) -> bool:
    if len(existing) < min_cached_candles:
        return False
    if not start_date or not end_date:
        return True

    candle_dates = sorted({
        ts.date() if hasattr(ts, "date") else ts
        for ts in (row.get("timestamp") for row in existing)
        if ts is not None
    })
    if not candle_dates:
        return False
    if candle_dates[0] > start_date or candle_dates[-1] < end_date:
        return False

    expected_weekdays = _count_weekdays(start_date, end_date)
    # Indian market holidays reduce the actual candle count below weekday count.
    # Keep the skip logic conservative: only skip when the cache is close to a
    # full business-day window and the requested range is covered end-to-end.
    holiday_tolerance = max(3, int(expected_weekdays * 0.08))
    minimum_complete_count = max(min_cached_candles, expected_weekdays - holiday_tolerance)
    return len(candle_dates) >= minimum_complete_count

def _make_candle(symbol: str, ts: datetime, o, h, l, c, v: int) -> dict:
    return {
        "symbol": symbol.upper(),
        "exchange": "NSE",
        "timeframe": "day",
        "timestamp": ts,
        "open": float(o or 0),
        "high": float(h or 0),
        "low": float(l or 0),
        "close": float(c or 0),
        "volume": int(v or 0),
    }


class NSEHistoricalFetcher:
    """5-provider waterfall fetcher for NSE daily OHLCV candles."""

    def __init__(
        self,
        *,
        allow_fallback: bool = True,
        max_attempts: int = 5,
        base_delay_seconds: float = 1.0,
    ) -> None:
        self._allow_fallback = allow_fallback
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._user_agents = list(DEFAULT_USER_AGENTS)
        self._ua_index = 0
        self._session = self._new_session()

    # ── Session helpers ───────────────────────────────────────────────────────

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
            try:
                resp = self._session.get(url, timeout=20)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.info("Historical warmup skipped url=%s cause=%s", url, exc)

    # ── Provider 1: NSE ───────────────────────────────────────────────────────

    def _parse_nse_payload(self, symbol: str, payload: dict) -> list[dict]:
        rows = payload.get("data", []) or []
        candles: list[dict] = []
        for row in rows:
            ts = datetime.strptime(row["CH_TIMESTAMP"], "%d-%b-%Y").replace(tzinfo=timezone.utc)
            candles.append(_make_candle(
                symbol, ts,
                row.get("CH_OPENING_PRICE") or 0,
                row.get("CH_TRADE_HIGH_PRICE") or 0,
                row.get("CH_TRADE_LOW_PRICE") or 0,
                row.get("CH_CLOSING_PRICE") or 0,
                int(float(row.get("CH_TOT_TRADED_QTY") or 0)),
            ))
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
            HTTPStatus.TOO_MANY_REQUESTS,
            HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.GATEWAY_TIMEOUT, HTTPStatus.INTERNAL_SERVER_ERROR,
        }
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                self._warmup()
                candles = self._fetch_from_nse(symbol=symbol, start=start, end=end)
                logger.info(
                    "Historical backfill succeeded symbol=%s provider=nse attempt=%s candles=%s",
                    symbol.upper(), attempt, len(candles),
                )
                return candles, attempt
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                last_error = exc
                should_retry = status_code in retryable and attempt < self._max_attempts
                logger.warning(
                    "Historical fetch failed symbol=%s provider=nse attempt=%s/%s status=%s retry=%s cause=%s",
                    symbol.upper(), attempt, self._max_attempts, status_code, should_retry, exc,
                )
                if not should_retry:
                    break
                self._rotate_user_agent()
                if attempt >= 2:
                    self._refresh_session()
                delay = self._base_delay_seconds * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.info(
                    "Historical retry scheduled symbol=%s provider=nse next_attempt=%s delay_seconds=%.2f ua_index=%s",
                    symbol.upper(), attempt + 1, delay, self._ua_index,
                )
                time.sleep(max(delay, 0))
            except requests.RequestException as exc:
                last_error = exc
                should_retry = attempt < self._max_attempts
                logger.warning(
                    "Historical fetch network error symbol=%s provider=nse attempt=%s/%s retry=%s cause=%s",
                    symbol.upper(), attempt, self._max_attempts, should_retry, exc,
                )
                if not should_retry:
                    break
                self._refresh_session()
                delay = self._base_delay_seconds * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(max(delay, 0))
        raise RuntimeError(f"nse fetch failed after {self._max_attempts} attempts: {last_error}")

    # ── Provider 2: yfinance ──────────────────────────────────────────────────

    def _fetch_from_yfinance(self, symbol: str, start: date, end: date) -> list[dict]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance not installed — run: pip install yfinance") from exc

        ticker_sym = f"{symbol.upper()}.NS"
        end_exclusive = end + timedelta(days=1)
        ticker = yf.Ticker(ticker_sym)
        df = ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end_exclusive.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
            actions=False,
        )
        if df is None or df.empty:
            raise RuntimeError(f"yfinance returned empty data for {ticker_sym} ({start}–{end})")

        candles: list[dict] = []
        for ts, row in df.iterrows():
            dt = ts.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            candles.append(_make_candle(
                symbol, dt,
                row.get("Open") or 0, row.get("High") or 0,
                row.get("Low") or 0, row.get("Close") or 0,
                int(row.get("Volume") or 0),
            ))

        logger.info("Historical backfill succeeded symbol=%s provider=yfinance candles=%s", symbol.upper(), len(candles))
        return sorted(candles, key=lambda x: x["timestamp"])

    # ── Provider 3: Yahoo raw HTTP ────────────────────────────────────────────

    def _fetch_from_yahoo_raw(self, symbol: str, start: date, end: date) -> list[dict]:
        params = {
            "period1": int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()),
            "period2": int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()),
            "interval": "1d",
            "includePrePost": "false",
            "events": "div,splits",
        }
        url = YAHOO_CHART_API.format(symbol=symbol.upper())
        session = requests.Session()
        session.headers["User-Agent"] = self._user_agents[self._ua_index]
        response = session.get(url, params=params, timeout=30)
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
            o, h, l, c = opens[i], (highs[i] if i < len(highs) else None), (lows[i] if i < len(lows) else None), (closes[i] if i < len(closes) else None)
            v = volumes[i] if i < len(volumes) else 0
            if None in (o, h, l, c):
                continue
            candles.append(_make_candle(symbol, datetime.fromtimestamp(ts, tz=timezone.utc), o, h, l, c, int(v or 0)))
        return sorted(candles, key=lambda x: x["timestamp"])

    # ── Provider 4: Stooq CSV ─────────────────────────────────────────────────

    def _fetch_from_stooq(self, symbol: str, start: date, end: date) -> list[dict]:
        """Free CSV, no auth. Stooq format: Date,Open,High,Low,Close,Volume"""
        url = STOOQ_API.format(
            symbol=symbol.upper(),
            d1=start.strftime("%Y%m%d"),
            d2=end.strftime("%Y%m%d"),
        )
        session = requests.Session()
        session.headers["User-Agent"] = self._user_agents[self._ua_index]
        resp = session.get(url, timeout=30)
        resp.raise_for_status()

        text = resp.text.strip()
        if not text or "No data" in text or len(text.splitlines()) < 2:
            raise RuntimeError(f"Stooq returned no data for {symbol}.NS ({start}–{end})")

        candles: list[dict] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            try:
                ts = datetime.strptime(row["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                candles.append(_make_candle(
                    symbol, ts,
                    row.get("Open") or 0, row.get("High") or 0,
                    row.get("Low") or 0, row.get("Close") or 0,
                    int(float(row.get("Volume") or 0)),
                ))
            except (KeyError, ValueError):
                continue

        if not candles:
            raise RuntimeError(f"Stooq CSV parse yielded zero candles for {symbol}")

        logger.info("Historical backfill succeeded symbol=%s provider=stooq candles=%s", symbol.upper(), len(candles))
        return sorted(candles, key=lambda x: x["timestamp"])

    # ── Provider 5: Alpha Vantage ─────────────────────────────────────────────

    def _fetch_from_alphavantage(self, symbol: str, start: date, end: date) -> list[dict]:
        """
        Most reliable on cloud. Requires free API key.
        Get yours at: https://www.alphavantage.co/support/#api-key
        Set env var: ALPHAVANTAGE_API_KEY=your_key_here
        Free tier: 25 calls/day, 500/month.
        """
        api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "ALPHAVANTAGE_API_KEY not set. "
                "Get a FREE key at https://www.alphavantage.co/support/#api-key "
                "and add it to your Render environment variables."
            )

        session = requests.Session()
        session.headers["User-Agent"] = self._user_agents[self._ua_index]

        # Try NSE suffix first, then BSE, then bare symbol
        for suffix in [".NSE", ".BSE", ""]:
            ticker = f"{symbol.upper()}{suffix}"
            params = {
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker,
                "outputsize": "full",
                "datatype": "json",
                "apikey": api_key,
            }
            resp = session.get(ALPHAVANTAGE_API, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()

            if "Error Message" in payload:
                logger.debug("Alpha Vantage error for %s: %s", ticker, payload["Error Message"])
                continue
            if "Information" in payload or "Note" in payload:
                msg = payload.get("Information") or payload.get("Note", "")
                raise RuntimeError(f"Alpha Vantage rate limit: {msg}")

            ts_data = payload.get("Time Series (Daily)", {})
            if not ts_data:
                continue

            candles: list[dict] = []
            for date_str, values in ts_data.items():
                try:
                    ts = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if ts.date() < start or ts.date() > end:
                        continue
                    # AV uses "1. open" keys; handle both formats
                    o = values.get("1. open") or values.get("open") or 0
                    h = values.get("2. high") or values.get("high") or 0
                    l = values.get("3. low") or values.get("low") or 0
                    c = values.get("4. close") or values.get("close") or 0
                    v = int(float(values.get("5. volume") or values.get("volume") or 0))
                    candles.append(_make_candle(symbol, ts, o, h, l, c, v))
                except (KeyError, ValueError):
                    continue

            if candles:
                logger.info(
                    "Historical backfill succeeded symbol=%s provider=alphavantage ticker=%s candles=%s",
                    symbol.upper(), ticker, len(candles),
                )
                return sorted(candles, key=lambda x: x["timestamp"])

        raise RuntimeError(
            f"Alpha Vantage returned no data for {symbol} with .NSE/.BSE suffixes. "
            "Indian equities may require Alpha Vantage premium. "
            "Try running backfill from your local machine instead."
        )

    # ── Public interface: 5-provider waterfall ────────────────────────────────

    def fetch_daily_with_meta(self, symbol: str, start: date, end: date) -> tuple[list[dict], FetchMeta]:
        errors: dict[str, str] = {}

        logger.info(
            "Historical provider waterfall start symbol=%s start_date=%s end_date=%s allow_fallback=%s max_attempts=%s",
            symbol.upper(), start, end, self._allow_fallback, self._max_attempts,
        )

        # 1. NSE — best quality but blocked on cloud datacenter IPs
        logger.info("Historical provider attempt symbol=%s provider=nse", symbol.upper())
        try:
            candles, attempts = self._fetch_nse_with_retries(symbol=symbol, start=start, end=end)
            return candles, FetchMeta(provider="nse", attempts=attempts, used_fallback=False)
        except Exception as exc:
            errors["nse"] = str(exc)
            logger.error("Historical fetch exhausted symbol=%s provider=nse cause=%s", symbol.upper(), exc)

        if not self._allow_fallback:
            raise RuntimeError(f"provider=nse error={errors['nse']}")

        # 2. yfinance — works locally; also blocked on datacenter IPs
        logger.info("Historical provider attempt symbol=%s provider=yfinance", symbol.upper())
        try:
            candles = self._fetch_from_yfinance(symbol=symbol, start=start, end=end)
            logger.warning("Fallback to yfinance symbol=%s", symbol.upper())
            return candles, FetchMeta(provider="yfinance", attempts=self._max_attempts, used_fallback=True)
        except Exception as exc:
            errors["yfinance"] = str(exc)
            logger.warning("Historical fallback failed symbol=%s provider=yfinance cause=%s", symbol.upper(), exc)

        # 3. Yahoo raw HTTP — same IP restrictions as yfinance
        logger.info("Historical provider attempt symbol=%s provider=yahoo_raw", symbol.upper())
        try:
            candles = self._fetch_from_yahoo_raw(symbol=symbol, start=start, end=end)
            if candles:
                logger.warning("Fallback to yahoo_raw symbol=%s", symbol.upper())
                return candles, FetchMeta(provider="yahoo_raw", attempts=self._max_attempts, used_fallback=True)
            raise RuntimeError("yahoo_raw returned empty list")
        except Exception as exc:
            errors["yahoo_raw"] = str(exc)
            logger.warning("Historical fallback failed symbol=%s provider=yahoo_raw cause=%s", symbol.upper(), exc)

        # 4. Stooq — free CSV, no auth, usually passes cloud IP checks
        logger.info("Historical provider attempt symbol=%s provider=stooq", symbol.upper())
        try:
            candles = self._fetch_from_stooq(symbol=symbol, start=start, end=end)
            logger.warning("Fallback to stooq symbol=%s", symbol.upper())
            return candles, FetchMeta(provider="stooq", attempts=self._max_attempts, used_fallback=True)
        except Exception as exc:
            errors["stooq"] = str(exc)
            logger.warning("Historical fallback failed symbol=%s provider=stooq cause=%s", symbol.upper(), exc)

        # 5. Alpha Vantage — reliable on cloud with free API key
        logger.info("Historical provider attempt symbol=%s provider=alphavantage", symbol.upper())
        try:
            candles = self._fetch_from_alphavantage(symbol=symbol, start=start, end=end)
            logger.warning("Fallback to alphavantage symbol=%s", symbol.upper())
            return candles, FetchMeta(provider="alphavantage", attempts=self._max_attempts, used_fallback=True)
        except Exception as exc:
            errors["alphavantage"] = str(exc)
            logger.error("Historical fallback failed symbol=%s provider=alphavantage cause=%s", symbol.upper(), exc)

        has_av_key = bool(os.environ.get("ALPHAVANTAGE_API_KEY", "").strip())
        hint = (
            "All 5 data providers failed. "
            + (
                "ALPHAVANTAGE_API_KEY is set but returned no data — "
                "verify the key is valid and you haven't exceeded 25 calls/day. "
                if has_av_key
                else
                "ACTION REQUIRED: Set ALPHAVANTAGE_API_KEY in your Render env vars "
                "(free key at https://www.alphavantage.co/support/#api-key). "
                "OR run the backfill from your LOCAL machine where NSE/Yahoo are not blocked. "
            )
        )
        raise RuntimeError(
            f"{hint} | " + " | ".join(f"{p}={e}" for p, e in errors.items())
        )

    def fetch_daily(self, symbol: str, start: date, end: date) -> list[dict]:
        candles, _ = self.fetch_daily_with_meta(symbol=symbol, start=start, end=end)
        return candles


async def backfill_historical_data(requests_iter: Iterable[BackfillRequest]) -> dict:
    from database.repository import HistoricalCandleRepository

    requests_list = list(requests_iter)
  
    cfg = load_config().get("historical", {})
    fetcher = NSEHistoricalFetcher(
        allow_fallback=bool(cfg.get("allow_fallback", True)),
        max_attempts=int(cfg.get("max_attempts", 5)),
        base_delay_seconds=float(cfg.get("base_delay_seconds", 1.0)),
    )
    max_attempts = int(cfg.get("max_attempts", 5))
    min_cached_candles = int(cfg.get("min_cached_candles", 20))
    inter_symbol_delay = float(cfg.get("inter_symbol_delay", 1.5))
    total = 0
    failures: list[dict] = []
    metrics: dict[str, int] = {
        "symbols_total": 0,
        "symbols_success": 0,
        "symbols_failed": 0,
        "symbols_retried": 0,
        "symbols_fallback_used": 0,
        "symbols_cached": 0,
    }

    logger.info(
        "Historical backfill started symbols=%s exchange=%s timeframe=%s start_date=%s end_date=%s request_count=%s",
        [req.symbol.upper() for req in requests_list],
        sorted({req.exchange for req in requests_list}) if requests_list else [],
        sorted({req.timeframe for req in requests_list}) if requests_list else [],
        min((req.start_date for req in requests_list), default=None),
        max((req.end_date for req in requests_list), default=None),
        len(requests_list),
    )

    dhan_broker = None
    dhan_instruments = {}
    dhan_cfg = load_config().get("brokers", {}).get("dhan")
    if dhan_cfg and dhan_cfg.get("enabled", True) and dhan_cfg.get("client_id") and dhan_cfg.get("access_token"):
        try:
            from brokers.dhan.adapter import DhanBroker
            from brokers.base import Exchange
            logger.info("Dhan configuration found, using Dhan for historical backfill if possible")
            dhan_broker = DhanBroker(dhan_cfg)
            if await dhan_broker.login():
                insts = await dhan_broker.get_instruments(Exchange.NSE)
                dhan_instruments = {i.symbol: i for i in insts}
            else:
                dhan_broker = None
        except Exception as e:
            logger.warning(f"Failed to load Dhan broker for backfill: {e}")
            dhan_broker = None

    fetched_count = 0
    for req in requests_list:
        metrics["symbols_total"] += 1
        if req.timeframe != "day":
            metrics["symbols_failed"] += 1
            failures.append({"symbol": req.symbol, "error": "only day timeframe currently supported"})
            continue

        # Skip symbols that already have sufficient data cached in the DB.
        try:
            from datetime import datetime as _dt, timezone as _tz
            _start = _dt.combine(req.start_date, _dt.min.time(), tzinfo=_tz.utc) if req.start_date else None
            _end = _dt.combine(req.end_date, _dt.max.time(), tzinfo=_tz.utc) if req.end_date else None
            existing = await HistoricalCandleRepository.fetch_window(
                [req.symbol.upper()], req.exchange, req.timeframe, _start, _end,
            )
            if _is_requested_range_fully_cached(existing, req.start_date, req.end_date, min_cached_candles):
                logger.info(
                    "Historical backfill symbol skipped (cached) symbol=%s existing_candles=%s",
                    req.symbol.upper(), len(existing),
                )
                metrics["symbols_cached"] += 1
                metrics["symbols_success"] += 1
                total += len(existing)
                continue
        except Exception:
            pass  # If DB check fails, proceed with fetch.

        # Rate-limit: add delay between provider requests to avoid 429s.
        if fetched_count > 0 and inter_symbol_delay > 0:
            await asyncio.sleep(inter_symbol_delay)

        try:
            logger.info(
                "Historical backfill symbol start symbol=%s exchange=%s timeframe=%s start_date=%s end_date=%s",
                req.symbol.upper(), req.exchange, req.timeframe, req.start_date, req.end_date,
            )
            
            candles = []
            meta = None
            
            if dhan_broker and req.symbol.upper() in dhan_instruments:
                try:
                    from datetime import datetime as _dt, time as _time, timezone as _tz
                    start_dt = _dt.combine(req.start_date, _time.min).replace(tzinfo=_tz.utc)
                    end_dt = _dt.combine(req.end_date, _time.max).replace(tzinfo=_tz.utc)
                    dhan_ohlcv = await dhan_broker.get_ohlcv(
                        dhan_instruments[req.symbol.upper()], 
                        "day", 
                        start_dt, 
                        end_dt
                    )
                    if dhan_ohlcv:
                        for c in dhan_ohlcv:
                            candles.append({
                                "symbol": req.symbol.upper(),
                                "exchange": "NSE",
                                "timeframe": "day",
                                "timestamp": c.timestamp.replace(tzinfo=_tz.utc) if c.timestamp.tzinfo is None else c.timestamp,
                                "open": float(c.open),
                                "high": float(c.high),
                                "low": float(c.low),
                                "close": float(c.close),
                                "volume": int(c.volume),
                            })
                        meta = FetchMeta(provider="dhan", attempts=1, used_fallback=False)
                except Exception as e:
                    logger.warning(f"Dhan historical fetch failed for {req.symbol}: {e}")

            if not candles:
                candles, meta = await asyncio.to_thread(
                    fetcher.fetch_daily_with_meta, req.symbol, req.start_date, req.end_date
                )
            
            await HistoricalCandleRepository.upsert_many(candles)
            total += len(candles)
            fetched_count += 1
            metrics["symbols_success"] += 1
            provider_key = f"provider_{meta.provider}_success"
            metrics[provider_key] = metrics.get(provider_key, 0) + 1
            if meta.attempts > 1:
                metrics["symbols_retried"] += 1
            if meta.used_fallback:
                metrics["symbols_fallback_used"] += 1
            logger.info(
                "Historical backfill symbol complete symbol=%s status=success candles=%s provider=%s attempts=%s used_fallback=%s",
                req.symbol.upper(), len(candles), meta.provider, meta.attempts, meta.used_fallback,
            )
        except Exception as exc:
            logger.warning("Backfill failed symbol=%s error=%s", req.symbol, exc)
            logger.info(
                "Historical backfill symbol complete symbol=%s status=failed error=%s",
                req.symbol.upper(), exc,
            )
            fetched_count += 1
            metrics["symbols_failed"] += 1
            failures.append({"symbol": req.symbol, "error": str(exc)})

    logger.info(
        "Historical backfill summary inserted=%s failures=%s "
        "symbols_total=%s symbols_success=%s symbols_failed=%s "
        "symbols_retried=%s symbols_fallback_used=%s symbols_cached=%s max_attempts=%s providers=%s",
        total, len(failures),
        metrics["symbols_total"], metrics["symbols_success"], metrics["symbols_failed"],
        metrics["symbols_retried"], metrics["symbols_fallback_used"], metrics.get("symbols_cached", 0),
        max_attempts,
        {k: v for k, v in metrics.items() if k.startswith("provider_")},
    )
    return {"inserted": total, "failures": failures, "metrics": metrics}
