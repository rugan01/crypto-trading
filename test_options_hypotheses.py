import unittest
from datetime import date

from options_hypotheses import metrics, Result


class HypothesisTests(unittest.TestCase):
    def test_metrics(self):
        rows = [Result("x", "BTC", "2026-01-01", "", "2026-01-02", "", "", 1, 2, 1, .1, .9, "time_exit", 0, 1),
                Result("x", "BTC", "2026-01-02", "", "2026-01-03", "", "", 1, 3, -1, .1, -1.1, "stop", -1, 0)]
        result = metrics(rows)
        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["win_rate"], .5)
        self.assertLess(result["net_pnl"], 0)


if __name__ == "__main__":
    unittest.main()
