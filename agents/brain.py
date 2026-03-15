"""
AI Agent Brain - The intelligence core of the trading bot.
Uses Gemini API to make multi-strategy trading decisions based on
real-time market data, technical indicators, and portfolio state.

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

MAX_DECISION_HISTORY = 200          # Cap memory usage
MIN_CONFIDENCE_THRESHOLD = 0.30     # Never go below this
MAX_CONFIDENCE_THRESHOLD = 0.95     # Never go above this
RATE_LIMIT_BACKOFF_SECONDS = 5.0    # Base wait before trying next model
# FIX 4: Cap + jitter prevent a long fallback list from blocking the decision loop
# for many seconds and avoid thundering-herd if multiple instances back off together.
RATE_LIMIT_BACKOFF_MAX_SECONDS = 30.0   # Hard ceiling on any single wait
RATE_LIMIT_BACKOFF_JITTER = 0.20        # ±20% uniform jitter applied after capping


# ─── SIGNAL TYPES ────────────────────────────────────────────────────────────

class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SHORT = "SHORT"
    COVER = "COVER"
    HOLD = "HOLD"
    SQUARE_OFF = "SQUARE_OFF"
    NO_ACTION = "NO_ACTION"


@dataclass
class TradingSignal:
    action: SignalAction
    symbol: str
    exchange: str
    strategy: str
    quantity: int
    entry_price: Optional[Decimal]
    stop_loss: Optional[Decimal]
    target: Optional[Decimal]
    confidence: float           # 0.0 - 1.0
    rationale: str
    risk_reward: Optional[float]
    timeframe: str              # intraday | swing | positional
    product: str                # MIS | CNC | NRML
    priority: int               # 1 = highest
    tags: list[str]

    @property
    def is_actionable(self) -> bool:
        return self.action not in (SignalAction.HOLD, SignalAction.NO_ACTION)


@dataclass
class MarketContext:
    """Everything the AI needs to make a decision."""
    timestamp: datetime
    nifty50_ltp: float
    banknifty_ltp: float
    india_vix: float
    market_trend: str           # bullish | bearish | sideways
    session: str                # pre_open | opening | mid_session | closing
    day_of_week: str
    available_capital: float
    used_margin: float
    open_positions: list[dict]
    watchlist_data: list[dict]  # symbol, ltp, indicators
    options_chain_summary: Optional[dict]
    recent_news_sentiment: Optional[str]
    pcr: Optional[float]        # Put-Call Ratio


# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are an elite quantitative trading agent for Indian financial markets (NSE/BSE).
You operate as an autonomous trading brain with deep expertise in:

1. **Technical Analysis**: EMA, MACD, RSI, Bollinger Bands, Supertrend, ATR, VWAP, pivot points
2. **Options Strategies**: Iron Condor, Bull Put Spread, Bear Call Spread, Short Strangle, straddles
3. **Market Microstructure**: Order flow, OI analysis, PCR, support/resistance levels
4. **Risk Management**: Kelly criterion, position sizing, max drawdown control
5. **Indian Market Specifics**: F&O lot sizes, SEBI regulations, STT impact, settlement cycles

## Decision Framework

When analyzing market data, follow this process:
1. **Market Regime Detection**: Identify if market is trending, ranging, or volatile
2. **Strategy Selection**: Choose the BEST strategy for current conditions
3. **Signal Generation**: Provide specific, actionable signals with exact price levels
4. **Risk Calculation**: Always include SL, target, and position size
5. **Confidence Scoring**: Rate each signal 0.0-1.0 based on confluence of indicators

## Indicator Interpretation Rules
- RSI > 70 = overbought (look for shorts/exits on BUY positions)
- RSI < 30 = oversold (look for longs/exits on SELL positions)
- MACD histogram turning positive from negative = bullish momentum building
- MACD histogram turning negative from positive = bearish momentum building
- BB width contracting (squeeze) = breakout imminent, wait for direction
- BB width expanding = trend in motion, trade with trend
- Supertrend bullish + price above VWAP = strong long bias
- Supertrend bearish + price below VWAP = strong short bias
- PCR > 1.2 = bearish (more puts = market hedging downside)
- PCR < 0.8 = bullish (less put protection = confidence)
- VIX > 20 = high fear, favour mean-reversion; VIX < 15 = low fear, favour momentum

## Available Strategies
- **Momentum**: RSI + MACD + Volume confirmation for trend trades
- **Mean Reversion**: Bollinger Band squeezes, RSI extremes
- **Options Selling**: Short premium when IV Rank > 50, defined risk spreads
- **Breakout**: ATR-based breakouts with volume confirmation
- **Index Scalping**: NIFTY/BANKNIFTY intraday with Supertrend

## Output Rules
- ALWAYS respond with valid JSON only, no markdown, no extra text
- Include specific price levels (not vague descriptions)
- If market conditions are unfavorable, return NO_ACTION signals
- Risk-first mindset: Never risk more than 2% of capital per trade
- Respect market hours (9:15 AM - 3:30 PM IST)
- Factor in STT and brokerage in profit calculations
- Minimum risk:reward must be 1.5:1 to generate a BUY/SELL signal

## Hard Risk Rules (NEVER violate)
- Max 5% capital per trade
- Max 10 open positions simultaneously
- Stop if daily loss exceeds 2% of capital
- Stop if account drawdown exceeds 8%
- Never average losing positions
- No signals in first 15 minutes (9:15-9:30) or last 15 minutes (3:15-3:30)
"""

DECISION_PROMPT_TEMPLATE = """
## Current Market Context
**Time**: {timestamp} IST
**Session**: {session}
**Day**: {day_of_week}

## Index Data
- NIFTY 50: {nifty50_ltp}
- BANK NIFTY: {banknifty_ltp}
- INDIA VIX: {india_vix} ({vix_interpretation})
- Market Trend: {market_trend}
- Put-Call Ratio: {pcr} ({pcr_interpretation})

## Portfolio State
- Available Capital: ₹{available_capital:,.0f}
- Used Margin: ₹{used_margin:,.0f}
- Open Positions: {open_positions_count}
{open_positions_summary}

## Watchlist Analysis
{watchlist_summary}

## Options Flow
{options_summary}

## News Sentiment
{news_sentiment}

---
Analyze the above data carefully. Apply your indicator interpretation rules.
Return ONLY a valid JSON object with this exact schema:

{{
  "market_regime": "trending_up | trending_down | ranging | high_volatility",
  "market_commentary": "2-sentence max market view",
  "signals": [
    {{
      "action": "BUY | SELL | SHORT | COVER | SQUARE_OFF | NO_ACTION",
      "symbol": "RELIANCE",
      "exchange": "NSE",
      "strategy": "momentum | mean_reversion | breakout | options_selling | scalping",
      "quantity": 10,
      "entry_price": 2450.50,
      "stop_loss": 2420.00,
      "target": 2510.00,
      "confidence": 0.78,
      "rationale": "Specific indicator reasons: RSI(14)=62 crossed above 60, MACD bullish crossover, volume 1.8x avg, breaking above 20-day resistance at 2445.",
      "risk_reward": 2.1,
      "timeframe": "intraday",
      "product": "MIS",
      "priority": 1,
      "tags": ["breakout", "high_volume", "trend_following"]
    }}
  ],
  "positions_to_exit": ["SYMBOL1"],
  "risk_assessment": "low | medium | high",
  "session_recommendation": "active_trading | selective | avoid_trading"
}}

Generate 0-5 signals based on conviction. Quality over quantity. No signal is better than a bad signal.
"""


# ─── AI AGENT ────────────────────────────────────────────────────────────────

class TradingAgent:
    """
    The AI brain that drives all trading decisions.
    Wraps multiple LLM providers with trading-specific prompting and response parsing.
    """

    # ── Parameter adjustment schema ───────────────────────────────────────────
    # Keys MUST match what the review_strategy prompt shows the AI in its example
    # JSON (currently: rsi_overbought, confidence_threshold). Additional indicator
    # keys are included so the AI can tune them if it chooses to suggest them.
    #
    # Format: field_name -> (type, min, max)
    # Fields absent from this schema are silently dropped — the AI cannot inject
    # arbitrary keys into bot configuration.
    PARAM_SCHEMA: dict[str, tuple[type, float, float]] = {
        # ── Reviewed directly by brain (must match review_strategy prompt) ──
        "confidence_threshold":  (float, 0.30,  0.95),
        "rsi_overbought":        (float, 60.0,  85.0),
        # ── Indicator parameters (suggested by AI, applied by strategy layer) ──
        "rsi_oversold":          (float, 15.0,  40.0),
        "rsi_period":            (int,   7,     21),
        "macd_fast":             (int,   5,     20),
        "macd_slow":             (int,   15,    40),
        "macd_signal_period":    (int,   5,     15),
        "bb_period":             (int,   10,    30),
        "bb_std":                (float, 1.5,    3.0),
        "atr_period":            (int,   7,     21),
        "supertrend_multiplier": (float, 1.0,    5.0),
        # ── Risk / position sizing ─────────────────────────────────────────
        "stop_loss_atr_mult":    (float, 0.5,    4.0),
        "target_atr_mult":       (float, 1.0,    8.0),
    }

    # ── Consumer key contract ─────────────────────────────────────────────────
    # The set of PARAM_SCHEMA keys that the engine / risk layer actually reads
    # from the `parameter_adjustments` dict returned by review_strategy().
    #
    # HOW TO USE THIS:
    #   1. When you wire up review_strategy() in engine.py / risk.py, populate
    #      this frozenset with every key your code actually consumes:
    #
    #          TradingAgent.PARAM_CONSUMER_KEYS = frozenset({
    #              "confidence_threshold", "rsi_overbought", ...
    #          })
    #
    #   2. Any key in PARAM_SCHEMA that is NOT in PARAM_CONSUMER_KEYS will
    #      produce a WARNING log on the first review cycle, making the mismatch
    #      immediately visible without requiring a manual audit.
    #
    #   3. Add a unit test that asserts PARAM_CONSUMER_KEYS is non-empty and
    #      is a subset of PARAM_SCHEMA.keys() — that catches typos on both sides.
    #
    # Until populated, the check is skipped (no false positives during development).
    PARAM_CONSUMER_KEYS: frozenset[str] = frozenset()

    DEFAULT_MODEL_TIERS: dict[str, list[str]] = {
        # Tiered from throughput-first to quality-first so fallbacks degrade gracefully.
        "ultra_fast": [
            "groq/llama-3.1-8b-instant",
            "groq/gemma2-9b-it",
        ],
        "fast": [
            "groq/mixtral-8x7b-32768",
            "openrouter/qwen/qwen-2.5-7b-instruct",
            "openrouter/mistralai/mistral-7b-instruct",
        ],
        "balanced": [
            "openrouter/qwen/qwen-2.5-14b-instruct",
            "openrouter/mistralai/mistral-nemo",
            "openrouter/deepseek/deepseek-chat",
        ],
        "quality": [
            "openrouter/meta-llama/llama-3.1-70b-instruct",
        ],
    }

    def __init__(self, config: dict):
        self.config = config
        self.gemini_api_key = os.getenv(config.get("api_key_env", "GEMINI_API_KEY"), "")
        self.groq_api_key = os.getenv(config.get("groq_api_key_env", "GROQ_API_KEY"), "")
        self.openrouter_api_key = os.getenv(config.get("openrouter_api_key_env", "OPENROUTER_API_KEY"), "")
        self.gemini_client = genai.Client(api_key=self.gemini_api_key) if self.gemini_api_key else None
        self.model = config.get("model", "gemini/gemini-2.5-flash")
        self.model_tiers = config.get("model_tiers", self.DEFAULT_MODEL_TIERS)
        self.fallback_models = self._resolve_fallback_models(config)
        self.max_tokens = config.get("max_tokens", 4096)
        self.temperature = config.get("temperature", 0.1)
        # FIX 1: Clamp and validate confidence_threshold at boot, not just in review_strategy.
        # Bad config values (0.01, 2, "high", None) are caught here before any trade runs.
        self.confidence_threshold: float = self._validated_confidence_threshold(
            config.get("confidence_threshold", 0.65),
            fallback=0.65,
            source="config",
        )
        self.decision_history: list[dict] = []
        logger.info(
            "AI model chain configured | primary=%s | fallbacks=%d | tiers=%s | order=%s",
            self.model,
            len(self.fallback_models),
            list((self.model_tiers or {}).keys()),
            [self.model, *self.fallback_models],
        )

    @staticmethod
    def _parse_model_identifier(model_id: str) -> tuple[str, str]:
        """Parse provider/model; keep backward compatibility for bare Gemini IDs."""
        if "/" not in model_id:
            return "gemini", model_id
        provider, model = model_id.split("/", 1)
        return provider.strip().lower(), model.strip()

    def _ensure_provider_key(self, provider: str) -> None:
        key_by_provider = {
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
            "openrouter": self.openrouter_api_key,
        }
        if not key_by_provider.get(provider):
            raise RuntimeError(f"Missing API key for provider '{provider}'.")

    def _resolve_fallback_models(self, config: dict) -> list[str]:
        """Build fallback model list from explicit list plus optional tier groups."""
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

        # Backward-compatible defaults when config provides neither tiered nor flat fallbacks.
        if not resolved:
            for models in self.DEFAULT_MODEL_TIERS.values():
                for model in models:
                    if model not in seen:
                        seen.add(model)
                        resolved.append(model)

        return resolved

    @staticmethod
    def _validated_confidence_threshold(
        raw: Any,
        *,
        fallback: float,
        source: str,
    ) -> float:
        """
        Parse, validate and clamp a confidence threshold value.
        Returns `fallback` if the value cannot be parsed or is out of range.
        Logs a warning so the issue is visible in the boot log.
        """
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
                "confidence_threshold from %s (%.4f) is outside [%.2f, %.2f]; "
                "clamping to %.2f.",
                source, ct, MIN_CONFIDENCE_THRESHOLD, MAX_CONFIDENCE_THRESHOLD, clamped,
            )
            return clamped

        return ct

    # Keys the AI commonly emits that map to a canonical PARAM_SCHEMA key.
    # Applied before schema validation so the AI's natural vocabulary is accepted.
    # When adding aliases: the *value* must be a key that exists in PARAM_SCHEMA.
    PARAM_ALIASES: dict[str, str] = {
        # AGENT_SYSTEM_PROMPT and most strategy configs call this "macd_signal";
        # PARAM_SCHEMA uses "macd_signal_period" to avoid ambiguity with the
        # indicator value of the same name that appears in watchlist_data.
        "macd_signal": "macd_signal_period",
    }

    def _validate_param_adjustments(self, raw: Any) -> dict:
        """
        Field-by-field validation of every key in parameter_adjustments.

        Rules applied in order:
        1. Alias normalisation — common AI-emitted names (e.g. "macd_signal") are
           transparently mapped to their canonical schema key before lookup.
        2. Unknown keys are dropped (AI cannot create new config keys).
        3. Non-numeric values are dropped with a warning.
        4. Values outside the declared [min, max] range are clamped with a warning.
        5. Integer-typed fields use round-half-up (not banker's rounding).

        Returns a clean dict containing only validated, safe values.
        """
        if not isinstance(raw, dict):
            logger.warning("parameter_adjustments is not a dict (%r); ignoring entirely.", type(raw).__name__)
            return {}

        clean: dict = {}
        for raw_field, value in raw.items():
            # FIX 3: resolve alias before schema lookup so the AI's natural
            # vocabulary ("macd_signal") is accepted and stored under the
            # canonical name ("macd_signal_period"). Without this, valid
            # AI suggestions were silently dropped as "unknown" keys.
            field = self.PARAM_ALIASES.get(raw_field, raw_field)
            if raw_field != field:
                logger.debug("Normalising alias %r → %r", raw_field, field)

            if field not in self.PARAM_SCHEMA:
                logger.warning("Dropping unknown parameter_adjustment field: %r = %r", raw_field, value)
                continue

            expected_type, lo, hi = self.PARAM_SCHEMA[field]

            # Parse to float first regardless of expected_type (handles numeric strings)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                logger.warning(
                    "Dropping %r: non-numeric value %r (expected %s in [%s, %s]).",
                    field, value, expected_type.__name__, lo, hi,
                )
                continue

            # Clamp to declared range
            if numeric < lo or numeric > hi:
                clamped = max(lo, min(hi, numeric))
                logger.warning(
                    "Clamping %r: %.4g is outside [%s, %s], using %.4g.",
                    field, numeric, lo, hi, clamped,
                )
                numeric = clamped

            # Cast to declared type using round-half-up (not banker's rounding).
            if expected_type is int:
                clean[field] = int(math.floor(numeric + 0.5))
            else:
                clean[field] = float(numeric)

        return clean

    # ── Error Classification ──────────────────────────────────────────────────

    def _is_rate_limited_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(t in msg for t in (
            "429",
            "rate limit",
            "too many requests",
            "quota",
            "resource_exhausted",
        ))

    def _is_unsupported_system_instruction_error(self, err: Exception) -> bool:
        return "developer instruction is not enabled" in str(err).lower()

    def _is_unavailable_model_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(t in msg for t in (
            "404", "not_found", "model is not found",
            "is not found for api version",
            "not supported for generatecontent",
            "unknown model",
            "403",                    # FIX: PERMISSION_DENIED for models not enabled
            "401",
            "unauthorized",
            "invalid api key",
            "permission_denied",
            "not have permission",
        ))

    # ── Safe Response Text Extraction ────────────────────────────────────────

    def _extract_response_text(self, response: Any) -> str:
        """
        Safely extract text from Gemini response.
        FIX: accessing .text on a blocked response raises ValueError, not returns None.
        Traverse candidates/parts directly to avoid this.
        """
        # Try candidates first (safer for blocked/partial responses)
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

        # Fallback to .text only if candidates empty (some SDK versions)
        try:
            text = getattr(response, "text", None)
            if text and text.strip():
                return text.strip()
        except (ValueError, AttributeError):
            # .text raises ValueError when response was blocked by safety filters
            pass

        return ""

    def _generate_with_openai_compatible(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        expect_json: bool,
    ) -> str:
        base_url = "https://api.groq.com/openai/v1/chat/completions" if provider == "groq" else "https://openrouter.ai/api/v1/chat/completions"
        api_key = self.groq_api_key if provider == "groq" else self.openrouter_api_key
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://agentic-trading-bot.local"
            headers["X-Title"] = "Agentic Trading Bot"

        payload = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
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
            raise RuntimeError(f"{provider} returned empty choices for model {model}.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            return "".join(parts).strip()
        return ""

    def _generate_with_provider(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        expect_json: bool,
    ) -> str:
        self._ensure_provider_key(provider)
        if provider == "gemini":
            if not self.gemini_client:
                raise RuntimeError("Gemini API key missing. Set GEMINI_API_KEY in .env")
            cfg = types.GenerateContentConfig(
                system_instruction=AGENT_SYSTEM_PROMPT,
                temperature=temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json" if expect_json else "text/plain",
            )
            response = self.gemini_client.models.generate_content(
                model=model,
                contents=prompt,
                config=cfg,
            )
            return self._extract_response_text(response)

        if provider in {"groq", "openrouter"}:
            return self._generate_with_openai_compatible(
                provider=provider,
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                expect_json=expect_json,
            )

        raise RuntimeError(f"Unsupported model provider '{provider}'.")

    # ── Core Generation (Sync, runs in thread) ───────────────────────────────

    def _generate_text_sync(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        expect_json: bool,
    ) -> tuple[str, str]:
        """
        Synchronous multi-provider call. Always run via asyncio.to_thread — never call directly.
        Returns (response_text, model_actually_used) so callers can log the real model.
        FIX 2: Previously always returned self.model even when a fallback was used.
        """
        all_models = [self.model] + [m for m in self.fallback_models if m != self.model]
        last_error: Exception | None = None
        failure_reasons: list[str] = []

        for idx, model_id in enumerate(all_models):
            provider, provider_model = self._parse_model_identifier(model_id)
            try:
                response_text = self._generate_with_provider(
                    provider=provider,
                    model=provider_model,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    expect_json=expect_json,
                )
                if model_id != self.model:
                    logger.warning("Using fallback model: %s", model_id)
                return response_text, model_id

            except Exception as e:
                last_error = e
                is_last = idx == len(all_models) - 1
                reason = "unknown"

                if is_last:
                    failure_reasons.append(f"{model_id}=terminal_error")
                    break

                if self._is_rate_limited_error(e):
                    # FIX 4: Cap the exponential growth and add ±jitter so a long
                    # fallback list never stalls the decision loop for 30+ seconds,
                    # and multiple instances don't all retry at the identical moment.
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
                    logger.warning("Model %s does not support system instructions; trying fallback.", model_id)
                    reason = "unsupported_system_instruction"
                    failure_reasons.append(f"{model_id}={reason}")
                    continue

                if self._is_unavailable_model_error(e):
                    logger.warning("Model %s unavailable/no permission; trying fallback.", model_id)
                    reason = "unavailable_or_permission"
                    failure_reasons.append(f"{model_id}={reason}")
                    continue

                failure_reasons.append(f"{model_id}={reason}")
                raise

        logger.error("All configured models failed | attempts=%s", ", ".join(failure_reasons))
        raise RuntimeError(f"All configured models failed. Last error: {last_error}")

    # ── Async wrapper ─────────────────────────────────────────────────────────

    async def _generate_text(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        expect_json: bool,
    ) -> tuple[str, str]:
        """
        Runs the synchronous provider-routed call in a thread pool.
        Returns (response_text, model_actually_used).
        """
        return await asyncio.to_thread(
            self._generate_text_sync,
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            expect_json=expect_json,
        )

    # ── JSON Extraction ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_json(raw_text: str) -> str:
        """
        FIX 3: Robust JSON extraction that handles all real-world Gemini output formats:
          - Plain JSON
          - ```json ... ``` (single or nested fences)
          - Leading prose before the fence/JSON
          - Trailing markdown/text after the closing brace
          - Single-line fenced payloads: ```json {"k": "v"} ```

        Strategy: find the first '{' or '[' and the matching closing brace/bracket,
        then validate it parses. Falls back to fence-stripping if no balanced block found.
        """
        raw = raw_text.strip()
        if not raw:
            return raw

        # ── Pass 1: find the outermost JSON object or array ──────────────────
        for start_char, end_char in (('{', '}'), ('[', ']')):
            start_idx = raw.find(start_char)
            if start_idx == -1:
                continue

            # Walk forward tracking nesting depth, respecting strings
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
                    json.loads(candidate)   # validate it's real JSON
                    return candidate
                except json.JSONDecodeError:
                    pass                    # fall through to fence-strip

        # ── Pass 2: fence-strip fallback (handles nested fences) ────────────
        for _ in range(3):
            if not raw.startswith("```"):
                break
            lines = raw.split("\n")
            # Drop the opening fence line (```json or ```)
            body_lines = lines[1:]
            # Drop trailing closing fence if present
            if body_lines and body_lines[-1].strip() == "```":
                body_lines = body_lines[:-1]
            raw = "\n".join(body_lines).strip()

        return raw

    # ── Main Decision Function ────────────────────────────────────────────────

    async def analyze_and_decide(self, context: MarketContext) -> list[TradingSignal]:
        """
        Core decision function. Takes market context, calls Gemini,
        returns actionable trading signals above the confidence threshold.
        """
        prompt = self._build_prompt(context)
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
            signals = self._parse_signals(decision, context)

            latency_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)

            normalized_signals = [
                {
                    "action": s.action.value,
                    "symbol": s.symbol,
                    "exchange": s.exchange,
                    "strategy": s.strategy,
                    "quantity": s.quantity,
                    "entry_price": float(s.entry_price) if s.entry_price is not None else None,
                    "stop_loss": float(s.stop_loss) if s.stop_loss is not None else None,
                    "target": float(s.target) if s.target is not None else None,
                    "confidence": s.confidence,
                    "rationale": s.rationale,
                    "risk_reward": s.risk_reward,
                    "timeframe": s.timeframe,
                    "product": s.product,
                    "priority": s.priority,
                    "tags": s.tags,
                    "is_actionable": s.is_actionable,
                }
                for s in signals
            ]

            record = {
                "timestamp": context.timestamp.isoformat(),
                "market_regime": decision.get("market_regime"),
                "commentary": decision.get("market_commentary"),
                "market_commentary": decision.get("market_commentary"),
                "risk_assessment": decision.get("risk_assessment"),
                "signals_count": len(signals),
                "signals": normalized_signals,
                "signals_raw": decision.get("signals", []),
                "positions_to_exit": decision.get("positions_to_exit", []),
                "session_recommendation": decision.get("session_recommendation"),
                "raw_response": decision,
                "model_used": model_used,          # FIX 2: actual model, not self.model
                "model_requested": self.model,     # handy for spotting fallback frequency
                "latency_ms": latency_ms,
            }
            self.decision_history.append(record)

            # FIX: Cap history to avoid unbounded memory growth
            if len(self.decision_history) > MAX_DECISION_HISTORY:
                self.decision_history = self.decision_history[-MAX_DECISION_HISTORY:]

            logger.info(
                "🤖 AI Decision | Regime: %s | Signals: %d | Risk: %s | Latency: %dms",
                decision.get("market_regime"),
                len(signals),
                decision.get("risk_assessment"),
                latency_ms,
            )

            actionable = [s for s in signals if s.confidence >= self.confidence_threshold]
            logger.info(
                "📊 %d/%d signals above confidence threshold (%.2f)",
                len(actionable), len(signals), self.confidence_threshold,
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
                logger.error("AI agent failed: all Gemini models are rate-limited.")
            else:
                logger.error("AI agent error: %s", e, exc_info=True)
            return []

    # ── Strategy Review ───────────────────────────────────────────────────────

    async def review_strategy(self, performance_data: dict) -> dict:
        """
        Periodic strategy review. AI evaluates what's working and
        suggests parameter adjustments.
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

            # FIX 4: Validate parameter_adjustments field-by-field in its own try/except.
            # A single malformed value (e.g. confidence_threshold="high") no longer
            # discards the entire review result — only that one field is removed.
            # FIX 3: validate every field in parameter_adjustments against PARAM_SCHEMA.
            # Unknown keys are dropped, out-of-range values are clamped, non-numeric
            # values are dropped — all field-by-field so one bad value never discards
            # the rest of the review result.
            raw_params = result.get("parameter_adjustments", {})
            validated_params = self._validate_param_adjustments(raw_params)
            result["parameter_adjustments"] = validated_params

            # Fix 2: warn at runtime when validated keys are not in the consumer
            # contract, so "validated but never applied" mismatches surface in logs
            # on the first real review cycle rather than silently being ignored.
            # The check is a no-op until PARAM_CONSUMER_KEYS is populated by the
            # engine/risk layer (see class-level docstring for instructions).
            if self.PARAM_CONSUMER_KEYS:
                unclaimed = set(validated_params) - self.PARAM_CONSUMER_KEYS
                if unclaimed:
                    logger.warning(
                        "review_strategy returned parameter keys not in "
                        "PARAM_CONSUMER_KEYS — they will be ignored by the engine: %s. "
                        "Add them to PARAM_CONSUMER_KEYS or remove from PARAM_SCHEMA.",
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

    # ── Prompt Builder ────────────────────────────────────────────────────────

    def _build_prompt(self, ctx: MarketContext) -> str:
        # Open positions summary
        positions_summary = ""
        if ctx.open_positions:
            for p in ctx.open_positions:
                pnl_str = f"₹{p.get('pnl', 0):+,.0f}"
                positions_summary += (
                    f"  - {p['symbol']} | {p['side']} {p['quantity']} | "
                    f"Avg: ₹{p.get('avg_price', 0):,.2f} | LTP: ₹{p.get('ltp', 0):,.2f} | P&L: {pnl_str}\n"
                )

        # Watchlist summary — FIX: include richer signal data for better AI decisions
        watchlist_summary = ""
        for w in ctx.watchlist_data[:15]:
            ind = w.get("indicators", {})
            levels = w.get("levels", {})
            rsi_val = ind.get("rsi", "N/A")
            macd_sig = ind.get("macd_signal", "N/A")
            bb_sig = ind.get("bb_signal", "N/A")
            supertrend = ind.get("supertrend", "N/A")
            vol_ratio = ind.get("volume_ratio", 1.0)
            overall = ind.get("overall_signal", "neutral")
            pivot = levels.get("pivot", "N/A")
            r1 = levels.get("r1", "N/A")
            s1 = levels.get("s1", "N/A")

            watchlist_summary += (
                f"  **{w['symbol']}** | LTP: ₹{w.get('ltp', 0):,.2f} | "
                f"Chg: {w.get('change_pct', 0):+.2f}% | "
                f"RSI: {rsi_val} | MACD: {macd_sig} | BB: {bb_sig} | "
                f"Supertrend: {supertrend} | Vol: {vol_ratio}x | "
                f"Signal: {overall} | Pivot: {pivot} R1: {r1} S1: {s1}\n"
            )

        # VIX interpretation
        vix = ctx.india_vix
        if vix > 25:
            vix_interp = "EXTREME FEAR - avoid directional trades"
        elif vix > 18:
            vix_interp = "HIGH FEAR - prefer mean-reversion"
        elif vix > 14:
            vix_interp = "MODERATE - normal trading"
        else:
            vix_interp = "LOW - favour momentum/trend"

        # PCR interpretation
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
            pcr_interp = "extremely bearish - high complacency"

        options_summary = (
            json.dumps(ctx.options_chain_summary, indent=2)
            if ctx.options_chain_summary
            else "Not available"
        )

        return DECISION_PROMPT_TEMPLATE.format(
            timestamp=ctx.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            session=ctx.session,
            day_of_week=ctx.day_of_week,
            nifty50_ltp=f"{ctx.nifty50_ltp:,.2f}",
            banknifty_ltp=f"{ctx.banknifty_ltp:,.2f}",
            india_vix=f"{ctx.india_vix:.2f}",
            vix_interpretation=vix_interp,
            market_trend=ctx.market_trend,
            pcr=f"{pcr:.2f}" if ctx.pcr else "N/A",
            pcr_interpretation=pcr_interp,
            available_capital=ctx.available_capital,
            used_margin=ctx.used_margin,
            open_positions_count=len(ctx.open_positions),
            open_positions_summary=positions_summary or "  None",
            watchlist_summary=watchlist_summary or "  No data",
            options_summary=options_summary,
            news_sentiment=ctx.recent_news_sentiment or "Not available",
        )

    # ── Signal Parser ─────────────────────────────────────────────────────────

    # ── Decimal helper ────────────────────────────────────────────────────────

    @staticmethod
    def _to_decimal(value: Any, field: str, symbol: str) -> Optional[Decimal]:
        """
        Convert a raw AI value to Decimal safely.
        Handles the two failure modes that Decimal conversion can produce:
          - InvalidOperation: Decimal("N/A"), Decimal("~2450"), Decimal("null"), etc.
          - ValueError: raised explicitly below for non-finite results (Inf, NaN).
        TypeError covers non-string/non-numeric inputs that str() cannot sensibly convert.
        We do NOT use bare `except Exception` — that would swallow AttributeErrors or
        MemoryErrors that signal real bugs and should propagate.
        """
        if value is None or value == "" or value is False:
            return None
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            # e.g. "N/A", "~2450", "null", "pending" — model returned garbage
            raise ValueError(
                f"invalid price for {field} on {symbol}: {value!r} (not a valid decimal)"
            ) from exc
        except (TypeError, ArithmeticError) as exc:
            # Unexpected type or arithmetic failure during conversion
            raise ValueError(
                f"invalid price for {field} on {symbol}: {value!r} ({type(exc).__name__})"
            ) from exc
        # Reject special values Decimal accepts but trading math cannot use
        if not result.is_finite():
            raise ValueError(
                f"invalid price for {field} on {symbol}: {value!r} (non-finite: {result})"
            )
        return result

    def _parse_signals(self, decision: dict, ctx: MarketContext) -> list[TradingSignal]:
        signals = []
        for raw_idx, s in enumerate(decision.get("signals", [])):
            # FIX 1: guard non-dict entries before any attribute access.
            # The AI can return a string, list, or null in the signals array;
            # calling .get() on those raises AttributeError before the try block.
            if not isinstance(s, dict):
                logger.warning(
                    "Skipping signals[%d]: expected dict, got %s (%r)",
                    raw_idx, type(s).__name__, s,
                )
                continue

            symbol = s.get("symbol", "UNKNOWN")
            try:
                qty = int(s.get("quantity", 0))
                if qty <= 0:
                    logger.warning("Skipping signal with quantity=%d for %s", qty, symbol)
                    continue

                confidence = max(0.0, min(1.0, float(s.get("confidence", 0.5))))

                entry_price = self._to_decimal(s.get("entry_price"), "entry_price", symbol)
                stop_loss   = self._to_decimal(s.get("stop_loss"),   "stop_loss",   symbol)
                target      = self._to_decimal(s.get("target"),      "target",      symbol)

                signals.append(TradingSignal(
                    action=SignalAction(s["action"]),
                    symbol=symbol,
                    exchange=s.get("exchange", "NSE"),
                    strategy=s.get("strategy", "unknown"),
                    quantity=qty,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    target=target,
                    confidence=confidence,
                    rationale=s.get("rationale", ""),
                    risk_reward=float(s["risk_reward"]) if s.get("risk_reward") else None,
                    timeframe=s.get("timeframe", "intraday"),
                    product=s.get("product", "MIS"),
                    priority=int(s.get("priority", 5)),
                    tags=s.get("tags", []),
                ))
            # FIX 2: widen the catch to include TypeError and AttributeError.
            # int()/float()/SignalAction() can raise TypeError when passed an
            # unexpected type; dict.get() on a nested non-dict raises AttributeError.
            # Neither should abort the entire parse — skip only this one signal.
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                logger.warning("Skipping malformed signal for %s: %s", symbol, e)

        # Sort by priority ASC, then confidence DESC
        signals.sort(key=lambda x: (x.priority, -x.confidence))
        return signals
