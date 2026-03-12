"""
AI Agent Brain - The intelligence core of the trading bot.
Uses Gemini API to make multi-strategy trading decisions based on
real-time market data, technical indicators, and portfolio state.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from google import genai
from google.genai import types

logger = logging.getLogger("agent.brain")


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
3. **Signal Generation**: Provide specific, actionable signals with exact levels
4. **Risk Calculation**: Always include SL, target, and position size
5. **Confidence Scoring**: Rate each signal 0.0-1.0 based on confluence

## Available Strategies
- **Momentum**: RSI + MACD + Volume confirmation for trend trades
- **Mean Reversion**: Bollinger Band squeezes, RSI extremes
- **Options Selling**: Short premium when IV Rank > 50, defined risk spreads
- **Breakout**: ATR-based breakouts with volume confirmation
- **Index Scalping**: NIFTY/BANKNIFTY intraday with Supertrend

## Output Rules
- ALWAYS respond with valid JSON only, no additional text
- Include specific price levels (not vague descriptions)
- If market conditions are unfavorable, return NO_ACTION signals
- Risk-first mindset: Never risk more than 2% of capital per trade
- Respect market hours (9:15 AM - 3:30 PM IST)
- Factor in STT and brokerage in profit calculations

## Risk Limits (HARD RULES)
- Max 5% capital per trade
- Max 10 open positions simultaneously  
- Stop if daily loss exceeds 2% of capital
- Stop if account drawdown exceeds 8%
- Never average losing positions
"""

DECISION_PROMPT_TEMPLATE = """
## Current Market Context
**Time**: {timestamp} IST
**Session**: {session}
**Day**: {day_of_week}

## Index Data
- NIFTY 50: {nifty50_ltp}
- BANK NIFTY: {banknifty_ltp}
- INDIA VIX: {india_vix}
- Market Trend: {market_trend}
- Put-Call Ratio: {pcr}

## Portfolio State
- Available Capital: ₹{available_capital:,.0f}
- Used Margin: ₹{used_margin:,.0f}
- Open Positions: {open_positions_count}
{open_positions_summary}

## Watchlist Analysis
{watchlist_summary}

## Options Flow (if available)
{options_summary}

## News Sentiment
{news_sentiment}

---
Analyze this data and generate trading signals. Return ONLY a JSON object:

{{
  "market_regime": "trending_up | trending_down | ranging | high_volatility",
  "market_commentary": "brief 2-sentence market view",
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
      "rationale": "RSI(14) at 62 crossing 60 with MACD bullish crossover. Volume 1.8x avg. Breaking above 20-day resistance at 2445.",
      "risk_reward": 2.1,
      "timeframe": "intraday",
      "product": "MIS",
      "priority": 1,
      "tags": ["breakout", "high_volume", "trend_following"]
    }}
  ],
  "positions_to_exit": ["SYMBOL1", "SYMBOL2"],
  "risk_assessment": "low | medium | high",
  "session_recommendation": "active_trading | selective | avoid_trading"
}}

Generate 0-5 signals based on conviction. Quality over quantity.
"""


# ─── AI AGENT ────────────────────────────────────────────────────────────────

class TradingAgent:
    """
    The AI brain that drives all trading decisions.
    Wraps Gemini with trading-specific prompting and response parsing.
    """

    def __init__(self, config: dict):
        self.config = config
        self.api_key = os.getenv(config.get("api_key_env", "GEMINI_API_KEY"), "")
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.model = config.get("model", "gemini-2.5-flash")
        self.fallback_models = config.get("fallback_models", [
            "gemma-3-1b-it",
            "gemma-3-4b-it",
            "gemma-3-12b-it",
            "gemma-3-27b-it",
        ])
        self.max_tokens = config.get("max_tokens", 4096)
        self.temperature = config.get("temperature", 0.1)
        self.confidence_threshold = config.get("confidence_threshold", 0.65)
        self.decision_history: list[dict] = []

    def _is_rate_limited_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(token in msg for token in ("429", "rate limit", "quota", "resource_exhausted"))

    def _extract_response_text(self, response: Any) -> str:
        text = getattr(response, "text", None)
        if text:
            return text.strip()

        candidates = getattr(response, "candidates", []) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    return part_text.strip()
        return ""

    def _generate_text(self, prompt: str, *, temperature: float, max_tokens: int, expect_json: bool) -> str:
        if not self.client:
            raise RuntimeError("Gemini API key missing. Set GEMINI_API_KEY in environment.")

        models = [self.model] + [m for m in self.fallback_models if m != self.model]
        last_error: Exception | None = None

        for idx, model in enumerate(models):
            try:
                config = types.GenerateContentConfig(
                    system_instruction=AGENT_SYSTEM_PROMPT,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json" if expect_json else "text/plain",
                )
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                if model != self.model:
                    logger.warning(f"Using fallback LLM model due to primary model limits: {model}")
                return self._extract_response_text(response)
            except Exception as e:
                last_error = e
                if self._is_rate_limited_error(e) and idx < len(models) - 1:
                    logger.warning(f"Model {model} rate-limited; trying fallback model.")
                    continue
                raise

        raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")

    @staticmethod
    def _strip_code_fences(raw_text: str) -> str:
        raw = raw_text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return raw.strip()

    async def analyze_and_decide(self, context: MarketContext) -> list[TradingSignal]:
        """
        Core decision function. Takes market context, calls Gemini,
        returns actionable trading signals.
        """
        prompt = self._build_prompt(context)

        try:
            raw_text = self._generate_text(
                prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                expect_json=True,
            )
            decision = json.loads(self._strip_code_fences(raw_text))
            signals = self._parse_signals(decision, context)

            # Log the decision
            self.decision_history.append({
                "timestamp": context.timestamp.isoformat(),
                "market_regime": decision.get("market_regime"),
                "commentary": decision.get("market_commentary"),
                "signals_count": len(signals),
                "session_recommendation": decision.get("session_recommendation"),
            })

            logger.info(
                f"🤖 AI Decision | Regime: {decision.get('market_regime')} | "
                f"Signals: {len(signals)} | Risk: {decision.get('risk_assessment')}"
            )

            # Filter by confidence threshold
            actionable = [s for s in signals if s.confidence >= self.confidence_threshold]
            logger.info(f"📊 {len(actionable)}/{len(signals)} signals above confidence threshold")

            return actionable

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            return []
        except Exception as e:
            err = str(e).lower()
            if "api key" in err and "missing" in err:
                logger.error("AI agent disabled: missing GEMINI_API_KEY.")
            elif self._is_rate_limited_error(e):
                logger.error("AI agent failed: all configured Gemini models are rate-limited.")
            else:
                logger.error(f"AI agent error: {e}")
            return []

    async def review_strategy(self, performance_data: dict) -> dict:
        """
        Periodic strategy review. AI evaluates what's working and
        adjusts strategy weights/parameters accordingly.
        """
        prompt = f"""
Review this trading bot's recent performance and provide strategic recommendations.

Performance Data:
{json.dumps(performance_data, indent=2)}

Respond with JSON:
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
  "overall_assessment": "text assessment"
}}
"""
        try:
            raw = self._generate_text(
                prompt,
                temperature=0.2,
                max_tokens=2048,
                expect_json=True,
            )
            return json.loads(self._strip_code_fences(raw))
        except Exception as e:
            logger.error(f"Strategy review error: {e}")
            return {}

    async def explain_position(self, position: dict) -> str:
        """Get AI explanation of why a position should be held or exited."""
        prompt = f"""
Analyze this open position and recommend: HOLD, TRAIL_STOP, or EXIT with brief rationale.

Position: {json.dumps(position, indent=2)}

Respond in 2-3 sentences max.
"""
        try:
            return self._generate_text(
                prompt,
                temperature=0.1,
                max_tokens=256,
                expect_json=False,
            )
        except Exception as e:
            logger.error(f"explain_position error: {e}")
            return "Unable to analyze position."

    # ── Prompt Builder ────────────────────────────────────────────────────────

    def _build_prompt(self, ctx: MarketContext) -> str:
        # Build open positions summary
        positions_summary = ""
        if ctx.open_positions:
            for p in ctx.open_positions:
                pnl_str = f"₹{p.get('pnl', 0):+,.0f}"
                positions_summary += (
                    f"  - {p['symbol']} | {p['side']} {p['quantity']} | "
                    f"Avg: ₹{p.get('avg_price', 0):,.2f} | LTP: ₹{p.get('ltp', 0):,.2f} | P&L: {pnl_str}\n"
                )

        # Build watchlist summary
        watchlist_summary = ""
        for w in ctx.watchlist_data[:15]:  # Limit to 15 symbols
            indicators = w.get("indicators", {})
            watchlist_summary += (
                f"  **{w['symbol']}** | LTP: ₹{w.get('ltp', 0):,.2f} | "
                f"Change: {w.get('change_pct', 0):+.2f}% | "
                f"Vol: {w.get('volume_ratio', 1.0):.1f}x | "
                f"RSI: {indicators.get('rsi', 'N/A')} | "
                f"MACD: {indicators.get('macd_signal', 'N/A')} | "
                f"Trend: {indicators.get('trend', 'N/A')}\n"
            )

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
            market_trend=ctx.market_trend,
            pcr=f"{ctx.pcr:.2f}" if ctx.pcr else "N/A",
            available_capital=ctx.available_capital,
            used_margin=ctx.used_margin,
            open_positions_count=len(ctx.open_positions),
            open_positions_summary=positions_summary or "  None",
            watchlist_summary=watchlist_summary or "  No data",
            options_summary=options_summary,
            news_sentiment=ctx.recent_news_sentiment or "Not available",
        )

    def _parse_signals(self, decision: dict, ctx: MarketContext) -> list[TradingSignal]:
        signals = []
        for s in decision.get("signals", []):
            try:
                signals.append(TradingSignal(
                    action=SignalAction(s["action"]),
                    symbol=s["symbol"],
                    exchange=s.get("exchange", "NSE"),
                    strategy=s.get("strategy", "unknown"),
                    quantity=int(s.get("quantity", 1)),
                    entry_price=Decimal(str(s["entry_price"])) if s.get("entry_price") else None,
                    stop_loss=Decimal(str(s["stop_loss"])) if s.get("stop_loss") else None,
                    target=Decimal(str(s["target"])) if s.get("target") else None,
                    confidence=float(s.get("confidence", 0.5)),
                    rationale=s.get("rationale", ""),
                    risk_reward=float(s.get("risk_reward", 1.0)) if s.get("risk_reward") else None,
                    timeframe=s.get("timeframe", "intraday"),
                    product=s.get("product", "MIS"),
                    priority=int(s.get("priority", 5)),
                    tags=s.get("tags", []),
                ))
            except (KeyError, ValueError) as e:
                logger.warning(f"Skipping malformed signal: {e} | {s}")

        # Sort by priority (lower number = higher priority)
        signals.sort(key=lambda x: (x.priority, -x.confidence))
        return signals
