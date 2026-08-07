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


def depth_aware_exit_limit(product: dict, quote: Quote, orderbook: dict,
                           remaining: int, attempt: int) -> tuple[Decimal, dict]:
    """Return a marketable-but-bounded buy limit from cumulative ask depth.

    A percentage-only retry barely moves a penny option after tick rounding.
    For premiums at or below USD 5, the first IOC may cross as far as twice the
    depth price. For larger premiums, it uses a 5% cushion. Later rounds widen
    by another 5% (or two ticks) while remaining limit orders.
    """
    tick = Decimal(str(product["tick_size"]))
    asks = orderbook.get("sell") or []
    cumulative = 0
    depth_price = quote.ask
    levels_used = 0
    for level in sorted(asks, key=lambda row: Decimal(str(row.get("price") or 0))):
        price = Decimal(str(level.get("price") or 0))
        size = int(Decimal(str(level.get("size") or 0)))
        if price <= 0 or size <= 0:
            continue
        depth_price = max(depth_price, price)
        cumulative += size
        levels_used += 1
        if cumulative >= remaining:
            break

    depth_supported = cumulative >= remaining
    base = max(quote.ask, depth_price)
    if base <= 0:
        raise RuntimeError("Cannot construct exit limit without a positive ask")
    retry_step = max(base * Decimal("0.05"), tick * 2)
    if base <= Decimal("5"):
        initial_cushion = max(base, tick * 5)
    else:
        initial_cushion = retry_step
    limit = base + initial_cushion + retry_step * attempt
    return limit, {
        "best_ask": quote.ask,
        "depth_price": depth_price,
        "depth_available": cumulative,
        "depth_supported": depth_supported,
        "depth_levels": levels_used,
        "penny_mode": base <= Decimal("5"),
    }


def enter_paired_slices(engine: ExecutionEngine, call_product: dict, put_product: dict,
                        call_symbol: str, put_symbol: str, requested_size: int,
                        initial_call: Quote, initial_put: Quote, slice_size: int = 25,
                        entry_window: float = 20, unmatched_grace: float = 10
                        ) -> tuple[int, Decimal, Decimal, int, int]:
    """Enter matched slices, retrying a missing leg but never flattening a fill.

    A completed matched slice is retained. An unmatched slice is retried against
    fresh quotes for ``unmatched_grace`` seconds. If the missing leg still cannot
    be filled, the leg that DID fill is kept and traded single-sided rather than
    round-tripped out: closing a good fill to "repair" symmetry pays two lots of
    commission plus the spread and surrenders the entry price, which on
    2026-08-01 cost $1.03 of commission, $0.60 of slippage, and a re-entry 13
    points worse on the same contract. The retained leg stays under the normal
    stop and forced-exit rules.

    Returns ``(matched_pairs, call_avg_price, put_avg_price, call_size, put_size)``
    where the sizes are the live short quantity per leg and may differ.
    """
    deadline = time.monotonic() + entry_window
    minimum_combined_credit = (initial_call.mid + initial_put.mid) * engine.strategy.min_credit_ratio
    matched = 0
    call_notional = Decimal(0)
    put_notional = Decimal(0)
    slice_index = 0
    # Live short quantity per leg. These diverge when one leg cannot be filled.
    call_live = 0
    put_live = 0

    while matched < requested_size and time.monotonic() < deadline:
        target = min(slice_size, requested_size - matched)
        slice_index += 1
        call_quote = Quote.from_ticker(engine.client.ticker(call_symbol))
        put_quote = Quote.from_ticker(engine.client.ticker(put_symbol))
        # Permissive production mode prioritizes completing the frozen daily
        # sample while retaining bounded IOC orders rather than market orders.
        entry_factor = Decimal("0.90") if engine.settings.permissive_entry else Decimal("0.98")
        call_limit = call_quote.bid * entry_factor
        put_limit = put_quote.bid * entry_factor
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
            # limit/bid are logged so an unfilled leg can be diagnosed after the
            # fact: without them there is no way to tell "priced too high" from
            # "no resting bid" once the book has moved on.
            engine.event("entry_leg_retry", slice=slice_index, attempt=attempt,
                         missing_leg=side_name, order=retry.get("id"), filled=got,
                         limit=limit, bid=quote.bid, ask=quote.ask,
                         bid_size=quote.bid_size, needed=need,
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
            # RETAIN the excess. Do not flatten a good fill to restore symmetry.
            if call_excess:
                call_notional += call_price * call_excess
            if put_excess:
                put_notional += put_price * put_excess
            call_live += call_filled
            put_live += put_filled
            engine.event("entry_slice_unmatched", slice=slice_index,
                         call_excess=call_excess, put_excess=put_excess,
                         action="retained_single_leg",
                         note="unpaired leg kept under normal stop and forced-exit rules")
            break
        call_live += call_filled
        put_live += put_filled
        if call_filled == 0 and put_filled == 0:
            time.sleep(0.5)

    call_avg = call_notional / call_live if call_live else Decimal(0)
    put_avg = put_notional / put_live if put_live else Decimal(0)
    return matched, call_avg, put_avg, call_live, put_live


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
                unmatched_grace: float = 10, exit_at: str | None = None,
                min_leg_bid: Decimal = Decimal("5")) -> int:
    settings = Settings.load(env_file)
    settings.assert_order_mode(allow_production=allow_production)
    if allow_production:
        os.environ["DELTA_PRODUCTION_ORDER_MODE"] = "1"
    strategy = StrategyConfig(
        asset=asset,
        size=size,
        persistence_ticks=2,
        min_credit_ratio=Decimal("0") if settings.permissive_entry else Decimal("0.95"),
    )
    engine = ExecutionEngine(settings, strategy)
    client = engine.client
    if client.active_orders_for(asset) or client.positions(asset):
        raise RuntimeError(f"Refusing to start: active {asset} orders or positions already exist")

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
        if projected_free < min_free_margin and not settings.permissive_entry:
            raise RuntimeError(f"NO TRADE: projected free margin {projected_free} below {min_free_margin}")
        if planned_max_loss > Decimal("25") and not settings.permissive_entry:
            raise RuntimeError(f"NO TRADE: planned max loss {planned_max_loss} exceeds 25")
        if settings.permissive_entry and (projected_free < min_free_margin
                                          or planned_max_loss > Decimal("25")):
            engine.event("risk_budget_warning", projected_free=projected_free,
                         required_free=min_free_margin,
                         planned_max_loss=planned_max_loss,
                         daily_loss_cap=25, mode="telemetry_only")
    # Market scenario snapshot, recorded every session whether or not it trades.
    # The strike selector already picks the nearest strike, which is provably the
    # right choice; what actually varies day to day is how far spot sits from it
    # and how much EXTRINSIC value that leaves to harvest. Intrinsic is not edge -
    # selling it is a directional bet. Twelve sessions is too few to gate on, so
    # this records the inputs now and the rule gets decided on real data later.
    try:
        strike = Decimal(str(call_row.get("strike_price") or 0))
        distance = spot - strike
        combined_bid = call_quote.bid + put_quote.bid
        intrinsic = abs(distance)
        engine.event(
            "market_context",
            spot=spot, strike=strike, distance=distance,
            distance_pct=(distance / spot * 100) if spot else Decimal(0),
            strike_spacing_note="one leg goes near-worthless as |distance| approaches "
                                "half the strike spacing",
            call_bid=call_quote.bid, call_ask=call_quote.ask,
            put_bid=put_quote.bid, put_ask=put_quote.ask,
            combined_bid=combined_bid,
            intrinsic=intrinsic,
            extrinsic=combined_bid - intrinsic,
            extrinsic_pct_of_credit=((combined_bid - intrinsic) / combined_bid * 100)
                                    if combined_bid else Decimal(0),
            call_spread_pct=call_quote.spread_pct, put_spread_pct=put_quote.spread_pct,
            call_mark_vol=call_row.get("mark_vol"), put_mark_vol=put_row.get("mark_vol"),
            call_oi=call_row.get("oi"), put_oi=put_row.get("oi"),
            expiry=expiry,
        )
    except Exception as exc:  # telemetry must never block a trade
        engine.event("market_context_failed", error_type=type(exc).__name__, error=str(exc))

    # Advisory only. A leg quoted below the floor is very likely to be unfillable
    # in size and contributes almost nothing to the credit, but this must never
    # block the trade - it is a flag to review the strike choice, not a gate.
    for leg_name, leg_quote in (("call", call_quote), ("put", put_quote)):
        if leg_quote.bid < min_leg_bid:
            engine.event("leg_bid_below_floor", leg=leg_name, symbol=leg_quote.symbol,
                         bid=leg_quote.bid, floor=min_leg_bid, mode="advisory_only",
                         note="thin leg: expect fill difficulty and negligible credit "
                              "contribution; a single-leg session is the likely outcome")

    engine.event("session_start", asset=asset, expiry=expiry, minutes=minutes, size=size,
                 call=call_symbol, put=put_symbol)

    engine.state = State.ENTERING
    # Testnet quotes can move between discovery and signed submission. A bounded
    # 5%-through-bid IOC remains a limit order while tolerating that latency.
    engine.event("entry_intent", call_bid=call_quote.bid, put_bid=put_quote.bid,
                 requested_size=size, slice_size=slice_size, entry_window=entry_window,
                 unmatched_grace=unmatched_grace)
    entered_size, call_fill, put_fill, call_size, put_size = enter_paired_slices(
        engine, call_product, put_product, call_symbol, put_symbol, size,
        call_quote, put_quote, slice_size, entry_window, unmatched_grace)
    # A single-sided fill is a live position, not a failed entry. Only a session
    # with nothing filled on either leg is unfilled.
    if call_size == 0 and put_size == 0:
        engine.halt("entry_unfilled")
        return 2
    engine.live_call = call_size > 0
    engine.live_put = put_size > 0
    # Captured so a stop-out can be attributed to the underlying move.
    engine.entry_spot = spot
    engine.strike = Decimal(str(call_row.get("strike_price") or 0))
    position_size = max(call_size, put_size)
    if not (engine.live_call and engine.live_put):
        engine.event("single_leg_session", live_leg="call" if engine.live_call else "put",
                     call_size=call_size, put_size=put_size,
                     entry_price=call_fill if engine.live_call else put_fill,
                     note="unpaired leg retained deliberately; stop and forced exit still apply")
    if position_size < size:
        engine.event("entry_reduced_size", requested_size=size, entered_size=position_size)
    engine.strategy = StrategyConfig(asset=asset, size=position_size, persistence_ticks=2)
    size = position_size
    # Credit and therefore the stop level reflect only the legs actually held.
    engine.record_entry(call_fill if engine.live_call else Decimal(0),
                        put_fill if engine.live_put else Decimal(0))

    book = QuoteBook()
    stream = PublicQuoteStream(settings.public_ws_url, [call_symbol, put_symbol], book.update)
    stream.start()
    monotonic_deadline = time.monotonic() + minutes * 60
    wall_deadline = None
    if exit_at:
        exit_time = datetime.strptime(exit_at, "%H:%M:%S").time()
        wall_deadline = datetime.combine(datetime.now(IST).date(), exit_time, IST)
        if wall_deadline <= datetime.now(IST):
            raise RuntimeError(f"Forced-exit time {exit_at} IST has already passed")
    reason = "time_exit"
    try:
        while ((datetime.now(IST) < wall_deadline) if wall_deadline
               else (time.monotonic() < monotonic_deadline)):
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
    except BaseException:
        stream.close()
        raise

    engine.state = State.EXITING
    engine.event("exit_start", reason=reason)
    try:
        # Submit risk-reducing orders before waiting for the quote thread to
        # terminate. WebSocket shutdown can take up to five seconds, which is
        # too long to sit exposed after a confirmed 0DTE stop.
        ok = close_positions(engine, [(call_product, call_symbol, call_size),
                                      (put_product, put_symbol, put_size)],
                             reconcile_positions=True)
    finally:
        stream.close()
    engine.state = State.CLOSED if ok else State.HALTED
    engine.event("closed" if ok else "halted", reason=reason if ok else "exit_incomplete")
    return 0 if ok else 3


def close_positions(engine: ExecutionEngine, legs: list[tuple[dict, str, int]],
                    reconcile_positions: bool = False) -> bool:
    client = engine.client
    tracked = {
        symbol: {"product": product, "remaining": initial_size}
        for product, symbol, initial_size in legs if initial_size > 0
    }

    if reconcile_positions and tracked:
        try:
            positions = client.positions(engine.strategy.asset)
        except Exception as exc:
            engine.event("exit_position_reconcile_failed", phase="before",
                         error_type=type(exc).__name__)
        else:
            broker_sizes = {
                str(position.get("product_symbol") or ""):
                    max(0, -int(position.get("size") or 0))
                for position in positions
            }
            for symbol, row in tracked.items():
                planned = row["remaining"]
                row["remaining"] = broker_sizes.get(symbol, 0)
                engine.event("exit_position_reconciled", symbol=symbol, phase="before",
                             planned=planned, broker_short=row["remaining"])

    for attempt in range(5):
        pending = [(symbol, row) for symbol, row in tracked.items() if row["remaining"] > 0]
        if not pending:
            break
        prepared = []
        for symbol, row in pending:
            product = row["product"]
            remaining = row["remaining"]
            quote = Quote.from_ticker(client.ticker(symbol))
            try:
                orderbook = client.l2_orderbook(symbol, depth=50)
            except Exception as exc:
                orderbook = {}
                engine.event("exit_depth_fallback", symbol=symbol, attempt=attempt + 1,
                             error_type=type(exc).__name__)
            limit, depth = depth_aware_exit_limit(
                product, quote, orderbook, remaining, attempt)
            payload = engine.order_payload(
                product, "buy", remaining, limit, f"x{product['id']}a{attempt + 1}", True)
            engine.event("exit_order_intent", symbol=symbol, attempt=attempt + 1,
                         requested=remaining, limit_price=payload["limit_price"], **depth)
            prepared.append((symbol, row, payload))

        # Every remaining leg is submitted in the same retry round. An
        # illiquid penny leg must never delay risk reduction on the other leg.
        with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
            futures = [
                (symbol, row, payload, pool.submit(client.place_order, payload))
                for symbol, row, payload in prepared
            ]
            for symbol, row, payload, future in futures:
                try:
                    order = future.result()
                except Exception as exc:
                    engine.event("exit_order_error", symbol=symbol, attempt=attempt + 1,
                                 requested=row["remaining"],
                                 limit_price=payload["limit_price"],
                                 error_type=type(exc).__name__)
                    continue
                got, price = filled(order)
                row["remaining"] = max(0, row["remaining"] - got)
                engine.event("exit_order", symbol=symbol, order=order.get("id"), filled=got,
                             fill_price=price, remaining=row["remaining"],
                             limit_price=payload["limit_price"], attempt=attempt + 1)
        if any(row["remaining"] > 0 for row in tracked.values()):
            time.sleep(1)

    all_closed = all(row["remaining"] == 0 for row in tracked.values())
    if reconcile_positions and tracked:
        try:
            positions = client.positions(engine.strategy.asset)
        except Exception as exc:
            engine.event("exit_position_reconcile_failed", phase="after",
                         error_type=type(exc).__name__)
            return False
        broker_sizes = {
            str(position.get("product_symbol") or ""):
                max(0, -int(position.get("size") or 0))
            for position in positions
        }
        for symbol, row in tracked.items():
            broker_short = broker_sizes.get(symbol, 0)
            engine.event("exit_position_reconciled", symbol=symbol, phase="after",
                         expected_remaining=row["remaining"], broker_short=broker_short)
            if broker_short:
                all_closed = False
    return all_closed


def main() -> int:
    p = argparse.ArgumentParser(description="Run a time-bounded Delta straddle session")
    p.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    p.add_argument("--size", type=int, default=1)
    p.add_argument("--minutes", type=int, default=15)
    p.add_argument("--start-at", help="Optional IST start time, HH:MM:SS")
    p.add_argument("--exit-at", help="Absolute forced-exit time in IST, HH:MM:SS")
    p.add_argument("--slice-size", type=int, default=25)
    p.add_argument("--entry-window", type=float, default=20)
    p.add_argument("--unmatched-grace", type=float, default=10)
    p.add_argument("--min-leg-bid", type=Decimal, default=Decimal("5"),
                   help="Advisory floor: warn when a leg's bid is below this. "
                        "Never blocks entry.")
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
                       unmatched_grace=args.unmatched_grace, exit_at=args.exit_at,
                       min_leg_bid=args.min_leg_bid)


if __name__ == "__main__":
    raise SystemExit(main())
