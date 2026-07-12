from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import DeltaRESTClient
from .config import Settings
from .telegram import TelegramNotifier

IST = ZoneInfo("Asia/Kolkata")


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only live Delta position monitor")
    p.add_argument("--asset", choices=["BTC", "ETH"], default="BTC")
    p.add_argument("--until", default="17:30:00")
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--log", type=Path, default=Path("outputs/live/monitor-20260712.jsonl"))
    args = p.parse_args()
    settings = Settings.load(); client = DeltaRESTClient(settings)
    notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(IST).date()
    until = datetime.combine(today, datetime.strptime(args.until, "%H:%M:%S").time(), IST)
    stop_sent = False; started = False; flat_sent = False
    campaign_symbols: set[str] = set(); campaign_entry: Decimal | None = None

    def record(event: str, **fields: object) -> None:
        row = {"time": datetime.now(IST).isoformat(), "event": event, **fields}
        with args.log.open("a") as f: f.write(json.dumps(row, default=str, sort_keys=True) + "\n")

    record("monitor_started", asset=args.asset, until=until.isoformat(), interval=args.interval)
    notifier.send(f"Delta live monitor active (read-only) for {args.asset} until {args.until} IST.")
    while datetime.now(IST) < until:
        positions = client.positions(args.asset)
        if not positions:
            if started and not flat_sent:
                record("flat", reason="positions_empty"); notifier.send(f"Delta {args.asset}: account is flat; monitor stopped."); flat_sent = True
                break
            record("no_position")
            time.sleep(args.interval); continue
        started = True
        current_symbols = {x["product_symbol"] for x in positions}
        if not campaign_symbols:
            campaign_symbols = current_symbols
            campaign_entry = sum(Decimal(str(x["entry_price"])) for x in positions)
        elif current_symbols != campaign_symbols:
            record("structure_changed", original_symbols=sorted(campaign_symbols), current_symbols=sorted(current_symbols))
            notifier.send(f"URGENT Delta {args.asset}: campaign structure changed. Monitor is read-only; review the remaining leg manually.")
            campaign_symbols = current_symbols
        entry = campaign_entry if len(current_symbols) == len(campaign_symbols) and campaign_entry is not None else None
        quotes = [client.ticker(x["product_symbol"]) for x in positions]
        asks = [Decimal(str((q.get("quotes") or {}).get("best_ask") or 0)) for q in quotes]
        buyback = sum(asks); stop = entry * Decimal("1.5") if entry is not None else None
        gross = (entry - buyback) * Decimal("0.001") * sum(abs(int(x["size"])) for x in positions) / 2 if entry is not None else None
        record("risk_tick", symbols=[x["product_symbol"] for x in positions], sizes=[x["size"] for x in positions],
               entry_credit=entry, campaign_entry=campaign_entry, asks=asks, executable_buyback=buyback, stop_level=stop, gross_pnl=gross)
        if stop is not None and buyback >= stop and not stop_sent:
            notifier.send(f"URGENT Delta {args.asset}: executable combined buyback {buyback} >= stop {stop}. No order was sent by this monitor.")
            record("stop_proximity_alert", buyback=buyback, stop=stop); stop_sent = True
        time.sleep(args.interval)
    record("monitor_stopped", reason="until_time" if not flat_sent else "flat")
    return 0


if __name__ == "__main__": raise SystemExit(main())
