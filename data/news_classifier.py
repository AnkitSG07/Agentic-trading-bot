from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from data.nse_feed import NewsSentimentAnalyzer

VALID_RECOMMENDATIONS = {"boost", "caution", "block", "ignore"}


@dataclass(frozen=True, slots=True)
class NewsClassification:
    headline: str
    affected_symbols: tuple[str, ...]
    sector: Optional[str]
    sentiment_score: float
    confidence: float
    freshness_timestamp: datetime
    impact_horizon: str
    recommendation: str


@dataclass(frozen=True, slots=True)
class NewsModifier:
    confidence_delta: float = 0.0
    priority_delta: int = 0
    blocked: bool = False
    caution_flags: tuple[str, ...] = ()
    event_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewsClassifierConfig:
    enabled: bool = True
    freshness_limit_minutes: int = 240
    confidence_modifier_cap: float = 0.2


class NewsClassifier:
    POSITIVE_KEYWORDS = tuple(word.lower() for word in NewsSentimentAnalyzer.BULLISH_KEYWORDS)
    NEGATIVE_KEYWORDS = tuple(word.lower() for word in NewsSentimentAnalyzer.BEARISH_KEYWORDS)
    BLOCK_KEYWORDS = ("fraud", "sebi probe", "default", "regulatory", "promoter pledge")

    def __init__(self, config: NewsClassifierConfig | None = None) -> None:
        self.config = config or NewsClassifierConfig()

    def classify_news(
        self,
        items: Iterable[dict],
        *,
        now: datetime | None = None,
    ) -> list[NewsClassification]:
        """Return structured classifications for news still inside the freshness window.

        Stale items older than ``config.freshness_limit_minutes`` are intentionally
        excluded rather than retained with downgraded confidence so that only
        currently-actionable modifiers can influence candidate ranking/safety.
        """
        if not self.config.enabled:
            return []
        reference_now = now or datetime.utcnow()
        classifications: list[NewsClassification] = []
        for raw in items:
            headline = str(raw.get("headline") or raw.get("title") or raw.get("text") or "").strip()
            if not headline:
                continue
            published_at = self._parse_timestamp(raw.get("published_at") or raw.get("timestamp"), fallback=reference_now)
            if reference_now - published_at > timedelta(minutes=int(self.config.freshness_limit_minutes)):
                continue
            affected_symbols = self._normalize_symbols(raw.get("affected_symbols") or raw.get("symbols") or raw.get("symbol"))
            sector = raw.get("sector")
            sentiment_score = self._score_sentiment(headline)
            confidence = self._confidence(sentiment_score, affected_symbols, sector, published_at, reference_now)
            impact_horizon = self._impact_horizon(published_at, reference_now)
            recommendation = self._recommendation(sentiment_score, confidence, headline)
            classifications.append(NewsClassification(
                headline=headline,
                affected_symbols=affected_symbols,
                sector=str(sector) if sector else None,
                sentiment_score=sentiment_score,
                confidence=confidence,
                freshness_timestamp=published_at,
                impact_horizon=impact_horizon,
                recommendation=recommendation,
            ))
        return classifications

    def modifier_for_candidate(
        self,
        classifications: Iterable[NewsClassification],
        *,
        symbol: str,
        sector: Optional[str] = None,
    ) -> NewsModifier:
        normalized_symbol = str(symbol or "").upper()
        normalized_sector = str(sector or "").lower() or None
        confidence_delta = 0.0
        priority_delta = 0
        blocked = False
        caution_flags: list[str] = []
        event_flags: list[str] = []

        for item in classifications:
            applies = normalized_symbol in item.affected_symbols or (normalized_sector and item.sector and item.sector.lower() == normalized_sector)
            if not applies:
                continue
            event_flags.append(f"news:{item.recommendation}")
            if item.recommendation == "boost":
                confidence_delta += 0.15 * item.confidence
                priority_delta += 1
            elif item.recommendation == "caution":
                confidence_delta -= 0.1 * item.confidence
                caution_flags.append(f"news_caution:{item.headline[:40]}")
            elif item.recommendation == "block":
                blocked = True
                confidence_delta -= 0.25 * item.confidence
                priority_delta -= 2
                caution_flags.append(f"news_block:{item.headline[:40]}")

        confidence_cap = max(float(self.config.confidence_modifier_cap), 0.0)
        confidence_delta = max(-confidence_cap, min(confidence_delta, confidence_cap))

        return NewsModifier(
            confidence_delta=round(confidence_delta, 4),
            priority_delta=priority_delta,
            blocked=blocked,
            caution_flags=tuple(caution_flags),
            event_flags=tuple(event_flags),
        )

    @staticmethod
    def _normalize_symbols(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            tokens = [token.strip().upper() for token in value.replace(',', ' ').split() if token.strip()]
            return tuple(tokens)
        if isinstance(value, Iterable):
            return tuple(str(token).strip().upper() for token in value if str(token).strip())
        return ()

    @staticmethod
    def _parse_timestamp(value: object, *, fallback: datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value.strip():
            raw = value.strip().replace('Z', '+00:00')
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is not None:
                    return parsed.replace(tzinfo=None)
                return parsed
            except ValueError:
                return fallback
        return fallback

    def _score_sentiment(self, headline: str) -> float:
        lower = headline.lower()
        positive_hits = sum(1 for keyword in self.POSITIVE_KEYWORDS if keyword in lower)
        negative_hits = sum(1 for keyword in self.NEGATIVE_KEYWORDS if keyword in lower)
        if positive_hits == 0 and negative_hits == 0:
            return 0.0
        raw = positive_hits - negative_hits
        return max(-1.0, min(raw / 3.0, 1.0))

    def _confidence(
        self,
        sentiment_score: float,
        affected_symbols: tuple[str, ...],
        sector: object,
        published_at: datetime,
        now: datetime,
    ) -> float:
        confidence = 0.35 + min(abs(sentiment_score), 1.0) * 0.35
        if affected_symbols:
            confidence += 0.2
        elif sector:
            confidence += 0.1
        if now - published_at <= timedelta(hours=6):
            confidence += 0.1
        return round(max(0.0, min(confidence, 0.99)), 2)

    @staticmethod
    def _impact_horizon(published_at: datetime, now: datetime) -> str:
        age = now - published_at
        if age <= timedelta(hours=6):
            return "intraday"
        if age <= timedelta(days=2):
            return "swing"
        return "position"

    def _recommendation(self, sentiment_score: float, confidence: float, headline: str) -> str:
        lower = headline.lower()
        if any(keyword in lower for keyword in self.BLOCK_KEYWORDS) and sentiment_score < 0 and confidence >= 0.6:
            return "block"
        if sentiment_score >= 0.45 and confidence >= 0.6:
            return "boost"
        if sentiment_score <= -0.45 and confidence >= 0.7:
            return "block"
        if sentiment_score <= -0.2 and confidence >= 0.5:
            return "caution"
        return "ignore"
