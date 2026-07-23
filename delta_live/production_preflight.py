from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from .client import DeltaRESTClient
from .config import Settings
from .engine import ExecutionEngine, StrategyConfig
from .liquidity import Quote, entry_gate
from .session import nearest_chain
from .telegram import TelegramNotifier


def run_preflight(asset: str, size: int, env_file: Path,
                  min_free_margin: Decimal = Decimal("30"),
                  max_loss: Decimal = Decimal("25")) -> int:
    settings = Settings.load(env_file)
    if settings.environment != "production":
        raise RuntimeError("Production preflight requires DELTA_ENV=production")
    client = DeltaRESTClient(settings)
    if client.active_orders():
        raise RuntimeError("NO TRADE: active orders already exist")
    if client.positions(asset):
        raise RuntimeError(f"NO TRADE: open {asset} positions already exist")

    expiry, chain = nearest_chain(client, asset)
    spot = Decimal(str(next(r["spot_price"] for r in chain if r.get("spot_price"))))
    engine = ExecutionEngine(settings, StrategyConfig(asset=asset, size=size))
    call_row, put_row = engine.select_atm(chain, spot)
    call_product = client.product(call_row["symbol"])
    put_product = client.product(put_row["symbol"])
    for product in (call_product, put_product):
        leverage = Decimal(str(client.order_leverage(int(product["id"]))["leverage"]))
        if leverage != Decimal("200"):
            raise RuntimeError(f"NO TRADE: {product['symbol']} leverage is {leverage}, not 200")

    call_quote, put_quote = Quote.from_ticker(call_row), Quote.from_ticker(put_row)
    gate = entry_gate(call_quote, put_quote, size, Decimal("0.15"), Decimal("0.95"))
    integrity_failure = gate.reason in {"invalid_size", "missing_two_sided_quote"}
    if not gate.allowed and (not settings.permissive_entry or integrity_failure):
        raise RuntimeError(f"NO TRADE: liquidity gate {gate.reason}; supported={gate.supported_size}")
    usd = next(b for b in client.balances() if b.get("asset_symbol") == "USD")
    available = Decimal(str(usd["available_balance"]))
    contract_value = Decimal(str(call_product["contract_value"]))
    base_margin = spot * contract_value * size / Decimal("200") * 2
    premium_margin = (call_quote.bid + put_quote.bid) * contract_value * size
    projected_free = available - base_margin - premium_margin - Decimal("5")
    planned_loss = (call_quote.bid + put_quote.bid) * Decimal("0.50") * contract_value * size + Decimal("5")
    if projected_free < min_free_margin and not settings.permissive_entry:
        raise RuntimeError(f"NO TRADE: projected free margin {projected_free} below {min_free_margin}")
    if planned_loss > max_loss and not settings.permissive_entry:
        raise RuntimeError(f"NO TRADE: planned loss {planned_loss} exceeds {max_loss}")

    warnings = []
    if not gate.allowed:
        warnings.append(f"liquidity={gate.reason}")
    if projected_free < min_free_margin:
        warnings.append(f"projected_free={projected_free}<{min_free_margin}")
    if planned_loss > max_loss:
        warnings.append(f"planned_loss={planned_loss}>{max_loss}")

    message = (f"Delta BTC production preflight PASS\nexpiry: {expiry}\n"
               f"ATM: {call_row['strike_price']}\nsize: {size} per leg\n"
               f"supported depth: {gate.supported_size}\n"
               f"mode: {'PERMISSIVE / warnings only' if settings.permissive_entry else 'GATED'}\n"
               f"warnings: {', '.join(warnings) if warnings else 'none'}\n"
               "entry remains scheduled for 17:00 IST")
    if not TelegramNotifier(settings.telegram_token, settings.telegram_chat_id).send(message):
        if settings.permissive_entry:
            print("WARNING Telegram preflight notification failed; execution remains scheduled")
        else:
            raise RuntimeError("NO TRADE: Telegram preflight notification failed")
    print(f"PASS expiry={expiry} strike={call_row['strike_price']} size={size} "
          f"supported={gate.supported_size} permissive={settings.permissive_entry} "
          f"warnings={warnings}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only production readiness check")
    parser.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    return run_preflight(args.asset, args.size, args.env_file)


if __name__ == "__main__":
    raise SystemExit(main())
