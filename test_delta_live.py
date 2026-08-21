import json
import tempfile
import time
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
from delta_live.session import (FEE_HURDLE_PCT_OF_CREDIT, FEE_RATE, MIN_FEE_COVERAGE_WARN,
                                close_positions, depth_aware_exit_limit, enter_paired_slices,
                                pnl_message, summarise_pnl)
from delta_live.alerting import alert
from delta_live.size_check import feasible_size
from delta_live.telegram import Delivery, TelegramNotifier


def now_us():
    return int(time.time() * 1_000_000)


def quote(symbol, bid, ask, size=200, mark=None):
    return Quote(symbol, Decimal(str(bid)), Decimal(str(ask)), Decimal(size), Decimal(size),
                 Decimal(str(mark if mark is not None else (bid + ask) / 2)), now_us())


def book(bid, ask, size=100, age_s=0.0):
    """An L2 payload the pricing path will accept. `age_s` ages the snapshot."""
    return {"buy": [{"price": bid, "size": size}],
            "sell": [{"price": ask, "size": size}],
            "last_updated_at": now_us() - int(age_s * 1_000_000)}


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

            def l2_orderbook(self, symbol, depth=50):
                return book(*((60, 61) if symbol == "CALL" else (38, 39)))

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

            def l2_orderbook(self, symbol, depth=50):
                return book(*((60, 61) if symbol == "CALL" else (38, 39)), size=200)

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
        commission plus slippage for no risk benefit.
        """
        class Client:
            def __init__(self):
                self.payloads = []

            def l2_orderbook(self, symbol, depth=50):
                return book(*((81, 85) if symbol == "CALL" else (Decimal("0.9"), 3)), size=500)

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


class StaleQuotePricingTests(unittest.TestCase):
    """The 2026-08-12 single-leg session.

    /v2/tickers is a cached snapshot: measured that evening it refreshed every
    ~5.5s and read 1.7-7.4s stale. It reported the put bid pinned at exactly
    28.00 for the whole 8.5-second retry window while the executable bid fell to
    22, where Bala later sold manually. Six IOC sells were priced 27.16 down to
    25.76 off that frozen quote and every one cancelled unfilled - and the old
    ladder's floor of 0.90 x the ENTRY bid was 25.20, so no number of retries
    could have reached the market.
    """
    FROZEN_TICKER_BID = 28      # what /v2/tickers kept reporting
    TRUE_BID = 22               # where the book actually was
    OLD_LADDER_FLOOR = Decimal("25.20")   # 0.90 x 28

    def settings(self, directory, permissive=True):
        return Settings("testnet", True, None, None, None, None, TESTNET_REST,
                        TESTNET_PUBLIC_WS, Path(directory), permissive)

    def engine(self, directory, client):
        return ExecutionEngine(self.settings(directory),
                               StrategyConfig(size=125, min_credit_ratio=Decimal("0")),
                               client=client, clock=lambda: datetime(2026, 8, 12, 17, 0))

    def enter(self, engine):
        return enter_paired_slices(
            engine, {"id": 1, "tick_size": "0.1"}, {"id": 2, "tick_size": "0.1"},
            "CALL", "PUT", 125, quote("CALL", 28, 30), quote("PUT", 28, 30),
            slice_size=125, entry_window=4, unmatched_grace=3)

    def test_retry_chases_the_live_bid_instead_of_the_frozen_one(self):
        outer = self

        class Client:
            def __init__(self):
                self.payloads = []
                self.put_reads = 0

            def ticker(self, symbol):   # never consulted while L2 is healthy
                raise AssertionError("pricing must not fall back to the cached ticker")

            def l2_orderbook(self, symbol, depth=50):
                if symbol == "CALL":
                    return book(35, 36, size=500)
                self.put_reads += 1
                # First read catches the pre-move book; the retry sees the truth.
                bid = outer.FROZEN_TICKER_BID if self.put_reads == 1 else outer.TRUE_BID
                return book(bid, bid + 2, size=500)

            def place_order(self, payload):
                self.payloads.append(payload)
                if payload["product_id"] == 1:
                    return {"id": len(self.payloads), "size": payload["size"],
                            "unfilled_size": 0, "average_fill_price": "35"}
                # A sell only fills if it actually crosses the resting bid.
                crosses = Decimal(payload["limit_price"]) <= Decimal(str(outer.TRUE_BID))
                return {"id": len(self.payloads), "size": payload["size"],
                        "unfilled_size": 0 if crosses else payload["size"],
                        "average_fill_price": str(outer.TRUE_BID) if crosses else None}

        with tempfile.TemporaryDirectory() as d:
            client = Client()
            entered, _, put_price, call_size, put_size = self.enter(self.engine(d, client))

        self.assertEqual((call_size, put_size), (125, 125), "the pair must complete")
        self.assertEqual(entered, 125)
        self.assertEqual(put_price, Decimal("22"))
        fill_limit = Decimal([p for p in client.payloads if p["product_id"] == 2][-1]["limit_price"])
        self.assertLess(fill_limit, self.OLD_LADDER_FLOOR,
                        "the old ladder could not price below 25.20 and so could never fill")
        self.assertLessEqual(fill_limit, Decimal(str(self.TRUE_BID)))

    def test_stale_book_is_refused_rather_than_priced_from(self):
        class Client:
            def __init__(self):
                self.payloads = []

            def ticker(self, symbol):
                raise AssertionError("stale L2 must not silently fall back to a staler ticker")

            def l2_orderbook(self, symbol, depth=50):
                if symbol == "CALL":
                    return book(35, 36, size=500)
                return book(28, 30, size=500, age_s=9.0)   # older than the 2s ceiling

            def place_order(self, payload):
                self.payloads.append(payload)
                if payload["product_id"] == 1:
                    return {"id": len(self.payloads), "size": payload["size"],
                            "unfilled_size": 0, "average_fill_price": "35"}
                return {"id": len(self.payloads), "size": payload["size"],
                        "unfilled_size": payload["size"]}

        with tempfile.TemporaryDirectory() as d:
            client = Client()
            engine = self.engine(d, client)
            _, _, _, call_size, put_size = self.enter(engine)
            rows = [json.loads(l) for l in engine.log_path.read_text().splitlines() if l.strip()]

        stale = [r for r in rows if r["event"] == "entry_retry_stale_quote"]
        self.assertTrue(stale, "a stale quote must be logged, not traded on")
        self.assertEqual(stale[0]["quote_source"], "l2")
        # The call is retained; no retry order was priced off the stale book.
        self.assertEqual((call_size, put_size), (125, 0))
        self.assertEqual(len([p for p in client.payloads if p["product_id"] == 2]), 1,
                         "only the initial slice order, no stale-priced retries")

    def test_chase_is_abandoned_when_the_book_runs_away(self):
        """A collapsed bid must stop the chase, not sell into the hole."""
        class Client:
            def __init__(self):
                self.payloads = []
                self.put_reads = 0

            def l2_orderbook(self, symbol, depth=50):
                if symbol == "CALL":
                    return book(35, 36, size=500)
                self.put_reads += 1
                return book(28, 30, size=500) if self.put_reads == 1 else book(5, 7, size=500)

            def place_order(self, payload):
                self.payloads.append(payload)
                if payload["product_id"] == 1:
                    return {"id": len(self.payloads), "size": payload["size"],
                            "unfilled_size": 0, "average_fill_price": "35"}
                return {"id": len(self.payloads), "size": payload["size"],
                        "unfilled_size": payload["size"]}

        with tempfile.TemporaryDirectory() as d:
            client = Client()
            engine = self.engine(d, client)
            _, _, _, call_size, put_size = self.enter(engine)
            rows = [json.loads(l) for l in engine.log_path.read_text().splitlines() if l.strip()]

        self.assertTrue([r for r in rows if r["event"] == "entry_retry_chase_abandoned"])
        self.assertEqual((call_size, put_size), (125, 0), "the filled call is still retained")
        self.assertEqual(len([p for p in client.payloads if p["product_id"] == 2]), 1)

    def test_l2_quote_reads_the_touch_not_an_aggregate(self):
        q = Quote.from_l2("PUT", {"buy": [{"price": 21, "size": 900}, {"price": 22, "size": 340}],
                                  "sell": [{"price": 26, "size": 12}, {"price": 24, "size": 55}],
                                  "last_updated_at": now_us()})
        self.assertEqual((q.bid, q.bid_size), (Decimal("22"), Decimal("340")))
        self.assertEqual((q.ask, q.ask_size), (Decimal("24"), Decimal("55")))
        self.assertLess(q.age_seconds(now_us()), Decimal("1"))

    def test_a_quote_without_a_timestamp_counts_as_stale(self):
        q = Quote.from_l2("PUT", {"buy": [{"price": 22, "size": 10}],
                                  "sell": [{"price": 24, "size": 10}]})
        self.assertGreater(q.age_seconds(now_us()), Decimal("100"))


class ExitPnlTests(unittest.TestCase):
    """Every exit reports realised P&L, stop and time exit alike."""
    def settings(self, directory):
        return Settings("production", True, None, None, None, None, TESTNET_REST,
                        TESTNET_PUBLIC_WS, Path(directory), False)

    def engine(self, directory, fills=None, boom=False):
        class Client:
            def fills(self, page_size=200, **kw):
                if boom:
                    raise RuntimeError("ip_not_whitelisted_for_api_key")
                return fills or []
        return ExecutionEngine(self.settings(directory), StrategyConfig(size=125),
                               client=Client(), clock=lambda: datetime(2026, 8, 12, 17, 24))

    def test_reproduces_the_12_august_single_leg_result(self):
        """Sold 125 calls at 35, bought back at 13.7, on 0.001 contracts."""
        fills = [{"order_id": 1467300551, "commission": "0.1806875"},
                 {"order_id": 1467357153, "commission": "0.07072625"},
                 {"order_id": 999, "commission": "5.00"}]   # manual trade, must be excluded
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, fills)
            eng.order_ids = {"1467300551", "1467357153"}
            eng.record_exit_fill("C-BTC-64200-120826", 125, Decimal("13.7"))
            pnl = summarise_pnl(eng, Decimal("0.001"),
                                [("C-BTC-64200-120826", Decimal("35"), 125)])

        self.assertEqual(pnl["entry_credit_usd"], Decimal("4.3750"))
        self.assertEqual(pnl["exit_debit_usd"], Decimal("1.7125"))
        self.assertEqual(pnl["gross_usd"], Decimal("2.6625"))
        self.assertEqual(pnl["commission_usd"], Decimal("0.2514"))
        self.assertEqual(pnl["net_usd"], Decimal("2.4111"))

    def test_a_liquidated_leg_is_priced_in_not_ignored(self):
        """A leg the exchange closed for us, reproducing the 20 Aug 2026 failure.

        Before the fix, exit_debit counted only the put the engine bought back, so the
        liquidated call contributed its full entry credit and ZERO exit debit -- the
        session reported a PROFIT of roughly the size of its actual LOSS. Figures here
        are synthetic; the shape is the real one and it is the shape that must never
        come back.
        """
        fills = [
            {"order_id": "1001", "commission": "0.50",
             "product_symbol": "P-BTC-72000-200826", "side": "sell", "size": "100",
             "price": "130"},
            {"order_id": "1002", "commission": "0.10",
             "product_symbol": "C-BTC-72000-200826", "side": "sell", "size": "100",
             "price": "20"},
            # Not one of ours: the exchange's liquidation order.
            {"order_id": "9999", "commission": "0.85",
             "product_symbol": "C-BTC-72000-200826", "side": "buy", "size": "100",
             "price": "240", "fill_type": "liquidation",
             "meta_data": {"total_liquidation_fee_in_settling_asset": "4.00"}},
            {"order_id": "1003", "commission": "0.05",
             "product_symbol": "P-BTC-72000-200826", "side": "buy", "size": "100",
             "price": "5"},
        ]
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, fills)
            eng.order_ids = {"1001", "1002", "1003"}
            # The engine only ever closed the put. The call simply vanished.
            eng.record_exit_fill("P-BTC-72000-200826", 100, Decimal("5"))
            pnl = summarise_pnl(eng, Decimal("0.001"),
                                [("C-BTC-72000-200826", Decimal("20"), 100),
                                 ("P-BTC-72000-200826", Decimal("130"), 100)])

        self.assertEqual(pnl["entry_credit_usd"], Decimal("15.0000"))
        # put 5 * 100 * 0.001 = 0.50  PLUS the liquidated call 240 * 100 * 0.001 = 24.00
        self.assertEqual(pnl["exit_debit_usd"], Decimal("24.5000"))
        self.assertEqual(pnl["gross_usd"], Decimal("-9.5000"))
        self.assertEqual(pnl["commission_usd"], Decimal("1.5000"))
        self.assertEqual(pnl["liquidation_fee_usd"], Decimal("4.0000"))
        self.assertTrue(pnl["foreign_close"])
        self.assertEqual(pnl["net_usd"], Decimal("-15.0000"))
        self.assertLess(pnl["net_usd"], 0, "a losing session must not report a profit")

    def test_leg_closed_by_a_manual_trade_is_still_priced_in(self):
        """Same failure shape as a liquidation but without the liquidation markers."""
        fills = [{"order_id": "1", "commission": "0.10", "product_symbol": "C",
                  "side": "sell", "size": "100", "price": "50"},
                 {"order_id": "999", "commission": "0.20", "product_symbol": "C",
                  "side": "buy", "size": "100", "price": "80"}]
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, fills)
            eng.order_ids = {"1"}
            pnl = summarise_pnl(eng, Decimal("0.001"), [("C", Decimal("50"), 100)])
        self.assertEqual(pnl["exit_debit_usd"], Decimal("8.0000"))
        self.assertEqual(pnl["gross_usd"], Decimal("-3.0000"))
        self.assertEqual(pnl["liquidation_fee_usd"], Decimal("0.0000"))
        self.assertTrue(pnl["foreign_close"])

    def test_ordinary_session_is_unchanged_by_the_reconciliation(self):
        """The fix must not alter a session the engine closed itself."""
        fills = [{"order_id": "1", "commission": "0.10", "product_symbol": "C",
                  "side": "sell", "size": "100", "price": "50"},
                 {"order_id": "2", "commission": "0.05", "product_symbol": "C",
                  "side": "buy", "size": "100", "price": "20"}]
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, fills)
            eng.order_ids = {"1", "2"}
            eng.record_exit_fill("C", 100, Decimal("20"))
            pnl = summarise_pnl(eng, Decimal("0.001"), [("C", Decimal("50"), 100)])
        self.assertEqual(pnl["exit_debit_usd"], Decimal("2.0000"))
        self.assertEqual(pnl["gross_usd"], Decimal("3.0000"))
        self.assertFalse(pnl["foreign_close"])
        self.assertEqual(pnl["net_usd"], Decimal("2.8500"))

    def test_uuid_order_ids_on_unrelated_fills_do_not_break_the_lookup(self):
        """13 Aug: Delta returned a UUID order_id on an unrelated product and the
        int() cast raised, so a cleanly reconciled session reported no net."""
        fills = [{"order_id": "1467300551", "commission": "0.18"},
                 {"order_id": "289dfaf09dcc4078b80a84a966881cc7", "commission": "9.99"}]
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, fills)
            eng.record_order_id(1467300551)
            eng.record_order_id("289dfaf09dcc4078b80a84a966881cc8")   # ours, but a UUID
            eng.record_exit_fill("C", 125, Decimal("13.7"))
            pnl = summarise_pnl(eng, Decimal("0.001"), [("C", Decimal("35"), 125)])
        self.assertIsNotNone(pnl["net_usd"])
        self.assertEqual(pnl["commission_usd"], Decimal("0.1800"))

    def test_manual_trades_on_the_same_contract_are_not_absorbed(self):
        fills = [{"order_id": 1, "commission": "0.10"}, {"order_id": 2, "commission": "9.99"}]
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, fills)
            eng.order_ids = {"1"}
            eng.record_exit_fill("C", 100, Decimal("1"))
            pnl = summarise_pnl(eng, Decimal("0.001"), [("C", Decimal("5"), 100)])
        self.assertEqual(pnl["commission_usd"], Decimal("0.1000"))

    def test_stop_exit_message_names_the_stop_and_carries_net(self):
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, [{"order_id": 1, "commission": "0.25"}])
            eng.order_ids = {"1"}
            eng.record_exit_fill("C", 125, Decimal("13.7"))
            legs = [("C", Decimal("35"), 125)]
            msg = pnl_message(eng, "combined_50_stop",
                              summarise_pnl(eng, Decimal("0.001"), legs), True, legs)
        self.assertIn("STOP LOSS", msg)
        self.assertIn("NET +$2.41", msg)
        self.assertIn("Commission:   $0.2500", msg)
        self.assertIn("Net:          +$2.4125", msg)
        self.assertIn("C: 125 @ 35 -> 13.7000", msg)

    def test_time_exit_message_reports_a_loss_with_a_minus_sign(self):
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, [{"order_id": 1, "commission": "0.50"}])
            eng.order_ids = {"1"}
            eng.record_exit_fill("C", 125, Decimal("60"))
            legs = [("C", Decimal("35"), 125)]
            msg = pnl_message(eng, "time_exit",
                              summarise_pnl(eng, Decimal("0.001"), legs), True, legs)
        self.assertIn("TIME EXIT", msg)
        # Sign outside the $, and the rounded headline agrees with the exact line.
        self.assertIn("NET -$3.63", msg)
        self.assertIn("Gross:        -$3.1250", msg)
        self.assertIn("Net:          -$3.6250", msg)

    def test_unreachable_fees_report_gross_rather_than_a_wrong_net(self):
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, boom=True)
            eng.record_exit_fill("C", 125, Decimal("13.7"))
            legs = [("C", Decimal("35"), 125)]
            pnl = summarise_pnl(eng, Decimal("0.001"), legs)
            msg = pnl_message(eng, "time_exit", pnl, True, legs)
            rows = [json.loads(l) for l in eng.log_path.read_text().splitlines() if l.strip()]
        self.assertEqual(pnl["gross_usd"], Decimal("2.6625"))
        self.assertIsNone(pnl["net_usd"])
        self.assertIn("net unavailable", msg)
        self.assertTrue([r for r in rows if r["event"] == "pnl_commission_unavailable"])

    def test_incomplete_exit_is_flagged_in_the_message(self):
        with tempfile.TemporaryDirectory() as d:
            eng = self.engine(d, [])
            legs = [("C", Decimal("35"), 125)]
            msg = pnl_message(eng, "time_exit",
                              summarise_pnl(eng, Decimal("0.001"), legs), False, legs)
        self.assertIn("WARNING: exit incomplete", msg)
        self.assertIn("not closed", msg)


class AlertingTests(unittest.TestCase):
    """15 Aug 2026: DNS died at 16:55. The session never ran AND the alert about
    it never arrived, because Telegram needs the same network. The log then said
    "Telegram is not configured" on a box whose credentials were fine."""

    def settings(self, directory, token="t", chat="c"):
        return Settings("production", True, None, None, token, chat,
                        TESTNET_REST, TESTNET_PUBLIC_WS, Path(directory), False)

    def test_missing_credentials_and_send_failure_are_different(self):
        self.assertIs(TelegramNotifier(None, None).deliver("x"), Delivery.NOT_CONFIGURED)
        with patch("delta_live.telegram.requests.post",
                   side_effect=__import__("requests").RequestException("dns")):
            self.assertIs(TelegramNotifier("t", "c").deliver("x"), Delivery.FAILED)

    def test_alert_still_lands_when_the_network_is_down(self):
        """The whole point: an outage must not be able to silence its own alert."""
        import requests as rq
        with tempfile.TemporaryDirectory() as d:
            with patch("delta_live.telegram.requests.post",
                       side_effect=rq.RequestException("dns")), \
                 patch("delta_live.alerting._desktop", return_value="sent"):
                r = alert(self.settings(d), "margin check failed 4x", level="ERROR")
            self.assertEqual(r.telegram, "failed")
            self.assertTrue(r.delivered, "an outage silenced its own alert")
            self.assertIn("logfile", r.channels_reached)
            log = (Path(d) / "ALERTS.log").read_text()
            self.assertIn("margin check failed 4x", log)
            self.assertIn("[ERROR]", log)
            # dated marker so an unnoticed failure is visible in a listing
            self.assertTrue(list(Path(d).glob("ALERT-*.txt")))

    def test_logfile_alone_is_enough_when_every_other_channel_dies(self):
        import requests as rq
        with tempfile.TemporaryDirectory() as d:
            with patch("delta_live.telegram.requests.post",
                       side_effect=rq.RequestException("dns")), \
                 patch("delta_live.alerting._desktop", return_value="failed:X"):
                r = alert(self.settings(d), "everything is down", level="ERROR")
            self.assertTrue(r.delivered)
            self.assertEqual(r.channels_reached, ["logfile"])

    def test_info_alerts_do_not_raise_a_desktop_popup(self):
        """Routine startup notices must not train the alert to be ignored."""
        with tempfile.TemporaryDirectory() as d:
            with patch("delta_live.telegram.requests.post") as post:
                post.return_value.raise_for_status.return_value = None
                r = alert(self.settings(d), "scheduler started", level="INFO")
            self.assertEqual(r.desktop, "skipped")
            self.assertEqual(r.telegram, "sent")


class SizeCheckFallbackTests(unittest.TestCase):
    """A failed margin check must never fall back to the LARGEST size."""

    def test_the_15_aug_fallback_would_have_been_unaffordable(self):
        # Representative of the 15 Aug evening: a balance that affords 125 lots but
        # NOT the 150 the scheduler fell back to. Spot and credit are that session's;
        # the balance is synthetic and only has to sit between the two requirements.
        avail, spot, credit = Decimal("105.00"), Decimal("63000"), Decimal("100")
        chosen = feasible_size(avail, spot, credit, target=150)
        self.assertEqual(chosen, 125, "size_check itself picks an affordable size")

        def margin(n):
            return spot * Decimal("0.001") / Decimal("200") * 2 * n + credit * Decimal("0.001") * n

        self.assertLessEqual(margin(chosen), avail)
        self.assertGreater(margin(150), avail,
                           "the old fallback demanded more margin than the account had")


class FeeHurdleTests(unittest.TestCase):
    """What a straddle must clear before any of it is edge.

    Delta charges a flat 4.130% of premium traded, both sides, with no fixed
    floor - verified against all 262 BTC option fills in the book. Because the
    fee is proportional, a small credit is NOT more fee-burdened than a large
    one, and no absolute minimum credit follows from commission at all.
    """
    def test_fee_rate_reproduces_a_real_commission(self):
        # Sold 125 calls at 35 on 0.001 contracts. Prices are market data; the point
        # is that the charge is exactly FEE_RATE of premium traded.
        premium = Decimal("35") * 125 * Decimal("0.001")
        self.assertEqual((premium * FEE_RATE).quantize(Decimal("0.0000001")),
                         Decimal("0.1806875"))

    def test_hurdle_is_a_ratio_and_is_about_eight_percent(self):
        self.assertAlmostEqual(float(FEE_HURDLE_PCT_OF_CREDIT), 0.0793, places=4)

    def test_the_hurdle_does_not_move_with_credit_size(self):
        """The point of the whole analysis: a 30-credit day is not worse off."""
        for credit in (Decimal("30"), Decimal("100"), Decimal("300")):
            breakeven = credit * (Decimal(1) - FEE_HURDLE_PCT_OF_CREDIT)
            gross = credit - breakeven
            fees = FEE_RATE * (credit + breakeven)
            self.assertAlmostEqual(float(gross - fees), 0.0, places=6,
                                   msg=f"credit {credit} should break even at {breakeven}")

    def test_8_august_loss_is_explained_by_decay_not_by_credit_size(self):
        """Credit 34.50, bought back 34.20: only 0.87% decay against a 7.93%
        hurdle. It lost because spot walked away, not because 34.50 was small."""
        credit, buyback = Decimal("34.5"), Decimal("34.2")
        decay_pct = (credit - buyback) / credit
        self.assertLess(decay_pct, FEE_HURDLE_PCT_OF_CREDIT)
        net = (credit - buyback) - FEE_RATE * (credit + buyback)
        self.assertLess(net, 0)

    def test_coverage_flags_thin_extrinsic_and_clears_fat_extrinsic(self):
        def coverage(credit, extrinsic):
            return Decimal(extrinsic) / (Decimal(credit) * FEE_HURDLE_PCT_OF_CREDIT)

        # 5 Aug: credit 80, extrinsic 7.5 -> the thinnest session on record,
        # and the worst result of that month.
        self.assertLess(coverage(80, "7.5"), MIN_FEE_COVERAGE_WARN)
        # 12 Aug: dead ATM, credit 35 of which 55.9 was extrinsic on the quotes.
        self.assertGreater(coverage(35, "55.9"), MIN_FEE_COVERAGE_WARN)

    def test_coverage_below_one_means_decay_cannot_pay_the_fees(self):
        credit = Decimal("100")
        extrinsic = credit * FEE_HURDLE_PCT_OF_CREDIT / 2      # half the hurdle
        self.assertLess(extrinsic / (credit * FEE_HURDLE_PCT_OF_CREDIT), Decimal("1"))


class AutoSizeTests(unittest.TestCase):
    """Auto-sizing must reproduce the sizing decisions actually taken this week.

    Sizing failed three times in three days because only base margin was
    checked. The requirement is base PLUS premium, and premium scales with the
    day's credit, so the same size fits one day and is rejected the next on an
    identical balance.

    Spot and credit below are the real sessions'; the BALANCES are representative
    round figures chosen to sit in the same bracket, since the account's actual
    balance is not something this repo publishes. Each case still pins the same
    decision the sizer had to make.
    """

    def test_credit_96_session_sizes_to_125_not_150(self):
        size = feasible_size(Decimal("110"), Decimal("64996.5"), Decimal("96"), target=150)
        self.assertEqual(size, 125)

    def test_credit_52_session_sizes_to_100(self):
        size = feasible_size(Decimal("85"), Decimal("64584.7"), Decimal("52"), target=150)
        self.assertEqual(size, 100)

    def test_credit_83_session_would_have_been_reduced_from_150(self):
        """That session ran 150 and printed a NEGATIVE projected_free at preflight."""
        size = feasible_size(Decimal("110"), Decimal("64075.6"), Decimal("83.1"), target=150)
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


if __name__ == "__main__":
    unittest.main()
