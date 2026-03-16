_REPLAY_TRADE_INSERT_COLUMNS = {
    "run_id",
    "timestamp",
    "symbol",
    "exchange",
    "action",
    "quantity",
    "price",
    "fees",
    "slippage_pct",
    "pnl",
    "rationale",
}


def sanitize_replay_trades_for_insert(trades: list[dict]) -> list[dict]:
    """Drop non-table keys before bulk insert into replay_trades."""
    return [{k: v for k, v in trade.items() if k in _REPLAY_TRADE_INSERT_COLUMNS} for trade in trades]
