import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from delta_live.config import Settings, TESTNET_PUBLIC_WS, TESTNET_REST
from delta_live.engine import ExecutionEngine, State, StrategyConfig
from delta_live.liquidity import Quote, entry_gate, tick_price
from delta_live.manage_open import open_short_straddle
from delta_live.session import enter_paired_slices


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

    def test_open_short_straddle_uses_broker_entry_prices(self):
        class Client:
            def positions(self, asset):
                return [
                    {"product_symbol": "P-BTC-63000-130726", "size": -100, "entry_price": "39"},
                    {"product_symbol": "C-BTC-63000-130726", "size": -100, "entry_price": "56"},
                ]

        call, put, size, credit = open_short_straddle(Client(), "BTC")
        self.assertEqual(call["product_symbol"], "C-BTC-63000-130726")
        self.assertEqual(put["product_symbol"], "P-BTC-63000-130726")
        self.assertEqual(size, 100)
        self.assertEqual(credit, Decimal("95"))

    def test_open_short_straddle_rejects_mismatched_sizes(self):
        class Client:
            def positions(self, asset):
                return [
                    {"product_symbol": "P-BTC-63000-130726", "size": -100, "entry_price": "39"},
                    {"product_symbol": "C-BTC-63000-130726", "size": -50, "entry_price": "56"},
                ]

        with self.assertRaisesRegex(RuntimeError, "sizes differ"):
            open_short_straddle(Client(), "BTC")

    def test_paired_entry_retries_missing_leg_before_flattening(self):
        class Client:
            def __init__(self):
                self.calls = 0

            def ticker(self, symbol):
                bid, ask = ((60, 61) if symbol == "CALL" else (38, 39))
                return {"symbol": symbol, "quotes": {"best_bid": bid, "best_ask": ask,
                        "bid_size": 100, "ask_size": 100}, "mark_price": (bid + ask) / 2,
                        "timestamp": 1}

            def place_order(self, payload):
                if payload["product_id"] == 1:
                    self.calls += 1
                    if self.calls == 1:
                        return {"id": 1, "size": 25, "unfilled_size": 25}
                    return {"id": 3, "size": 25, "unfilled_size": 0,
                            "average_fill_price": "58"}
                return {"id": 2, "size": 25, "unfilled_size": 0,
                        "average_fill_price": "44"}

        with tempfile.TemporaryDirectory() as directory:
            engine = ExecutionEngine(self.settings(directory), StrategyConfig(size=25),
                                     client=Client(), clock=lambda: datetime(2026, 7, 13, 17, 0))
            initial_call = quote("CALL", 60, 61, size=100)
            initial_put = quote("PUT", 38, 39, size=100)
            entered, call_price, put_price = enter_paired_slices(
                engine, {"id": 1, "tick_size": "0.1"}, {"id": 2, "tick_size": "0.1"},
                "CALL", "PUT", 25, initial_call, initial_put, slice_size=25,
                entry_window=2, unmatched_grace=1)
        self.assertEqual(entered, 25)
        self.assertEqual(call_price, Decimal("58"))
        self.assertEqual(put_price, Decimal("44"))


if __name__ == "__main__":
    unittest.main()
