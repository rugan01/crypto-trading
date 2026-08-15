from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import DeltaRESTClient
from .config import Settings
from .engine import ExecutionEngine, StrategyConfig

IST = ZoneInfo("Asia/Kolkata")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Delta 0DTE sandbox execution engine")
    p.add_argument("command", choices=["doctor", "discover", "dry-run", "telegram-test",
                                       "telegram-notify", "alert"])
    p.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    p.add_argument("--size", type=int, default=200)
    p.add_argument("--expiry", help="DD-MM-YYYY; default is today or next day")
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--message", help="Message for telegram-notify / alert")
    p.add_argument("--level", default="INFO",
                   help="alert severity: INFO, NO_TRADE, ERROR. Non-INFO also raises a "
                        "desktop notification, which does not need the network.")
    return p


def main() -> int:
    args = parser().parse_args()
    settings = Settings.load(args.env_file)
    client = DeltaRESTClient(settings)
    if args.command == "alert":
        # Multi-channel. The local channels survive a network outage, which is
        # the case that matters: on 15 Aug the only alert path shared its
        # failure mode with the thing it was reporting.
        from .alerting import alert as fan_out
        if not args.message:
            raise SystemExit("--message is required for alert")
        result = fan_out(settings, args.message, level=args.level)
        print(result.summary())
        return 0 if result.delivered else 2
    if args.command in {"telegram-test", "telegram-notify"}:
        from .telegram import Delivery, TelegramNotifier
        message = (args.message if args.command == "telegram-notify" else
                   "Delta 0DTE engine test: Telegram notifications are working (no order placed).")
        if not message:
            raise SystemExit("--message is required for telegram-notify")
        outcome = TelegramNotifier(settings.telegram_token,
                                   settings.telegram_chat_id).deliver(message)
        print({Delivery.SENT: "Telegram message sent",
               Delivery.NOT_CONFIGURED: "Telegram is not configured "
                                        "(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)",
               Delivery.FAILED: "Telegram SEND FAILED - credentials are present but "
                                "api.telegram.org could not be reached (network/DNS)"}[outcome])
        return 0 if outcome is Delivery.SENT else 2
    if args.command == "doctor":
        print(json.dumps({"environment": settings.environment, "dry_run": settings.dry_run,
                          "rest_url": settings.rest_url, "public_ws_url": settings.public_ws_url,
                          "api_credentials": bool(settings.api_key and settings.api_secret),
                          "telegram": bool(settings.telegram_token and settings.telegram_chat_id)}, indent=2))
        return 0
    expiry = args.expiry or datetime.now(IST).strftime("%d-%m-%Y")
    chain = client.option_chain(args.asset, expiry)
    if not chain and not args.expiry:
        for offset in range(1, 8):
            expiry = (datetime.now(IST) + timedelta(days=offset)).strftime("%d-%m-%Y")
            chain = client.option_chain(args.asset, expiry)
            if chain:
                break
    if not chain:
        print(f"No {args.asset} options found on testnet for {expiry}", file=sys.stderr)
        return 2
    spot = Decimal(str(next((r.get("spot_price") for r in chain if r.get("spot_price")), 0)))
    engine = ExecutionEngine(settings, StrategyConfig(asset=args.asset, size=args.size))
    call, put = engine.select_atm(chain, spot)
    if args.command == "discover":
        print(json.dumps({"expiry": expiry, "spot": str(spot), "call": call["symbol"],
                          "put": put["symbol"], "contracts": len(chain)}, indent=2))
        return 0
    engine.preflight(call, put)
    print(json.dumps({"status": "DRY_RUN_READY", "environment": settings.environment,
                      "expiry": expiry, "call": call["symbol"], "put": put["symbol"],
                      "size": args.size}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
