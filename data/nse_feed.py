"""
NSE Data Feed - Real-time Options Chain, PCR, and Market Data

NSE blocks datacenter IPs with 403. Strategy:
  1. Pull index LTPs from Dhan/Zerodha broker quotes (already authenticated)
  2. Pull options chain from Dhan/Zerodha (no raw NSE scraping needed)
  3. Fall back to hardcoded safe defaults if brokers also fail
  4. NSE scraping kept only as a last resort with longer delays
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from brokers.base import BaseBroker

logger = logging.getLogger("data.nse_feed")

NSE_BASE = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# NSE index instrument tokens for Dhan
DHAN_INDEX_TOKENS = {
    "NIFTY 50":   "13",
    "NIFTY BANK": "25",
    "INDIA VIX":  "999920005",
}

# Zerodha instrument tokens for indices
ZERODHA_INDEX_TOKENS = {
    "NIFTY 50":   256265,
    "NIFTY BANK": 260105,
    "INDIA VIX":  264969,
}

# Safe fallback values when everything fails
_FALLBACK_INDEX = {
    "nifty":     22000.0,
    "banknifty": 47000.0,
    "vix":       14.0,
    "finnifty":  0.0,
}

# How long to cache a successful fetch (seconds)
_INDEX_CACHE_TTL   = 3.0
_OPTIONS_CACHE_TTL = 30.0


class NSEDataFeed:
    """
    Fetches index data and options chain.
    Primary source: broker quote APIs (Dhan / Zerodha).
    Fallback:       NSE public API with session warm-up.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._session_valid = False

        # Broker references injected by the engine after startup
        self._dhan_broker:     Optional["BaseBroker"] = None
        self._zerodha_broker:  Optional["BaseBroker"] = None

        # Simple in-memory cache
        self._index_cache:   Optional[dict] = None
        self._index_cache_ts: float = 0.0
        self._options_cache: dict[str, dict] = {}
        self._options_cache_ts: dict[str, float] = {}

        # Track whether NSE scraping is broken on this deployment
        self._nse_blocked      = False
        self._nse_block_logged = False

    # ── Broker injection ──────────────────────────────────────────────────────

    def set_brokers(
        self,
        dhan_broker:    Optional["BaseBroker"] = None,
        zerodha_broker: Optional["BaseBroker"] = None,
    ) -> None:
        """Called by the engine once brokers are connected."""
        self._dhan_broker    = dhan_broker
        self._zerodha_broker = zerodha_broker
        logger.info(
            "NSEDataFeed broker sources configured | dhan=%s zerodha=%s",
            dhan_broker    is not None,
            zerodha_broker is not None,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_index_data(self) -> dict:
        """
        Returns {"nifty": float, "banknifty": float, "vix": float, "finnifty": float}.
        Tries broker quotes first, then NSE scraping, then cached/fallback values.
        """
        now = time.monotonic()
        if self._index_cache and (now - self._index_cache_ts) < _INDEX_CACHE_TTL:
            return self._index_cache

        result = await self._fetch_index_from_brokers()
        if result:
            self._index_cache    = result
            self._index_cache_ts = now
            return result

        # Only try NSE scraping if we haven't confirmed it's blocked
        if not self._nse_blocked:
            result = await self._fetch_index_from_nse()
            if result:
                self._index_cache    = result
                self._index_cache_ts = now
                return result

        # Return stale cache or safe defaults
        if self._index_cache:
            return self._index_cache
        return dict(_FALLBACK_INDEX)

    async def get_option_chain(self, symbol: str = "NIFTY") -> dict:
        """
        Returns parsed options chain. Tries broker first, then NSE.
        """
        now = time.monotonic()
        cached_ts = self._options_cache_ts.get(symbol, 0.0)
        if symbol in self._options_cache and (now - cached_ts) < _OPTIONS_CACHE_TTL:
            return self._options_cache[symbol]

        chain = await self._fetch_options_from_broker(symbol)
        if not chain and not self._nse_blocked:
            chain = await self._fetch_options_from_nse(symbol)

        if chain:
            self._options_cache[symbol]    = chain
            self._options_cache_ts[symbol] = now
            return chain

        return self._options_cache.get(symbol, self._empty_chain(symbol))

    async def get_pcr(self, symbol: str = "NIFTY") -> float:
        chain = await self.get_option_chain(symbol)
        return chain.get("pcr", 1.0)

    async def get_india_vix(self) -> float:
        data = await self.get_index_data()
        return data.get("vix", 14.0)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Source 1: Broker quote APIs ───────────────────────────────────────────

    async def _fetch_index_from_brokers(self) -> Optional[dict]:
        """Pull NIFTY / BANKNIFTY / VIX from Dhan or Zerodha quote APIs."""

        # Try Dhan first (it's your primary broker)
        result = await self._fetch_index_dhan()
        if result:
            return result

        # Try Zerodha
        result = await self._fetch_index_zerodha()
        if result:
            return result

        return None

    async def _fetch_index_dhan(self) -> Optional[dict]:
        if not self._dhan_broker or not getattr(self._dhan_broker, "dhan", None):
            return None
        try:
            import asyncio as _asyncio
            client = self._dhan_broker.dhan

            # Dhan LTP endpoint accepts a list of securities
            resp = await _asyncio.to_thread(
                client.get_ltp_data,
                securities={
                    "IDX_I": [
                        DHAN_INDEX_TOKENS["NIFTY 50"],
                        DHAN_INDEX_TOKENS["NIFTY BANK"],
                        DHAN_INDEX_TOKENS["INDIA VIX"],
                    ]
                },
            )
            if resp.get("status") != "success":
                return None

            data = resp.get("data", {}).get("IDX_I", {})
            nifty     = float(data.get(DHAN_INDEX_TOKENS["NIFTY 50"],   {}).get("last_price", 0) or 0)
            banknifty = float(data.get(DHAN_INDEX_TOKENS["NIFTY BANK"], {}).get("last_price", 0) or 0)
            vix       = float(data.get(DHAN_INDEX_TOKENS["INDIA VIX"],  {}).get("last_price", 0) or 0)

            if nifty > 0:
                logger.debug("Index data from Dhan | NIFTY=%.2f BANKNIFTY=%.2f VIX=%.2f", nifty, banknifty, vix)
                return {
                    "nifty":     nifty,
                    "banknifty": banknifty if banknifty > 0 else _FALLBACK_INDEX["banknifty"],
                    "vix":       vix       if vix > 0       else _FALLBACK_INDEX["vix"],
                    "finnifty":  0.0,
                }
        except Exception as e:
            logger.debug("Dhan index fetch failed: %s", e)
        return None

    async def _fetch_index_zerodha(self) -> Optional[dict]:
        if not self._zerodha_broker or not getattr(self._zerodha_broker, "kite", None):
            return None
        try:
            import asyncio as _asyncio
            kite = self._zerodha_broker.kite
            tokens = [
                f"NSE:{t}" for t in
                ["NIFTY 50", "NIFTY BANK", "INDIA VIX", "NIFTY FIN SERVICE"]
            ]
            quotes = await _asyncio.to_thread(kite.quote, tokens)

            nifty     = float(quotes.get("NSE:NIFTY 50",          {}).get("last_price", 0) or 0)
            banknifty = float(quotes.get("NSE:NIFTY BANK",         {}).get("last_price", 0) or 0)
            vix       = float(quotes.get("NSE:INDIA VIX",          {}).get("last_price", 0) or 0)
            finnifty  = float(quotes.get("NSE:NIFTY FIN SERVICE",  {}).get("last_price", 0) or 0)

            if nifty > 0:
                logger.debug("Index data from Zerodha | NIFTY=%.2f BANKNIFTY=%.2f VIX=%.2f", nifty, banknifty, vix)
                return {
                    "nifty":     nifty,
                    "banknifty": banknifty if banknifty > 0 else _FALLBACK_INDEX["banknifty"],
                    "vix":       vix       if vix > 0       else _FALLBACK_INDEX["vix"],
                    "finnifty":  finnifty,
                }
        except Exception as e:
            logger.debug("Zerodha index fetch failed: %s", e)
        return None

    # ── Source 2: Options chain from broker ───────────────────────────────────

    async def _fetch_options_from_broker(self, symbol: str) -> Optional[dict]:
        """Pull options chain from Dhan or Zerodha — no NSE scraping needed."""
        if self._dhan_broker:
            try:
                chain = await self._dhan_options_chain(symbol)
                if chain:
                    return chain
            except Exception as e:
                logger.debug("Dhan options chain failed for %s: %s", symbol, e)

        if self._zerodha_broker:
            try:
                chain = await self._zerodha_options_chain(symbol)
                if chain:
                    return chain
            except Exception as e:
                logger.debug("Zerodha options chain failed for %s: %s", symbol, e)

        return None

    async def _dhan_options_chain(self, symbol: str) -> Optional[dict]:
        """Use Dhan's option chain API."""
        import asyncio as _asyncio
        client = getattr(self._dhan_broker, "dhan", None)
        if not client:
            return None

        # Dhan option chain — expiry_code 0 = current expiry
        resp = await _asyncio.to_thread(
            client.get_option_chain,
            under_security_id=DHAN_INDEX_TOKENS.get(
                "NIFTY 50" if symbol == "NIFTY" else "NIFTY BANK"
            ),
            under_exchange_segment="IDX_I",
            expiry="",       # current expiry
        )
        if resp.get("status") != "success":
            return None

        return self._parse_dhan_option_chain(resp.get("data", {}), symbol)

    async def _zerodha_options_chain(self, symbol: str) -> Optional[dict]:
        """Use Zerodha instruments + quotes to build options chain."""
        import asyncio as _asyncio
        from datetime import datetime
        kite = getattr(self._zerodha_broker, "kite", None)
        if not kite:
            return None

        # Get near-expiry options instruments
        instruments = await _asyncio.to_thread(kite.instruments, "NFO")
        underlying  = symbol  # "NIFTY" or "BANKNIFTY"
        today       = datetime.now().date()

        # Filter nearest expiry options
        options = [
            i for i in instruments
            if i.get("name") == underlying
            and i.get("instrument_type") in ("CE", "PE")
            and i.get("expiry") and i["expiry"] >= today
        ]
        if not options:
            return None

        # Nearest expiry
        min_expiry = min(i["expiry"] for i in options)
        chain_inst = [i for i in options if i["expiry"] == min_expiry]
        tokens     = [f"NFO:{i['tradingsymbol']}" for i in chain_inst[:200]]

        if not tokens:
            return None

        quotes = await _asyncio.to_thread(kite.quote, tokens)
        return self._build_chain_from_zerodha_quotes(quotes, chain_inst, symbol)

    # ── Source 3: NSE scraping (fallback, works only on non-datacenter IPs) ──

    async def _get_nse_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=NSE_HEADERS,
                timeout=15,
                follow_redirects=True,
            )
            try:
                await self._client.get(f"{NSE_BASE}/", timeout=8)
                self._session_valid = True
            except Exception:
                self._session_valid = False
        return self._client

    async def _fetch_index_from_nse(self) -> Optional[dict]:
        """Scrape NSE allIndices — only works on residential/office IPs."""
        try:
            client = await self._get_nse_client()
            resp   = await client.get(f"{NSE_BASE}/api/allIndices", timeout=10)

            if resp.status_code == 403:
                if not self._nse_block_logged:
                    logger.warning(
                        "NSE API blocked (403) — this is normal on cloud deployments "
                        "(Render, Railway, AWS, etc.). Index data will come from broker "
                        "quote APIs instead. Set brokers via nse_feed.set_brokers()."
                    )
                    self._nse_block_logged = True
                self._nse_blocked = True
                return None

            resp.raise_for_status()
            data = self._safe_json(resp)

            result = dict(_FALLBACK_INDEX)
            name_map = {
                "NIFTY 50":          "nifty",
                "NIFTY BANK":        "banknifty",
                "INDIA VIX":         "vix",
                "NIFTY FIN SERVICE": "finnifty",
            }
            for idx in data.get("data", []):
                key = name_map.get(idx.get("index", ""))
                if key:
                    result[key] = float(idx.get("last", 0) or 0)

            return result if result["nifty"] > 0 else None

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                self._nse_blocked = True
            return None
        except Exception as e:
            logger.debug("NSE index scrape error: %s", e)
            return None

    async def _fetch_options_from_nse(self, symbol: str) -> Optional[dict]:
        """Scrape NSE options chain — blocked on datacenter IPs."""
        if self._nse_blocked:
            return None
        try:
            client = await self._get_nse_client()
            if symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                url = f"{NSE_BASE}/api/option-chain-indices?symbol={symbol}"
            else:
                url = f"{NSE_BASE}/api/option-chain-equities?symbol={symbol}"

            resp = await client.get(url, timeout=12)
            if resp.status_code == 403:
                self._nse_blocked = True
                return None
            resp.raise_for_status()
            return self._parse_nse_option_chain(self._safe_json(resp), symbol)
        except Exception as e:
            logger.debug("NSE options chain scrape failed for %s: %s", symbol, e)
            return None

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_dhan_option_chain(self, raw: dict, symbol: str) -> dict:
        """Parse Dhan option chain response into standard format."""
        strikes_data = []
        total_call_oi = 0
        total_put_oi  = 0
        underlying    = float(raw.get("underlying_ltp", 0) or 0)
        atm_strike    = None
        min_diff      = float("inf")

        for row in raw.get("options", []):
            strike = float(row.get("strike_price", 0) or 0)
            ce     = row.get("call_options", {}) or {}
            pe     = row.get("put_options",  {}) or {}
            ce_oi  = int(ce.get("oi", 0) or 0)
            pe_oi  = int(pe.get("oi", 0) or 0)
            total_call_oi += ce_oi
            total_put_oi  += pe_oi
            if underlying and abs(strike - underlying) < min_diff:
                min_diff   = abs(strike - underlying)
                atm_strike = strike
            strikes_data.append({
                "strike":    strike,
                "ce_oi":     ce_oi,
                "ce_ltp":    float(ce.get("ltp", 0) or 0),
                "ce_iv":     float(ce.get("iv",  0) or 0),
                "ce_volume": int(ce.get("volume", 0) or 0),
                "ce_oi_change": int(ce.get("oi_change", 0) or 0),
                "pe_oi":     pe_oi,
                "pe_ltp":    float(pe.get("ltp", 0) or 0),
                "pe_iv":     float(pe.get("iv",  0) or 0),
                "pe_volume": int(pe.get("volume", 0) or 0),
                "pe_oi_change": int(pe.get("oi_change", 0) or 0),
            })

        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0
        return self._build_chain_summary(symbol, underlying, strikes_data, atm_strike, pcr)

    def _build_chain_from_zerodha_quotes(
        self,
        quotes: dict,
        instruments: list,
        symbol: str,
    ) -> dict:
        """Build options chain dict from Zerodha quote data."""
        strikes_map: dict[float, dict] = {}
        underlying = 0.0

        for inst in instruments:
            key   = f"NFO:{inst['tradingsymbol']}"
            quote = quotes.get(key, {})
            if not quote:
                continue
            strike = float(inst.get("strike", 0) or 0)
            ltp    = float(quote.get("last_price", 0) or 0)
            oi     = int(quote.get("oi", 0) or 0)
            volume = int(quote.get("volume", 0) or 0)
            itype  = inst.get("instrument_type", "")

            if strike not in strikes_map:
                strikes_map[strike] = {
                    "strike": strike,
                    "ce_oi": 0, "ce_ltp": 0.0, "ce_iv": 0.0, "ce_volume": 0, "ce_oi_change": 0,
                    "pe_oi": 0, "pe_ltp": 0.0, "pe_iv": 0.0, "pe_volume": 0, "pe_oi_change": 0,
                }

            if itype == "CE":
                strikes_map[strike]["ce_oi"]     = oi
                strikes_map[strike]["ce_ltp"]    = ltp
                strikes_map[strike]["ce_volume"] = volume
            elif itype == "PE":
                strikes_map[strike]["pe_oi"]     = oi
                strikes_map[strike]["pe_ltp"]    = ltp
                strikes_map[strike]["pe_volume"] = volume

        strikes_data  = list(strikes_map.values())
        total_call_oi = sum(s["ce_oi"] for s in strikes_data)
        total_put_oi  = sum(s["pe_oi"] for s in strikes_data)
        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0

        # Estimate underlying from ATM (CE ≈ PE when near ATM)
        atm_strike = None
        if strikes_data:
            mid = sorted(strikes_data, key=lambda x: abs(x["ce_ltp"] - x["pe_ltp"]))
            atm_strike = mid[0]["strike"] if mid else None

        return self._build_chain_summary(symbol, underlying, strikes_data, atm_strike, pcr)

    def _build_chain_summary(
        self,
        symbol: str,
        underlying: float,
        strikes_data: list,
        atm_strike: Optional[float],
        pcr: float,
    ) -> dict:
        """Common output format regardless of source."""
        by_call_oi = sorted(strikes_data, key=lambda x: x["ce_oi"], reverse=True)
        by_put_oi  = sorted(strikes_data, key=lambda x: x["pe_oi"], reverse=True)

        atm = next((s for s in strikes_data if s["strike"] == atm_strike), {})
        straddle = (atm.get("ce_ltp", 0) or 0) + (atm.get("pe_ltp", 0) or 0)

        return {
            "symbol":              symbol,
            "underlying":          underlying,
            "timestamp":           datetime.now().isoformat(),
            "pcr":                 pcr,
            "pcr_interpretation":  self._interpret_pcr(pcr),
            "total_call_oi":       sum(s["ce_oi"] for s in strikes_data),
            "total_put_oi":        sum(s["pe_oi"] for s in strikes_data),
            "atm_strike":          atm_strike,
            "atm_straddle_price":  round(straddle, 2),
            "expected_move_pct":   round(straddle / underlying * 100, 2) if underlying else 0,
            "max_pain_strike":     self._max_pain(strikes_data),
            "key_resistance":      by_call_oi[0]["strike"] if by_call_oi else None,
            "key_support":         by_put_oi[0]["strike"]  if by_put_oi  else None,
            "top_5_ce_oi":         [{"strike": s["strike"], "oi": s["ce_oi"], "iv": s["ce_iv"]} for s in by_call_oi[:5]],
            "top_5_pe_oi":         [{"strike": s["strike"], "oi": s["pe_oi"], "iv": s["pe_iv"]} for s in by_put_oi[:5]],
            "strikes":             strikes_data,
            "expiry_dates":        [],
        }

    def _parse_nse_option_chain(self, raw: dict, symbol: str) -> dict:
        """Existing NSE parser — kept for residential IP fallback."""
        records   = raw.get("records", {})
        data      = records.get("data", [])
        underlying = float(records.get("underlyingValue", 0) or 0)
        strikes_data  = []
        total_call_oi = 0
        total_put_oi  = 0
        atm_strike    = None
        min_diff      = float("inf")

        for item in data:
            strike = float(item.get("strikePrice", 0) or 0)
            ce     = item.get("CE", {}) or {}
            pe     = item.get("PE", {}) or {}
            ce_oi  = int(ce.get("openInterest", 0) or 0)
            pe_oi  = int(pe.get("openInterest", 0) or 0)
            total_call_oi += ce_oi
            total_put_oi  += pe_oi
            if underlying and abs(strike - underlying) < min_diff:
                min_diff   = abs(strike - underlying)
                atm_strike = strike
            strikes_data.append({
                "strike":       strike,
                "ce_oi":        ce_oi,
                "ce_oi_change": int(ce.get("changeinOpenInterest", 0) or 0),
                "ce_ltp":       float(ce.get("lastPrice", 0) or 0),
                "ce_iv":        float(ce.get("impliedVolatility", 0) or 0),
                "ce_volume":    int(ce.get("totalTradedVolume", 0) or 0),
                "pe_oi":        pe_oi,
                "pe_oi_change": int(pe.get("changeinOpenInterest", 0) or 0),
                "pe_ltp":       float(pe.get("lastPrice", 0) or 0),
                "pe_iv":        float(pe.get("impliedVolatility", 0) or 0),
                "pe_volume":    int(pe.get("totalTradedVolume", 0) or 0),
            })

        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0
        return self._build_chain_summary(symbol, underlying, strikes_data, atm_strike, pcr)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _max_pain(self, strikes_data: list) -> Optional[float]:
        if not strikes_data:
            return None
        min_pain     = float("inf")
        max_pain_str = None
        for test in strikes_data:
            ts    = test["strike"]
            total = sum(
                max(0, (ts - s["strike"]) * s["ce_oi"]) +
                max(0, (s["strike"] - ts) * s["pe_oi"])
                for s in strikes_data
            )
            if total < min_pain:
                min_pain     = total
                max_pain_str = ts
        return max_pain_str

    def _interpret_pcr(self, pcr: float) -> str:
        if pcr > 1.5:  return "extremely_bullish"
        if pcr > 1.2:  return "bullish"
        if pcr > 0.8:  return "neutral"
        if pcr > 0.5:  return "bearish"
        return "extremely_bearish"

    def _safe_json(self, response: httpx.Response) -> dict:
        try:
            return response.json()
        except Exception:
            return json.loads(response.content.decode("utf-8", errors="ignore"))

    def _empty_chain(self, symbol: str) -> dict:
        return {
            "symbol": symbol, "underlying": 0.0,
            "timestamp": datetime.now().isoformat(),
            "pcr": 1.0, "pcr_interpretation": "neutral",
            "total_call_oi": 0, "total_put_oi": 0,
            "atm_strike": None, "atm_straddle_price": 0,
            "expected_move_pct": 0, "max_pain_strike": None,
            "key_resistance": None, "key_support": None,
            "top_5_ce_oi": [], "top_5_pe_oi": [],
            "strikes": [], "expiry_dates": [],
        }


# ─── News Sentiment (unchanged) ───────────────────────────────────────────────

class NewsSentimentAnalyzer:

    BULLISH_KEYWORDS = [
        "surge", "rally", "gain", "rise", "bull", "buy", "upgrade", "positive",
        "growth", "beat", "record", "strong", "breakout", "inflow", "FII buying",
        "DII buying", "GST collection", "GDP beat", "rate cut", "stimulus",
        "profit", "earnings beat", "order win", "acquisition", "expansion",
    ]
    BEARISH_KEYWORDS = [
        "fall", "drop", "crash", "bear", "sell", "downgrade", "negative",
        "loss", "miss", "weak", "breakdown", "outflow", "FII selling",
        "rate hike", "inflation", "recession", "slowdown", "default",
        "earnings miss", "margin pressure", "regulatory", "SEBI probe",
        "fraud", "promoter pledge", "block deal sell",
    ]

    async def get_market_sentiment(self) -> str:
        try:
            return await self._fetch_and_analyze()
        except Exception as e:
            logger.debug("Sentiment analysis error: %s", e)
            return "Neutral market sentiment. No major news catalysts."

    async def _fetch_and_analyze(self) -> str:
        headlines = await self._fetch_nse_announcements()
        if not headlines:
            return "No significant market announcements today."
        score    = 0
        analyzed = []
        for h in headlines[:10]:
            h_lower   = h.lower()
            bull_hits = sum(1 for kw in self.BULLISH_KEYWORDS if kw in h_lower)
            bear_hits = sum(1 for kw in self.BEARISH_KEYWORDS if kw in h_lower)
            score    += bull_hits - bear_hits
            if bull_hits > bear_hits:
                analyzed.append(f"▲ {h[:60]}")
            elif bear_hits > bull_hits:
                analyzed.append(f"▼ {h[:60]}")
        sentiment = "Bullish" if score > 2 else "Bearish" if score < -2 else "Neutral"
        top = "; ".join(analyzed[:3]) if analyzed else "No major corporate announcements"
        return f"{sentiment} sentiment (score: {score:+d}). Top news: {top}"

    async def _fetch_nse_announcements(self) -> list[str]:
        try:
            async with httpx.AsyncClient(headers=NSE_HEADERS, timeout=10) as client:
                await client.get(f"{NSE_BASE}/")
                resp = await client.get(
                    f"{NSE_BASE}/api/home-corporate-announcements?index=favAnnouncements"
                )
                data = resp.json()
                return [
                    f"{a.get('desc', '')} - {a.get('sm_name', '')}"
                    for a in data.get("data", [])[:15]
                ]
        except Exception:
            return []
