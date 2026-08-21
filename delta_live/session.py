from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
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


# An order must never be priced from a quote older than this. Set from a
# 30-sample probe of both sources on 2026-08-12 (BTC ATM option, 0.4s apart):
#
#   L2 book   p50 0.61s   p90 2.00s   max 2.28s    0/30 over 3s
#   /v2/tickers p50 4.21s p90 6.61s   max 8.19s   22/30 over 3s
#
# 3s therefore rejects effectively none of the healthy source and most of the
# stale one. A 2s ceiling was tried first and refused 10% of good L2 reads,
# which just burns retry attempts without avoiding any bad fill.
MAX_PRICING_QUOTE_AGE_S = Decimal("3.0")

# Retry ladder, anchored on the live bid rather than the entry-time bid. The
# cushion only absorbs read-to-fill latency, so it starts tight and widens
# slowly; a sell priced just under a real bid crosses and fills at the bid.
RETRY_CUSHION_START = Decimal("0.995")
RETRY_CUSHION_STEP = Decimal("0.005")
RETRY_CUSHION_FLOOR = Decimal("0.97")
# Circuit breaker, not the primary control: if the live bid has fallen this far
# below the entry-time bid, stop chasing and let the retain rule decide. The
# minimum_combined_credit gate remains the designed control for credit quality.
MAX_CHASE_OF_ENTRY_BID = Decimal("0.75")

# Delta's options commission, measured over all 262 BTC option fills in the
# book: a flat 4.130% of premium traded, identical on buys and sells, with NO
# fixed floor - a $0.0008 premium pays $0.000033, the same rate. Large premiums
# are sometimes charged less where a notional cap binds, so 4.130% is the
# worst case and the right number to plan against.
FEE_RATE = Decimal("0.0413")

# Round-tripping a straddle pays the fee twice, on the credit in and the debit
# out. Breakeven is buying back at (1-r)/(1+r) = 92.07% of the credit, so the
# structure must give back 7.93% of its entry credit just to cover fees.
#
# Note this is a RATIO, not a dollar amount: because the fee is perfectly
# proportional to premium, a 30-point credit is no more fee-burdened than a
# 300-point one. There is therefore no absolute minimum credit to derive here.
# What actually has to pay the 7.93% is EXTRINSIC value - intrinsic does not
# decay, and selling it is a directional bet, not a theta trade.
FEE_HURDLE_PCT_OF_CREDIT = (Decimal(1) - (Decimal(1) - FEE_RATE) / (Decimal(1) + FEE_RATE))

# Advisory only. Warn when extrinsic covers the fee hurdle by less than this
# multiple, i.e. when the decay edge is thin relative to what the round trip
# costs. Set to fire on roughly a third of sessions so three occurrences
# accumulate in about a week; NOTHING is gated on it until that sample exists.
MIN_FEE_COVERAGE_WARN = Decimal("1.5")


def pricing_quote(engine: ExecutionEngine, symbol: str) -> tuple[Quote, str]:
    """Freshest available quote for pricing an order, with its source.

    Prefers /v2/l2orderbook (median 0.61s stale) over /v2/tickers (5.5s
    refresh, median 4.21s stale on read). On 2026-08-12 the ticker reported
    the put bid pinned at exactly 28.00 for the entire 8.5-second retry window
    while the executable bid fell to 22, so every IOC retry was priced above
    the book and cancelled unfilled. Callers must still check `age_seconds`:
    this returns the best quote available, not necessarily a usable one.
    """
    try:
        book = engine.client.l2_orderbook(symbol, depth=5)
    except Exception as exc:
        engine.event("pricing_quote_l2_failed", symbol=symbol,
                     error_type=type(exc).__name__, fallback="ticker")
    else:
        quote = Quote.from_l2(symbol, book)
        if quote.bid > 0:
            return quote, "l2"
        engine.event("pricing_quote_l2_empty", symbol=symbol, fallback="ticker")
    return Quote.from_ticker(engine.client.ticker(symbol)), "ticker"


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
        call_quote, call_source = pricing_quote(engine, call_symbol)
        put_quote, put_source = pricing_quote(engine, put_symbol)
        now_us = time.time() * 1_000_000
        # Logged, not gated. The retry ladder below refuses to price off a stale
        # quote because it has the retain rule to fall back on; blocking the
        # first slice the same way would risk burning the entry window whenever
        # L2 is unavailable and only the cached ticker is left.
        engine.event("entry_slice_quotes", slice=slice_index,
                     call_source=call_source, put_source=put_source,
                     call_bid=call_quote.bid, put_bid=put_quote.bid,
                     call_bid_size=call_quote.bid_size, put_bid_size=put_quote.bid_size,
                     call_age_s=call_quote.age_seconds(now_us),
                     put_age_s=put_quote.age_seconds(now_us))
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
        engine.record_order_id(call_order.get("id"))
        engine.record_order_id(put_order.get("id"))
        engine.event("entry_slice", slice=slice_index, target=target,
                     call_order=call_order.get("id"), put_order=put_order.get("id"),
                     call_filled=call_filled, put_filled=put_filled)

        grace_deadline = min(deadline, time.monotonic() + unmatched_grace)
        attempt = 0
        placed = 0
        while call_filled != put_filled and time.monotonic() < grace_deadline:
            attempt += 1
            if call_filled < put_filled:
                symbol, product, side_name = call_symbol, call_product, "call"
                other_price, entry_bid = put_price, initial_call.bid
                need = put_filled - call_filled
                suffix = f"ce{slice_index}r{attempt}"
            else:
                symbol, product, side_name = put_symbol, put_product, "put"
                other_price, entry_bid = call_price, initial_put.bid
                need = call_filled - put_filled
                suffix = f"pe{slice_index}r{attempt}"
            quote, source = pricing_quote(engine, symbol)
            age = quote.age_seconds(time.time() * 1_000_000)
            if quote.bid <= 0 or age > MAX_PRICING_QUOTE_AGE_S:
                # Refusing to trade beats pricing off a snapshot of a book that
                # has moved. This is the 2026-08-12 failure: six IOC sells were
                # priced from one frozen quote and every one cancelled unfilled.
                engine.event("entry_retry_stale_quote", slice=slice_index, attempt=attempt,
                             missing_leg=side_name, quote_source=source, quote_age_s=age,
                             max_age_s=MAX_PRICING_QUOTE_AGE_S, bid=quote.bid)
                time.sleep(0.3)
                continue
            # Chase the LIVE bid. The cushion only has to absorb the latency
            # between reading the book and the order landing, so it is small and
            # widens slowly - the old ladder stepped down from the ENTRY bid to
            # a floor of 0.90x, which on 12 August put its most aggressive
            # possible price 13% above the market and made a fill arithmetically
            # impossible however many times it retried.
            cushion = max(RETRY_CUSHION_FLOOR, RETRY_CUSHION_START - RETRY_CUSHION_STEP * placed)
            limit = quote.bid * cushion
            if entry_bid > 0 and limit < entry_bid * MAX_CHASE_OF_ENTRY_BID:
                # The book has run away from the entry price. Stop chasing and
                # let the retain rule take over rather than selling into a hole.
                engine.event("entry_retry_chase_abandoned", slice=slice_index, attempt=attempt,
                             missing_leg=side_name, candidate_limit=limit, live_bid=quote.bid,
                             entry_bid=entry_bid, floor=entry_bid * MAX_CHASE_OF_ENTRY_BID)
                break
            if other_price + limit < minimum_combined_credit:
                engine.event("entry_retry_wait", reason="combined_credit_below_floor",
                             missing_leg=side_name, candidate_limit=limit,
                             minimum_combined_credit=minimum_combined_credit)
                time.sleep(0.5)
                continue
            placed += 1
            retry = engine.client.place_order(engine.order_payload(
                product, "sell", need, limit, suffix, False))
            got, price = filled(retry)
            engine.record_order_id(retry.get("id"))
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
            # "no resting bid" once the book has moved on. quote_source and
            # quote_age_s make a stale-quote failure visible from the log alone
            # rather than needing a live probe to reconstruct it.
            engine.event("entry_leg_retry", slice=slice_index, attempt=attempt,
                         missing_leg=side_name, order=retry.get("id"), filled=got,
                         limit=limit, bid=quote.bid, ask=quote.ask,
                         bid_size=quote.bid_size, needed=need,
                         quote_source=source, quote_age_s=age, cushion=cushion,
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


def usd(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(Decimal("0.0001"))


def summarise_pnl(engine: ExecutionEngine, contract_value: Decimal,
                  entry_legs: list[tuple[str, Decimal, int]]) -> dict:
    """Realised USD P&L for the session, net of Delta's own commission.

    ``entry_legs`` is ``[(symbol, average_entry_price, size)]`` for the legs
    actually held - which on a retained single-leg session is one leg, not two.

    Commission comes from authenticated fills restricted to this engine's own
    order ids, so an UNRELATED manual trade on the same contract cannot be absorbed
    into the automated session's number. It is best-effort: a fees lookup failing
    after the book is already flat must not fail the session, so the caller
    reports gross and flags the gap instead.

    The one exception, added after the 20 Aug 2026 liquidation: if a leg we were
    short is no longer on the book and we did not close it, the fill that DID close
    it is priced in, whoever placed it. Excluding it does not keep the number clean,
    it makes the number wrong -- that session reported a $14.07 profit on a $14.25
    loss. `foreign_close` flags when this happened and `liquidation_fee_usd` carries
    the exchange's penalty, which is not commission and is not optional.
    """
    entry_credit = sum((price * size for _, price, size in entry_legs), Decimal(0)) * contract_value
    exit_debit = sum((row["notional"] for row in engine.exit_fills.values()),
                     Decimal(0)) * contract_value
    commission: Decimal | None = None
    liquidation_fee = Decimal(0)
    foreign: list[dict] = []

    # A leg can leave the book without this engine closing it. On 20 Aug 2026 the call
    # was LIQUIDATED by the exchange at 232 while the engine's own stop was still 38
    # seconds from firing. `engine.exit_fills` only ever holds fills from orders this
    # engine placed, so that leg contributed its full entry credit and ZERO exit debit:
    # the session reported +$14.07 when it had actually lost $14.25, sign inverted, a
    # $28.31 error. Any leg closed by anyone other than us -- liquidation, a manual
    # trade, an exchange settlement -- has to be priced from authenticated fills or the
    # book is fiction.
    try:
        fills = engine.client.fills(page_size=200)
        commission = sum((Decimal(str(f.get("commission") or 0))
                          for f in fills
                          if str(f.get("order_id") or "").strip() in engine.order_ids),
                         Decimal(0))
        wanted = {sym: size for sym, _price, size in entry_legs}
        closed_by_us = {sym: row["size"] for sym, row in engine.exit_fills.items()}
        for sym, size in wanted.items():
            shortfall = Decimal(size) - Decimal(closed_by_us.get(sym, 0))
            if shortfall <= 0:
                continue
            # Find closing fills on this symbol that were NOT ours, newest first.
            for f in fills:
                if shortfall <= 0:
                    break
                if f.get("product_symbol") != sym:
                    continue
                if str(f.get("order_id") or "").strip() in engine.order_ids:
                    continue
                if str(f.get("side") or "").lower() != "buy":      # we are always short
                    continue
                took = min(shortfall, Decimal(str(f.get("size") or 0)))
                if took <= 0:
                    continue
                price = Decimal(str(f.get("price") or 0))
                exit_debit += price * took * contract_value
                commission = (commission or Decimal(0)) + Decimal(str(f.get("commission") or 0))
                meta = f.get("meta_data") or {}
                fee = Decimal(str(meta.get("total_liquidation_fee_in_settling_asset")
                                  or meta.get("liquidation_fee_in_settling_asset") or 0))
                liquidation_fee += fee
                foreign.append({"symbol": sym, "size": int(took), "price": str(price),
                                "fill_type": f.get("fill_type"),
                                "liquidation_fee": str(fee),
                                "order_id": str(f.get("order_id") or "")})
                shortfall -= took
            if shortfall > 0:
                engine.event("pnl_unreconciled_leg", symbol=sym, missing_size=int(shortfall),
                             note="leg left the book and no matching fill was found; "
                                  "P&L understates the loss on this leg")
    except Exception as exc:
        engine.event("pnl_commission_unavailable", error_type=type(exc).__name__, error=str(exc))

    if foreign:
        engine.event("pnl_foreign_close", legs=foreign,
                     liquidation_fee_usd=usd(liquidation_fee),
                     note="leg(s) closed outside this engine -- priced from authenticated fills")

    gross = entry_credit - exit_debit
    total_cost = None if commission is None else commission + liquidation_fee
    return {
        "entry_credit_usd": usd(entry_credit),
        "exit_debit_usd": usd(exit_debit),
        "gross_usd": usd(gross),
        "commission_usd": None if commission is None else usd(commission),
        "liquidation_fee_usd": usd(liquidation_fee),
        "foreign_close": bool(foreign),
        "net_usd": None if total_cost is None else usd(gross - total_cost),
    }


def pnl_message(engine: ExecutionEngine, reason: str, pnl: dict, closed: bool,
                entry_legs: list[tuple[str, Decimal, int]]) -> str:
    label = {"time_exit": "TIME EXIT",
             "combined_50_stop": "STOP LOSS",
             "combined_50_stop_rest_recovery": "STOP LOSS (REST recovery)"}.get(reason, reason.upper())

    def money(amount: Decimal, places: str = "0.0001") -> str:
        """Magnitude only. For credit, debit and commission the direction is in
        the label, so a leading sign would just be noise."""
        return f"${Decimal(amount).quantize(Decimal(places), rounding=ROUND_HALF_UP)}"

    def signed(amount: Decimal, places: str = "0.0001") -> str:
        """Sign outside the currency symbol, and half-up so the rounded
        headline never disagrees with the exact figure below it."""
        rounded = Decimal(amount).quantize(Decimal(places), rounding=ROUND_HALF_UP)
        return f"{'-' if rounded < 0 else '+'}${abs(rounded)}"

    net = pnl["net_usd"]
    headline = "net unavailable" if net is None else f"NET {signed(net, '0.01')}"
    lines = [f"Delta {engine.settings.environment.upper()} | {label} | {headline}",
             f"Entry credit: {money(pnl['entry_credit_usd'])}",
             f"Exit debit:   {money(pnl['exit_debit_usd'])}",
             f"Gross:        {signed(pnl['gross_usd'])}"]
    if pnl["commission_usd"] is None:
        lines.append("Commission:   unavailable (fills lookup failed); gross only")
    else:
        lines.append(f"Commission:   {money(pnl['commission_usd'])}")
        lines.append(f"Net:          {signed(net)}")
    for symbol, price, size in entry_legs:
        got = engine.exit_fills.get(symbol, {})
        out = (got["notional"] / got["size"]) if got.get("size") else None
        lines.append(f"{symbol}: {size} @ {price} -> "
                     + (f"{usd(out)}" if out is not None else "not closed"))
    if not closed:
        lines.append("WARNING: exit incomplete, position may not be flat.")
    return "\n".join(lines)


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
    contract_value = Decimal(str(call_product["contract_value"]))
    if settings.environment == "production":
        wallet = next(b for b in client.balances() if b.get("asset_symbol") == "USD")
        available = Decimal(str(wallet["available_balance"]))
        base_margin = spot * contract_value * size / Decimal("200") * 2
        premium_margin = (call_quote.bid + put_quote.bid) * contract_value * size
        projected_free = available - base_margin - premium_margin - Decimal("5")
        planned_max_loss = (call_quote.bid + put_quote.bid) * Decimal("0.50") * contract_value * size + Decimal("5")
        engine.event("margin_preflight", available=available, base_margin=base_margin,
                     premium_margin=premium_margin, fee_buffer=5, projected_free=projected_free,
                     required_free=min_free_margin, planned_max_loss=planned_max_loss,
                     daily_loss_cap=25)
        # SOLVENCY IS NOT NEGOTIABLE AND permissive_entry DOES NOT APPLY TO IT.
        #
        # permissive_entry exists to let MARKET-QUALITY gates through -- a wider spread
        # than ideal, a credit ratio below target. Those are judgements about whether a
        # trade is attractive. Margin is not a judgement: if free margin cannot carry an
        # ordinary adverse move, the exchange closes the position for you.
        #
        # On 20 Aug 2026 this check computed projected_free = $13.25 against a $30
        # requirement and, because permissive_entry was on, emitted a warning and entered
        # anyway. BTC then moved 0.45% in five minutes -- an unremarkable five minutes --
        # the call mark went 22.1 -> 239.9, and the exchange liquidated the leg at 232
        # with a $4.26 liquidation fee. The session lost $14.25, the worst on record, and
        # it was a sizing failure rather than a strategy one: the straddle was fine and
        # the stop was correctly placed at 226.65, it simply never got the chance.
        #
        # This was also the SECOND time permissive_entry allowed an entry free margin
        # could not support; the first, on 31 July, happened to go unpunished.
        if projected_free < min_free_margin:
            engine.event("margin_abort", projected_free=projected_free,
                         required_free=min_free_margin,
                         permissive_entry=settings.permissive_entry,
                         note="hard abort: permissive_entry does not apply to solvency")
            raise RuntimeError(
                f"NO TRADE: projected free margin {projected_free} below {min_free_margin}"
                f" (permissive_entry={settings.permissive_entry} does not override this)")
        if planned_max_loss > Decimal("25"):
            engine.event("risk_budget_abort", planned_max_loss=planned_max_loss,
                         daily_loss_cap=25,
                         permissive_entry=settings.permissive_entry,
                         note="hard abort: permissive_entry does not apply to the loss cap")
            raise RuntimeError(
                f"NO TRADE: planned max loss {planned_max_loss} exceeds 25"
                f" (permissive_entry={settings.permissive_entry} does not override this)")
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

    # Can this structure pay its own round-trip commission out of decay?
    #
    # Recorded every session, gating nothing. Across 23 completed sessions the
    # correlation between entry credit and net outcome is +0.06 (-0.05 once
    # normalised by credit) - entry credit does NOT predict the result, and a
    # floor at 50 points would have blocked four sessions worth +$4.02 of the
    # book's +$12.09 lifetime net. So the warning is on fee COVERAGE, not on
    # credit: extrinsic is the only part of the credit that decays, and it has
    # to clear the 7.93% hurdle before any of the trade is edge rather than a
    # directional bet. Three flagged sessions and this gets revisited on data.
    try:
        combined_bid = call_quote.bid + put_quote.bid
        extrinsic = combined_bid - abs(spot - Decimal(str(call_row.get("strike_price") or 0)))
        hurdle = combined_bid * FEE_HURDLE_PCT_OF_CREDIT
        coverage = (extrinsic / hurdle) if hurdle > 0 else Decimal(0)
        thin = coverage < MIN_FEE_COVERAGE_WARN
        engine.event("fee_coverage_warning" if thin else "fee_coverage",
                     combined_credit=combined_bid, fee_rate=FEE_RATE,
                     hurdle_pct_of_credit=FEE_HURDLE_PCT_OF_CREDIT * 100,
                     hurdle_points=hurdle, extrinsic_points=extrinsic,
                     coverage_ratio=coverage, warn_below=MIN_FEE_COVERAGE_WARN,
                     mode="telemetry_only",
                     note="extrinsic is the only part of the credit that decays; "
                          "below 1.0 the trade cannot cover commission from decay "
                          "at all and any profit must come from a directional move")
    except Exception as exc:
        engine.event("fee_coverage_failed", error_type=type(exc).__name__, error=str(exc))

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
    # Realised P&L on every exit, stop or time alike. Reported after the close
    # so a slow fills lookup cannot delay risk reduction, and wrapped because a
    # reporting failure must never change the session's exit status.
    try:
        entry_legs = [leg for leg in
                      ((call_symbol, call_fill, call_size), (put_symbol, put_fill, put_size))
                      if leg[2] > 0]
        pnl = summarise_pnl(engine, contract_value, entry_legs)
        engine.event("session_pnl", reason=reason, exit_complete=ok, **pnl)
        engine.notify(pnl_message(engine, reason, pnl, ok, entry_legs))
    except Exception as exc:
        engine.event("session_pnl_failed", error_type=type(exc).__name__, error=str(exc))
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
                engine.record_order_id(order.get("id"))
                if got:
                    engine.record_exit_fill(symbol, got, price)
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
