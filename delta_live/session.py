from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import DeltaRESTClient
from .config import Settings
from .engine import ExecutionEngine, State, StrategyConfig
from .liquidity import Quote
from .stream import PublicQuoteStream

IST = ZoneInfo("Asia/Kolkata")


class QuoteBook:
    def __init__(self):
        self.rows: dict[str, Quote] = {}
        self.system_live = False
        self.lock = threading.Lock()

    def update(self, message: dict) -> None:
        if message.get("type") == "system_status":
            self.system_live = message.get("status") == "live"
            return
        if message.get("type") != "ticker":
            return
        for row in message.get("d") or []:
            q = row.get("q") or []
            if len(q) < 4:
                continue
            quote = Quote(str(row.get("s")), Decimal(str(q[2] or 0)), Decimal(str(q[0] or 0)),
                          Decimal(str(q[3] or 0)), Decimal(str(q[1] or 0)),
                          Decimal(str(row.get("m") or 0)), int(message.get("ts") or 0))
            with self.lock:
                self.rows[quote.symbol] = quote

    def pair(self, call: str, put: str) -> tuple[Quote, Quote] | None:
        with self.lock:
            if call in self.rows and put in self.rows:
                return self.rows[call], self.rows[put]
        return None


def filled(order: dict) -> tuple[int, Decimal]:
    size = int(order.get("size") or 0)
    remaining = int(order.get("unfilled_size") or 0)
    price = Decimal(str(order.get("average_fill_price") or 0))
    return size - remaining, price


def enter_paired_slices(engine: ExecutionEngine, call_product: dict, put_product: dict,
                        call_symbol: str, put_symbol: str, requested_size: int,
                        initial_call: Quote, initial_put: Quote, slice_size: int = 25,
                        entry_window: float = 20, unmatched_grace: float = 10
                        ) -> tuple[int, Decimal, Decimal]:
    """Enter matched slices, retrying a missing leg before flattening it.

    A completed matched slice is retained. An unmatched slice is retried against
    fresh quotes for ``unmatched_grace`` seconds and is flattened only if the
    missing leg still cannot be completed inside the combined-credit floor.
    """
    deadline = time.monotonic() + entry_window
    minimum_combined_credit = (initial_call.mid + initial_put.mid) * engine.strategy.min_credit_ratio
    matched = 0
    call_notional = Decimal(0)
    put_notional = Decimal(0)
    slice_index = 0

    while matched < requested_size and time.monotonic() < deadline:
        target = min(slice_size, requested_size - matched)
        slice_index += 1
        call_quote = Quote.from_ticker(engine.client.ticker(call_symbol))
        put_quote = Quote.from_ticker(engine.client.ticker(put_symbol))
        call_limit = call_quote.bid * Decimal("0.98")
        put_limit = put_quote.bid * Decimal("0.98")
        if call_limit + put_limit < minimum_combined_credit:
            engine.event("entry_retry_wait", reason="combined_credit_below_floor",
                         call_limit=call_limit, put_limit=put_limit,
                         minimum_combined_credit=minimum_combined_credit)
            time.sleep(0.5)
            continue

        with ThreadPoolExecutor(max_workers=2) as pool:
            call_future = pool.submit(engine.client.place_order, engine.order_payload(
                call_product, "sell", target, call_limit, f"ce{slice_index}a", False))
            put_future = pool.submit(engine.client.place_order, engine.order_payload(
                put_product, "sell", target, put_limit, f"pe{slice_index}a", False))
            call_order, put_order = call_future.result(), put_future.result()
        call_filled, call_price = filled(call_order)
        put_filled, put_price = filled(put_order)
        engine.event("entry_slice", slice=slice_index, target=target,
                     call_order=call_order.get("id"), put_order=put_order.get("id"),
                     call_filled=call_filled, put_filled=put_filled)

        grace_deadline = min(deadline, time.monotonic() + unmatched_grace)
        attempt = 0
        while call_filled != put_filled and time.monotonic() < grace_deadline:
            attempt += 1
            if call_filled < put_filled:
                quote = Quote.from_ticker(engine.client.ticker(call_symbol))
                limit = quote.bid * max(Decimal("0.90"), Decimal("0.98") - Decimal("0.01") * attempt)
                other_price = put_price
                product, side_name = call_product, "call"
                need = put_filled - call_filled
                suffix = f"ce{slice_index}r{attempt}"
            else:
                quote = Quote.from_ticker(engine.client.ticker(put_symbol))
                limit = quote.bid * max(Decimal("0.90"), Decimal("0.98") - Decimal("0.01") * attempt)
                other_price = call_price
                product, side_name = put_product, "put"
                need = call_filled - put_filled
                suffix = f"pe{slice_index}r{attempt}"
            if other_price + limit < minimum_combined_credit:
                engine.event("entry_retry_wait", reason="combined_credit_below_floor",
                             missing_leg=side_name, candidate_limit=limit,
                             minimum_combined_credit=minimum_combined_credit)
                time.sleep(0.5)
                continue
            retry = engine.client.place_order(engine.order_payload(
                product, "sell", need, limit, suffix, False))
            got, price = filled(retry)
            if side_name == "call":
                if got:
                    call_price = ((call_price * call_filled) + (price * got)) / (call_filled + got)
                call_filled += got
            else:
                if got:
                    put_price = ((put_price * put_filled) + (price * got)) / (put_filled + got)
                put_filled += got
            engine.event("entry_leg_retry", slice=slice_index, attempt=attempt,
                         missing_leg=side_name, order=retry.get("id"), filled=got,
                         call_filled=call_filled, put_filled=put_filled)
            if call_filled != put_filled:
                time.sleep(0.5)

        paired = min(call_filled, put_filled)
        if paired:
            matched += paired
            call_notional += call_price * paired
            put_notional += put_price * paired
        call_excess, put_excess = call_filled - paired, put_filled - paired
        if call_excess or put_excess:
            engine.event("entry_slice_unmatched", slice=slice_index,
                         call_excess=call_excess, put_excess=put_excess)
            close_positions(engine, [(call_product, call_symbol, call_excess),
                                     (put_product, put_symbol, put_excess)])
            break
        if call_filled == 0 and put_filled == 0:
            time.sleep(0.5)

    if matched == 0:
        return 0, Decimal(0), Decimal(0)
    return matched, call_notional / matched, put_notional / matched


def nearest_chain(client: DeltaRESTClient, asset: str) -> tuple[str, list[dict]]:
    now = datetime.now(IST)
    for offset in range(8):
        expiry = (now + timedelta(days=offset)).strftime("%d-%m-%Y")
        chain = client.option_chain(asset, expiry)
        if chain:
            return expiry, chain
    raise RuntimeError("No testnet option expiry found in the next seven days")


def run_session(asset: str, size: int, minutes: int, env_file: Path,
                allow_production: bool = False, min_free_margin: Decimal = Decimal("30"),
                slice_size: int = 25, entry_window: float = 20,
                unmatched_grace: float = 10) -> int:
    settings = Settings.load(env_file)
    settings.assert_order_mode(allow_production=allow_production)
    if allow_production:
        os.environ["DELTA_PRODUCTION_ORDER_MODE"] = "1"
    strategy = StrategyConfig(asset=asset, size=size, persistence_ticks=2)
    engine = ExecutionEngine(settings, strategy)
    client = engine.client
    if client.active_orders() or client.positions(asset):
        raise RuntimeError("Refusing to start: demo account has active orders or positions")

    expiry, chain = nearest_chain(client, asset)
    spot = Decimal(str(next(r["spot_price"] for r in chain if r.get("spot_price"))))
    call_row, put_row = engine.select_atm(chain, spot)
    call_symbol, put_symbol = call_row["symbol"], put_row["symbol"]
    call_product, put_product = client.product(call_symbol), client.product(put_symbol)
    for product in (call_product, put_product):
        leverage = Decimal(str(client.order_leverage(int(product["id"]))["leverage"]))
        if leverage != Decimal("200"):
            raise RuntimeError(f"NO TRADE: {product['symbol']} leverage is {leverage}, not 200")
    call_quote, put_quote = engine.preflight(call_row, put_row)
    if settings.environment == "production":
        usd = next(b for b in client.balances() if b.get("asset_symbol") == "USD")
        available = Decimal(str(usd["available_balance"]))
        contract_value = Decimal(str(call_product["contract_value"]))
        base_margin = spot * contract_value * size / Decimal("200") * 2
        premium_margin = (call_quote.bid + put_quote.bid) * contract_value * size
        projected_free = available - base_margin - premium_margin - Decimal("5")
        planned_max_loss = (call_quote.bid + put_quote.bid) * Decimal("0.50") * contract_value * size + Decimal("5")
        engine.event("margin_preflight", available=available, base_margin=base_margin,
                     premium_margin=premium_margin, fee_buffer=5, projected_free=projected_free,
                     required_free=min_free_margin, planned_max_loss=planned_max_loss,
                     daily_loss_cap=25)
        if projected_free < min_free_margin:
            raise RuntimeError(f"NO TRADE: projected free margin {projected_free} below {min_free_margin}")
        if planned_max_loss > Decimal("25"):
            raise RuntimeError(f"NO TRADE: planned max loss {planned_max_loss} exceeds 25")
    engine.event("session_start", asset=asset, expiry=expiry, minutes=minutes, size=size,
                 call=call_symbol, put=put_symbol)

    engine.state = State.ENTERING
    # Testnet quotes can move between discovery and signed submission. A bounded
    # 5%-through-bid IOC remains a limit order while tolerating that latency.
    engine.event("entry_intent", call_bid=call_quote.bid, put_bid=put_quote.bid,
                 requested_size=size, slice_size=slice_size, entry_window=entry_window,
                 unmatched_grace=unmatched_grace)
    entered_size, call_fill, put_fill = enter_paired_slices(
        engine, call_product, put_product, call_symbol, put_symbol, size,
        call_quote, put_quote, slice_size, entry_window, unmatched_grace)
    if entered_size == 0:
        engine.halt("entry_unfilled")
        return 2
    if entered_size < size:
        engine.event("entry_reduced_size", requested_size=size, entered_size=entered_size)
        engine.strategy = StrategyConfig(asset=asset, size=entered_size, persistence_ticks=2)
    size = entered_size
    engine.record_entry(call_fill, put_fill)

    book = QuoteBook()
    stream = PublicQuoteStream(settings.public_ws_url, [call_symbol, put_symbol], book.update)
    stream.start()
    deadline = time.monotonic() + minutes * 60
    reason = "time_exit"
    try:
        while time.monotonic() < deadline:
            pair = book.pair(call_symbol, put_symbol)
            if pair and stream.last_message_age <= 3:
                if engine.observe_stop(*pair):
                    reason = "combined_50_stop"
                    break
            else:
                # REST recovery keeps risk monitoring alive during a stream gap.
                call = Quote.from_ticker(client.ticker(call_symbol))
                put = Quote.from_ticker(client.ticker(put_symbol))
                engine.event("rest_recovery_tick", stream_age=round(stream.last_message_age, 2))
                if engine.observe_stop(call, put):
                    reason = "combined_50_stop_rest_recovery"
                    break
            time.sleep(strategy.poll_seconds)
    finally:
        stream.close()

    engine.state = State.EXITING
    engine.event("exit_start", reason=reason)
    ok = close_positions(engine, [(call_product, call_symbol, size), (put_product, put_symbol, size)])
    engine.state = State.CLOSED if ok else State.HALTED
    engine.event("closed" if ok else "halted", reason=reason if ok else "exit_incomplete")
    return 0 if ok else 3


def close_positions(engine: ExecutionEngine, legs: list[tuple[dict, str, int]]) -> bool:
    client = engine.client
    all_closed = True
    for product, symbol, initial_size in legs:
        remaining = initial_size
        for attempt in range(5):
            if remaining <= 0:
                break
            quote = Quote.from_ticker(client.ticker(symbol))
            # Increasing bounded limits improve fill probability without a market order.
            limit = quote.ask * (Decimal(1) + Decimal(attempt) * Decimal("0.02"))
            order = client.place_order(engine.order_payload(product, "buy", remaining, limit,
                                      f"x{product['id']}{attempt}", True))
            got, price = filled(order)
            remaining -= got
            engine.event("exit_order", symbol=symbol, order=order.get("id"), filled=got,
                         fill_price=price, remaining=remaining, attempt=attempt + 1)
            if remaining:
                time.sleep(1)
        if remaining:
            all_closed = False
    return all_closed


def main() -> int:
    p = argparse.ArgumentParser(description="Run a time-bounded Delta straddle session")
    p.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    p.add_argument("--size", type=int, default=1)
    p.add_argument("--minutes", type=int, default=15)
    p.add_argument("--start-at", help="Optional IST start time, HH:MM:SS")
    p.add_argument("--slice-size", type=int, default=25)
    p.add_argument("--entry-window", type=float, default=20)
    p.add_argument("--unmatched-grace", type=float, default=10)
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--confirm-sandbox-orders", action="store_true")
    p.add_argument("--confirm-production-orders", action="store_true")
    args = p.parse_args()
    settings = Settings.load(args.env_file)
    if settings.environment == "production" and not args.confirm_production_orders:
        raise SystemExit("Add --confirm-production-orders to authorize live orders")
    if settings.environment == "testnet" and not args.confirm_sandbox_orders:
        raise SystemExit("Add --confirm-sandbox-orders to authorize demo orders")
    if (args.size < 1 or args.minutes < 1 or args.minutes > 30 or args.slice_size < 1
            or args.entry_window <= 0 or args.unmatched_grace <= 0):
        raise SystemExit("size must be positive; minutes must be 1..30")
    if args.start_at:
        target_time = datetime.strptime(args.start_at, "%H:%M:%S").time()
        target = datetime.combine(datetime.now(IST).date(), target_time, IST)
        seconds = (target - datetime.now(IST)).total_seconds()
        if seconds < -60 or seconds > 15 * 60:
            raise SystemExit("Scheduled start must be from one minute late to 15 minutes ahead")
        while (target - datetime.now(IST)).total_seconds() > 0:
            time.sleep(min(5, (target - datetime.now(IST)).total_seconds()))
    return run_session(args.asset, args.size, args.minutes, args.env_file,
                       allow_production=args.confirm_production_orders,
                       slice_size=args.slice_size, entry_window=args.entry_window,
                       unmatched_grace=args.unmatched_grace)


if __name__ == "__main__":
    raise SystemExit(main())
