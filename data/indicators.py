"""
Technical Indicators Engine
Computes all technical indicators needed for signal generation.
Uses pandas-ta for efficiency.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger("data.indicators")


@dataclass
class IndicatorBundle:
    """Complete set of technical indicators for a symbol."""
    symbol: str
    timeframe: str
    timestamp: pd.Timestamp

    # Trend
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    trend: str = "sideways"     # bullish | bearish | sideways

    # Momentum
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None

    # Volatility
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width: Optional[float] = None
    atr: Optional[float] = None
    atr_pct: Optional[float] = None

    # Volume
    vwap: Optional[float] = None
    volume_sma20: Optional[float] = None
    volume_ratio: Optional[float] = None  # Current vol / 20-day avg vol

    # Support/Resistance
    pivot: Optional[float] = None
    r1: Optional[float] = None
    r2: Optional[float] = None
    s1: Optional[float] = None
    s2: Optional[float] = None

    # Supertrend
    supertrend: Optional[float] = None
    supertrend_direction: Optional[int] = None  # 1 = bullish, -1 = bearish

    # Signals
    rsi_signal: str = "neutral"       # oversold | overbought | neutral
    macd_signal_str: str = "neutral"  # bullish | bearish | neutral | crossover_up | crossover_down
    bb_signal: str = "neutral"        # squeeze | expansion | upper_touch | lower_touch
    overall_signal: str = "neutral"   # strong_buy | buy | neutral | sell | strong_sell

    # Raw data
    ltp: Optional[float] = None
    change_pct: Optional[float] = None


class IndicatorsEngine:
    """Compute technical indicators from OHLCV data."""

    def compute(self, df: pd.DataFrame, symbol: str = "", timeframe: str = "day") -> IndicatorBundle:
        """
        Compute all indicators from a OHLCV DataFrame.

        Args:
            df: DataFrame with columns [open, high, low, close, volume]
                Index should be datetime
            symbol: Instrument symbol
            timeframe: Data timeframe (minute, 5minute, 15minute, day, etc.)

        Returns:
            IndicatorBundle with all computed indicators
        """
        if df.empty or len(df) < 20:
            logger.warning(f"Insufficient data for {symbol}: {len(df)} rows")
            return IndicatorBundle(symbol=symbol, timeframe=timeframe, timestamp=pd.Timestamp.now())

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        bundle = IndicatorBundle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=df.index[-1] if isinstance(df.index[-1], pd.Timestamp) else pd.Timestamp.now(),
            ltp=float(df["close"].iloc[-1]),
            change_pct=float((df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100)
            if len(df) >= 2 else 0.0,
        )

        try:
            self._compute_trend(df, bundle)
            self._compute_momentum(df, bundle)
            self._compute_volatility(df, bundle)
            self._compute_volume(df, bundle)
            self._compute_supertrend(df, bundle)
            self._compute_pivots(df, bundle)
            self._compute_signals(bundle)
        except Exception as e:
            logger.error(f"Indicator computation error for {symbol}: {e}")

        return bundle

    # ── Trend Indicators ─────────────────────────────────────────────────────

    def _compute_trend(self, df: pd.DataFrame, b: IndicatorBundle) -> None:
        close = df["close"]

        if len(df) >= 9:
            b.ema_9 = float(ta.ema(close, length=9).iloc[-1])
        if len(df) >= 21:
            b.ema_21 = float(ta.ema(close, length=21).iloc[-1])
        if len(df) >= 50:
            b.ema_50 = float(ta.ema(close, length=50).iloc[-1])
        if len(df) >= 200:
            b.ema_200 = float(ta.ema(close, length=200).iloc[-1])

        # Determine trend
        ltp = float(close.iloc[-1])
        if b.ema_9 and b.ema_21 and b.ema_50:
            if ltp > b.ema_9 > b.ema_21 > b.ema_50:
                b.trend = "bullish"
            elif ltp < b.ema_9 < b.ema_21 < b.ema_50:
                b.trend = "bearish"
            else:
                b.trend = "sideways"

    # ── Momentum ─────────────────────────────────────────────────────────────

    def _compute_momentum(self, df: pd.DataFrame, b: IndicatorBundle) -> None:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # RSI
        rsi_series = ta.rsi(close, length=14)
        if rsi_series is not None and not rsi_series.empty:
            b.rsi = float(rsi_series.iloc[-1])

        # MACD
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            b.macd = float(macd_df["MACD_12_26_9"].iloc[-1])
            b.macd_signal = float(macd_df["MACDs_12_26_9"].iloc[-1])
            b.macd_histogram = float(macd_df["MACDh_12_26_9"].iloc[-1])

        # Stochastic
        stoch_df = ta.stoch(high, low, close, k=14, d=3)
        if stoch_df is not None and not stoch_df.empty:
            b.stoch_k = float(stoch_df.iloc[:, 0].iloc[-1])
            b.stoch_d = float(stoch_df.iloc[:, 1].iloc[-1])

    # ── Volatility ───────────────────────────────────────────────────────────

    def _compute_volatility(self, df: pd.DataFrame, b: IndicatorBundle) -> None:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # Bollinger Bands
        bb_df = ta.bbands(close, length=20, std=2.0)
        if bb_df is not None and not bb_df.empty:
            b.bb_upper = float(bb_df["BBU_20_2.0"].iloc[-1])
            b.bb_middle = float(bb_df["BBM_20_2.0"].iloc[-1])
            b.bb_lower = float(bb_df["BBL_20_2.0"].iloc[-1])
            b.bb_width = float(
                (bb_df["BBU_20_2.0"].iloc[-1] - bb_df["BBL_20_2.0"].iloc[-1])
                / bb_df["BBM_20_2.0"].iloc[-1] * 100
            )

        # ATR
        atr_series = ta.atr(high, low, close, length=14)
        if atr_series is not None and not atr_series.empty:
            b.atr = float(atr_series.iloc[-1])
            b.atr_pct = float(b.atr / close.iloc[-1] * 100)

    # ── Volume ───────────────────────────────────────────────────────────────

    def _compute_volume(self, df: pd.DataFrame, b: IndicatorBundle) -> None:
        if "volume" not in df.columns:
            return

        close = df["close"]
        volume = df["volume"]

        # VWAP (intraday use only, but compute for reference)
        try:
            vwap_series = ta.vwap(df["high"], df["low"], close, volume)
            if vwap_series is not None and not vwap_series.empty:
                b.vwap = float(vwap_series.iloc[-1])
        except Exception:
            pass

        # Volume SMA
        if len(df) >= 20:
            b.volume_sma20 = float(volume.rolling(20).mean().iloc[-1])
            if b.volume_sma20 and b.volume_sma20 > 0:
                b.volume_ratio = float(volume.iloc[-1] / b.volume_sma20)

    # ── Supertrend ────────────────────────────────────────────────────────────

    def _compute_supertrend(self, df: pd.DataFrame, b: IndicatorBundle) -> None:
        if len(df) < 14:
            return
        try:
            st_df = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3.0)
            if st_df is not None and not st_df.empty:
                st_col = [c for c in st_df.columns if "SUPERT_" in c and "d" not in c.lower()]
                dir_col = [c for c in st_df.columns if "SUPERTd_" in c]
                if st_col:
                    b.supertrend = float(st_df[st_col[0]].iloc[-1])
                if dir_col:
                    b.supertrend_direction = int(st_df[dir_col[0]].iloc[-1])
        except Exception as e:
            logger.debug(f"Supertrend error: {e}")

    # ── Pivot Points ─────────────────────────────────────────────────────────

    def _compute_pivots(self, df: pd.DataFrame, b: IndicatorBundle) -> None:
        """Classic pivot points from previous day's data."""
        if len(df) < 2:
            return
        prev = df.iloc[-2]
        h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
        pivot = (h + l + c) / 3
        b.pivot = pivot
        b.r1 = 2 * pivot - l
        b.r2 = pivot + (h - l)
        b.s1 = 2 * pivot - h
        b.s2 = pivot - (h - l)

    # ── Signal Scoring ────────────────────────────────────────────────────────

    def _compute_signals(self, b: IndicatorBundle) -> None:
        """Aggregate indicators into directional signals."""
        score = 0
        max_score = 0

        # RSI signals
        if b.rsi is not None:
            max_score += 2
            if b.rsi < 30:
                b.rsi_signal = "oversold"
                score += 2
            elif b.rsi > 70:
                b.rsi_signal = "overbought"
                score -= 2
            elif b.rsi > 55:
                score += 1
            elif b.rsi < 45:
                score -= 1

        # MACD signals
        if b.macd is not None and b.macd_signal is not None:
            max_score += 2
            if b.macd > b.macd_signal and b.macd_histogram and b.macd_histogram > 0:
                b.macd_signal_str = "bullish"
                score += 2
            elif b.macd < b.macd_signal:
                b.macd_signal_str = "bearish"
                score -= 2

        # Trend signals
        if b.trend == "bullish":
            max_score += 2
            score += 2
        elif b.trend == "bearish":
            max_score += 2
            score -= 2

        # Supertrend
        if b.supertrend_direction is not None:
            max_score += 1
            score += b.supertrend_direction  # +1 or -1

        # Bollinger Band signals
        if b.ltp and b.bb_upper and b.bb_lower and b.bb_middle:
            max_score += 1
            if b.ltp <= b.bb_lower:
                b.bb_signal = "lower_touch"
                score += 1
            elif b.ltp >= b.bb_upper:
                b.bb_signal = "upper_touch"
                score -= 1
            elif b.bb_width and b.bb_width < 2.0:
                b.bb_signal = "squeeze"

        # Overall score
        if max_score == 0:
            b.overall_signal = "neutral"
            return

        ratio = score / max_score
        if ratio >= 0.7:
            b.overall_signal = "strong_buy"
        elif ratio >= 0.4:
            b.overall_signal = "buy"
        elif ratio <= -0.7:
            b.overall_signal = "strong_sell"
        elif ratio <= -0.4:
            b.overall_signal = "sell"
        else:
            b.overall_signal = "neutral"

    def to_dict(self, bundle: IndicatorBundle) -> dict:
        """Convert bundle to dict for AI agent consumption."""
        return {
            "symbol": bundle.symbol,
            "ltp": bundle.ltp,
            "change_pct": round(bundle.change_pct or 0, 2),
            "trend": bundle.trend,
            "indicators": {
                "rsi": round(bundle.rsi, 1) if bundle.rsi else None,
                "macd_signal": bundle.macd_signal_str,
                "bb_signal": bundle.bb_signal,
                "rsi_signal": bundle.rsi_signal,
                "trend": bundle.trend,
                "supertrend": "bullish" if bundle.supertrend_direction == 1 else "bearish" if bundle.supertrend_direction == -1 else None,
                "volume_ratio": round(bundle.volume_ratio, 2) if bundle.volume_ratio else None,
                "atr_pct": round(bundle.atr_pct, 2) if bundle.atr_pct else None,
                "bb_width": round(bundle.bb_width, 2) if bundle.bb_width else None,
                "vwap": round(bundle.vwap, 2) if bundle.vwap else None,
                "overall_signal": bundle.overall_signal,
            },
            "levels": {
                "pivot": round(bundle.pivot, 2) if bundle.pivot else None,
                "r1": round(bundle.r1, 2) if bundle.r1 else None,
                "s1": round(bundle.s1, 2) if bundle.s1 else None,
                "bb_upper": round(bundle.bb_upper, 2) if bundle.bb_upper else None,
                "bb_lower": round(bundle.bb_lower, 2) if bundle.bb_lower else None,
            },
        }
