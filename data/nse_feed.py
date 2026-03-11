"""
NSE Data Feed - Real-time Options Chain, PCR, and Market Data
Fetches from NSE's public API (no auth required).
Also handles news sentiment via a simple keyword-based scorer.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger("data.nse_feed")

NSE_BASE = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


class NSEDataFeed:
    """
    Fetches options chain, PCR, and index data from NSE public API.
    Maintains a session cookie (required by NSE).
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._session_valid = False
        self._pcr_cache: dict[str, float] = {}
        self._oi_cache: dict[str, dict] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=NSE_HEADERS,
                timeout=15,
                follow_redirects=True,
            )
            # Warm up session cookie
            try:
                await self._client.get(f"{NSE_BASE}/", timeout=10)
                self._session_valid = True
            except Exception:
                pass
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Options Chain ─────────────────────────────────────────────────────────

    async def get_option_chain(self, symbol: str = "NIFTY") -> dict:
        """
        Fetch full options chain from NSE.
        Returns dict with strikePrices, CE/PE OI, IV data.
        """
        try:
            client = await self._get_client()
            url = f"{NSE_BASE}/api/option-chain-indices?symbol={symbol}"
            if symbol not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                url = f"{NSE_BASE}/api/option-chain-equities?symbol={symbol}"

            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

            chain = self._parse_option_chain(data, symbol)
            self._oi_cache[symbol] = chain
            return chain

        except Exception as e:
            logger.warning(f"Option chain fetch error for {symbol}: {e}")
            return self._oi_cache.get(symbol, {})

    def _parse_option_chain(self, raw: dict, symbol: str) -> dict:
        """Parse NSE option chain response into structured format."""
        records = raw.get("records", {})
        data = records.get("data", [])
        underlying_value = records.get("underlyingValue", 0)
        expiry_dates = records.get("expiryDates", [])

        total_call_oi = 0
        total_put_oi = 0
        atm_strike = None
        min_diff = float("inf")
        strikes_data = []

        for item in data:
            strike = item.get("strikePrice", 0)
            ce = item.get("CE", {})
            pe = item.get("PE", {})

            ce_oi = ce.get("openInterest", 0)
            pe_oi = pe.get("openInterest", 0)
            total_call_oi += ce_oi
            total_put_oi += pe_oi

            diff = abs(strike - underlying_value)
            if diff < min_diff:
                min_diff = diff
                atm_strike = strike

            if strike:
                strikes_data.append({
                    "strike": strike,
                    "ce_oi": ce_oi,
                    "ce_oi_change": ce.get("changeinOpenInterest", 0),
                    "ce_ltp": ce.get("lastPrice", 0),
                    "ce_iv": ce.get("impliedVolatility", 0),
                    "ce_volume": ce.get("totalTradedVolume", 0),
                    "pe_oi": pe_oi,
                    "pe_oi_change": pe.get("changeinOpenInterest", 0),
                    "pe_ltp": pe.get("lastPrice", 0),
                    "pe_iv": pe.get("impliedVolatility", 0),
                    "pe_volume": pe.get("totalTradedVolume", 0),
                })

        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0

        # Find max pain strike
        max_pain = self._calculate_max_pain(strikes_data)

        # ATM straddle price (tells you expected move)
        atm_data = next((s for s in strikes_data if s["strike"] == atm_strike), {})
        atm_straddle = atm_data.get("ce_ltp", 0) + atm_data.get("pe_ltp", 0)

        # Support/Resistance from OI
        strikes_sorted_by_put_oi = sorted(strikes_data, key=lambda x: x["pe_oi"], reverse=True)
        strikes_sorted_by_call_oi = sorted(strikes_data, key=lambda x: x["ce_oi"], reverse=True)

        return {
            "symbol": symbol,
            "underlying": underlying_value,
            "timestamp": datetime.now().isoformat(),
            "expiry_dates": expiry_dates[:4],  # Next 4 expiries
            "pcr": pcr,
            "pcr_interpretation": self._interpret_pcr(pcr),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "atm_strike": atm_strike,
            "atm_straddle_price": round(atm_straddle, 2),
            "expected_move_pct": round(atm_straddle / underlying_value * 100, 2) if underlying_value else 0,
            "max_pain_strike": max_pain,
            "key_resistance": strikes_sorted_by_call_oi[0]["strike"] if strikes_sorted_by_call_oi else None,
            "key_support": strikes_sorted_by_put_oi[0]["strike"] if strikes_sorted_by_put_oi else None,
            "top_5_ce_oi": [{"strike": s["strike"], "oi": s["ce_oi"], "iv": s["ce_iv"]} for s in strikes_sorted_by_call_oi[:5]],
            "top_5_pe_oi": [{"strike": s["strike"], "oi": s["pe_oi"], "iv": s["pe_iv"]} for s in strikes_sorted_by_put_oi[:5]],
            "strikes": strikes_data,
        }

    def _calculate_max_pain(self, strikes_data: list[dict]) -> Optional[float]:
        """Max pain = strike where total option buyers lose the most."""
        if not strikes_data:
            return None
        min_pain = float("inf")
        max_pain_strike = None
        for test_strike_data in strikes_data:
            test_strike = test_strike_data["strike"]
            total_loss = 0
            for s in strikes_data:
                # CE writers lose when price > strike
                if test_strike > s["strike"]:
                    total_loss += (test_strike - s["strike"]) * s["ce_oi"]
                # PE writers lose when price < strike
                if test_strike < s["strike"]:
                    total_loss += (s["strike"] - test_strike) * s["pe_oi"]
            if total_loss < min_pain:
                min_pain = total_loss
                max_pain_strike = test_strike
        return max_pain_strike

    def _interpret_pcr(self, pcr: float) -> str:
        if pcr > 1.5:
            return "extremely_bullish"
        elif pcr > 1.2:
            return "bullish"
        elif pcr > 0.8:
            return "neutral"
        elif pcr > 0.5:
            return "bearish"
        else:
            return "extremely_bearish"

    # ── PCR ───────────────────────────────────────────────────────────────────

    async def get_pcr(self, symbol: str = "NIFTY") -> float:
        """Get current Put-Call Ratio."""
        chain = await self.get_option_chain(symbol)
        return chain.get("pcr", 1.0)

    # ── India VIX ────────────────────────────────────────────────────────────

    async def get_india_vix(self) -> float:
        """Fetch current India VIX from NSE."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{NSE_BASE}/api/allIndices")
            resp.raise_for_status()
            data = resp.json()
            for index in data.get("data", []):
                if index.get("index") == "INDIA VIX":
                    return float(index.get("last", 14.0))
        except Exception as e:
            logger.warning(f"VIX fetch error: {e}")
        return 14.0

    # ── Market Indices ────────────────────────────────────────────────────────

    async def get_index_data(self) -> dict:
        """Get NIFTY, BANKNIFTY, VIX in one call."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{NSE_BASE}/api/allIndices")
            resp.raise_for_status()
            data = resp.json()

            result = {"nifty": 0.0, "banknifty": 0.0, "vix": 14.0, "finnifty": 0.0}
            name_map = {
                "NIFTY 50": "nifty",
                "NIFTY BANK": "banknifty",
                "INDIA VIX": "vix",
                "NIFTY FIN SERVICE": "finnifty",
            }
            for idx in data.get("data", []):
                key = name_map.get(idx.get("index", ""))
                if key:
                    result[key] = float(idx.get("last", 0))
            return result
        except Exception as e:
            logger.warning(f"Index data fetch error: {e}")
            return {"nifty": 22000.0, "banknifty": 47000.0, "vix": 14.0, "finnifty": 0.0}

    # ── IV Rank ───────────────────────────────────────────────────────────────

    async def get_iv_rank(self, symbol: str, lookback_days: int = 252) -> float:
        """
        Calculate IV Rank = (Current IV - 52W Low IV) / (52W High IV - 52W Low IV) * 100
        Uses cached option chain data.
        """
        chain = self._oi_cache.get(symbol, {})
        strikes = chain.get("strikes", [])
        if not strikes:
            return 50.0  # Neutral default

        current_iv = 0.0
        iv_count = 0
        for s in strikes:
            if s.get("ce_iv", 0) > 0:
                current_iv += s["ce_iv"]
                iv_count += 1
            if s.get("pe_iv", 0) > 0:
                current_iv += s["pe_iv"]
                iv_count += 1

        if iv_count > 0:
            current_iv = current_iv / iv_count
        # Without historical IV data, return a reasonable estimate
        # In production, store daily IV snapshots in the DB and compute true IV rank
        return min(max(current_iv, 0), 100)


# ─── NEWS SENTIMENT ──────────────────────────────────────────────────────────

class NewsSentimentAnalyzer:
    """
    Simple keyword-based news sentiment for Indian markets.
    In production, integrate with: TickerTape, Moneycontrol, or a paid news API.
    """

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
        """
        Returns a sentiment string for the AI agent context.
        In production, replace with real news API calls.
        """
        try:
            return await self._fetch_and_analyze()
        except Exception as e:
            logger.warning(f"Sentiment analysis error: {e}")
            return "Neutral market sentiment. No major news catalysts."

    async def _fetch_and_analyze(self) -> str:
        """
        Fetch headlines and compute sentiment score.
        Currently uses NSE announcements + a basic scorer.
        """
        headlines = await self._fetch_nse_announcements()
        if not headlines:
            return "No significant market announcements today."

        score = 0
        analyzed = []
        for h in headlines[:10]:
            h_lower = h.lower()
            bull_hits = sum(1 for kw in self.BULLISH_KEYWORDS if kw in h_lower)
            bear_hits = sum(1 for kw in self.BEARISH_KEYWORDS if kw in h_lower)
            score += bull_hits - bear_hits
            if bull_hits > bear_hits:
                analyzed.append(f"▲ {h[:60]}")
            elif bear_hits > bull_hits:
                analyzed.append(f"▼ {h[:60]}")

        if score > 2:
            sentiment = "Bullish"
        elif score < -2:
            sentiment = "Bearish"
        else:
            sentiment = "Neutral"

        top = "; ".join(analyzed[:3]) if analyzed else "No major corporate announcements"
        return f"{sentiment} sentiment (score: {score:+d}). Top news: {top}"

    async def _fetch_nse_announcements(self) -> list[str]:
        """Fetch corporate announcements from NSE."""
        try:
            async with httpx.AsyncClient(headers=NSE_HEADERS, timeout=10) as client:
                await client.get(f"{NSE_BASE}/")
                resp = await client.get(
                    f"{NSE_BASE}/api/home-corporate-announcements"
                    "?index=favAnnouncements"
                )
                data = resp.json()
                announcements = data.get("data", [])
                return [
                    f"{a.get('desc', '')} - {a.get('sm_name', '')}"
                    for a in announcements[:15]
                ]
        except Exception:
            return []
