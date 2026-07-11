import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from delta_live.config import Settings, TESTNET_PUBLIC_WS, TESTNET_REST
from delta_live.engine import ExecutionEngine, State, StrategyConfig
from delta_live.liquidity import Quote, entry_gate, tick_price


def quote(symbol, bid, ask, size=200, mark=None):
    return Quote(symbol, Decimal(str(bid)), Decimal(str(ask)), Decimal(size), Decimal(size),
                 Decimal(str(mark if mark is not None else (bid + ask) / 2)), 1)


class LiveEngineTests(unittest.TestCase):
    def settings(self, directory):
        return Settings("testnet", True, None, None, None, None, TESTNET_REST,
                        TESTNET_PUBLIC_WS, Path(directory))

    def test_liquidity_gate_accepts_good_quotes(self):
        result = entry_gate(quote("C", 35, 36), quote("P", 15, 16), 200,
                            Decimal("0.15"), Decimal("0.95"))
        self.assertTrue(result.allowed)

    def test_liquidity_gate_rejects_wide_leg(self):
        result = entry_gate(quote("C", 35, 50), quote("P", 15, 16), 200,
                            Decimal("0.15"), Decimal("0.80"))
        self.assertEqual(result.reason, "spread_too_wide")

    def test_combined_stop_requires_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = ExecutionEngine(self.settings(directory), StrategyConfig(persistence_ticks=2),
                                     clock=lambda: datetime(2026, 7, 11, 17, 1))
            engine.record_entry(Decimal("35"), Decimal("15"))
            self.assertFalse(engine.observe_stop(quote("C", 34, 40), quote("P", 29, 36)))
            self.assertTrue(engine.observe_stop(quote("C", 34, 40), quote("P", 29, 36)))
            self.assertEqual(engine.state, State.EXITING)

    def test_mark_spike_does_not_trigger_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = ExecutionEngine(self.settings(directory), StrategyConfig(persistence_ticks=1),
                                     clock=lambda: datetime(2026, 7, 11, 17, 1))
            engine.record_entry(Decimal("35"), Decimal("15"))
            self.assertFalse(engine.observe_stop(quote("C", 20, 21, mark=100), quote("P", 10, 11, mark=80)))

    def test_tick_rounding_is_conservative(self):
        self.assertEqual(tick_price(Decimal("15.07"), Decimal("0.1"), "sell"), "15.0")
        self.assertEqual(tick_price(Decimal("15.07"), Decimal("0.1"), "buy"), "15.1")


if __name__ == "__main__":
    unittest.main()
