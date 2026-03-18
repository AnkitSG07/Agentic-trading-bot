from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass
class SelectorConfig:
    min_stock_price: float = 50.0
    max_stock_price: float = 5000.0
    min_avg_daily_volume: float = 100000.0
    min_avg_daily_turnover: float = 5000000.0
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

    def select_affordable_candidates(
        self,
        frames: dict[str, pd.DataFrame],
        budget_cap: float,
        max_symbols: int | None = None,
        symbols: Iterable[str] | None = None,
        fee_pct: float = 0.0003,
        slippage_pct: float = 0.0005,
    ) -> list[dict]:
        max_items = max_symbols or self.config.max_auto_pick_symbols
        ranked = self.rank_candidates(frames, symbols=symbols)
        selected: list[dict] = []
        allowance_multiplier = 1.0 + max(float(fee_pct), 0.0) + max(float(slippage_pct), 0.0) + 0.002

        for item in ranked:
            effective_price = float(item["ltp"]) * allowance_multiplier
            qty = int(float(budget_cap) // effective_price)
            if qty <= 0:
                continue
            estimated_cost = round(qty * effective_price, 2)
            expected_return_pct = self._expected_return_pct(item)
            estimated_profit = round(estimated_cost * expected_return_pct / 100.0, 2)
            selected.append({
                **item,
                "estimated_qty": qty,
                "estimated_cost": estimated_cost,
                "expected_return_pct": expected_return_pct,
                "estimated_profit_rupees": estimated_profit,
                "budget_cap": round(float(budget_cap), 2),
                "allowance_multiplier": round(allowance_multiplier, 6),
                "reason": f'{item["reason"]}; est return {expected_return_pct:.2f}% on ₹{estimated_cost:,.2f}',
            })
            if len(selected) >= max_items:
                break
        return selected

    def _score_symbol(self, symbol: str, df: pd.DataFrame | None) -> dict | None:
        if df is None or df.empty or "close" not in df.columns or "volume" not in df.columns or len(df) < 20:
            return None

        clean = df.dropna(subset=["close", "volume"]).tail(60).copy()
        if len(clean) < 20:
            return None

        ltp = float(clean["close"].iloc[-1])
        avg_volume_20d = float(clean["volume"].tail(20).mean())
        avg_turnover_20d = float((clean["close"].tail(20).astype(float) * clean["volume"].tail(20).astype(float)).mean())
        if ltp < float(self.config.min_stock_price) or ltp > float(self.config.max_stock_price):
            return None
        if avg_volume_20d < float(self.config.min_avg_daily_volume):
            return None
        if avg_turnover_20d < float(self.config.min_avg_daily_turnover):
            return None

        close = clean["close"].astype(float)
        momentum_5 = self._safe_pct_change(close.iloc[-1], close.iloc[-6]) if len(close) >= 6 else 0.0
        momentum_20 = self._safe_pct_change(close.iloc[-1], close.iloc[-20])
        sma_10 = float(close.tail(10).mean())
        sma_20 = float(close.tail(20).mean())
        trend_bonus = 1.5 if ltp > sma_10 > sma_20 else (0.75 if ltp > sma_20 else -1.0)
        liquidity_score = min(avg_volume_20d / max(float(self.config.min_avg_daily_volume), 1.0), 5.0)
        trend_quality = self._trend_quality(close)

        score = round(
            (momentum_20 * 0.40)
            + (momentum_5 * 0.20)
            + (trend_bonus * 10.0)
            + (liquidity_score * 5.0)
            + (trend_quality * 12.0),
            2,
        )

        reasons = []
        if momentum_20 > 0:
            reasons.append(f"20d momentum {momentum_20:.2f}%")
        if momentum_5 > 0:
            reasons.append(f"5d momentum {momentum_5:.2f}%")
        if ltp > sma_10 > sma_20:
            reasons.append("price above 10d/20d trend")
        reasons.append(f"20d avg volume {avg_volume_20d:,.0f}")
        reasons.append(f"20d avg turnover ₹{avg_turnover_20d:,.0f}")
        reasons.append(f"trend quality {trend_quality:.2f}")

        return {
            "symbol": symbol,
            "score": score,
            "rank": 0,
            "ltp": round(ltp, 2),
            "avg_volume_20d": round(avg_volume_20d, 2),
            "avg_turnover_20d": round(avg_turnover_20d, 2),
            "trend_quality": round(trend_quality, 4),
            "momentum_5": round(momentum_5, 4),
            "momentum_20": round(momentum_20, 4),
            "trend_bonus": round(trend_bonus, 4),
            "reason": "; ".join(reasons),
        }

    @staticmethod
    def _expected_return_pct(item: dict) -> float:
        raw = (
            max(float(item.get("momentum_20") or 0.0), -10.0) * 0.18
            + max(float(item.get("momentum_5") or 0.0), -10.0) * 0.10
            + max(float(item.get("trend_bonus") or 0.0), -2.0) * 1.35
            + max(float(item.get("trend_quality") or 0.0), 0.0) * 2.5
        )
        return round(max(1.0, min(raw, 12.0)), 2)

    @staticmethod
    def _safe_pct_change(current: float, previous: float) -> float:
        if previous == 0:
            return 0.0
        return ((current - previous) / previous) * 100.0

    @staticmethod
    def _trend_quality(close: pd.Series) -> float:
        if len(close) < 10:
            return 0.0
        abs_returns = close.pct_change().dropna().abs()
        if abs_returns.empty:
            return 0.0
        direction = abs(float(close.iloc[-1] - close.iloc[0]))
        path = float(close.diff().abs().dropna().sum())
        if path <= 0:
            return 0.0
        efficiency = max(0.0, min(direction / path, 1.0))
        smoothness = 1.0 / (1.0 + float(abs_returns.std(ddof=0) * 100))
        return round((efficiency * 0.7) + (smoothness * 0.3), 4)
