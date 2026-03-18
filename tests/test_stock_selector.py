import pandas as pd

from data.stock_selector import SelectorConfig, StockSelector


def _frame(closes, volumes):
    return pd.DataFrame({"close": closes, "volume": volumes, "open": closes, "high": closes, "low": closes})


def test_selector_filters_and_ranks_candidates():
    selector = StockSelector(SelectorConfig(min_stock_price=50, max_stock_price=500, min_avg_daily_volume=100000, max_auto_pick_symbols=3))
    frames = {
        "AAA": _frame([100 + i for i in range(25)], [150000] * 25),
        "BBB": _frame([120] * 24 + [118], [200000] * 25),
        "LOWVOL": _frame([150 + i for i in range(25)], [1000] * 25),
        "TOOEXP": _frame([900 + i for i in range(25)], [300000] * 25),
    }

    ranked = selector.rank_candidates(frames)

    assert [item["symbol"] for item in ranked] == ["AAA", "BBB"]
    assert ranked[0]["rank"] == 1
    assert ranked[0]["score"] >= ranked[1]["score"]
    assert "avg volume" in ranked[0]["reason"]
