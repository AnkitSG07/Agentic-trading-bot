from datetime import datetime

from agents.brain import MarketContext, TradingAgent


def _ctx(*, capital: float, ltp: float = 2500.0) -> MarketContext:
    return MarketContext(
        timestamp=datetime(2024, 1, 2, 10, 0, 0),
        nifty50_ltp=22000.0,
        banknifty_ltp=47000.0,
        india_vix=14.0,
        market_trend="sideways",
        session="mid_session",
        day_of_week="Tuesday",
        available_capital=capital,
        used_margin=0.0,
        open_positions=[],
        watchlist_data=[
            {
                "symbol": "RELIANCE",
                "ltp": ltp,
                "change_pct": 0.0,
                "indicators": {"overall_signal": "neutral"},
                "levels": {},
            }
        ],
        options_chain_summary=None,
        recent_news_sentiment=None,
        pcr=1.0,
    )


def test_zero_quantity_signal_gets_fallback_size():
    agent = TradingAgent({"max_capital_per_trade_pct": 5, "min_trade_quantity": 1})
    decision = {
        "signals": [
            {
                "action": "BUY",
                "symbol": "RELIANCE",
                "quantity": 0,
                "entry_price": 2500,
                "confidence": 0.9,
                "risk_reward": 1.8,
            }
        ]
    }

    signals = agent._parse_signals(decision, _ctx(capital=100000, ltp=2500))

    assert len(signals) == 1
    assert signals[0].quantity == 2


def test_zero_quantity_signal_skipped_when_capital_cannot_afford_min_qty():
    agent = TradingAgent({"max_capital_per_trade_pct": 5, "min_trade_quantity": 1})
    decision = {
        "signals": [
            {
                "action": "BUY",
                "symbol": "RELIANCE",
                "quantity": 0,
                "entry_price": 5000,
                "confidence": 0.9,
                "risk_reward": 1.8,
            }
        ]
    }

    signals = agent._parse_signals(decision, _ctx(capital=1000, ltp=5000))

    assert signals == []
