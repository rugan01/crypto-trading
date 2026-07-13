from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .config import Settings
from .client import DeltaRESTClient
from .engine import ExecutionEngine, State, StrategyConfig
from .liquidity import Quote
from .session import IST, QuoteBook, close_positions
from .stream import PublicQuoteStream


def open_short_straddle(client, asset: str) -> tuple[dict, dict, int, Decimal]:
    positions = [row for row in client.positions(asset) if int(row.get("size") or 0) != 0]
    if len(positions) != 2:
        raise RuntimeError(f"Expected exactly two open {asset} option legs; found {len(positions)}")

    by_kind: dict[str, dict] = {}
    for row in positions:
        symbol = str(row.get("product_symbol") or "")
        size = int(row.get("size") or 0)
        if size >= 0 or not symbol.startswith(("C-", "P-")):
            raise RuntimeError("Expected exactly one short call and one short put")
        by_kind[symbol[0]] = row
    if set(by_kind) != {"C", "P"}:
        raise RuntimeError("Expected exactly one short call and one short put")

    call, put = by_kind["C"], by_kind["P"]
    call_parts = str(call["product_symbol"]).split("-")
    put_parts = str(put["product_symbol"]).split("-")
    if call_parts[1:] != put_parts[1:]:
        raise RuntimeError("Open call and put do not share the same asset, strike, and expiry")
    call_size, put_size = abs(int(call["size"])), abs(int(put["size"]))
    if call_size != put_size:
        raise RuntimeError(f"Open call/put sizes differ: {call_size} versus {put_size}")
    entry_credit = Decimal(str(call.get("entry_price") or 0)) + Decimal(str(put.get("entry_price") or 0))
    if entry_credit <= 0:
        raise RuntimeError("Broker positions do not contain valid entry prices")
    return call, put, call_size, entry_credit


def run_manager(asset: str, until: str, stop_multiple: Decimal, env_file: Path,
                allow_production: bool = False) -> int:
    settings = Settings.load(env_file)
    settings.assert_order_mode(allow_production=allow_production)
    if allow_production:
        os.environ["DELTA_PRODUCTION_ORDER_MODE"] = "1"
    if stop_multiple <= Decimal("1"):
        raise RuntimeError("Stop multiple must be greater than 1")

    strategy = StrategyConfig(asset=asset, persistence_ticks=2)
    engine = ExecutionEngine(settings, strategy)
    client = engine.client
    if client.active_orders():
        raise RuntimeError("Refusing to adopt positions while active orders exist")
    call_pos, put_pos, size, entry_credit = open_short_straddle(client, asset)
    call_symbol = str(call_pos["product_symbol"])
    put_symbol = str(put_pos["product_symbol"])
    call_product = client.product(call_symbol)
    put_product = client.product(put_symbol)

    target_time = datetime.strptime(until, "%H:%M:%S").time()
    deadline = datetime.combine(datetime.now(IST).date(), target_time, IST)
    engine.strategy = StrategyConfig(asset=asset, size=size, persistence_ticks=2,
                                     stop_pct=stop_multiple - Decimal("1"))
    engine.entry_credit = entry_credit
    engine.state = State.OPEN
    engine.event("adopted_position", asset=asset, call=call_symbol, put=put_symbol,
                 size=size, combined_entry_credit=entry_credit, stop_level=engine.stop_level,
                 forced_exit=deadline.isoformat())
    engine.event("stop_armed", trigger="executable_combined_ask", level=engine.stop_level,
                 persistence_ticks=engine.strategy.persistence_ticks)

    book = QuoteBook()
    stream = PublicQuoteStream(settings.public_ws_url, [call_symbol, put_symbol], book.update)
    stream.start()
    reason = "time_exit"
    next_reconcile = time.monotonic()
    try:
        while datetime.now(IST) < deadline:
            pair = book.pair(call_symbol, put_symbol)
            if pair and stream.last_message_age <= 3:
                call_quote, put_quote = pair
            else:
                call_quote = Quote.from_ticker(client.ticker(call_symbol))
                put_quote = Quote.from_ticker(client.ticker(put_symbol))
                engine.event("rest_recovery_tick", stream_age=round(stream.last_message_age, 2))
            if engine.observe_stop(call_quote, put_quote):
                reason = "combined_premium_stop"
                break

            if time.monotonic() >= next_reconcile:
                current = [p for p in client.positions(asset) if int(p.get("size") or 0) != 0]
                current_symbols = {str(p.get("product_symbol")) for p in current}
                if current_symbols != {call_symbol, put_symbol}:
                    reason = "structure_changed"
                    engine.event("halted", reason=reason, open_symbols=sorted(current_symbols))
                    break
                next_reconcile = time.monotonic() + 10
            time.sleep(engine.strategy.poll_seconds)
    finally:
        stream.close()

    engine.state = State.EXITING
    engine.event("exit_start", reason=reason)
    current = {str(p.get("product_symbol")): abs(int(p.get("size") or 0))
               for p in client.positions(asset) if int(p.get("size") or 0) != 0}
    legs = []
    if current.get(call_symbol):
        legs.append((call_product, call_symbol, current[call_symbol]))
    if current.get(put_symbol):
        legs.append((put_product, put_symbol, current[put_symbol]))
    ok = close_positions(engine, legs)
    remaining = [p for p in client.positions(asset) if int(p.get("size") or 0) != 0]
    ok = ok and not remaining
    engine.state = State.CLOSED if ok else State.HALTED
    engine.event("closed" if ok else "halted", reason=reason if ok else "exit_incomplete")
    return 0 if ok else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Adopt and manage an existing short option straddle")
    parser.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    parser.add_argument("--until", default="17:25:00", help="Forced-exit time in IST")
    parser.add_argument("--stop-multiple", type=Decimal, default=Decimal("1.5"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--check-only", action="store_true",
                        help="Validate the open structure without monitoring or placing exits")
    parser.add_argument("--confirm-production-orders", action="store_true")
    args = parser.parse_args()
    settings = Settings.load(args.env_file)
    if args.check_only:
        call, put, size, credit = open_short_straddle(DeltaRESTClient(settings), args.asset)
        stop = credit * args.stop_multiple
        print(f"validated {args.asset} short straddle: {size} per leg")
        print(f"call={call['product_symbol']} put={put['product_symbol']}")
        print(f"combined_entry_credit={credit} stop_level={stop} forced_exit={args.until} IST")
        return 0
    if settings.environment == "production" and not args.confirm_production_orders:
        raise SystemExit("Add --confirm-production-orders to authorize production exits")
    return run_manager(args.asset, args.until, args.stop_multiple, args.env_file,
                       allow_production=args.confirm_production_orders)


if __name__ == "__main__":
    raise SystemExit(main())
