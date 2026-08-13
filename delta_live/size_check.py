"""Largest straddle size the account can actually margin, capped at a target.

Sizing has failed three times in three days because the requirement was checked
against base margin alone. The exchange needs base PLUS premium margin inside
available balance, and premium margin scales with the day's credit - so the same
size can fit one day and be rejected the next on an identical balance.

This computes the size that fits and clamps the configured target down to it.
It can only ever reduce size, never raise it above the cap, so the worst case is
a smaller position than intended rather than a failed or partially filled entry.

    ./.venv/bin/python -m delta_live.size_check --asset BTC --target 150
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

CONTRACT_VALUE = Decimal("0.001")
LEVERAGE = Decimal("200")
FEE_BUFFER = Decimal("5")
# Leave a little room so a quote moving between this check and the order does
# not turn a just-fits size into a rejection.
SAFETY = Decimal("0.95")
# Keep sizes on a round increment so slice arithmetic stays clean.
INCREMENT = 25
# Below this the position is too small to be worth the fixed commission load.
MINIMUM = 50


def feasible_size(available: Decimal, spot: Decimal, credit: Decimal, *,
                  target: int, contract_value: Decimal = CONTRACT_VALUE,
                  leverage: Decimal = LEVERAGE, fee_buffer: Decimal = FEE_BUFFER,
                  safety: Decimal = SAFETY, increment: int = INCREMENT,
                  minimum: int = MINIMUM) -> int:
    """Size that fits the margin budget, rounded down and capped at `target`.

    Returns 0 when even `minimum` cannot be afforded, which the caller must
    treat as NO TRADE rather than as a small position.
    """
    if spot <= 0 or target <= 0:
        return 0
    base_per_contract = spot * contract_value / leverage * 2
    premium_per_contract = credit * contract_value
    per_contract = base_per_contract + premium_per_contract
    if per_contract <= 0:
        return 0
    budget = (available - fee_buffer) * safety
    if budget <= 0:
        return 0
    raw = int(budget / per_contract)
    size = min(target, (raw // increment) * increment)
    return size if size >= minimum else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Largest affordable straddle size, capped at target")
    p.add_argument("--asset", default="BTC")
    p.add_argument("--target", type=int, default=150)
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    args = p.parse_args()

    from .config import Settings
    from .client import DeltaRESTClient
    from .session import nearest_chain

    settings = Settings.load(args.env_file)
    client = DeltaRESTClient(settings)

    usd = next(b for b in client.balances() if b.get("asset_symbol") == "USD")
    available = Decimal(str(usd["available_balance"]))

    _, chain = nearest_chain(client, args.asset)
    spot = Decimal(str(next(r["spot_price"] for r in chain if r.get("spot_price"))))
    by_strike: dict[Decimal, dict] = {}
    for row in chain:
        strike = Decimal(str(row.get("strike_price") or 0))
        by_strike.setdefault(strike, {})[row.get("contract_type")] = row
    complete = [k for k, v in by_strike.items() if len(v) == 2]
    if not complete:
        print("SIZE=0 reason=no_complete_strike", file=sys.stderr)
        print(0)
        return 0
    strike = min(complete, key=lambda k: abs(k - spot))
    legs = by_strike[strike]
    credit = sum(Decimal(str((legs[side].get("quotes") or {}).get("best_bid") or 0))
                 for side in ("call_options", "put_options"))

    size = feasible_size(available, spot, credit, target=args.target)
    base = spot * CONTRACT_VALUE / LEVERAGE * 2 * (size or 1)
    prem = credit * CONTRACT_VALUE * (size or 1)
    print(f"size_check asset={args.asset} target={args.target} chosen={size} "
          f"available={available:.2f} spot={spot} strike={strike} credit={credit} "
          f"required_at_chosen={(base + prem):.2f}", file=sys.stderr)
    print(size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
