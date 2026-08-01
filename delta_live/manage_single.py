from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .config import Settings
from .engine import ExecutionEngine, State, StrategyConfig
from .liquidity import Quote
from .session import IST, QuoteBook, close_positions
from .stream import PublicQuoteStream


def remaining_short_leg(client, asset: str) -> tuple[dict, int]:
    positions = [row for row in client.positions(asset) if int(row.get("size") or 0) != 0]
    if len(positions) != 1:
        raise RuntimeError(f"Expected exactly one remaining {asset} option leg; found {len(positions)}")
    position = positions[0]
    symbol = str(position.get("product_symbol") or "")
    size = int(position.get("size") or 0)
    if size >= 0 or not symbol.startswith(("C-", "P-")):
        raise RuntimeError("Expected one remaining short call or put")
    return position, abs(size)


def run_manager(asset: str, until: str, campaign_credit: Decimal,
                closed_leg_exit: Decimal, stop_multiple: Decimal,
                env_file: Path, allow_production: bool = False) -> int:
    settings = Settings.load(env_file)
    settings.assert_order_mode(allow_production=allow_production)
    if allow_production:
        os.environ["DELTA_PRODUCTION_ORDER_MODE"] = "1"
    if stop_multiple <= Decimal("1") or campaign_credit <= 0 or closed_leg_exit < 0:
        raise RuntimeError("Invalid campaign credit, closed-leg exit, or stop multiple")

    strategy = StrategyConfig(asset=asset, persistence_ticks=2)
    engine = ExecutionEngine(settings, strategy)
    client = engine.client
    if client.active_orders():
        raise RuntimeError("Refusing to adopt the remaining leg while active orders exist")
    position, size = remaining_short_leg(client, asset)
    symbol = str(position["product_symbol"])
    product = client.product(symbol)
    stop_level = campaign_credit * stop_multiple - closed_leg_exit
    if stop_level <= 0:
        raise RuntimeError("Computed remaining-leg stop is not positive")

    deadline = datetime.combine(
        datetime.now(IST).date(), datetime.strptime(until, "%H:%M:%S").time(), IST)
    engine.strategy = StrategyConfig(asset=asset, size=size, persistence_ticks=2)
    engine.state = State.OPEN
    engine.event("adopted_position", asset=asset, remaining_symbol=symbol, size=size,
                 campaign_entry_credit=campaign_credit, closed_leg_exit=closed_leg_exit,
                 remaining_leg_stop=stop_level, forced_exit=deadline.isoformat())
    engine.event("stop_armed", trigger="remaining_leg_executable_ask", level=stop_level,
                 persistence_ticks=engine.strategy.persistence_ticks)

    book = QuoteBook()
    stream = PublicQuoteStream(settings.public_ws_url, [symbol], book.update)
    stream.start()
    reason = "time_exit"
    hits = 0
    next_reconcile = time.monotonic()
    try:
        while datetime.now(IST) < deadline:
            with book.lock:
                quote = book.rows.get(symbol)
            if quote is None or stream.last_message_age > 3:
                quote = Quote.from_ticker(client.ticker(symbol))
                engine.event("rest_recovery_tick", stream_age=round(stream.last_message_age, 2))
            hits = hits + 1 if quote.ask >= stop_level else 0
            engine.event("risk_tick_single", symbol=symbol, executable_buyback=quote.ask,
                         stop_level=stop_level, consecutive_hits=hits, mark=quote.mark,
                         monitored_size=size)
            if hits >= engine.strategy.persistence_ticks:
                reason = "remaining_leg_stop"
                engine.event("stop_triggered", symbol=symbol, executable_buyback=quote.ask,
                             stop_level=stop_level, size=size)
                break

            if time.monotonic() >= next_reconcile:
                current = [p for p in client.positions(asset) if int(p.get("size") or 0) != 0]
                if not current:
                    reason = "position_closed_externally"
                    break
                if len(current) != 1 or str(current[0].get("product_symbol")) != symbol:
                    raise RuntimeError("Remaining-leg structure changed while monitoring")
                size = abs(int(current[0]["size"]))
                next_reconcile = time.monotonic() + 5
            time.sleep(engine.strategy.poll_seconds)

        engine.state = State.EXITING
        engine.event("exit_start", reason=reason, monitored_size=size)
        current = [p for p in client.positions(asset) if int(p.get("size") or 0) != 0]
        legs = []
        for row in current:
            if str(row.get("product_symbol")) == symbol and int(row.get("size") or 0) < 0:
                live_size = abs(int(row["size"]))
                legs.append((product, symbol, live_size))
        ok = close_positions(engine, legs) if legs else True
        remaining = [p for p in client.positions(asset) if int(p.get("size") or 0) != 0]
        ok = ok and not remaining
    finally:
        stream.close()

    engine.state = State.CLOSED if ok else State.HALTED
    engine.event("closed" if ok else "halted", reason=reason if ok else "exit_incomplete")
    return 0 if ok else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage one remaining short option leg")
    parser.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    parser.add_argument("--until", default="17:24:30")
    parser.add_argument("--campaign-entry-credit", type=Decimal, required=True)
    parser.add_argument("--closed-leg-exit-price", type=Decimal, required=True)
    parser.add_argument("--stop-multiple", type=Decimal, default=Decimal("1.5"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--confirm-production-orders", action="store_true")
    args = parser.parse_args()
    settings = Settings.load(args.env_file)
    if args.check_only:
        from .client import DeltaRESTClient
        position, size = remaining_short_leg(DeltaRESTClient(settings), args.asset)
        stop = args.campaign_entry_credit * args.stop_multiple - args.closed_leg_exit_price
        print(f"validated remaining short: {position['product_symbol']} size={size}")
        print(f"remaining_leg_stop={stop} forced_exit={args.until} IST")
        return 0
    if settings.environment == "production" and not args.confirm_production_orders:
        raise SystemExit("Add --confirm-production-orders to authorize production exits")
    return run_manager(args.asset, args.until, args.campaign_entry_credit,
                       args.closed_leg_exit_price, args.stop_multiple, args.env_file,
                       allow_production=args.confirm_production_orders)


if __name__ == "__main__":
    raise SystemExit(main())
