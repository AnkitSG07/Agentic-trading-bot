from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

import pandas as pd

from core.pipeline_models import TradeCandidate
from data.indicators import IndicatorsEngine
from data.news_classifier import NewsClassifier
from data.stock_selector import SelectorConfig, StockSelector


@dataclass(slots=True)
class CandidateBuilderConfig:
    exchange: str = "NSE"
    timeframe: str = "day"
    product: str = "MIS"
    strategy: str = "deterministic_momentum"
    setup_type: str = "trend_continuation"
    capital_budget: float = 0.0
    min_rank_score: float = -999999.0
    max_candidates: int = 10
    risk_reward_target: float = 2.0
    selector_config: SelectorConfig = field(default_factory=SelectorConfig)


class CandidateBuilder:
    def __init__(
        self,
        config: CandidateBuilderConfig | None = None,
        *,
        indicators_engine: IndicatorsEngine | None = None,
        stock_selector: StockSelector | None = None,
        news_classifier: NewsClassifier | None = None,
    ) -> None:
        self.config = config or CandidateBuilderConfig()
        self.indicators_engine = indicators_engine or IndicatorsEngine()
        self.stock_selector = stock_selector or StockSelector(self.config.selector_config)
        self.news_classifier = news_classifier or NewsClassifier()

    def build_candidates(
        self,
        frames: dict[str, pd.DataFrame],
        *,
        price_references: Optional[dict[str, float]] = None,
        symbols: Optional[Iterable[str]] = None,
        generated_at: Optional[datetime] = None,
        regime: Optional[str] = None,
        session_name: Optional[str] = None,
        sector_map: Optional[dict[str, str]] = None,
        news_items: Optional[Iterable[dict]] = None,
    ) -> list[TradeCandidate]:
        if not frames:
            return []

        generated_at = generated_at or datetime.utcnow()
        ranked = self.stock_selector.rank_candidates(frames, symbols=symbols)
        if not ranked:
            return []

        classifications = self.news_classifier.classify_news(news_items or [], now=generated_at)
        ranked_by_symbol = {item["symbol"]: item for item in ranked}
        candidates: list[TradeCandidate] = []

        for item in ranked[: self.config.max_candidates]:
            symbol = item["symbol"]
            if float(item.get("score", 0.0)) < float(self.config.min_rank_score):
                continue
            df = frames.get(symbol)
            if df is None or df.empty:
                continue
            bundle = self.indicators_engine.compute(df, symbol=symbol, timeframe=self.config.timeframe)
            side = self._resolve_side(bundle, item)
            if side is None:
                continue

            sector_tag = (sector_map or {}).get(symbol)
            modifier = self.news_classifier.modifier_for_candidate(classifications, symbol=symbol, sector=sector_tag)
            if modifier.blocked:
                continue

            entry_price = self._decimal_price((price_references or {}).get(symbol, bundle.ltp or item.get("ltp") or 0.0))
            if entry_price <= 0:
                continue
            risk_unit = self._risk_unit(bundle, entry_price)
            stop_loss, target = self._levels(side, entry_price, risk_unit)
            risk_reward = self._risk_reward(entry_price, stop_loss, target)
            max_affordable_qty = self._affordable_qty(entry_price)
            signal_strength = self._signal_strength(bundle, ranked_by_symbol[symbol], modifier)
            trend_score = self._trend_score(bundle)
            liquidity_score = self._liquidity_score(item)
            volatility_regime = self._volatility_regime(bundle)
            caution_flags = list(modifier.caution_flags)
            if session_name in {"opening", "closing"}:
                caution_flags.append(f"session:{session_name}")
            event_flags = list(modifier.event_flags)
            if regime:
                event_flags.append(f"regime:{regime}")

            priority = self._priority(item, modifier)

            candidates.append(TradeCandidate(
                candidate_id=self._candidate_id(symbol, side, generated_at),
                symbol=symbol,
                exchange=self.config.exchange,
                side=side,
                setup_type=self.config.setup_type,
                strategy=self.config.strategy,
                timeframe=self.config.timeframe,
                product=self.config.product,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target=target,
                risk_reward=risk_reward,
                signal_strength=signal_strength,
                trend_score=trend_score,
                liquidity_score=liquidity_score,
                volatility_regime=volatility_regime,
                sector_tag=sector_tag,
                ltp_reference=entry_price,
                max_affordable_qty=max_affordable_qty,
                generated_at=generated_at,
                priority=priority,
                caution_flags=caution_flags,
                event_flags=event_flags,
            ))
        candidates.sort(key=lambda candidate: (-candidate.priority, -candidate.signal_strength, candidate.symbol))
        return candidates

    @staticmethod
    def _resolve_side(bundle, rank_item: dict) -> Optional[str]:
        mapping = {
            "buy": "BUY",
            "strong_buy": "BUY",
            "sell": "SHORT",
            "strong_sell": "SHORT",
        }
        direct = mapping.get(bundle.overall_signal)
        if direct:
            return direct

        bullish_votes = 0
        bearish_votes = 0
        if bundle.macd is not None and bundle.macd_signal is not None:
            if bundle.macd > bundle.macd_signal:
                bullish_votes += 1
            elif bundle.macd < bundle.macd_signal:
                bearish_votes += 1
        if bundle.supertrend_direction == 1:
            bullish_votes += 1
        elif bundle.supertrend_direction == -1:
            bearish_votes += 1
        if float(rank_item.get("momentum_20") or 0.0) > 0:
            bullish_votes += 1
        elif float(rank_item.get("momentum_20") or 0.0) < 0:
            bearish_votes += 1
        if float(rank_item.get("trend_bonus") or 0.0) > 0:
            bullish_votes += 1
        elif float(rank_item.get("trend_bonus") or 0.0) < 0:
            bearish_votes += 1

        if bullish_votes >= 2 and bullish_votes > bearish_votes:
            return "BUY"
        if bearish_votes >= 2 and bearish_votes > bullish_votes:
            return "SHORT"
        return None

    @staticmethod
    def _decimal_price(value: float | Decimal) -> Decimal:
        return Decimal(str(round(float(value), 2))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _risk_unit(self, bundle, entry_price: Decimal) -> Decimal:
        atr = float(bundle.atr or 0.0)
        atr_fallback = float(entry_price) * 0.01
        risk_value = max(atr, atr_fallback, 0.5)
        return self._decimal_price(risk_value)

    def _levels(self, side: str, entry_price: Decimal, risk_unit: Decimal) -> tuple[Decimal, Decimal]:
        reward_unit = risk_unit * Decimal(str(self.config.risk_reward_target))
        if side == "BUY":
            return (entry_price - risk_unit).quantize(Decimal("0.01")), (entry_price + reward_unit).quantize(Decimal("0.01"))
        return (entry_price + risk_unit).quantize(Decimal("0.01")), (entry_price - reward_unit).quantize(Decimal("0.01"))

    @staticmethod
    def _risk_reward(entry_price: Decimal, stop_loss: Decimal, target: Decimal) -> float:
        risk = abs(float(entry_price - stop_loss))
        reward = abs(float(target - entry_price))
        if risk <= 0:
            return 0.0
        return round(reward / risk, 2)

    def _affordable_qty(self, entry_price: Decimal) -> int:
        budget = float(self.config.capital_budget or 0.0)
        if budget <= 0:
            return 0
        return max(int(budget // float(entry_price)), 0)

    @staticmethod
    def _volatility_regime(bundle) -> str:
        atr_pct = float(bundle.atr_pct or 0.0)
        if atr_pct >= 3.0:
            return "high"
        if atr_pct >= 1.0:
            return "normal"
        return "low"

    @staticmethod
    def _liquidity_score(rank_item: dict) -> float:
        avg_volume = float(rank_item.get("avg_volume_20d") or 0.0)
        score = min(avg_volume / 100000.0, 10.0)
        return round(score, 2)

    @staticmethod
    def _trend_score(bundle) -> float:
        score = 0.0
        if bundle.trend == "bullish":
            score += 0.5
        elif bundle.trend == "bearish":
            score -= 0.5
        if bundle.supertrend_direction == 1:
            score += 0.5
        elif bundle.supertrend_direction == -1:
            score -= 0.5
        if bundle.macd is not None and bundle.macd_signal is not None:
            score += 0.25 if bundle.macd > bundle.macd_signal else -0.25
        return round(score, 2)

    @staticmethod
    def _signal_strength(bundle, rank_item: dict, modifier) -> float:
        base = {
            "strong_buy": 0.9,
            "buy": 0.7,
            "sell": 0.7,
            "strong_sell": 0.9,
        }.get(bundle.overall_signal, 0.0)
        rank_component = min(max(float(rank_item.get("score") or 0.0) / 100.0, 0.0), 0.3)
        value = base + rank_component + float(modifier.confidence_delta or 0.0)
        return round(max(0.0, min(value, 1.0)), 4)

    @staticmethod
    def _priority(rank_item: dict, modifier) -> int:
        base_rank = int(rank_item.get("rank") or 0)
        if base_rank <= 0:
            base_rank = 9999
        base_priority = 1000 - base_rank
        news_priority = int(modifier.priority_delta or 0) * 10
        return max(1, base_priority + news_priority)

    @staticmethod
    def _candidate_id(symbol: str, side: str, generated_at: datetime) -> str:
        return f"{symbol}:{side}:{generated_at.isoformat()}"
