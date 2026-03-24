import pandas as pd

from data.indicators import IndicatorsEngine


def _price_frame(closes: list[float], volume: int = 1500) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": closes,
            "high": [price + 1 for price in closes],
            "low": [price - 1 for price in closes],
            "close": closes,
            "volume": [volume] * (len(closes) - 1) + [volume * 2],
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="D"),
    )


def test_bullish_indicator_scoring_not_stuck_on_neutral():
    engine = IndicatorsEngine()
    closes = [100 + i for i in range(30)]

    bundle = engine.compute(_price_frame(closes), symbol="BULL", timeframe="day")

    assert bundle.overall_signal in {"buy", "strong_buy"}
    assert bundle.macd_signal_str in {"bullish", "crossover_up"}


def test_bearish_indicator_scoring_not_stuck_on_neutral():
    engine = IndicatorsEngine()
    closes = [130 - i for i in range(30)]

    bundle = engine.compute(_price_frame(closes), symbol="BEAR", timeframe="day")

    assert bundle.overall_signal in {"sell", "strong_sell"}
    assert bundle.macd_signal_str in {"bearish", "crossover_down"}


def test_mixed_indicator_scoring_can_remain_neutral():
    engine = IndicatorsEngine()
    closes = [100] * 10 + [101, 99, 100, 100, 101, 99, 100, 100, 100, 100, 101, 99, 100, 100, 100, 100, 101, 99, 100, 100]

    bundle = engine.compute(_price_frame(closes, volume=1000), symbol="MIXED", timeframe="day")

    assert bundle.overall_signal == "neutral"
