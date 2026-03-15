import unittest
from decimal import Decimal

from core.replay_engine import _max_drawdown, _merge_position, _summarize_trades


class ReplayMathTests(unittest.TestCase):
    def test_max_drawdown(self):
        eq = [
            {"equity": 100},
            {"equity": 120},
            {"equity": 90},
            {"equity": 95},
        ]
        dd = _max_drawdown(eq)
        self.assertAlmostEqual(dd, 25.0)

    def test_merge_position_uses_weighted_average_entry(self):
        qty, entry = _merge_position(
            old_qty=Decimal("10"),
            old_entry=Decimal("100"),
            add_qty=Decimal("5"),
            add_entry=Decimal("130"),
        )
        self.assertEqual(qty, Decimal("15"))
        self.assertAlmostEqual(float(entry), 110.0)

    def test_trade_summary_uses_only_realized_closes(self):
        trades = [
            {"action": "BUY", "pnl": 0.0},
            {"action": "SELL", "pnl": 50.0},
            {"action": "BUY", "pnl": 0.0},
            {"action": "SELL", "pnl": -25.0},
        ]
        summary = _summarize_trades(trades)
        self.assertEqual(summary["order_count"], 4)
        self.assertEqual(summary["completed_trades"], 2)
        self.assertAlmostEqual(summary["win_rate"], 50.0)
        self.assertAlmostEqual(summary["profit_factor"], 2.0)


if __name__ == "__main__":
    unittest.main()
