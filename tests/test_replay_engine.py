import unittest

from core.replay_engine import _max_drawdown


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


if __name__ == "__main__":
    unittest.main()
