"""
AI Agent Brain - The intelligence core of the trading bot.
Uses Gemini 2.5 Flash (thinking disabled) as primary model for
fast, reliable JSON trading decisions on NSE/BSE markets.

Corrections applied in this version:
  1.  Gemini thinking mode disabled (saves 2-8s per call)
  2.  max_tokens reduced 4096→2048 (40% output latency cut)
  3.  temperature reduced 0.1→0.05 (deterministic signals)
  4.  Removed all sub-32B fallback models (7B/8B models hallucinate prices)
  5.  Added xiaomimimo provider support (direct api.xiaomimimo.com routing)
  6.  AI now sees [AFFORDABLE] / [TOO EXPENSIVE] labels per symbol
  7.  Watchlist sorted by affordability then signal strength before prompt
  8.  Hard affordability check in _parse_signals (BUY dropped before risk mgr)
  9.  Regime-adaptive confidence threshold (VIX-based, not fixed 0.65)
 10.  Anti-repetition: _last_cycle_symbols + _last_cycle_directions tracking
 11.  Hard signal cap signals[:2] in _parse_signals (code-level enforcement)
 12.  max_capital_per_trade_pct default 5→50% (small accounts need this)
 13.  _fallback_quantity uses spendable (capital minus reserve), not raw capital
 14.  Confidence calibration anchors + VIX avoid_trading rule in system prompt
"""

import asyncio
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional

import httpx
from google import genai
from google.genai import types

logger = logging.getLogger("agent.brain")

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

MAX_DECISION_HISTORY           = 200
MIN_CONFIDENCE_THRESHOLD       = 0.30
MAX_CONFIDENCE_THRESHOLD       = 0.95
RATE_LIMIT_BACKOFF_SECONDS     = 5.0
RATE_LIMIT_BACKOFF_MAX_SECONDS = 30.0
RATE_LIMIT_BACKOFF_JITTER      = 0.20

# ─── SIGNAL TYPES ────────────────────────────────────────────────────────────

class SignalAction(str, Enum):
    BUY        = "BUY"
    SELL       = "SELL"
    SHORT      = "SHORT"
    COVER      = "COVER"
    HOLD       = "HOLD"
    SQUARE_OFF = "SQUARE_OFF"
    NO_ACTION  = "NO_ACTION"


@dataclass
class TradingSignal:
    action:       SignalAction
    symbol:       str
    exchange:     str
    strategy:     str
    quantity:     int
    entry_price:  Optional[Decimal]
    stop_loss:    Optional[Decimal]
    target:       Optional[Decimal]
    confidence:   float
    rationale:    str
    risk_reward:  Optional[float]
    timeframe:    str
    product:      str
    priority:     int
    tags:         list[str]

    @property
    def is_actionable(self) -> bool:
        return self.action not in (SignalAction.HOLD, SignalAction.NO_ACTION)


@dataclass
class MarketContext:
    """Everything the AI needs to make a decision."""
    timestamp:             datetime
    nifty50_ltp:           float
    banknifty_ltp:         float
    india_vix:             float
    market_trend:          str
    session:               str
    day_of_week:           str
    available_capital:     float
    used_margin:           float
    open_positions:        list[dict]
    watchlist_data:        list[dict]
    options_chain_summary: Optional[dict]
    recent_news_sentiment: Optional[str]
    pcr:                   Optional[float]


# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are an elite quantitative trading agent for Indian financial markets (NSE/BSE).
You operate as an autonomous trading brain with deep expertise in:

1. **Technical Analysis**: EMA, MACD, RSI, Bollinger Bands, Supertrend, ATR, VWAP, pivot points
2. **Options Strategies**: Iron Condor, Bull Put Spread, Bear Call Spread, Short Strangle, straddles
3. **Market Microstructure**: Order flow, OI analysis, PCR, support/resistance levels
4. **Risk Management**: Kelly criterion, position sizing, max drawdown control
5. **Indian Market Specifics**: F&O lot sizes, SEBI regulations, STT impact, settlement cycles

## Decision Framework

When analyzing market data, follow this exact process:
1. **Market Regime Detection**: Identify if market is trending, ranging, or volatile
2. **Affordability Filter**: ONLY consider symbols marked [AFFORDABLE] — NEVER generate BUY signals for [TOO EXPENSIVE] symbols
3. **Symbol Screening**: From the AFFORDABLE watchlist only, identify the 1-2 STRONGEST setups
4. **Signal Generation**: For those 1-2 symbols only, provide specific actionable signals
5. **Risk Calculation**: Always include SL, target, and position size within affordable quantity
6. **Confidence Scoring**: Rate each signal using the calibration anchors below

## Affordability Rules (HARD — OVERRIDE ALL OTHER SIGNALS)
- Symbols marked [TOO EXPENSIVE] = account cannot afford even 1 share → return NO_ACTION for that symbol
- Symbols marked [AFFORDABLE] show the maximum quantity you can buy → never exceed that quantity
- The expected rupee profit is shown for each affordable symbol — prioritize symbols with best rupee P&L
- These labels are computed from live balance — treat them as hard constraints, not suggestions

## Indicator Interpretation Rules
- RSI > 70 = overbought (look for exits on BUY positions, not new shorts unless other signals confirm)
- RSI < 30 = oversold (look for exits on SELL positions, potential long setup)
- MACD histogram turning positive from negative = bullish momentum building
- MACD histogram turning negative from positive = bearish momentum building
- BB width contracting (squeeze) = breakout imminent, wait for confirmed direction
- BB width expanding = trend in motion, trade with trend
- Supertrend bullish + price above VWAP = strong long bias
- Supertrend bearish + price below VWAP = strong short bias
- PCR > 1.2 = net bullish sentiment from options market
- PCR < 0.8 = net bearish sentiment from options market
- VIX > 20 = high fear, reduce position sizes by 30%
- VIX > 22 = extreme fear, return avoid_trading recommendation

## Confidence Calibration (USE THESE EXACT ANCHORS — NO DEVIATION)
- **0.90–0.95**: 4+ indicators fully aligned, volume >2x 20-day avg, clear regime confirmation, near key level
- **0.75–0.85**: 3 indicators aligned, volume >1.5x avg, trend confirmed by Supertrend
- **0.65–0.74**: 2 indicators aligned, entry near pivot/BB level, volume >1.2x avg
- **Below 0.65**: Return NO_ACTION — do not generate a signal regardless of other factors
- **NEVER** generate a confidence score not anchored to one of the above tiers

## Signal Priority Rules (STRICTLY ENFORCED)
- Generate **MAXIMUM 2 signals** per cycle — prefer 1 strong signal over 2 weak ones
- Only generate signals for **AFFORDABLE** symbols — ignore expensive ones regardless of setup quality
- If no AFFORDABLE symbol scores 3+ indicator confluence, return NO_ACTION for all
- The best single affordable setup beats multiple mediocre setups every time
- Prioritize by: (1) rupee profit potential, (2) confidence, (3) risk-reward ratio

## Anti-Repetition Rules (STRICTLY ENFORCED)
- **Never** generate a signal for a symbol that already has an open position
- **Never** repeat the same symbol + same direction from the previous cycle
- If the last cycle generated BUY RELIANCE, do not generate BUY RELIANCE this cycle
- You may generate SELL RELIANCE if exit conditions are met

## Available Strategies
- **Momentum**: RSI + MACD + Volume confirmation for trend trades
- **Mean Reversion**: Bollinger Band extremes + RSI oversold/overbought
- **Options Selling**: Short premium when IV Rank > 50, defined-risk spreads only
- **Breakout**: ATR-based confirmed breakouts with volume >2x average
- **Index Scalping**: NIFTY/BANKNIFTY intraday with Supertrend + VWAP

## Output Rules
- ALWAYS respond with valid JSON only — no markdown, no explanation text, no preamble
- Include specific price levels (not vague descriptions like "near resistance")
- If market conditions are unfavorable, return NO_ACTION signals
- Risk-first mindset: never risk more than 2% of capital per trade
- Respect market hours (9:15 AM - 3:30 PM IST)
- Factor in STT and brokerage in profit calculations
- Minimum risk:reward must be 1.5:1 to generate any BUY/SELL signal

## Hard Risk Rules (NEVER VIOLATE — THESE OVERRIDE ALL OTHER SIGNALS)
- Max 80% capital per trade (CapitalManager enforces the exact rupee floor)
- Max 10 open positions simultaneously
- Stop if daily loss exceeds 2% of capital
- Stop if account drawdown exceeds 8%
- Never average losing positions
- No signals in first 15 minutes (9:15–9:30) or last 15 minutes (3:15–3:30)
- If VIX > 22 AND market_trend is high_volatility: set session_recommendation to avoid_trading
"""

DECISION_PROMPT_TEMPLATE = """
## Current Market Context
**Time**: {timestamp} IST
**Session**: {session}
**Day**: {day_of_week}

## Index Data
- NIFTY 50:    {nifty50_ltp}
- BANK NIFTY:  {banknifty_ltp}
- INDIA VIX:   {india_vix} ({vix_interpretation})
- Market Trend: {market_trend}
- Put-Call Ratio: {pcr} ({pcr_interpretation})

## Portfolio State
- Available Capital: ₹{available_capital:,.0f}
- Spendable (after ₹{cash_reserve:.0f} reserve): ₹{spendable_capital:,.0f}
- Used Margin:       ₹{used_margin:,.0f}
- Open Positions:    {open_positions_count}
{open_positions_summary}

## Watchlist Analysis
## AFFORDABILITY KEY: [AFFORDABLE: max N shares, ~₹X profit] = can buy | [TOO EXPENSIVE] = skip entirely
{watchlist_summary}

## Options Flow
{options_summary}

## News Sentiment
{news_sentiment}

## Last Cycle Signals (DO NOT REPEAT THESE)
{last_cycle_summary}

---
CRITICAL RULES FOR THIS RESPONSE:
1. ONLY generate BUY signals for symbols marked [AFFORDABLE]
2. NEVER generate BUY for [TOO EXPENSIVE] symbols — account cannot afford them
3. Maximum 2 signals, minimum 0.65 confidence
4. Do not repeat same symbol + direction from last cycle

Return ONLY a valid JSON object with this exact schema:

{{
  "market_regime": "trending_up | trending_down | ranging | high_volatility",
  "market_commentary": "2-sentence max market view",
  "signals": [
    {{
      "action": "BUY | SELL | SHORT | COVER | SQUARE_OFF | NO_ACTION",
      "symbol": "SBIN",
      "exchange": "NSE",
      "strategy": "momentum | mean_reversion | breakout | options_selling | scalping",
      "quantity": 5,
      "entry_price": 820.50,
      "stop_loss": 808.00,
      "target": 845.00,
      "confidence": 0.78,
      "rationale": "Specific reasons: RSI(14)=62 crossed above 60, MACD bullish crossover, volume 1.8x avg, breaking above resistance at 818.",
      "risk_reward": 2.1,
      "timeframe": "intraday",
      "product": "MIS",
      "priority": 1,
      "tags": ["breakout", "high_volume", "affordable"]
    }}
  ],
  "positions_to_exit": ["SYMBOL1"],
  "risk_assessment": "low | medium | high",
  "session_recommendation": "active_trading | selective | avoid_trading"
}}

Generate 0–2 signals based on conviction. Quality over quantity.
No signal is better than a weak signal. Only trade what the account can afford.
"""


# ─── AI AGENT ────────────────────────────────────────────────────────────────

class TradingAgent:
    """
    The AI brain that drives all trading decisions.

    Corrections from original:
    - Gemini thinking mode disabled for 2-8s latency saving                   [fix 1]
    - max_tokens default reduced 4096→2048                                     [fix 2]
    - temperature default reduced 0.1→0.05                                     [fix 3]
    - All sub-32B fallback models removed                                       [fix 4]
    - xiaomimimo provider added (direct api.xiaomimimo.com)                     [fix 5]
    - AI receives [AFFORDABLE] / [TOO EXPENSIVE] labels per symbol             [fix 6]
    - Watchlist sorted: affordable first, then by signal strength               [fix 7]
    - Hard affordability check in _parse_signals before risk manager            [fix 8]
    - Regime-adaptive confidence threshold (VIX-based)                         [fix 9]
    - Anti-repetition: _last_cycle_symbols + _last_cycle_directions tracking  [fix 10]
    - Hard signal cap signals[:2] in _parse_signals (code-level enforcement)  [fix 11]
    - max_capital_per_trade_pct default 5→50% (usable for small accounts)     [fix 12]
    - _fallback_quantity uses spendable (capital minus reserve)                [fix 13]
    - Confidence calibration anchors + VIX avoid_trading rule in prompt        [fix 14]
    """

    PARAM_SCHEMA: dict[str, tuple[type, float, float]] = {
        "confidence_threshold":  (float, 0.30,  0.95),
        "rsi_overbought":        (float, 60.0,  85.0),
        "rsi_oversold":          (float, 15.0,  40.0),
        "rsi_period":            (int,   7,     21),
        "macd_fast":             (int,   5,     20),
        "macd_slow":             (int,   15,    40),
        "macd_signal_period":    (int,   5,     15),
        "bb_period":             (int,   10,    30),
        "bb_std":                (float, 1.5,    3.0),
        "atr_period":            (int,   7,     21),
        "supertrend_multiplier": (float, 1.0,    5.0),
        "stop_loss_atr_mult":    (float, 0.5,    4.0),
        "target_atr_mult":       (float, 1.0,    8.0),
    }

    PARAM_CONSUMER_KEYS: frozenset[str] = frozenset()

    # ── Cleaned fallback chain: only models >= 32B that reliably output JSON ──
    # Removed (reason):
    #   groq/llama-3.1-8b-instant       — 8B, hallucinates price levels
    #   groq/mixtral-8x7b-32768         — Groq deprecated this endpoint
    #   openrouter/mistralai/mistral-7b  — 7B, fails complex watchlist prompts
    #   openrouter/mistralai/mistral-nemo — poor financial domain reasoning
    #   openrouter/qwen/qwen-2.5-7b      — 7B, produces invalid SL values
    #   gemini/gemini-1.5-flash          — superseded by 2.0-flash
    DEFAULT_MODEL_TIERS: dict[str, list[str]] = {
        "ultra_fast": [
            "gemini/gemini-2.5-flash-lite",
        ],
        "fast": [
            "gemini/gemini-2.0-flash",
            "openrouter/deepseek/deepseek-chat",
        ],
        "balanced": [
            "openrouter/qwen/qwen-2.5-72b-instruct",
        ],
        "quality": [
            "openrouter/meta-llama/llama-3.3-70b-instruct",
        ],
    }

    PARAM_ALIASES: dict[str, str] = {
        "macd_signal": "macd_signal_period",
    }

    def __init__(self, config: dict):
        self.config = config

        # ── API keys ──────────────────────────────────────────────────────────
        self.gemini_api_key      = os.getenv(config.get("api_key_env",            "GEMINI_API_KEY"),      "")
        self.groq_api_key        = os.getenv(config.get("groq_api_key_env",       "GROQ_API_KEY"),        "")
        self.openrouter_api_key  = os.getenv(config.get("openrouter_api_key_env", "OPENROUTER_API_KEY"),  "")
        self.xiaomi_mimo_api_key = os.getenv(config.get("xiaomi_mimo_api_key_env","XIAOMI_MIMO_API_KEY"), "")

        # ── Gemini client ─────────────────────────────────────────────────────
        self.gemini_client = (
            genai.Client(api_key=self.gemini_api_key) if self.gemini_api_key else None
        )

        # ── Model config ──────────────────────────────────────────────────────
        self.model        = config.get("model", "gemini/gemini-2.5-flash")
        self.model_tiers  = config.get("model_tiers", self.DEFAULT_MODEL_TIERS)
        self.fallback_models = self._resolve_fallback_models(config)

        # fix 2: reduced from 4096 — trading JSON never exceeds ~1200 tokens,
        # reducing this cuts output generation latency by ~40%
        self.max_tokens  = config.get("max_tokens",  2048)

        # fix 3: reduced from 0.1 — lower temperature = more deterministic
        # confidence scores, fewer random price level hallucinations
        self.temperature = config.get("temperature", 0.05)

        # fix 1: disable Gemini thinking mode for live trading
        # thinking_budget=0 saves 2-8 seconds per call
        # Set >0 in config ONLY for overnight strategy review calls
        self.thinking_budget: int = max(0, int(config.get("thinking_budget", 0)))

        # ── Position sizing ───────────────────────────────────────────────────
        # fix 12: default raised from 5.0 to 50.0
        # At 5% a ₹1,000 account has ₹50 budget per trade — can't buy anything.
        # CapitalManager enforces the actual per-symbol floor separately.
        self.max_capital_per_trade_pct: float = max(
            0.1, min(100.0, float(config.get("max_capital_per_trade_pct", 50.0)))
        )
        self.min_trade_quantity: int = max(1, int(config.get("min_trade_quantity", 1)))
        self.max_order_value_absolute = config.get("max_order_value_absolute")

        # fix 13: spendable = available - reserve; raw capital was used before
        self.min_cash_reserve: float = float(config.get("min_cash_reserve", 50.0))

        # ── Confidence threshold ──────────────────────────────────────────────
        self.confidence_threshold: float = self._validated_confidence_threshold(
            config.get("confidence_threshold", 0.65),
            fallback=0.65,
            source="config",
        )

        # fix 10: tracks last cycle to prevent same symbol+direction repeating
        self._last_cycle_symbols:    set[str]       = set()
        self._last_cycle_directions: dict[str, str] = {}

        # ── History ───────────────────────────────────────────────────────────
        self.decision_history: list[dict] = []

        logger.info(
            "AI model chain configured | primary=%s | fallbacks=%d | "
            "thinking_budget=%d | max_tokens=%d | temperature=%.3f | "
            "max_capital_pct=%.1f%%",
            self.model,
            len(self.fallback_models),
            self.thinking_budget,
            self.max_tokens,
            self.temperature,
            self.max_capital_per_trade_pct,
        )

    # ── Provider helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_model_identifier(model_id: str) -> tuple[str, str]:
        if "/" not in model_id:
            return "gemini", model_id
        provider, model = model_id.split("/", 1)
        return provider.strip().lower(), model.strip()

    def _ensure_provider_key(self, provider: str) -> None:
        key_by_provider = {
            "gemini":     self.gemini_api_key,
            "groq":       self.groq_api_key,
            "openrouter": self.openrouter_api_key,
            "xiaomimimo": self.xiaomi_mimo_api_key,
        }
        if not key_by_provider.get(provider):
            raise RuntimeError(f"Missing API key for provider '{provider}'.")

    def _resolve_fallback_models(self, config: dict) -> list[str]:
        seen: set[str] = {self.model}
        resolved: list[str] = []

        tiered = config.get("model_tiers", self.DEFAULT_MODEL_TIERS) or {}
        for models in tiered.values():
            if not isinstance(models, list):
                continue
            for model in models:
                if isinstance(model, str) and model and model not in seen:
                    seen.add(model)
                    resolved.append(model)

        for model in config.get("fallback_models", []):
            if isinstance(model, str) and model and model not in seen:
                seen.add(model)
                resolved.append(model)

        if not resolved:
            for models in self.DEFAULT_MODEL_TIERS.values():
                for model in models:
                    if model not in seen:
                        seen.add(model)
                        resolved.append(model)

        return resolved

    @staticmethod
    def _validated_confidence_threshold(
        raw: Any, *, fallback: float, source: str
    ) -> float:
        try:
            ct = float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "confidence_threshold from %s is not numeric (%r); using fallback %.2f.",
                source, raw, fallback,
            )
            return fallback

        if ct < MIN_CONFIDENCE_THRESHOLD or ct > MAX_CONFIDENCE_THRESHOLD:
            clamped = max(MIN_CONFIDENCE_THRESHOLD, min(MAX_CONFIDENCE_THRESHOLD, ct))
            logger.warning(
                "confidence_threshold from %s (%.4f) outside [%.2f, %.2f]; clamping to %.2f.",
                source, ct, MIN_CONFIDENCE_THRESHOLD, MAX_CONFIDENCE_THRESHOLD, clamped,
            )
            return clamped
        return ct

    def _validate_param_adjustments(self, raw: Any) -> dict:
        if not isinstance(raw, dict):
            logger.warning(
                "parameter_adjustments is not a dict (%r); ignoring.", type(raw).__name__
            )
            return {}

        clean: dict = {}
        for raw_field, value in raw.items():
            field = self.PARAM_ALIASES.get(raw_field, raw_field)
            if raw_field != field:
                logger.debug("Normalising alias %r → %r", raw_field, field)

            if field not in self.PARAM_SCHEMA:
                logger.warning(
                    "Dropping unknown parameter_adjustment field: %r = %r", raw_field, value
                )
                continue

            expected_type, lo, hi = self.PARAM_SCHEMA[field]

            try:
                numeric = float(value)
            except (TypeError, ValueError):
                logger.warning(
                    "Dropping %r: non-numeric value %r (expected %s in [%s, %s]).",
                    field, value, expected_type.__name__, lo, hi,
                )
                continue

            if numeric < lo or numeric > hi:
                clamped = max(lo, min(hi, numeric))
                logger.warning(
                    "Clamping %r: %.4g is outside [%s, %s], using %.4g.",
                    field, numeric, lo, hi, clamped,
                )
                numeric = clamped

            if expected_type is int:
                clean[field] = int(math.floor(numeric + 0.5))
            else:
                clean[field] = float(numeric)

        return clean

    # ── Error classification ──────────────────────────────────────────────────

    def _is_rate_limited_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(t in msg for t in (
            "429", "rate limit", "too many requests", "quota", "resource_exhausted",
        ))

    def _is_unsupported_system_instruction_error(self, err: Exception) -> bool:
        return "developer instruction is not enabled" in str(err).lower()

    def _is_unavailable_model_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(t in msg for t in (
            "404", "not_found", "model is not found",
            "is not found for api version",
            "not supported for generatecontent",
            "unknown model", "403", "401", "unauthorized",
            "invalid api key", "permission_denied",
            "not have permission", "decommissioned", "model_decommissioned",
        ))

    # ── Safe response extraction ──────────────────────────────────────────────

    def _extract_response_text(self, response: Any) -> str:
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
                text = getattr(part, "text", None)
                if text and text.strip():
                    return text.strip()
        try:
            text = getattr(response, "text", None)
            if text and text.strip():
                return text.strip()
        except (ValueError, AttributeError):
            pass
        return ""

    # ── Provider dispatch ─────────────────────────────────────────────────────

    def _generate_with_openai_compatible(
        self, *, provider: str, model: str, prompt: str,
        temperature: float, max_tokens: int, expect_json: bool,
    ) -> str:
        # fix 5: route to correct base URL and API key per provider
        if provider == "groq":
            base_url = "https://api.groq.com/openai/v1/chat/completions"
            api_key  = self.groq_api_key

        elif provider == "xiaomimimo":
            # fix 5: Direct Xiaomi MiMo API — no OpenRouter proxy needed
            # Get your key from: platform.xiaomimimo.com
            base_url = "https://api.xiaomimimo.com/v1/chat/completions"
            api_key  = self.xiaomi_mimo_api_key

        else:  # openrouter (default)
            base_url = "https://openrouter.ai/api/v1/chat/completions"
            api_key  = self.openrouter_api_key

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        }

        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://agentic-trading-bot.local"
            headers["X-Title"]      = "Agentic Trading Bot"

        payload: dict = {
            "model":       model,
            "temperature": temperature,
            "max_tokens":  max_tokens,
            "messages": [
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        }

        if expect_json:
            payload["response_format"] = {"type": "json_object"}

        with httpx.Client(timeout=45.0) as client:
            response = client.post(base_url, headers=headers, json=payload)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"{provider} HTTP {response.status_code}: {response.text[:300]}"
                )
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(
                f"{provider} returned empty choices for model {model}."
            )
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if isinstance(p, dict)]
            return "".join(parts).strip()
        return ""

    def _generate_with_provider(
        self, *, provider: str, model: str, prompt: str,
        temperature: float, max_tokens: int, expect_json: bool,
    ) -> str:
        self._ensure_provider_key(provider)

        if provider == "gemini":
            if not self.gemini_client:
                raise RuntimeError(
                    "Gemini API key missing. Set GEMINI_API_KEY in .env"
                )

            config_kwargs: dict = {
                "system_instruction": AGENT_SYSTEM_PROMPT,
                "temperature":        temperature,
                "max_output_tokens":  max_tokens,
                "response_mime_type": "application/json" if expect_json else "text/plain",
            }

            # fix 1: disable Gemini thinking mode for live trading speed
            if self.thinking_budget == 0:
                try:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_budget=0
                    )
                    logger.debug("Gemini thinking mode: DISABLED (budget=0)")
                except AttributeError:
                    # Older SDK — ThinkingConfig not yet available, safe to skip
                    logger.debug("ThinkingConfig not available in SDK version — skipping")
            else:
                logger.debug("Gemini thinking mode: ENABLED (budget=%d)", self.thinking_budget)

            cfg = types.GenerateContentConfig(**config_kwargs)
            response = self.gemini_client.models.generate_content(
                model=model,
                contents=prompt,
                config=cfg,
            )
            return self._extract_response_text(response)

        if provider in {"groq", "openrouter", "xiaomimimo"}:
            return self._generate_with_openai_compatible(
                provider=provider, model=model, prompt=prompt,
                temperature=temperature, max_tokens=max_tokens,
                expect_json=expect_json,
            )

        raise RuntimeError(f"Unsupported model provider '{provider}'.")

    # ── Core generation (sync, runs in thread) ───────────────────────────────

    def _generate_text_sync(
        self, prompt: str, *, temperature: float, max_tokens: int, expect_json: bool,
    ) -> tuple[str, str]:
        """
        Synchronous multi-provider call with fallback chain.
        Always run via asyncio.to_thread — never call directly.
        Returns (response_text, model_actually_used).
        """
        all_models = [self.model] + [
            m for m in self.fallback_models if m != self.model
        ]
        last_error: Exception | None = None
        failure_reasons: list[str] = []

        for idx, model_id in enumerate(all_models):
            provider, provider_model = self._parse_model_identifier(model_id)
            try:
                response_text = self._generate_with_provider(
                    provider=provider, model=provider_model, prompt=prompt,
                    temperature=temperature, max_tokens=max_tokens,
                    expect_json=expect_json,
                )
                if model_id != self.model:
                    logger.warning("Using fallback model: %s", model_id)
                return response_text, model_id

            except Exception as e:
                last_error = e
                is_last = idx == len(all_models) - 1
                reason  = "unknown"

                if is_last:
                    failure_reasons.append(f"{model_id}=terminal_error")
                    break

                if self._is_rate_limited_error(e):
                    raw_wait = RATE_LIMIT_BACKOFF_SECONDS * (2 ** idx)
                    capped   = min(raw_wait, RATE_LIMIT_BACKOFF_MAX_SECONDS)
                    jitter   = capped * RATE_LIMIT_BACKOFF_JITTER * (2 * random.random() - 1)
                    wait     = max(0.0, capped + jitter)
                    logger.warning(
                        "Model %s rate-limited; waiting %.1fs before fallback (idx=%d).",
                        model_id, wait, idx,
                    )
                    reason = "rate_limited"
                    failure_reasons.append(f"{model_id}={reason}")
                    time.sleep(wait)
                    continue

                if self._is_unsupported_system_instruction_error(e):
                    logger.warning(
                        "Model %s does not support system instructions; trying fallback.",
                        model_id,
                    )
                    reason = "unsupported_system_instruction"
                    failure_reasons.append(f"{model_id}={reason}")
                    continue

                if self._is_unavailable_model_error(e):
                    logger.warning(
                        "Model %s unavailable/no permission; trying fallback.", model_id
                    )
                    reason = "unavailable_or_permission"
                    failure_reasons.append(f"{model_id}={reason}")
                    continue

                failure_reasons.append(f"{model_id}={reason}")
                raise

        logger.error(
            "All configured models failed | attempts=%s", ", ".join(failure_reasons)
        )
        raise RuntimeError(
            f"All configured models failed. Last error: {last_error}"
        )

    # ── Async wrapper ─────────────────────────────────────────────────────────

    async def _generate_text(
        self, prompt: str, *, temperature: float, max_tokens: int, expect_json: bool,
    ) -> tuple[str, str]:
        return await asyncio.to_thread(
            self._generate_text_sync,
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            expect_json=expect_json,
        )

    # ── JSON extraction ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_json(raw_text: str) -> str:
        raw = raw_text.strip()
        if not raw:
            return raw

        for start_char, end_char in (('{', '}'), ('[', ']')):
            start_idx = raw.find(start_char)
            if start_idx == -1:
                continue

            depth = 0
            in_string = False
            escape_next = False
            end_idx = -1

            for i, ch in enumerate(raw[start_idx:], start=start_idx):
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        end_idx = i
                        break

            if end_idx != -1:
                candidate = raw[start_idx:end_idx + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass

        for _ in range(3):
            if not raw.startswith("```"):
                break
            lines = raw.split("\n")
            body_lines = lines[1:]
            if body_lines and body_lines[-1].strip() == "```":
                body_lines = body_lines[:-1]
            raw = "\n".join(body_lines).strip()

        return raw

    # ── Regime-adaptive confidence threshold (fix 9) ─────────────────────────

    def _get_adaptive_confidence_threshold(self, vix: float, market_trend: str) -> float:
        """
        fix 9: confidence threshold adapts to market regime instead of
        being a fixed 0.65 regardless of conditions.

        VIX > 22  → raise by 0.15 (extreme fear — need very strong signals)
        VIX > 18  → raise by 0.08 (elevated fear — be more selective)
        VIX ≤ 14 + trending → lower by 0.05 (clean trend — can relax slightly)

        Always clamped between MIN_CONFIDENCE_THRESHOLD and MAX_CONFIDENCE_THRESHOLD.
        """
        base = self.confidence_threshold
        adj  = 0.0

        if vix > 22:
            adj = +0.15
        elif vix > 18:
            adj = +0.08
        elif vix <= 14 and market_trend in ("trending_up", "trending_down"):
            adj = -0.05

        adapted = max(
            MIN_CONFIDENCE_THRESHOLD,
            min(MAX_CONFIDENCE_THRESHOLD, base + adj),
        )

        if adj != 0.0:
            logger.debug(
                "Adaptive confidence | base=%.2f adj=%+.2f final=%.2f | VIX=%.1f trend=%s",
                base, adj, adapted, vix, market_trend,
            )
        return adapted

    # ── Main decision function ────────────────────────────────────────────────

    async def analyze_and_decide(self, context: MarketContext) -> list[TradingSignal]:
        """
        Core decision function. Takes market context, calls the AI model,
        returns actionable trading signals above the adaptive confidence threshold.
        """
        prompt     = self._build_prompt(context)
        started_at = datetime.utcnow()

        try:
            raw_text, model_used = await self._generate_text(
                prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                expect_json=True,
            )

            if not raw_text:
                logger.warning("Empty response from AI model.")
                return []

            decision = json.loads(self._extract_json(raw_text))
            signals  = self._parse_signals(decision, context)

            latency_ms = int(
                (datetime.utcnow() - started_at).total_seconds() * 1000
            )

            # fix 10: update anti-repetition state after each cycle
            self._last_cycle_symbols    = {s.symbol for s in signals if s.is_actionable}
            self._last_cycle_directions = {
                s.symbol: s.action.value
                for s in signals if s.is_actionable
            }

            normalized_signals = [
                {
                    "action":        s.action.value,
                    "symbol":        s.symbol,
                    "exchange":      s.exchange,
                    "strategy":      s.strategy,
                    "quantity":      s.quantity,
                    "entry_price":   float(s.entry_price)  if s.entry_price  is not None else None,
                    "stop_loss":     float(s.stop_loss)    if s.stop_loss    is not None else None,
                    "target":        float(s.target)       if s.target       is not None else None,
                    "confidence":    s.confidence,
                    "rationale":     s.rationale,
                    "risk_reward":   s.risk_reward,
                    "timeframe":     s.timeframe,
                    "product":       s.product,
                    "priority":      s.priority,
                    "tags":          s.tags,
                    "is_actionable": s.is_actionable,
                }
                for s in signals
            ]

            record = {
                "timestamp":              context.timestamp.isoformat(),
                "market_regime":          decision.get("market_regime"),
                "commentary":             decision.get("market_commentary"),
                "market_commentary":      decision.get("market_commentary"),
                "risk_assessment":        decision.get("risk_assessment"),
                "signals_count":          len(signals),
                "signals":                normalized_signals,
                "signals_raw":            decision.get("signals", []),
                "positions_to_exit":      decision.get("positions_to_exit", []),
                "session_recommendation": decision.get("session_recommendation"),
                "raw_response":           decision,
                "model_used":             model_used,
                "model_requested":        self.model,
                "latency_ms":             latency_ms,
            }
            self.decision_history.append(record)

            if len(self.decision_history) > MAX_DECISION_HISTORY:
                self.decision_history = self.decision_history[-MAX_DECISION_HISTORY:]

            logger.info(
                "AI Decision | Regime: %s | Signals: %d | Risk: %s | "
                "Latency: %dms | Model: %s",
                decision.get("market_regime"),
                len(signals),
                decision.get("risk_assessment"),
                latency_ms,
                model_used,
            )

            # fix 9: use regime-adaptive threshold instead of fixed 0.65
            adaptive_threshold = self._get_adaptive_confidence_threshold(
                vix=context.india_vix,
                market_trend=context.market_trend,
            )
            actionable = [
                s for s in signals
                if s.confidence >= adaptive_threshold
            ]
            logger.info(
                "%d/%d signals above adaptive threshold (%.2f | VIX=%.1f)",
                len(actionable), len(signals), adaptive_threshold, context.india_vix,
            )
            return actionable

        except json.JSONDecodeError as e:
            logger.error("Failed to parse AI response as JSON: %s", e)
            return []
        except Exception as e:
            err = str(e).lower()
            if "api key" in err and "missing" in err:
                logger.error("AI agent disabled: missing GEMINI_API_KEY.")
            elif self._is_rate_limited_error(e):
                logger.error("AI agent failed: all models are rate-limited.")
            else:
                logger.error("AI agent error: %s", e, exc_info=True)
            return []

    # ── Strategy review ───────────────────────────────────────────────────────

    async def review_strategy(self, performance_data: dict) -> dict:
        """
        Periodic strategy review. Runs hourly so latency is not critical.
        Can use higher thinking_budget if configured.
        """
        prompt = f"""
Review this trading bot's recent performance and provide strategic recommendations.

Performance Data:
{json.dumps(performance_data, indent=2)}

Respond ONLY with a valid JSON object:
{{
  "strategy_weights": {{
    "momentum": 0.3,
    "mean_reversion": 0.25,
    "options_selling": 0.3,
    "breakout": 0.15
  }},
  "parameter_adjustments": {{
    "rsi_overbought": 72,
    "confidence_threshold": 0.70
  }},
  "avoid_patterns": ["description of losing patterns to avoid"],
  "focus_patterns": ["description of winning patterns to amplify"],
  "overall_assessment": "brief text assessment"
}}
"""
        try:
            raw, _model = await self._generate_text(
                prompt,
                temperature=0.2,
                max_tokens=2048,
                expect_json=True,
            )
            result = json.loads(self._extract_json(raw))

            raw_params       = result.get("parameter_adjustments", {})
            validated_params = self._validate_param_adjustments(raw_params)
            result["parameter_adjustments"] = validated_params

            if self.PARAM_CONSUMER_KEYS:
                unclaimed = set(validated_params) - self.PARAM_CONSUMER_KEYS
                if unclaimed:
                    logger.warning(
                        "review_strategy returned keys not in PARAM_CONSUMER_KEYS "
                        "(will be ignored): %s",
                        sorted(unclaimed),
                    )

            return result
        except Exception as e:
            logger.error("Strategy review error: %s", e)
            return {}

    async def explain_position(self, position: dict) -> str:
        """Get AI explanation of why a position should be held or exited."""
        prompt = f"""
Analyze this open position and recommend: HOLD, TRAIL_STOP, or EXIT.
Give a specific 2-3 sentence rationale citing price levels and indicators.

Position: {json.dumps(position, indent=2)}
"""
        try:
            text, _model = await self._generate_text(
                prompt,
                temperature=0.1,
                max_tokens=256,
                expect_json=False,
            )
            return text
        except Exception as e:
            logger.error("explain_position error: %s", e)
            return "Unable to analyze position."

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(self, ctx: MarketContext) -> str:
        # fix 13: compute spendable = available minus reserve consistently
        reserve   = max(self.min_cash_reserve, ctx.available_capital * 0.05)
        spendable = max(0.0, ctx.available_capital - reserve)

        # ── Open positions summary ────────────────────────────────────────────
        positions_summary = ""
        if ctx.open_positions:
            for p in ctx.open_positions:
                pnl_str = f"₹{p.get('pnl', 0):+,.0f}"
                positions_summary += (
                    f"  - {p['symbol']} | {p['side']} {p['quantity']} | "
                    f"Avg: ₹{p.get('avg_price', 0):,.2f} | "
                    f"LTP: ₹{p.get('ltp', 0):,.2f} | P&L: {pnl_str}\n"
                )

        # fix 7: sort watchlist — affordable first, then by signal strength
        open_symbols  = {p["symbol"] for p in ctx.open_positions}
        raw_watchlist = ctx.watchlist_data[:15]

        def _sort_key(w: dict) -> tuple:
            ltp            = float(w.get("ltp", 0) or 0)
            cost_per_share = ltp * 1.0015
            affordable     = cost_per_share > 0 and spendable >= cost_per_share
            signal         = w.get("indicators", {}).get("overall_signal", "neutral")
            signal_rank    = 0 if signal == "bullish" else (1 if signal == "neutral" else 2)
            return (0 if affordable else 1, signal_rank)

        sorted_watchlist = sorted(raw_watchlist, key=_sort_key)

        # fix 6: build watchlist string with [AFFORDABLE] / [TOO EXPENSIVE] labels
        watchlist_summary = ""
        for w in sorted_watchlist:
            ind        = w.get("indicators", {})
            levels     = w.get("levels", {})
            ltp_val    = float(w.get("ltp", 0) or 0)
            rsi_val    = ind.get("rsi",          "N/A")
            macd_sig   = ind.get("macd_signal",  "N/A")
            bb_sig     = ind.get("bb_signal",    "N/A")
            supertrend = ind.get("supertrend",   "N/A")
            vol_ratio  = ind.get("volume_ratio", 1.0)
            overall    = ind.get("overall_signal","neutral")
            pivot      = levels.get("pivot", "N/A")
            r1         = levels.get("r1",    "N/A")
            s1         = levels.get("s1",    "N/A")

            has_position  = w["symbol"] in open_symbols
            position_flag = " [HAS OPEN POSITION — SKIP]" if has_position else ""

            # fix 10: inject last-cycle direction into watchlist line
            last_dir    = self._last_cycle_directions.get(w["symbol"])
            repeat_flag = (
                f" [LAST CYCLE: {last_dir} — DO NOT REPEAT SAME DIRECTION]"
                if last_dir else ""
            )

            # fix 6: compute affordability label
            cost_per_share = ltp_val * 1.0015
            if cost_per_share > 0 and spendable >= cost_per_share and not has_position:
                max_qty      = int(spendable / cost_per_share)
                rupee_profit = (ltp_val * 0.02) * max_qty  # 2% move estimate
                afford_label = f" [AFFORDABLE: max {max_qty} shares, ~₹{rupee_profit:,.0f} profit]"
            else:
                afford_label = " [TOO EXPENSIVE — SKIP]" if not has_position else ""

            watchlist_summary += (
                f"  **{w['symbol']}**{afford_label}{position_flag}{repeat_flag} | "
                f"LTP: ₹{ltp_val:,.2f} | "
                f"Chg: {w.get('change_pct', 0):+.2f}% | "
                f"RSI: {rsi_val} | MACD: {macd_sig} | BB: {bb_sig} | "
                f"Supertrend: {supertrend} | Vol: {vol_ratio}x | "
                f"Signal: {overall} | Pivot: {pivot} R1: {r1} S1: {s1}\n"
            )

        # ── VIX interpretation ────────────────────────────────────────────────
        vix = ctx.india_vix
        if vix > 25:
            vix_interp = "EXTREME FEAR — avoid all directional trades"
        elif vix > 22:
            vix_interp = "VERY HIGH FEAR — return avoid_trading recommendation"
        elif vix > 18:
            vix_interp = "HIGH FEAR — prefer mean-reversion, reduce size 30%"
        elif vix > 14:
            vix_interp = "MODERATE — normal trading"
        else:
            vix_interp = "LOW — favour momentum/trend strategies"

        # ── PCR interpretation ────────────────────────────────────────────────
        pcr = ctx.pcr or 1.0
        if pcr > 1.5:
            pcr_interp = "extremely bullish contrarian signal"
        elif pcr > 1.2:
            pcr_interp = "bullish"
        elif pcr > 0.8:
            pcr_interp = "neutral"
        elif pcr > 0.5:
            pcr_interp = "bearish"
        else:
            pcr_interp = "extremely bearish — high complacency risk"

        options_summary = (
            json.dumps(ctx.options_chain_summary, indent=2)
            if ctx.options_chain_summary
            else "Not available"
        )

        # fix 10: inject last-cycle memory into prompt
        last_cycle_summary = "No previous cycle data."
        if self.decision_history:
            last      = self.decision_history[-1]
            last_sigs = last.get("signals", [])
            actionable_last = [
                s for s in last_sigs
                if s.get("action") not in ("HOLD", "NO_ACTION")
            ]
            if actionable_last:
                lines = []
                for s in actionable_last[:3]:
                    lines.append(
                        f"  - {s.get('symbol')} {s.get('action')} "
                        f"@ confidence {s.get('confidence', 0):.2f} "
                        f"(strategy: {s.get('strategy', 'unknown')})"
                    )
                last_cycle_summary = "\n".join(lines)
            else:
                last_cycle_summary = "Previous cycle returned NO_ACTION for all symbols."

        return DECISION_PROMPT_TEMPLATE.format(
            timestamp             = ctx.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            session               = ctx.session,
            day_of_week           = ctx.day_of_week,
            nifty50_ltp           = f"{ctx.nifty50_ltp:,.2f}",
            banknifty_ltp         = f"{ctx.banknifty_ltp:,.2f}",
            india_vix             = f"{ctx.india_vix:.2f}",
            vix_interpretation    = vix_interp,
            market_trend          = ctx.market_trend,
            pcr                   = f"{pcr:.2f}" if ctx.pcr else "N/A",
            pcr_interpretation    = pcr_interp,
            available_capital     = ctx.available_capital,
            cash_reserve          = reserve,
            spendable_capital     = spendable,
            used_margin           = ctx.used_margin,
            open_positions_count  = len(ctx.open_positions),
            open_positions_summary= positions_summary or "  None",
            watchlist_summary     = watchlist_summary or "  No data",
            options_summary       = options_summary,
            news_sentiment        = ctx.recent_news_sentiment or "Not available",
            last_cycle_summary    = last_cycle_summary,
        )

    # ── Signal parser ─────────────────────────────────────────────────────────

    @staticmethod
    def _to_decimal(value: Any, field: str, symbol: str) -> Optional[Decimal]:
        if value is None or value == "" or value is False:
            return None
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(
                f"invalid price for {field} on {symbol}: {value!r}"
            ) from exc
        except (TypeError, ArithmeticError) as exc:
            raise ValueError(
                f"invalid price for {field} on {symbol}: {value!r} ({type(exc).__name__})"
            ) from exc
        if not result.is_finite():
            raise ValueError(
                f"invalid price for {field} on {symbol}: {value!r} (non-finite)"
            )
        return result

    def _parse_signals(
        self, decision: dict, ctx: MarketContext
    ) -> list[TradingSignal]:
        signals      = []
        open_symbols = {p["symbol"] for p in ctx.open_positions}

        # fix 8: pre-compute spendable for hard affordability check
        reserve   = max(self.min_cash_reserve, ctx.available_capital * 0.05)
        spendable = max(0.0, ctx.available_capital - reserve)

        for raw_idx, s in enumerate(decision.get("signals", [])):
            if not isinstance(s, dict):
                logger.warning(
                    "Skipping signals[%d]: expected dict, got %s (%r)",
                    raw_idx, type(s).__name__, s,
                )
                continue

            symbol = s.get("symbol", "UNKNOWN")

            try:
                action = SignalAction(
                    s.get("action", SignalAction.NO_ACTION.value)
                )

                # Hard filter: skip signals for symbols with open positions
                if symbol in open_symbols and action not in (
                    SignalAction.HOLD, SignalAction.NO_ACTION,
                    SignalAction.SQUARE_OFF, SignalAction.COVER,
                ):
                    logger.info(
                        "Skipping %s %s — already has open position",
                        action.value, symbol,
                    )
                    continue

                # fix 8: hard affordability check — drop BUY/SHORT signals the
                # account cannot afford BEFORE they reach the risk manager.
                # This prevents wasted API calls for unaffordable signals.
                if action in (SignalAction.BUY, SignalAction.SHORT):
                    entry_price_raw = s.get("entry_price")
                    ltp_fallback    = next(
                        (float(w.get("ltp", 0)) for w in ctx.watchlist_data
                         if w.get("symbol") == symbol),
                        0.0,
                    )
                    price_to_check   = float(entry_price_raw or ltp_fallback or 0)
                    cost_with_buffer = price_to_check * 1.0015  # 0.15% transaction buffer

                    if cost_with_buffer > 0 and spendable < cost_with_buffer:
                        logger.info(
                            "Dropping %s %s — unaffordable: cost=₹%.2f > spendable=₹%.2f",
                            action.value, symbol, cost_with_buffer, spendable,
                        )
                        continue

                qty = int(s.get("quantity", 0))
                if qty <= 0:
                    qty = self._fallback_quantity_for_signal(s, ctx)
                    if qty <= 0:
                        logger.warning(
                            "Skipping signal with quantity=%d for %s | "
                            "action=%s | capital=%.2f",
                            qty, symbol, action.value, ctx.available_capital,
                        )
                        continue
                    logger.info(
                        "Applied fallback quantity=%d for %s | action=%s",
                        qty, symbol, action.value,
                    )

                confidence = max(0.0, min(1.0, float(s.get("confidence", 0.5))))

                entry_price = self._to_decimal(s.get("entry_price"), "entry_price", symbol)
                stop_loss   = self._to_decimal(s.get("stop_loss"),   "stop_loss",   symbol)
                target      = self._to_decimal(s.get("target"),      "target",      symbol)

                signals.append(TradingSignal(
                    action      = action,
                    symbol      = symbol,
                    exchange    = s.get("exchange", "NSE"),
                    strategy    = s.get("strategy", "unknown"),
                    quantity    = qty,
                    entry_price = entry_price,
                    stop_loss   = stop_loss,
                    target      = target,
                    confidence  = confidence,
                    rationale   = s.get("rationale", ""),
                    risk_reward = float(s["risk_reward"]) if s.get("risk_reward") else None,
                    timeframe   = s.get("timeframe", "intraday"),
                    product     = s.get("product", "MIS"),
                    priority    = int(s.get("priority", 5)),
                    tags        = s.get("tags", []),
                ))

            except (KeyError, ValueError, TypeError, AttributeError) as e:
                logger.warning("Skipping malformed signal for %s: %s", symbol, e)

        # Sort by priority ASC then confidence DESC
        signals.sort(key=lambda x: (x.priority, -x.confidence))

        # fix 11: hard cap at 2 — code-level backstop even if model ignores prompt
        if len(signals) > 2:
            logger.info(
                "Capping signals from %d to 2 (max 2 per cycle rule)",
                len(signals),
            )
            signals = signals[:2]

        return signals

    def _fallback_quantity_for_signal(
        self, raw_signal: dict, ctx: MarketContext
    ) -> int:
        symbol      = str(raw_signal.get("symbol") or "").upper()
        entry_price = raw_signal.get("entry_price")

        resolved_price: Optional[Decimal] = None
        if entry_price not in (None, ""):
            try:
                resolved_price = self._to_decimal(entry_price, "entry_price", symbol)
            except ValueError:
                resolved_price = None

        if resolved_price is None:
            for instrument in ctx.watchlist_data:
                if str(instrument.get("symbol", "")).upper() == symbol:
                    ltp = instrument.get("ltp")
                    try:
                        resolved_price = self._to_decimal(ltp, "ltp", symbol)
                    except ValueError:
                        resolved_price = None
                    break

        if resolved_price is None or resolved_price <= 0:
            return 0

        # fix 13: use spendable (capital minus reserve), not raw available capital
        capital   = Decimal(str(max(ctx.available_capital, 0.0)))
        reserve   = Decimal(str(max(self.min_cash_reserve, float(capital) * 0.05)))
        spendable = max(Decimal("0"), capital - reserve)

        per_trade_budget = spendable * Decimal(
            str(self.max_capital_per_trade_pct / 100.0)
        )
        if self.max_order_value_absolute is not None:
            per_trade_budget = min(
                per_trade_budget,
                Decimal(str(self.max_order_value_absolute)),
            )
        if per_trade_budget <= 0:
            return 0

        raw_qty = int(per_trade_budget / resolved_price)
        if raw_qty >= self.min_trade_quantity:
            return raw_qty

        min_ticket_value = resolved_price * Decimal(self.min_trade_quantity)
        if spendable >= min_ticket_value:
            return self.min_trade_quantity
        return 0
