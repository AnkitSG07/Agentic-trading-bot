from database.replay_utils import sanitize_replay_trades_for_insert


def test_sanitize_replay_trades_drops_non_table_columns():
    trades = [
        {
            "run_id": "run-1",
            "timestamp": "2024-01-01T00:00:00Z",
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "action": "BUY",
            "quantity": 1,
            "price": 2500.0,
            "fees": 1.2,
            "slippage_pct": 0.1,
            "pnl": 0.0,
            "realized": False,
            "rationale": "entry",
            "unexpected": "ignore-me",
        }
    ]

    sanitized = sanitize_replay_trades_for_insert(trades)

    assert len(sanitized) == 1
    assert "realized" not in sanitized[0]
    assert "unexpected" not in sanitized[0]
    assert sanitized[0]["symbol"] == "RELIANCE"
