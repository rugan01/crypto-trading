import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from delta_live.client import DeltaRESTClient
from delta_live.config import Settings, TESTNET_PUBLIC_WS, TESTNET_REST
from delta_live.engine import ExecutionEngine, State, StrategyConfig
from delta_live.liquidity import Quote, entry_gate, tick_price
from delta_live.manage_open import open_short_straddle
from delta_live.session import close_positions, depth_aware_exit_limit, enter_paired_slices
from delta_live.size_check import feasible_size


def quote(symbol, bid, ask, size=200, mark=None):
    return Quote(symbol, Decimal(str(bid)), Decimal(str(ask)), Decimal(size), Decimal(size),
                 Decimal(str(mark if mark is not None else (bid + ask) / 2)), 1)


class LiveEngineTests(unittest.TestCase):
    def settings(self, directory, permissive=False):
        return Settings("testnet", True, None, None, None, None, TESTNET_REST,
                        TESTNET_PUBLIC_WS, Path(directory), permissive)

    def test_liquidity_gate_accepts_good_quotes(self):
        result = entry_gate(quote("C", 35, 36), quote("P", 15, 16), 200,
                            Decimal("0.15"), Decimal("0.95"))
        self.assertTrue(result.allowed)

    def test_liquidity_gate_rejects_wide_leg(self):
        result = entry_gate(quote("C", 35, 50), quote("P", 15, 16), 200,
                            Decimal("0.15"), Decimal("0.80"))
        self.assertEqual(result.reason, "spread_too_wide")

    def test_permissive_preflight_logs_wide_spread_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = ExecutionEngine(self.settings(directory, permissive=True),
                                     StrategyConfig(size=125),
                                     clock=lambda: datetime(2026, 7, 18, 17, 0))
            call = {"symbol": "CALL", "quotes": {"best_bid": 10, "best_ask": 20,
                    "bid_size": 125, "ask_size": 125}, "mark_price": 15, "timestamp": 1}
            put = {"symbol": "PUT", "quotes": {"best_bid": 80, "best_ask": 82,
                   "bid_size": 125, "ask_size": 125}, "mark_price": 81, "timestamp": 1}

            engine.preflight(call, put)

            self.assertEqual(engine.state, State.READY)
            events = engine.log_path.read_text()
            self.assertIn('"event": "entry_gate_warning"', events)
            self.assertIn('"reason": "spread_too_wide"', events)

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

    def test_penny_exit_limit_crosses_meaningfully_beyond_depth_price(self):
        limit, details = depth_aware_exit_limit(
            {"tick_size": "0.1"}, quote("CALL", 0.4, 0.5, size=125),
            {"sell": [{"price": "0.5", "size": 125}]}, 125, 0)

        self.assertEqual(limit, Decimal("1.0"))
        self.assertTrue(details["depth_supported"])
        self.assertTrue(details["penny_mode"])

    def test_exit_limit_uses_price_at_cumulative_depth(self):
        limit, details = depth_aware_exit_limit(
            {"tick_size": "0.1"}, quote("CALL", 0.4, 0.5, size=50),
            {"sell": [
                {"price": "0.5", "size": 50},
                {"price": "0.6", "size": 100},
            ]}, 125, 0)

        self.assertEqual(details["depth_price"], Decimal("0.6"))
        self.assertEqual(details["depth_available"], 150)
        self.assertEqual(limit, Decimal("1.2"))

    def test_exit_submits_both_legs_in_first_round_and_reconciles_flat(self):
        class Client:
            def __init__(self):
                self.payloads = []
                self.open_sizes = {"CALL": -125, "PUT": -125}

            def positions(self, asset):
                return [
                    {"product_symbol": symbol, "size": size}
                    for symbol, size in self.open_sizes.items() if size
                ]

            def ticker(self, symbol):
                bid, ask = ((0.4, 0.5) if symbol == "CALL" else (18.5, 18.7))
                return {"symbol": symbol, "quotes": {
                    "best_bid": bid, "best_ask": ask,
                    "bid_size": 125, "ask_size": 125,
                }, "mark_price": (bid + ask) / 2, "timestamp": 1}

            def l2_orderbook(self, symbol, depth=50):
                price = "0.5" if symbol == "CALL" else "18.7"
                return {"sell": [{"price": price, "size": 125}]}

            def place_order(self, payload):
                self.payloads.append(payload)
                symbol = "CALL" if payload["product_id"] == 1 else "PUT"
                self.open_sizes[symbol] = 0
                return {
                    "id": len(self.payloads),
                    "size": payload["size"],
                    "unfilled_size": 0,
                    "average_fill_price": "0.5" if symbol == "CALL" else "18.7",
                }

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            engine = ExecutionEngine(
                self.settings(directory), StrategyConfig(asset="BTC", size=125),
                client=client, clock=lambda: datetime(2026, 7, 23, 17, 24, 30))

            closed = close_positions(
                engine,
                [
                    ({"id": 1, "tick_size": "0.1"}, "CALL", 125),
                    ({"id": 2, "tick_size": "0.1"}, "PUT", 125),
                ],
                reconcile_positions=True,
            )

        self.assertTrue(closed)
        self.assertEqual(len(client.payloads), 2)
        self.assertEqual({payload["client_order_id"][-4:] for payload in client.payloads},
                         {"x1a1", "x2a1"})
        call_payload = next(p for p in client.payloads if p["product_id"] == 1)
        self.assertEqual(call_payload["limit_price"], "1.0")
        self.assertTrue(all(payload["reduce_only"] for payload in client.payloads))

    def test_unfilled_penny_leg_does_not_delay_or_repeat_filled_other_leg(self):
        class Client:
            def __init__(self):
                self.payloads = []
                self.open_sizes = {"CALL": -125, "PUT": -125}
                self.call_attempts = 0

            def positions(self, asset):
                return [
                    {"product_symbol": symbol, "size": size}
                    for symbol, size in self.open_sizes.items() if size
                ]

            def ticker(self, symbol):
                bid, ask = ((0.4, 0.5) if symbol == "CALL" else (18.5, 18.7))
                return {"symbol": symbol, "quotes": {
                    "best_bid": bid, "best_ask": ask,
                    "bid_size": 125, "ask_size": 125,
                }, "mark_price": (bid + ask) / 2, "timestamp": 1}

            def l2_orderbook(self, symbol, depth=50):
                price = "0.5" if symbol == "CALL" else "18.7"
                return {"sell": [{"price": price, "size": 125}]}

            def place_order(self, payload):
                self.payloads.append(payload)
                if payload["product_id"] == 1:
                    self.call_attempts += 1
                    if self.call_attempts == 1:
                        return {"id": 1, "size": 125, "unfilled_size": 125}
                    self.open_sizes["CALL"] = 0
                    return {"id": 3, "size": 125, "unfilled_size": 0,
                            "average_fill_price": "0.5"}
                self.open_sizes["PUT"] = 0
                return {"id": 2, "size": 125, "unfilled_size": 0,
                        "average_fill_price": "18.7"}

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            engine = ExecutionEngine(
                self.settings(directory), StrategyConfig(asset="BTC", size=125),
                client=client, clock=lambda: datetime(2026, 7, 23, 17, 24, 30))
            with patch("delta_live.session.time.sleep"):
                closed = close_positions(
                    engine,
                    [
                        ({"id": 1, "tick_size": "0.1"}, "CALL", 125),
                        ({"id": 2, "tick_size": "0.1"}, "PUT", 125),
                    ],
                    reconcile_positions=True,
                )

        self.assertTrue(closed)
        call_payloads = [p for p in client.payloads if p["product_id"] == 1]
        put_payloads = [p for p in client.payloads if p["product_id"] == 2]
        self.assertEqual(len(call_payloads), 2)
        self.assertEqual(len(put_payloads), 1)
        self.assertTrue(call_payloads[0]["client_order_id"].endswith("x1a1"))
        self.assertTrue(call_payloads[1]["client_order_id"].endswith("x1a2"))
        self.assertTrue(put_payloads[0]["client_order_id"].endswith("x2a1"))

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
            entered, call_price, put_price, call_size, put_size = enter_paired_slices(
                engine, {"id": 1, "tick_size": "0.1"}, {"id": 2, "tick_size": "0.1"},
                "CALL", "PUT", 25, initial_call, initial_put, slice_size=25,
                entry_window=2, unmatched_grace=1)
        self.assertEqual(entered, 25)
        self.assertEqual(call_price, Decimal("58"))
        self.assertEqual(put_price, Decimal("44"))
        self.assertEqual((call_size, put_size), (25, 25))

    def test_paired_entry_submits_full_125_contract_pair_in_one_shot(self):
        class Client:
            def __init__(self):
                self.payloads = []

            def ticker(self, symbol):
                bid, ask = ((60, 61) if symbol == "CALL" else (38, 39))
                return {"symbol": symbol, "quotes": {"best_bid": bid, "best_ask": ask,
                        "bid_size": 200, "ask_size": 200}, "mark_price": (bid + ask) / 2,
                        "timestamp": 1}

            def place_order(self, payload):
                self.payloads.append(payload)
                return {"id": len(self.payloads), "size": payload["size"], "unfilled_size": 0,
                        "average_fill_price": "58" if payload["product_id"] == 1 else "38"}

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            engine = ExecutionEngine(self.settings(directory, permissive=True),
                                     StrategyConfig(size=125, min_credit_ratio=Decimal("0")),
                                     client=client,
                                     clock=lambda: datetime(2026, 7, 18, 17, 0))
            entered, _, _, call_size, put_size = enter_paired_slices(
                engine, {"id": 1, "tick_size": "0.1"}, {"id": 2, "tick_size": "0.1"},
                "CALL", "PUT", 125, quote("CALL", 60, 61), quote("PUT", 38, 39),
                slice_size=125, entry_window=2, unmatched_grace=1)

        self.assertEqual(entered, 125)
        self.assertEqual((call_size, put_size), (125, 125))
        self.assertEqual(len(client.payloads), 2)
        self.assertEqual({payload["size"] for payload in client.payloads}, {125})
        self.assertEqual({payload["limit_price"] for payload in client.payloads}, {"54.0", "34.2"})

    def test_unfillable_leg_is_retained_not_flattened(self):
        """The 2026-08-01 failure: call fills, put never does.

        The filled call must be KEPT, not bought back. Round-tripping it cost
        $1.03 of commission plus $0.60 of slippage for no risk benefit.
        """
        class Client:
            def __init__(self):
                self.payloads = []

            def ticker(self, symbol):
                bid, ask = ((81, 85) if symbol == "CALL" else (Decimal("0.9"), 3))
                return {"symbol": symbol, "quotes": {"best_bid": bid, "best_ask": ask,
                        "bid_size": 500, "ask_size": 500}, "mark_price": bid,
                        "timestamp": 1}

            def place_order(self, payload):
                self.payloads.append(payload)
                if payload["product_id"] == 1:            # call fills in full
                    return {"id": len(self.payloads), "size": payload["size"],
                            "unfilled_size": 0, "average_fill_price": "81"}
                return {"id": len(self.payloads), "size": payload["size"],
                        "unfilled_size": payload["size"]}  # put never fills

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            engine = ExecutionEngine(self.settings(directory, permissive=True),
                                     StrategyConfig(size=150, min_credit_ratio=Decimal("0")),
                                     client=client,
                                     clock=lambda: datetime(2026, 8, 1, 17, 0))
            entered, call_price, put_price, call_size, put_size = enter_paired_slices(
                engine, {"id": 1, "tick_size": "0.1"}, {"id": 2, "tick_size": "0.1"},
                "CALL", "PUT", 150, quote("CALL", 81, 85), quote("PUT", Decimal("0.9"), 3),
                slice_size=150, entry_window=2, unmatched_grace=1)

        # The call is retained at full size; no pair was ever formed.
        self.assertEqual((call_size, put_size), (150, 0))
        self.assertEqual(entered, 0)
        self.assertEqual(call_price, Decimal("81"))
        # Critically: no BUY order was ever submitted against the filled call.
        buys = [p for p in client.payloads if p["side"] == "buy"]
        self.assertEqual(buys, [], "filled leg must never be flattened to restore symmetry")

    def test_single_leg_stop_ignores_the_unfilled_leg(self):
        """Stop level and buyback must both reflect only the leg actually held."""
        with tempfile.TemporaryDirectory() as directory:
            engine = ExecutionEngine(self.settings(directory), StrategyConfig(size=150),
                                     client=None, clock=lambda: datetime(2026, 8, 1, 17, 0))
            engine.live_call, engine.live_put = True, False
            engine.record_entry(Decimal("81"), Decimal(0))
            self.assertEqual(engine.entry_credit, Decimal("81"))
            self.assertEqual(engine.stop_level, Decimal("121.5"))   # 81 * 1.5
            # A rich ask on the leg we do NOT hold must not trigger the stop.
            self.assertFalse(engine.observe_stop(quote("CALL", 60, 61), quote("PUT", 900, 999)))
            self.assertFalse(engine.observe_stop(quote("CALL", 60, 61), quote("PUT", 900, 999)))
            # The held leg breaching 121.5 for two ticks must trigger it.
            self.assertFalse(engine.observe_stop(quote("CALL", 120, 125), quote("PUT", 0, 0)))
            self.assertTrue(engine.observe_stop(quote("CALL", 120, 125), quote("PUT", 0, 0)))


class ActiveOrderScopeTests(unittest.TestCase):
    """The entry guard must block on leftover state for the traded asset only.

    On 2026-08-06 an account-wide check aborted a BTC session because of a
    reduce-only protective stop on P-XAUT-4200-070826, while the BTC book was
    completely flat.
    """

    def client_with(self, symbols):
        class Client(DeltaRESTClient):
            def __init__(self, syms):
                self._syms = syms

            def active_orders(self):
                return [{"product_symbol": s, "state": "pending"} for s in self._syms]

        return Client(symbols)

    def test_unrelated_asset_does_not_block(self):
        client = self.client_with(["P-XAUT-4200-070826", "C-ETH-1800-110726"])
        self.assertEqual(client.active_orders_for("BTC"), [])

    def test_same_asset_option_blocks(self):
        client = self.client_with(["P-XAUT-4200-070826", "C-BTC-64600-060826"])
        blocking = client.active_orders_for("BTC")
        self.assertEqual([o["product_symbol"] for o in blocking], ["C-BTC-64600-060826"])

    def test_same_asset_perpetual_blocks(self):
        """A BTC perp carries delta on the same underlying and is leftover state,
        even though it does not use the hyphenated option symbol format."""
        client = self.client_with(["BTCUSD"])
        self.assertEqual(len(client.active_orders_for("BTC")), 1)

    def test_empty_book_does_not_block(self):
        self.assertEqual(self.client_with([]).active_orders_for("BTC"), [])

    def test_missing_or_null_symbol_is_ignored(self):
        class Client(DeltaRESTClient):
            def __init__(self):
                pass

            def active_orders(self):
                return [{"product_symbol": None}, {}, {"product_symbol": "C-BTC-64600-060826"}]

        self.assertEqual(len(Client().active_orders_for("BTC")), 1)


class AutoSizeTests(unittest.TestCase):
    """Auto-sizing must reproduce the sizing decisions actually taken this week.

    Sizing failed three times in three days because only base margin was
    checked. The requirement is base PLUS premium, and premium scales with the
    day's credit, so the same size fits one day and is rejected the next on an
    identical balance.
    """

    def test_reproduces_8_august_decision_125(self):
        # available $106.43, spot 64,996.5, credit 96 -> 150 needs $111.89, fails
        size = feasible_size(Decimal("106.43"), Decimal("64996.5"), Decimal("96"), target=150)
        self.assertEqual(size, 125)

    def test_reproduces_6_august_decision_100(self):
        # available $83.00, spot 64,584.7, credit 52
        size = feasible_size(Decimal("83.00"), Decimal("64584.7"), Decimal("52"), target=150)
        self.assertEqual(size, 100)

    def test_would_have_reduced_5_august_from_150(self):
        """5 Aug ran 150 and printed projected_free -2.20 at preflight."""
        size = feasible_size(Decimal("114.518"), Decimal("64075.6"), Decimal("83.1"), target=150)
        self.assertEqual(size, 125)

    def test_never_exceeds_target_even_when_rich(self):
        size = feasible_size(Decimal("100000"), Decimal("64000"), Decimal("50"), target=150)
        self.assertEqual(size, 150)

    def test_high_credit_reduces_size_at_same_balance(self):
        """The failure mode that base-margin-only checking misses."""
        cheap = feasible_size(Decimal("106.43"), Decimal("64996.5"), Decimal("40"), target=150)
        rich = feasible_size(Decimal("106.43"), Decimal("64996.5"), Decimal("150"), target=150)
        self.assertGreater(cheap, rich)

    def test_returns_zero_rather_than_a_token_position(self):
        self.assertEqual(feasible_size(Decimal("20"), Decimal("64000"), Decimal("50"), target=150), 0)

    def test_zero_budget_and_bad_inputs_are_safe(self):
        self.assertEqual(feasible_size(Decimal("5"), Decimal("64000"), Decimal("50"), target=150), 0)
        self.assertEqual(feasible_size(Decimal("500"), Decimal("0"), Decimal("50"), target=150), 0)
        self.assertEqual(feasible_size(Decimal("500"), Decimal("64000"), Decimal("50"), target=0), 0)

    def test_result_is_always_on_the_increment(self):
        for avail in ("83", "95", "106.43", "118", "131"):
            size = feasible_size(Decimal(avail), Decimal("64996.5"), Decimal("96"), target=150)
            self.assertEqual(size % 25, 0, f"available {avail} gave {size}")


if __name__ == "__main__":
    unittest.main()


class UnderlyingMoveTests(unittest.TestCase):
    """Stop-outs must be attributable to the underlying move, not inferred.

    Before this, the log recorded only that premium expanded - so a stop could
    not be told apart from a vol or spread move without guessing.
    """

    def engine(self, directory, entry_spot, strike, credit):
        settings = Settings("testnet", True, None, None, None, None, TESTNET_REST,
                            TESTNET_PUBLIC_WS, Path(directory), False)
        eng = ExecutionEngine(settings, StrategyConfig(size=100, persistence_ticks=2),
                              clock=lambda: datetime(2026, 8, 5, 17, 0))
        eng.entry_spot = Decimal(str(entry_spot))
        eng.strike = Decimal(str(strike))
        eng.entry_credit = Decimal(str(credit))
        eng.state = State.OPEN
        return eng

    def test_spot_derived_from_parity_and_move_measured(self):
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, 64075.6, 64000, 80)
            # marks imply spot = 64000 + 140 - 2 = 64138
            out = eng.underlying_move(quote("C", 139, 141, mark=140),
                                      quote("P", 1.5, 2.5, mark=2))
            self.assertEqual(out["spot_at_trigger"], Decimal("64138"))
            self.assertEqual(out["spot_move_from_entry"],
                             Decimal("64138") - Decimal("64075.6"))
            self.assertEqual(out["stop_headroom_points"], Decimal("120") - Decimal("80"))
            self.assertEqual(out["spot_source"], "put_call_parity_on_marks")

    def test_single_leg_reports_unavailable_rather_than_wrong(self):
        """Parity needs both legs; a single-leg session must not fabricate spot."""
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, 64075.6, 64000, 80)
            eng.live_put = False
            out = eng.underlying_move(quote("C", 139, 141, mark=140), quote("P", 0, 0, mark=0))
            self.assertIsNone(out["spot_at_trigger"])
            self.assertEqual(out["spot_source"], "unavailable_single_leg_or_no_strike")

    def test_stop_triggered_event_carries_the_fields(self):
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, 64075.6, 64000, 80)
            c, p = quote("C", 139, 141, mark=140), quote("P", 1.5, 2.5, mark=2)
            self.assertFalse(eng.observe_stop(c, p))
            self.assertTrue(eng.observe_stop(c, p))
            rows = [json.loads(l) for l in eng.log_path.read_text().splitlines() if l.strip()]
            trig = [r for r in rows if r["event"] == "stop_triggered"][0]
            self.assertIn("spot_at_trigger", trig)
            self.assertIn("spot_move_from_entry", trig)
            self.assertIn("spot_move_pct", trig)


class AutoSizeTests(unittest.TestCase):
    """Auto-sizing must reproduce the sizing decisions actually taken this week.

    Sizing failed three times in three days because only base margin was
    checked. The requirement is base PLUS premium, and premium scales with the
    day's credit, so the same size fits one day and is rejected the next on an
    identical balance.
    """

    def test_reproduces_8_august_decision_125(self):
        # available $106.43, spot 64,996.5, credit 96 -> 150 needs $111.89, fails
        size = feasible_size(Decimal("106.43"), Decimal("64996.5"), Decimal("96"), target=150)
        self.assertEqual(size, 125)

    def test_reproduces_6_august_decision_100(self):
        # available $83.00, spot 64,584.7, credit 52
        size = feasible_size(Decimal("83.00"), Decimal("64584.7"), Decimal("52"), target=150)
        self.assertEqual(size, 100)

    def test_would_have_reduced_5_august_from_150(self):
        """5 Aug ran 150 and printed projected_free -2.20 at preflight."""
        size = feasible_size(Decimal("114.518"), Decimal("64075.6"), Decimal("83.1"), target=150)
        self.assertEqual(size, 125)

    def test_never_exceeds_target_even_when_rich(self):
        size = feasible_size(Decimal("100000"), Decimal("64000"), Decimal("50"), target=150)
        self.assertEqual(size, 150)

    def test_high_credit_reduces_size_at_same_balance(self):
        """The failure mode that base-margin-only checking misses."""
        cheap = feasible_size(Decimal("106.43"), Decimal("64996.5"), Decimal("40"), target=150)
        rich = feasible_size(Decimal("106.43"), Decimal("64996.5"), Decimal("150"), target=150)
        self.assertGreater(cheap, rich)

    def test_returns_zero_rather_than_a_token_position(self):
        self.assertEqual(feasible_size(Decimal("20"), Decimal("64000"), Decimal("50"), target=150), 0)

    def test_zero_budget_and_bad_inputs_are_safe(self):
        self.assertEqual(feasible_size(Decimal("5"), Decimal("64000"), Decimal("50"), target=150), 0)
        self.assertEqual(feasible_size(Decimal("500"), Decimal("0"), Decimal("50"), target=150), 0)
        self.assertEqual(feasible_size(Decimal("500"), Decimal("64000"), Decimal("50"), target=0), 0)

    def test_result_is_always_on_the_increment(self):
        for avail in ("83", "95", "106.43", "118", "131"):
            size = feasible_size(Decimal(avail), Decimal("64996.5"), Decimal("96"), target=150)
            self.assertEqual(size % 25, 0, f"available {avail} gave {size}")


if __name__ == "__main__":
    unittest.main()
