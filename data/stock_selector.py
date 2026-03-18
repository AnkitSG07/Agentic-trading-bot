from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass
class SelectorConfig:
    min_stock_price: float = 50.0
    max_stock_price: float = 5000.0
    min_avg_daily_volume: float = 100000.0
    max_auto_pick_symbols: int = 10


class StockSelector:
    """Rank stocks from a broader universe using simple trend, momentum, and liquidity."""

    def __init__(self, config: SelectorConfig):
        self.config = config

    def rank_candidates(self, frames: dict[str, pd.DataFrame], symbols: Iterable[str] | None = None) -> list[dict]:
        candidates: list[dict] = []
        symbol_iter = list(symbols) if symbols is not None else list(frames.keys())
        for symbol in symbol_iter:
            df = frames.get(symbol)
            ranked = self._score_symbol(symbol, df)
            if ranked:
                candidates.append(ranked)

        candidates.sort(key=lambda item: item["score"], reverse=True)
        for idx, item in enumerate(candidates, start=1):
            item["rank"] = idx
        return candidates

    def _score_symbol(self, symbol: str, df: pd.DataFrame | None) -> dict | None:
        if df is None or df.empty or "close" not in df.columns or "volume" not in df.columns or len(df) < 20:
            return None

        clean = df.dropna(subset=["close", "volume"]).tail(60).copy()
        if len(clean) < 20:
            return None

        ltp = float(clean["close"].iloc[-1])
        avg_volume_20d = float(clean["volume"].tail(20).mean())
        if ltp < float(self.config.min_stock_price) or ltp > float(self.config.max_stock_price):
            return None
        if avg_volume_20d < float(self.config.min_avg_daily_volume):
            return None

        close = clean["close"].astype(float)
        momentum_5 = self._safe_pct_change(close.iloc[-1], close.iloc[-6]) if len(close) >= 6 else 0.0
        momentum_20 = self._safe_pct_change(close.iloc[-1], close.iloc[-20])
        sma_10 = float(close.tail(10).mean())
        sma_20 = float(close.tail(20).mean())
        trend_bonus = 1.5 if ltp > sma_10 > sma_20 else (0.75 if ltp > sma_20 else -1.0)
        liquidity_score = min(avg_volume_20d / max(float(self.config.min_avg_daily_volume), 1.0), 5.0)

        score = round((momentum_20 * 0.45) + (momentum_5 * 0.25) + (trend_bonus * 10.0) + (liquidity_score * 5.0), 2)
        reasons = []
        if momentum_20 > 0:
            reasons.append(f"20d momentum {momentum_20:.2f}%")
        if momentum_5 > 0:
            reasons.append(f"5d momentum {momentum_5:.2f}%")
        if ltp > sma_10 > sma_20:
            reasons.append("price above 10d/20d trend")
        reasons.append(f"20d avg volume {avg_volume_20d:,.0f}")

        return {
            "symbol": symbol,
            "score": score,
            "rank": 0,
            "ltp": round(ltp, 2),
            "avg_volume_20d": round(avg_volume_20d, 2),
            "reason": "; ".join(reasons),
        }

    @staticmethod
    def _safe_pct_change(current: float, previous: float) -> float:
        if previous == 0:
            return 0.0
        return ((current - previous) / previous) * 100.0
