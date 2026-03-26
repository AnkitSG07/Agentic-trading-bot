"""
AI Agent Brain - The intelligence core of the trading bot.
Uses Gemini 2.5 Flash (thinking disabled) as primary model for
fast, reliable JSON trading decisions on NSE/BSE markets.

Corrections applied in this version:
  1.  Gemini thinking mode disabled (saves 2-8s per call)
  2.  max_tokens reduced 4096→2048 (40% output latency cut)
  3.  temperature reduced 0.1→0.05 (deterministic signals)
  4.  Removed all sub-32B fallback models (7B/8B models hallucinate prices)
  5.  (removed) xiaomimimo provider — unverified financial reasoning, 7B-class
  6.  AI now sees [AFFORDABLE] / [TOO EXPENSIVE] labels per symbol
  7.  Watchlist sorted by affordability then signal strength before prompt
  8.  Hard affordability check in _parse_signals (BUY dropped before risk mgr)
  9.  Regime-adaptive confidence threshold (VIX-based, not fixed 0.65)
 10.  Anti-repetition: _last_cycle_symbols + _last_cycle_directions tracking
 11.  Hard signal cap signals[:2] in _parse_signals (code-level enforcement)
 12.  max_capital_per_trade_pct default 5→50% (small accounts need this)
 13.  _fallback_quantity uses spendable (capital minus reserve), not raw capital
 14.  Confidence calibration anchors + VIX avoid_trading rule in system prompt
 15.  Groq 70B models added to fallback chain; stale provider IDs removed
 16.  Circuit breaker cooldown reduced 30→10 calls so recovery is faster
      when rate limits clear (30-call blackout was too aggressive for replay)
 17.  override_model param added so review_strategy() uses gemini-2.5-pro
"""

import asyncio
import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional

import httpx
from google import genai
from google.genai import types

from core.pipeline_models import AICandidateEvaluation, AIEvaluationResult, ApprovedCandidate, TradeCandidate

logger = logging.getLogger("agent.brain")

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

MAX_DECISION_HISTORY              = 200
MIN_CONFIDENCE_THRESHOLD          = 0.30
MAX_CONFIDENCE_THRESHOLD          = 0.95
RATE_LIMIT_BACKOFF_SECONDS        = 0.5
RATE_LIMIT_BACKOFF_MAX_SECONDS    = 5.0
RATE_LIMIT_BACKOFF_JITTER         = 0.20
DEFAULT_DECISION_TIMEOUT_SECONDS  = 8.0
DEFAULT_PROVIDER_TIMEOUT_SECONDS  = 4.0
DEFAULT_MAX_MODELS_PER_DECISION   = 4
DEFAULT_AI_ABSOLUTE_MAX_NEW_ENTRIES = 2

MODEL_ID_ALIASES: dict[str, str] = {
    # OpenRouter currently serves DeepSeek V3 chat traffic under deepseek-chat.
    "openrouter/deepseek/deepseek-v3": "openrouter/deepseek/deepseek-chat",
    "deepseek/deepseek-v3": "deepseek/deepseek-chat",
}

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

    Model chain: 10+ models, all Gemini-class or large open/free instruct models.
    Primary: gemini-2.5-flash | Review: gemini-2.5-pro
    Fallbacks: gemini-2.5-flash-lite, gemini-2.0-flash,
               groq/llama-3.1-70b, groq/llama-3.3-70b-specdec,
               deepseek-chat, step-3.5-flash:free, qwen3-next-80b:free,
               gpt-oss-120b:free, llama-3.3-70b:free, trinity-large-preview:free
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

    # ── Fallback chain — all models are Gemini-class or large open/free instruct models.
    # No small speculative fallback should be used for trading decisions.
    #
    # Removed models (reason):
    #   xiaomimimo/mimo-v2-flash          — unverified financial reasoning, 7B-class
    #   groq/llama-3.1-8b-instant        — 8B, hallucinates price levels
    #   groq/mixtral-8x7b-32768          — Groq deprecated endpoint
    #   openrouter/mistralai/mistral-7b   — 7B, fails complex prompts
    #   openrouter/mistralai/mistral-nemo — poor financial domain reasoning
    #   openrouter/qwen/qwen-2.5-7b       — 7B, invalid SL values
    #   gemini/gemini-1.5-flash           — superseded by 2.0-flash
    DEFAULT_MODEL_TIERS: dict[str, list[str]] = {
        "ultra_fast": [
            "gemini/gemini-2.5-flash-lite",
            "openrouter/stepfun/step-3.5-flash:free",
        ],
        "fast": [
            "gemini/gemini-2.0-flash",
            "openrouter/deepseek/deepseek-chat",
            # Groq 70B — 30 RPM free, ~0.4s latency
            "groq/llama-3.1-70b-versatile",
            # Groq speculative decoding — best Groq JSON adherence
            "groq/llama-3.3-70b-specdec",
        ],
        "balanced": [
            "openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
            "openrouter/openai/gpt-oss-120b:free",
        ],
        "quality": [
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "openrouter/arcee-ai/trinity-large-preview:free",
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

        # ── Gemini client ─────────────────────────────────────────────────────
        self.gemini_client = (
            genai.Client(api_key=self.gemini_api_key) if self.gemini_api_key else None
        )

        # ── Model config ──────────────────────────────────────────────────────
        self.model        = config.get("model", "gemini/gemini-2.5-flash")
        self.model_tiers  = config.get("model_tiers", self.DEFAULT_MODEL_TIERS)
        self.fallback_models = self._resolve_fallback_models(config)
        self.max_models_per_decision: int = max(
            1,
            int(config.get("max_models_per_decision", DEFAULT_MAX_MODELS_PER_DECISION)),
        )

        # fix 2: reduced from 4096
        self.max_tokens  = config.get("max_tokens",  2048)

        # fix 3: reduced from 0.1
        self.temperature = config.get("temperature", 0.05)

        # fix 1: disable Gemini thinking for live trading speed
        self.thinking_budget: int = max(0, int(config.get("thinking_budget", 0)))
        self.decision_timeout_seconds: float = max(
            0.5,
            float(config.get("decision_timeout_seconds", DEFAULT_DECISION_TIMEOUT_SECONDS)),
        )
        self.provider_timeout_seconds: float = max(
            0.5,
            float(config.get("provider_timeout_seconds", DEFAULT_PROVIDER_TIMEOUT_SECONDS)),
        )
        self.max_fallback_wait_seconds: float = max(
            0.0,
            float(config.get("max_fallback_wait_seconds", RATE_LIMIT_BACKOFF_MAX_SECONDS)),
        )

        # ── Position sizing ───────────────────────────────────────────────────
        # fix 12: default raised from 5.0 to 50.0
        self.max_capital_per_trade_pct: float = max(
            0.1, min(100.0, float(config.get("max_capital_per_trade_pct", 50.0)))
        )
        self.min_trade_quantity: int = max(1, int(config.get("min_trade_quantity", 1)))
        self.max_order_value_absolute = config.get("max_order_value_absolute")

        # fix 13: spendable = available - reserve
        self.min_cash_reserve: float = float(config.get("min_cash_reserve", 50.0))

        # ── Confidence threshold ──────────────────────────────────────────────
        self.confidence_threshold: float = self._validated_confidence_threshold(
            config.get("confidence_threshold", 0.65),
            fallback=0.65,
            source="config",
        )
        self.ai_absolute_max_new_entries: int = max(
            0, int(config.get("ai_absolute_max_new_entries", DEFAULT_AI_ABSOLUTE_MAX_NEW_ENTRIES))
        )
        self.ai_absolute_capital_multiplier: float = max(
            0.0, min(1.0, float(config.get("ai_absolute_capital_multiplier", 1.0)))
        )
        self.min_risk_reward: float = max(0.5, float(config.get("min_risk_reward", 1.5)))
        self.fallback_min_trend_liquidity: float = max(0.0, min(1.0, float(config.get("fallback_min_trend_liquidity", 0.50))))
        self.fallback_replay_allow_top1: bool = bool(config.get("fallback_replay_allow_top1", True))
        self.fallback_replay_confidence_floor: float = max(
            MIN_CONFIDENCE_THRESHOLD,
            min(MAX_CONFIDENCE_THRESHOLD, float(config.get("fallback_replay_confidence_floor", 0.60))),
        )
      
        # fix 10: anti-repetition tracking
        self._last_cycle_symbols:    set[str]       = set()
        self._last_cycle_directions: dict[str, str] = {}

        # ── History ───────────────────────────────────────────────────────────
        self.decision_history: list[dict] = []

        # ── Circuit breaker ────────────────────────────────────────────────────
        self._model_consecutive_failures: dict[str, int] = {}
        self._model_skip_until: dict[str, int] = {}
        self._call_counter: int = 0
        self.circuit_breaker_threshold: int = 3
        # circuit_breaker_cooldown: calls to skip after threshold failures
        # 10 = safe for live trading (60s interval × 10 = 10 min blackout max)
        # 30 was too aggressive for replay where each "call" = one candle
        self.circuit_breaker_cooldown: int = max(1, int(config.get("circuit_breaker_cooldown", 10)))

        logger.info(
            "AI model chain configured | primary=%s | fallbacks=%d | "
            "thinking_budget=%d | max_tokens=%d | temperature=%.3f | "
            "decision_timeout=%.1fs | provider_timeout=%.1fs | max_models=%d | "
            "max_capital_pct=%.1f%% | cb_cooldown=%d",
            self.model,
            len(self.fallback_models),
            self.thinking_budget,
            self.max_tokens,
            self.temperature,
            self.decision_timeout_seconds,
            self.provider_timeout_seconds,
            self.max_models_per_decision,
            self.max_capital_per_trade_pct,
            self.circuit_breaker_cooldown,
        )

    # ── Provider helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_model_identifier(model_id: str) -> tuple[str, str]:
        model_id = MODEL_ID_ALIASES.get(model_id, model_id)
        if "/" not in model_id:
            return "gemini", model_id
        provider, model = model_id.split("/", 1)
        return provider.strip().lower(), model.strip()

    def _ensure_provider_key(self, provider: str) -> None:
        key_by_provider = {
            "gemini":     self.gemini_api_key,
            "groq":       self.groq_api_key,
            "openrouter": self.openrouter_api_key,
        }
        if not key_by_provider.get(provider):
            raise RuntimeError(f"Missing API key for provider '{provider}'.")

    def _resolve_fallback_models(self, config: dict) -> list[str]:
        seen: set[str] = {self.model}
        resolved: list[str] = []

        explicit_fallbacks = config.get("fallback_models", []) or []
        for model in explicit_fallbacks:
            if isinstance(model, str) and model and model not in seen:
                seen.add(model)
                resolved.append(model)

        tiered = config.get("model_tiers", self.DEFAULT_MODEL_TIERS) or {}
        for models in tiered.values():
            if not isinstance(models, list):
                continue
            for model in models:
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
            "503", "unavailable", "high demand", "overloaded", "server error", "402",
        ))

    def _is_unsupported_system_instruction_error(self, err: Exception) -> bool:
        return "developer instruction is not enabled" in str(err).lower()

    def _is_timeout_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(t in msg for t in (
            "timed out",
            "timeout",
            "read operation timed out",
            "read timed out",
        ))

    def _is_unavailable_model_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(t in msg for t in (
            "404", "not_found", "model is not found",
            "is not found for api version",
            "not supported for generatecontent",
            "is not a valid model id", "invalid model id",
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
        timeout_seconds: float | None = None,
    ) -> str:
        if provider == "groq":
            base_url = "https://api.groq.com/openai/v1/chat/completions"
            api_key  = self.groq_api_key

        else:  # openrouter
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

        timeout = max(0.5, float(timeout_seconds or self.provider_timeout_seconds))
        with httpx.Client(timeout=timeout) as client:
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
        timeout_seconds: float | None = None,
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

            # fix 1: disable thinking for live trading speed
            if self.thinking_budget == 0:
                try:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_budget=0
                    )
                    logger.debug("Gemini thinking mode: DISABLED (budget=0)")
                except (AttributeError, TypeError, Exception) as exc:
                    logger.debug("ThinkingConfig not usable (%s) — skipping", exc)
            else:
                logger.debug("Gemini thinking mode: ENABLED (budget=%d)", self.thinking_budget)

            cfg = types.GenerateContentConfig(**config_kwargs)
            response = self.gemini_client.models.generate_content(
                model=model,
                contents=prompt,
                config=cfg,
            )
            return self._extract_response_text(response)

        if provider in {"groq", "openrouter"}:
            return self._generate_with_openai_compatible(
                provider=provider, model=model, prompt=prompt,
                temperature=temperature, max_tokens=max_tokens,
                expect_json=expect_json,
                timeout_seconds=timeout_seconds,
            )

        raise RuntimeError(f"Unsupported model provider '{provider}'.")

    # ── Core generation (sync, runs in thread) ───────────────────────────────

    def _generate_text_sync(
        self, prompt: str, *, temperature: float, max_tokens: int, expect_json: bool,
        override_model: str | None = None,
    ) -> tuple[str, str]:
        """
        Synchronous multi-provider call with fallback chain.
        Always run via asyncio.to_thread — never call directly.
        Returns (response_text, model_actually_used).

        If override_model is provided, it is tried first before the
        standard fallback chain (useful for review_strategy with a
        higher-quality model like gemini-2.5-pro).
        """
        if override_model:
            all_models = [override_model] + [
                m for m in self.fallback_models if m != override_model
            ]
        else:
            all_models = [self.model] + [
                m for m in self.fallback_models if m != self.model
            ]
        all_models = all_models[: self.max_models_per_decision]
        last_error: Exception | None = None
        failure_reasons: list[str] = []
        self._call_counter += 1
        started_at = time.monotonic()

        for idx, model_id in enumerate(all_models):
            elapsed = time.monotonic() - started_at
            remaining_budget = self.decision_timeout_seconds - elapsed
            if remaining_budget <= 0:
                failure_reasons.append("decision_budget_exhausted")
                logger.warning(
                    "Decision timeout reached before trying %s | budget=%.1fs | attempts=%d",
                    model_id,
                    self.decision_timeout_seconds,
                    idx,
                )
                break
            # ── Circuit breaker check ────────────────────────────────────────
            skip_until = self._model_skip_until.get(model_id, 0)
            if skip_until > self._call_counter:
                remaining = skip_until - self._call_counter
                logger.info(
                    "Circuit breaker: skipping %s (%d consecutive failures, %d calls remaining)",
                    model_id,
                    self._model_consecutive_failures.get(model_id, 0),
                    remaining,
                )
                failure_reasons.append(f"{model_id}=circuit_breaker_skip")
                continue

            provider, provider_model = self._parse_model_identifier(model_id)
            try:
                response_text = self._generate_with_provider(
                    provider=provider, model=provider_model, prompt=prompt,
                    temperature=temperature, max_tokens=max_tokens,
                    expect_json=expect_json,
                    timeout_seconds=min(self.provider_timeout_seconds, max(0.5, remaining_budget)),
                )
                # Success — reset circuit breaker for this model
                if self._model_consecutive_failures.get(model_id, 0) > 0:
                    logger.info(
                        "Circuit breaker reset for %s (was at %d failures)",
                        model_id, self._model_consecutive_failures[model_id],
                    )
                self._model_consecutive_failures[model_id] = 0

                if model_id != self.model:
                    logger.warning("Using fallback model: %s", model_id)
                return response_text, model_id

            except Exception as e:
                last_error = e
                is_last = idx == len(all_models) - 1
                reason  = "unknown"

                if self._is_rate_limited_error(e):
                    prev_fails = self._model_consecutive_failures.get(model_id, 0)
                    self._model_consecutive_failures[model_id] = prev_fails + 1
                    if self._model_consecutive_failures[model_id] >= self.circuit_breaker_threshold:
                        self._model_skip_until[model_id] = (
                            self._call_counter + self.circuit_breaker_cooldown
                        )
                        logger.warning(
                            "Circuit breaker TRIPPED for %s after %d failures — "
                            "skipping for next %d calls.",
                            model_id,
                            self._model_consecutive_failures[model_id],
                            self.circuit_breaker_cooldown,
                        )
                        failure_reasons.append(f"{model_id}=circuit_breaker_tripped")
                        continue

                    fail_count = self._model_consecutive_failures[model_id]
                    raw_wait = RATE_LIMIT_BACKOFF_SECONDS * (2 ** max(0, fail_count - 1))
                    capped = min(raw_wait, self.max_fallback_wait_seconds)
                    jitter = capped * RATE_LIMIT_BACKOFF_JITTER * (2 * random.random() - 1)
                    wait = max(0.0, capped + jitter)
                    wait = min(wait, max(0.0, remaining_budget - 0.1))
                    logger.warning(
                        "Model %s rate-limited; waiting %.1fs before fallback (idx=%d, fails=%d, remaining_budget=%.1fs).",
                        model_id, wait, idx, fail_count, max(0.0, remaining_budget),
                    )
                    reason = "rate_limited"
                    failure_reasons.append(f"{model_id}={reason}")
                    if is_last:
                        break
                    if wait > 0:
                        time.sleep(wait)
                    continue

                if self._is_timeout_error(e):
                    logger.warning(
                        "Model %s timed out after %.1fs; trying fallback.",
                        model_id,
                        min(self.provider_timeout_seconds, max(0.5, remaining_budget)),
                    )
                    reason = "timeout"
                    failure_reasons.append(f"{model_id}={reason}")
                    if is_last:
                        break
                    continue

                if self._is_unsupported_system_instruction_error(e):
                    logger.warning(
                        "Model %s does not support system instructions; trying fallback.",
                        model_id,
                    )
                    reason = "unsupported_system_instruction"
                    failure_reasons.append(f"{model_id}={reason}")
                    if is_last:
                        break
                    continue

                if self._is_unavailable_model_error(e):
                    prev_fails = self._model_consecutive_failures.get(model_id, 0)
                    self._model_consecutive_failures[model_id] = prev_fails + 1
                    if self._model_consecutive_failures[model_id] >= self.circuit_breaker_threshold:
                        self._model_skip_until[model_id] = (
                            self._call_counter + self.circuit_breaker_cooldown
                        )
                        logger.warning(
                            "Circuit breaker TRIPPED for %s after %d unavailable/permission failures — "
                            "skipping for next %d calls.",
                            model_id,
                            self._model_consecutive_failures[model_id],
                            self.circuit_breaker_cooldown,
                        )
                        failure_reasons.append(f"{model_id}=circuit_breaker_tripped_unavailable")
                        continue
                    logger.warning(
                        "Model %s unavailable/no permission; trying fallback.", model_id
                    )
                    reason = "unavailable_or_permission"
                    failure_reasons.append(f"{model_id}={reason}")
                    if is_last:
                        break
                    continue

                if is_last:
                    failure_reasons.append(f"{model_id}=terminal_error")
                    break

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
        override_model: str | None = None,
    ) -> tuple[str, str]:
        return await asyncio.to_thread(
            self._generate_text_sync,
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            expect_json=expect_json,
            override_model=override_model,
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

    # ── Regime-adaptive confidence threshold ─────────────────────────────────

    def _get_adaptive_confidence_threshold(self, vix: float, market_trend: str) -> float:
        """
        fix 9: confidence threshold adapts to market regime.
        VIX > 22  → raise by 0.15
        VIX > 18  → raise by 0.08
        VIX ≤ 14 + trending → lower by 0.05
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
  
    @staticmethod
    def _normalize_operating_mode(raw_mode: Any) -> str | None:
        valid = {"active_trading", "selective", "capital_preservation", "avoid_trading"}
        mode = str(raw_mode or "").strip().lower()
        return mode if mode in valid else None

    def _mode_constraints(self, operating_mode: str) -> dict[str, float | int]:
        requested = {
            "active_trading": {"confidence_floor": 0.65, "max_new_entries": 2, "capital_multiplier": 1.0},
            "selective": {"confidence_floor": 0.72, "max_new_entries": 1, "capital_multiplier": 0.75},
            "capital_preservation": {"confidence_floor": 0.80, "max_new_entries": 1, "capital_multiplier": 0.50},
            "avoid_trading": {"confidence_floor": 0.95, "max_new_entries": 0, "capital_multiplier": 0.0},
        }.get(operating_mode, {"confidence_floor": 0.72, "max_new_entries": 1, "capital_multiplier": 0.75})
        return {
            "confidence_floor": round(max(self.confidence_threshold, float(requested["confidence_floor"])), 2),
            "max_new_entries": min(self.ai_absolute_max_new_entries, int(requested["max_new_entries"])),
            "capital_multiplier": round(min(self.ai_absolute_capital_multiplier, float(requested["capital_multiplier"])), 2),
        }

    def _infer_operating_mode(self, context: MarketContext, candidates: list[TradeCandidate]) -> str:
        if context.india_vix > 22 or context.session in {"opening", "closing"}:
            return "avoid_trading"
        if context.india_vix > 18 or not candidates:
            return "capital_preservation"
        if len(candidates) >= 2 and context.market_trend in {"trending_up", "trending_down"} and context.india_vix <= 14:
            return "active_trading"
        return "selective"

    def _build_candidate_prompt(self, candidates: list[TradeCandidate], ctx: MarketContext) -> str:
        rows = []
        for candidate in candidates:
            rows.append({
                "candidate_id": candidate.candidate_id,
                "symbol": candidate.symbol,
                "side": candidate.side,
                "strategy": candidate.strategy,
                "setup_type": candidate.setup_type,
                "timeframe": candidate.timeframe,
                "entry_price": float(candidate.entry_price),
                "stop_loss": float(candidate.stop_loss),
                "target": float(candidate.target),
                "risk_reward": candidate.risk_reward,
                "signal_strength": candidate.signal_strength,
                "trend_score": candidate.trend_score,
                "liquidity_score": candidate.liquidity_score,
                "volatility_regime": candidate.volatility_regime,
                "priority": candidate.priority,
                "caution_flags": candidate.caution_flags,
                "event_flags": candidate.event_flags,
                "max_affordable_qty": candidate.max_affordable_qty,
            })

        market_context_json = json.dumps({
            "timestamp": ctx.timestamp.isoformat(),
            "market_trend": ctx.market_trend,
            "session": ctx.session,
            "india_vix": ctx.india_vix,
            "available_capital": ctx.available_capital,
            "recent_news_sentiment": ctx.recent_news_sentiment,
        })
        candidates_json = json.dumps(rows)

        return (
            "Evaluate only the provided trade candidates for Indian markets.\n"
            "Do not invent any new symbols, quantities, or price geometry.\n"
            "You may only approve/reject and rank provided candidates.\n"
            f"Market context: {market_context_json}\n"
            f"Candidates: {candidates_json}\n"
            "Return valid JSON with keys market_regime, operating_mode, market_commentary, candidate_evaluations. "
            "Each candidate_evaluation must contain candidate_id, approved, confidence, rationale, priority, risk_notes.\n"
            "Never include a candidate_id that was not provided."
        )

    def _heuristic_evaluation_result(
        self,
        candidates: list[TradeCandidate],
        context: MarketContext,
        *,
        operating_mode: str,
        commentary: str,
    ) -> AIEvaluationResult:
        constraints = self._mode_constraints(operating_mode)
        floor = float(constraints["confidence_floor"])
        allowed = int(constraints["max_new_entries"])
        dynamic_floor = self._get_adaptive_confidence_threshold(context.india_vix, context.market_trend)
        effective_floor = max(floor, dynamic_floor)
        if self.fallback_replay_allow_top1 and context.session == "mid_session":
            effective_floor = max(self.fallback_replay_confidence_floor, min(effective_floor, self.fallback_replay_confidence_floor))
            allowed = max(allowed, 1)
        ranked = sorted(candidates, key=lambda candidate: (-candidate.priority, -candidate.signal_strength, candidate.symbol))
        evaluations: list[AICandidateEvaluation] = []
        approvals = 0
        for idx, candidate in enumerate(ranked, start=1):
            confidence = round(max(0.0, min(candidate.signal_strength, 1.0)), 4)
            liquidity_component = float(candidate.liquidity_score)
            if liquidity_component > 1.0:
                liquidity_component = min(max(liquidity_component / 10.0, 0.0), 1.0)
            trend_liquidity_quality = (
                max(0.0, min((candidate.trend_score + 1.0) / 2.0, 1.0))
                + max(0.0, min(liquidity_component, 1.0))
            ) / 2.0
            approved = (
                approvals < allowed
                and confidence >= effective_floor
                and candidate.max_affordable_qty > 0
                and candidate.risk_reward >= self.min_risk_reward
                and trend_liquidity_quality >= self.fallback_min_trend_liquidity
            )
            risk_notes: list[str] = []
            if confidence < effective_floor:
                risk_notes.append(f"Below confidence floor {effective_floor:.2f}")
            if candidate.max_affordable_qty <= 0:
                risk_notes.append("Unaffordable candidate")
            if candidate.risk_reward < self.min_risk_reward:
                risk_notes.append("Risk/reward below minimum")
            if trend_liquidity_quality < self.fallback_min_trend_liquidity:
                risk_notes.append("Trend and liquidity quality below fallback minimum")
            if approved:
                approvals += 1
            evaluations.append(AICandidateEvaluation(
                candidate_id=candidate.candidate_id,
                approved=approved,
                confidence=confidence,
                rationale=(
                    "Deterministic fallback approval based on signal strength, affordability, and risk/reward."
                    if approved else
                    "Deterministic fallback rejection based on safety thresholds."
                ),
                priority=idx,
                risk_notes=risk_notes,
            ))
        return AIEvaluationResult(
            candidate_evaluations=evaluations,
            market_regime=context.market_trend,
            operating_mode=operating_mode,
            market_commentary=commentary,
            mode_constraints=constraints,
        )

    @staticmethod
    def _parse_numeric(value: Any, default: float = 0.0) -> tuple[float, bool]:
        if value is None:
            return default, False
        if isinstance(value, (int, float)):
            if not math.isfinite(float(value)):
                return default, False
            return float(value), True
        text = str(value).strip()
        if not text:
            return default, False
        try:
            return float(text), True
        except (TypeError, ValueError):
            match = re.search(r"-?\d+(?:\.\d+)?", text)
            if not match:
                return default, False
            try:
                return float(match.group(0)), True
            except (TypeError, ValueError):
                return default, False

    def _sanitize_candidate_evaluations(
        self,
        raw_evaluations: Any,
        candidates: list[TradeCandidate],
        constraints: dict[str, float | int],
    ) -> list[AICandidateEvaluation]:
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        parsed: dict[str, tuple[AICandidateEvaluation, int]] = {}

        if isinstance(raw_evaluations, list):
            for raw in raw_evaluations:
                if not isinstance(raw, dict):
                    continue
                candidate_id = str(raw.get("candidate_id") or "").strip()
                if candidate_id not in candidate_by_id or candidate_id in parsed:
                    continue
                candidate = candidate_by_id[candidate_id]
                approved = bool(raw.get("approved", False))
                rationale = str(raw.get("rationale") or "No rationale provided.")
                priority_raw, priority_ok = self._parse_numeric(raw.get("priority", candidate.priority or 0), default=float(candidate.priority or 0))
                priority_hint = int(priority_raw) if priority_ok else int(candidate.priority or 0)
                risk_notes = [str(note) for note in raw.get("risk_notes", [])] if isinstance(raw.get("risk_notes"), list) else []
                confidence, confidence_ok = self._parse_numeric(raw.get("confidence", 0.0), default=0.0)
                if not confidence_ok:
                    confidence = 0.0
                    approved = False
                    risk_notes.append("Invalid confidence format from model")
                confidence = max(0.0, min(1.0, confidence))
                parsed[candidate_id] = (AICandidateEvaluation(
                    candidate_id=candidate_id,
                    approved=approved,
                    confidence=confidence,
                    rationale=rationale,
                    priority=priority_hint,
                    risk_notes=risk_notes,
                ), priority_hint)

        floor = float(constraints["confidence_floor"])
        ranked: list[tuple[AICandidateEvaluation, TradeCandidate]] = []
        for candidate in candidates:
            evaluation, _priority_hint = parsed.get(candidate.candidate_id, (
                AICandidateEvaluation(
                    candidate_id=candidate.candidate_id,
                    approved=False,
                    confidence=0.0,
                    rationale="Candidate was not selected by AI evaluation.",
                    priority=candidate.priority or 0,
                    risk_notes=[],
                ),
                candidate.priority or 0,
            ))
            if evaluation.confidence < floor:
                evaluation.approved = False
                evaluation.risk_notes.append(f"Below confidence floor {floor:.2f}")
            if candidate.max_affordable_qty <= 0:
                evaluation.approved = False
                evaluation.risk_notes.append("Unaffordable candidate")
            if candidate.risk_reward < self.min_risk_reward:
                evaluation.approved = False
                evaluation.risk_notes.append("Risk/reward below minimum")
            expected_edge_score = float(getattr(candidate, "expected_edge_score", 0.0))
            if expected_edge_score > 0 and expected_edge_score < float(self.config.get("min_expected_edge_score", 0.55)):
                evaluation.approved = False
                evaluation.risk_notes.append("Expected edge score below minimum")
            ranked.append((evaluation, candidate))

        ranked.sort(key=lambda item: (-int(item[0].approved), -item[0].confidence, -(item[0].priority or 0), -item[1].signal_strength, item[1].symbol))

        approvals = 0
        final: list[AICandidateEvaluation] = []
        allowed = int(constraints["max_new_entries"])
        for idx, (evaluation, _candidate) in enumerate(ranked, start=1):
            if evaluation.approved:
                if approvals >= allowed:
                    evaluation.approved = False
                    evaluation.risk_notes.append("Rejected by operating mode max entries ceiling")
                else:
                    approvals += 1
            evaluation.priority = idx
            final.append(evaluation)
        return final

    async def evaluate_candidates(
        self, candidates: list[TradeCandidate], context: MarketContext
    ) -> AIEvaluationResult:
        operating_mode = self._infer_operating_mode(context, candidates)
        if not candidates:
            return AIEvaluationResult(
                candidate_evaluations=[],
                market_regime=context.market_trend,
                operating_mode=operating_mode,
                market_commentary="No candidates supplied for AI evaluation.",
                mode_constraints=self._mode_constraints(operating_mode),
            )

        prompt = self._build_candidate_prompt(candidates, context)
        try:
            raw_text, _model_used = await self._generate_text(
                prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                expect_json=True,
            )
            payload = json.loads(self._extract_json(raw_text))
        except Exception as exc:
            return self._heuristic_evaluation_result(
                candidates,
                context,
                operating_mode=operating_mode,
                commentary=f"Fallback evaluation used because AI evaluation failed: {exc}",
            )

        market_regime = str(payload.get("market_regime") or context.market_trend)
        operating_mode = self._normalize_operating_mode(payload.get("operating_mode")) or operating_mode
        constraints = self._mode_constraints(operating_mode)
        evaluations = self._sanitize_candidate_evaluations(
            payload.get("candidate_evaluations", []),
            candidates,
            constraints,
        )
        return AIEvaluationResult(
            candidate_evaluations=evaluations,
            market_regime=market_regime,
            operating_mode=operating_mode,
            market_commentary=str(payload.get("market_commentary") or f"Evaluated {len(candidates)} candidate(s)."),
            mode_constraints=constraints,
        )

    async def check_provider_health(self) -> bool:
        """Run a minimal live provider probe for engine preflight checks."""
        if not str(getattr(self, "model", "") or "").strip():
            return False
        try:
            response, _model_used = await self._generate_text(
                "Reply with OK.",
                temperature=0.0,
                max_tokens=8,
                expect_json=False,
            )
        except Exception:
            return False
        return bool(str(response or "").strip())

    async def evaluate_candidate_pipeline(
        self, context: MarketContext, candidates: Optional[list[TradeCandidate]] = None
    ) -> tuple[list[TradeCandidate], AIEvaluationResult, list[ApprovedCandidate]]:
        supplied_candidates = candidates
        if supplied_candidates is None:
            raw_supplied = getattr(context, "trade_candidates", None)
            supplied_candidates = raw_supplied if isinstance(raw_supplied, list) else self._candidates_from_watchlist(context)

        evaluation_result = await self.evaluate_candidates(supplied_candidates, context)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in supplied_candidates}
        approved_candidates: list[ApprovedCandidate] = []
        for evaluation in evaluation_result.candidate_evaluations:
            if not evaluation.approved:
                continue
            candidate = candidate_by_id.get(evaluation.candidate_id)
            if candidate is None:
                continue
            approved_candidates.append(ApprovedCandidate(candidate=candidate, evaluation=evaluation))
        return supplied_candidates, evaluation_result, approved_candidates

    def _candidates_from_watchlist(self, context: MarketContext) -> list[TradeCandidate]:
        reserve = max(self.min_cash_reserve, context.available_capital * 0.05)
        spendable = max(0.0, context.available_capital - reserve)
        candidates: list[TradeCandidate] = []
        for idx, item in enumerate(context.watchlist_data, start=1):
            indicators = item.get("indicators", {})
            signal = str(indicators.get("overall_signal") or "neutral")
            if signal not in {"buy", "strong_buy", "sell", "strong_sell"}:
                continue
            side = "BUY" if signal in {"buy", "strong_buy"} else "SHORT"
            symbol = str(item.get("symbol") or "").upper()
            ltp = Decimal(str(item.get("ltp") or 0))
            if ltp <= 0:
                continue
            levels = item.get("levels", {})
            if side == "BUY":
                stop = Decimal(str(levels.get("s1") or (float(ltp) * 0.99)))
                target = Decimal(str(levels.get("r1") or (float(ltp) * 1.02)))
            else:
                stop = Decimal(str(levels.get("r1") or (float(ltp) * 1.01)))
                target = Decimal(str(levels.get("s1") or (float(ltp) * 0.98)))
            risk = abs(float(ltp - stop))
            reward = abs(float(target - ltp))
            risk_reward = round(reward / risk, 2) if risk > 0 else 0.0
            max_affordable_qty = int(spendable // float(ltp)) if float(ltp) > 0 else 0
            candidates.append(TradeCandidate(
                candidate_id=f"{symbol}:{side}:{context.timestamp.isoformat()}",
                symbol=symbol,
                exchange=str(item.get("exchange") or "NSE"),
                side=side,
                setup_type="watchlist_candidate",
                strategy="ai_wrapper",
                timeframe="day",
                product="MIS",
                entry_price=ltp,
                stop_loss=stop,
                target=target,
                risk_reward=risk_reward,
                signal_strength=max(0.0, min(float(item.get("score") or 0.0) / 100.0, 1.0)),
                trend_score=0.5 if side == "BUY" else -0.5,
                liquidity_score=max(0.0, min(float(indicators.get("volume_ratio") or 0.0), 10.0)),
                volatility_regime="normal",
                sector_tag=None,
                ltp_reference=ltp,
                max_affordable_qty=max_affordable_qty,
                generated_at=context.timestamp,
                priority=max(1, 1000 - idx),
                caution_flags=[],
                event_flags=[],
            ))
        return candidates

    async def analyze_and_decide(self, context: MarketContext) -> list[TradingSignal]:
        started_at = datetime.utcnow()
        candidates, evaluation_result, approved_candidates = await self.evaluate_candidate_pipeline(context)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        signals: list[TradingSignal] = []

        for approved in approved_candidates:
            evaluation = approved.evaluation
            candidate = candidate_by_id.get(approved.candidate_id)
            if not candidate:
                continue
            qty = min(
                candidate.max_affordable_qty,
                self._fallback_quantity_for_signal({
                    "symbol": candidate.symbol,
                    "entry_price": str(candidate.entry_price),
                }, context),
            )
            if qty <= 0:
                continue
            action = SignalAction.BUY if candidate.side == "BUY" else SignalAction.SHORT
            signals.append(TradingSignal(
                action=action,
                symbol=candidate.symbol,
                exchange=candidate.exchange,
                strategy=candidate.strategy,
                quantity=qty,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                target=candidate.target,
                confidence=evaluation.confidence,
                rationale=evaluation.rationale,
                risk_reward=candidate.risk_reward,
                timeframe=candidate.timeframe,
                product=candidate.product,
                priority=evaluation.priority,
                tags=list(candidate.caution_flags) + list(candidate.event_flags),
            ))

        self.decision_history.append({
            "timestamp": started_at.isoformat(),
            "market_regime": evaluation_result.market_regime,
            "operating_mode": evaluation_result.operating_mode,
            "mode_constraints": dict(evaluation_result.mode_constraints),
            "signals": [
                {
                    "symbol": signal.symbol,
                    "action": signal.action.value,
                    "confidence": signal.confidence,
                    "quantity": signal.quantity,
                }
                for signal in signals
            ],
        })
        if len(self.decision_history) > MAX_DECISION_HISTORY:
            self.decision_history = self.decision_history[-MAX_DECISION_HISTORY:]

        return signals

    # ── Strategy review ───────────────────────────────────────────────────────

    async def review_strategy(self, performance_data: dict) -> dict:
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
            # Use the best available model for review — not the fast trading model
            raw, _model = await self._generate_text(
                prompt,
                temperature=0.2,
                max_tokens=2048,
                expect_json=True,
                override_model="gemini/gemini-2.5-pro",
            )
            result = json.loads(self._extract_json(raw))

            raw_params       = result.get("parameter_adjustments", {})
            validated_params = self._validate_param_adjustments(raw_params)
            result["parameter_adjustments"] = validated_params

            if self.PARAM_CONSUMER_KEYS:
                unclaimed = set(validated_params) - self.PARAM_CONSUMER_KEYS
                if unclaimed:
                    logger.warning(
                        "review_strategy returned keys not in PARAM_CONSUMER_KEYS: %s",
                        sorted(unclaimed),
                    )

            return result
        except Exception as e:
            logger.error("Strategy review error: %s", e)
            return {}

    async def explain_position(self, position: dict) -> str:
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
        # fix 13: spendable = available minus reserve
        reserve   = max(self.min_cash_reserve, ctx.available_capital * 0.05)
        spendable = max(0.0, ctx.available_capital - reserve)

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

        # fix 6: affordability labels
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

            last_dir    = self._last_cycle_directions.get(w["symbol"])
            repeat_flag = (
                f" [LAST CYCLE: {last_dir} — DO NOT REPEAT SAME DIRECTION]"
                if last_dir else ""
            )

            cost_per_share = ltp_val * 1.0015
            if cost_per_share > 0 and spendable >= cost_per_share and not has_position:
                max_qty      = int(spendable / cost_per_share)
                rupee_profit = (ltp_val * 0.02) * max_qty
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

        # fix 8: pre-compute spendable
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

                if symbol in open_symbols and action not in (
                    SignalAction.HOLD, SignalAction.NO_ACTION,
                    SignalAction.SQUARE_OFF, SignalAction.COVER,
                ):
                    logger.info(
                        "Skipping %s %s — already has open position",
                        action.value, symbol,
                    )
                    continue

                # fix 8: hard affordability check before risk manager
                if action in (SignalAction.BUY, SignalAction.SHORT):
                    entry_price_raw = s.get("entry_price")
                    ltp_fallback    = next(
                        (float(w.get("ltp", 0)) for w in ctx.watchlist_data
                         if w.get("symbol") == symbol),
                        0.0,
                    )
                    price_to_check   = float(entry_price_raw or ltp_fallback or 0)
                    cost_with_buffer = price_to_check * 1.0015

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
                            "Skipping signal with quantity=%d for %s | action=%s | capital=%.2f",
                            qty, symbol, action.value, ctx.available_capital,
                        )
                        continue
                    logger.info(
                        "Applied fallback quantity=%d for %s | action=%s",
                        qty, symbol, action.value,
                    )

                confidence  = max(0.0, min(1.0, float(s.get("confidence", 0.5))))
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

        signals.sort(key=lambda x: (x.priority, -x.confidence))

        # fix 11: hard cap at 2
        if len(signals) > 2:
            logger.info("Capping signals from %d to 2", len(signals))
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

        # preserve compatibility: fallback sizing uses available capital directly
        capital   = Decimal(str(max(ctx.available_capital, 0.0)))
        spendable = capital

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
