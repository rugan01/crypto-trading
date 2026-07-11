import unittest
from datetime import date, datetime, timezone

from delta_0dte import Contract, Trade, close_map, common_atm, performance_metrics, price_at, resolve_atm_pair


class CoreTests(unittest.TestCase):
    def contract(self, side, strike):
        return Contract(f"{side}-BTC-{strike}-010126", "BTC", side, strike, date(2026,1,1),
                        datetime(2026,1,1,12,tzinfo=timezone.utc), 0.001, 0.0005)

    def test_common_atm_requires_paired_strike(self):
        chain = {100: {"C": self.contract("C",100), "P": self.contract("P",100)},
                 110: {"C": self.contract("C",110)}}
        call, put = common_atm(chain, 108)
        self.assertEqual(call.strike, 100)
        self.assertEqual(put.strike, 100)

    def test_price_tolerance_only_looks_forward(self):
        self.assertEqual(price_at({1060: 4.0}, 1000, 120), (1060, 4.0))
        self.assertIsNone(price_at({940: 3.0}, 1000, 120))

    def test_far_partial_catalog_is_not_treated_as_atm(self):
        class Client:
            def product(self, symbol):
                return None
        chain = {73800: {"C": self.contract("C",73800), "P": self.contract("P",73800)}}
        self.assertIsNone(resolve_atm_pair(Client(), "BTC", date(2026,1,1), 71500, chain))

    def test_close_map(self):
        self.assertEqual(close_map([{"time": 1, "close": "2.5"}]), {1: 2.5})

    def test_performance_metrics(self):
        def trade(day, pnl):
            return Trade("BTC", day, day+"T13:00:00+05:30", "30m", 100, "C", "P", 200, .001,
                         "combined", 100, 1, 1, 1, 1, day, day, "time_exit", pnl, 0, pnl, 0, 0)
        metrics = performance_metrics([trade("2026-01-01", 10), trade("2026-01-02", -5), trade("2026-01-03", -6), trade("2026-01-04", 12)])
        self.assertEqual(metrics["avg_profitable_trade"], 11)
        self.assertEqual(metrics["avg_losing_trade"], -5.5)
        self.assertEqual(metrics["max_drawdown"], 11)
        self.assertEqual(metrics["max_drawdown_duration_days"], 3)
        self.assertTrue(metrics["recovered"])


if __name__ == "__main__": unittest.main()
