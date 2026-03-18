import pandas as pd

from data.stock_selector import SelectorConfig, StockSelector


def _frame(closes, volumes):
    return pd.DataFrame({"close": closes, "volume": volumes, "open": closes, "high": closes, "low": closes})


def test_selector_filters_and_ranks_candidates():
    selector = StockSelector(SelectorConfig(min_stock_price=50, max_stock_price=500, min_avg_daily_volume=100000, min_avg_daily_turnover=10000000, max_auto_pick_symbols=3))
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
    assert "avg turnover" in ranked[0]["reason"]
    assert "trend quality" in ranked[0]["reason"]


def test_selector_rejects_low_turnover_names():
    selector = StockSelector(SelectorConfig(min_stock_price=10, max_stock_price=500, min_avg_daily_volume=1000, min_avg_daily_turnover=5000000))
    frames = {
        "LOWTURN": _frame([20] * 25, [1000] * 25),
        "OK": _frame([100 + i for i in range(25)], [100000] * 25),
    }

    ranked = selector.rank_candidates(frames)

    assert [item["symbol"] for item in ranked] == ["OK"]


def test_selector_excludes_symbols_above_budget_and_keeps_rank_order():
    selector = StockSelector(SelectorConfig(min_stock_price=10, max_stock_price=1000, min_avg_daily_volume=1000, min_avg_daily_turnover=100000, max_auto_pick_symbols=3))
    frames = {
        "FAST": _frame([120 + i for i in range(25)], [150000] * 25),
        "CHEAP": _frame([80 + i for i in range(25)], [140000] * 25),
        "EXPENSIVE": _frame([1200 + i for i in range(25)], [200000] * 25),
    }

    picked = selector.select_affordable_candidates(frames, budget_cap=220, max_symbols=2)

    assert [item["symbol"] for item in picked] == ["CHEAP", "FAST"]
    assert all(item["estimated_cost"] <= 220 for item in picked)
    assert "EXPENSIVE" not in [item["symbol"] for item in picked]


def test_selector_returns_estimated_quantity_cost_and_profit_fields():
    selector = StockSelector(SelectorConfig(min_stock_price=10, max_stock_price=1000, min_avg_daily_volume=1000, min_avg_daily_turnover=100000, max_auto_pick_symbols=2))
    frames = {
        "AAA": _frame([90 + i for i in range(25)], [100000] * 25),
        "BBB": _frame([110 + i for i in range(25)], [120000] * 25),
    }

    picked = selector.select_affordable_candidates(frames, budget_cap=1000, max_symbols=2)

    assert picked
    for item in picked:
        assert item["estimated_qty"] > 0
        assert item["estimated_cost"] > 0
        assert item["estimated_profit_rupees"] >= 0
        assert item["expected_return_pct"] >= 1.0
        assert "est return" in item["reason"]
