from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import Settings
from .engine import ExecutionEngine, State, StrategyConfig
from .session import close_positions


def flatten(asset: str, env_file: Path, allow_production: bool = False) -> int:
    settings = Settings.load(env_file)
    settings.assert_order_mode(allow_production=allow_production)
    if allow_production:
        os.environ["DELTA_PRODUCTION_ORDER_MODE"] = "1"
    engine = ExecutionEngine(settings, StrategyConfig(asset=asset))
    engine.state = State.EXITING
    client = engine.client
    positions = [p for p in client.positions(asset) if int(p.get("size") or 0) != 0]
    if not positions:
        engine.state = State.CLOSED
        engine.event("closed", reason="emergency_reconcile_already_flat")
        return 0
    if client.active_orders():
        engine.event("halted", reason="emergency_flatten_active_orders_present")
        return 3

    legs = []
    for position in positions:
        size = int(position.get("size") or 0)
        symbol = str(position.get("product_symbol") or "")
        if size >= 0 or not symbol:
            engine.event("halted", reason="emergency_flatten_unexpected_position",
                         symbol=symbol, size=size)
            return 3
        legs.append((client.product(symbol), symbol, abs(size)))
    engine.event("emergency_flatten_start", asset=asset, legs=len(legs))
    ok = close_positions(engine, legs)
    remaining = [p for p in client.positions(asset) if int(p.get("size") or 0) != 0]
    ok = ok and not remaining
    engine.state = State.CLOSED if ok else State.HALTED
    engine.event("closed" if ok else "halted",
                 reason="emergency_flatten_complete" if ok else "emergency_flatten_incomplete")
    return 0 if ok else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Emergency flatten of open short option positions")
    parser.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--confirm-production-orders", action="store_true")
    args = parser.parse_args()
    settings = Settings.load(args.env_file)
    if settings.environment == "production" and not args.confirm_production_orders:
        raise SystemExit("Add --confirm-production-orders to authorize production exits")
    return flatten(args.asset, args.env_file, allow_production=args.confirm_production_orders)


if __name__ == "__main__":
    raise SystemExit(main())
