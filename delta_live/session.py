from __future__ import annotations

import argparse
import json
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


def nearest_chain(client: DeltaRESTClient, asset: str) -> tuple[str, list[dict]]:
    now = datetime.now(IST)
    for offset in range(8):
        expiry = (now + timedelta(days=offset)).strftime("%d-%m-%Y")
        chain = client.option_chain(asset, expiry)
        if chain:
            return expiry, chain
    raise RuntimeError("No testnet option expiry found in the next seven days")


def run_session(asset: str, size: int, minutes: int, env_file: Path,
                allow_production: bool = False, min_free_margin: Decimal = Decimal("30")) -> int:
    settings = Settings.load(env_file)
    settings.assert_order_mode(allow_production=allow_production)
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
    tolerance = Decimal("0.98") if settings.environment == "production" else Decimal("0.95")
    call_limit = call_quote.bid * tolerance
    put_limit = put_quote.bid * tolerance
    engine.event("entry_intent", call_bid=call_quote.bid, put_bid=put_quote.bid,
                 call_limit=call_limit, put_limit=put_limit)
    call_order = client.place_order(engine.order_payload(call_product, "sell", size, call_limit, "ce", False))
    put_order = client.place_order(engine.order_payload(put_product, "sell", size, put_limit, "pe", False))
    call_size, call_fill = filled(call_order)
    put_size, put_fill = filled(put_order)
    engine.event("entry_orders", call_order=call_order.get("id"), put_order=put_order.get("id"),
                 call_filled=call_size, put_filled=put_size)

    if call_size != size or put_size != size:
        engine.halt("partial_or_unmatched_entry")
        close_positions(engine, [(call_product, call_symbol, call_size),
                                 (put_product, put_symbol, put_size)])
        return 2
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
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--confirm-sandbox-orders", action="store_true")
    p.add_argument("--confirm-production-orders", action="store_true")
    args = p.parse_args()
    settings = Settings.load(args.env_file)
    if settings.environment == "production" and not args.confirm_production_orders:
        raise SystemExit("Add --confirm-production-orders to authorize live orders")
    if settings.environment == "testnet" and not args.confirm_sandbox_orders:
        raise SystemExit("Add --confirm-sandbox-orders to authorize demo orders")
    if args.size < 1 or args.minutes < 1 or args.minutes > 30:
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
                       allow_production=args.confirm_production_orders)


if __name__ == "__main__":
    raise SystemExit(main())
