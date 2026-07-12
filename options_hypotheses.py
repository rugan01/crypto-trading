#!/usr/bin/env python3
"""Research runner for three non-straddle option hypotheses.

This is intentionally separate from delta_0dte.py. Rules are fixed, defined-risk,
and chronological; it does not search a parameter grid.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from delta_0dte import IST, DeltaClient, ASSETS, catalog, close_map, minute_ts, parse_contract, price_at

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "hypotheses"
ASSETS = ("BTC", "ETH")
SLIP = 0.005
LOTS = 200


@dataclass(frozen=True)
class Leg:
    symbol: str
    side: str  # buy or sell
    price: float
    contract_value: float
    strike: float


@dataclass
class Result:
    strategy: str
    asset: str
    signal_date: str
    entry_time: str
    target_expiry: str
    direction: str
    legs: str
    entry_debit_credit: float
    exit_value: float
    gross_pnl: float
    fees: float
    net_pnl: float
    exit_reason: str
    max_adverse: float
    max_favourable: float


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def spot_series(client: DeltaClient, asset: str, start: date, end: date) -> dict[int, float]:
    """Fetch spot in one-day chunks because Delta caps a candle response at 2,000 rows."""
    out: dict[int, float] = {}
    day = start
    while day <= end:
        rows = client.candles(asset + "USD", minute_ts(day, 0), minute_ts(day, 23 * 60 + 59))
        out.update(close_map(rows))
        day += timedelta(days=1)
    return out


def metrics(rows: list[Result]) -> dict[str, Any]:
    pnl = [r.net_pnl for r in rows]
    wins = [x for x in pnl if x > 0]; losses = [x for x in pnl if x < 0]
    equity = peak = dd = 0.0; dd_days = 0; under = None
    for r in sorted(rows, key=lambda x: x.signal_date):
        equity += r.net_pnl
        if equity >= peak:
            if under:
                dd_days = max(dd_days, (date.fromisoformat(r.signal_date) - under).days)
            peak = equity; under = None
        elif under is None:
            under = date.fromisoformat(r.signal_date)
        dd = max(dd, peak - equity)
    mean = sum(pnl) / len(pnl) if pnl else 0
    stdev = math.sqrt(sum((x - mean) ** 2 for x in pnl) / (len(pnl) - 1)) if len(pnl) > 1 else 0
    return {"trades": len(pnl), "net_pnl": sum(pnl), "avg_trade": mean,
            "win_rate": sum(x > 0 for x in pnl) / len(pnl) if pnl else 0,
            "avg_win": sum(wins) / len(wins) if wins else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            "sharpe_annualized": mean / stdev * math.sqrt(365) if stdev else None,
            "max_drawdown": dd, "max_drawdown_days": dd_days,
            "stop_rate": sum(r.exit_reason == "stop" for r in rows) / len(rows) if rows else 0}


def strike_contracts(client: DeltaClient, asset: str, expiry: date, spot: float,
                     known: dict[tuple[str, date], dict[float, dict[str, Any]]] | None = None) -> dict[tuple[str, int], Any]:
    increment = 100 if asset == "BTC" else 10
    centre = round(spot / increment) * increment
    suffix = expiry.strftime("%d%m%y")
    out: dict[tuple[str, int], Any] = {}
    for distance in range(-5, 6):
        strike = centre + distance * increment
        if strike <= 0:
            continue
        for side, prefix in (("C", "C"), ("P", "P")):
            contract = None
            if known and (asset, expiry) in known:
                contract = known[(asset, expiry)].get(strike, {}).get(prefix)
            elif not known:
                row = client.product(f"{prefix}-{asset}-{int(strike)}-{suffix}")
                contract = parse_contract(row) if row else None
            if contract:
                out[(side, distance)] = contract
    return out


def fee(client: DeltaClient, spot: dict[int, float], leg: Leg, ts: int, lots: int, rate: float) -> float:
    sq = price_at(spot, ts, 120)
    underlying = sq[1] if sq else 0
    notional = underlying * leg.contract_value * lots * rate
    cap = leg.price * leg.contract_value * lots * 0.035
    return min(notional, cap) * 1.18


def simulate(client: DeltaClient, strategy: str, asset: str, day: date, entry_ts: int,
             expiry: date, legs: list[tuple[Any, str]], price: dict[str, dict[int, float]],
             spot: dict[int, float], lots: int, direction: str, stop_fraction: float) -> Result | None:
    entry_quotes = []
    for contract, side in legs:
        q = price_at(price[contract.symbol], entry_ts, 120)
        if not q or q[1] <= 0:
            return None
        fill = q[1] * (1 + SLIP if side == "buy" else 1 - SLIP)
        entry_quotes.append(Leg(contract.symbol, side, fill, contract.contract_value, contract.strike))
    multiplier = entry_quotes[0].contract_value * lots
    entry_cf = sum((-1 if leg.side == "buy" else 1) * leg.price for leg in entry_quotes) * multiplier
    # entry_cf is negative for a debit, positive for a credit
    target_ts = minute_ts(expiry, 17 * 60 + 25)
    mark_pnl: list[float] = []
    stop_value = abs(entry_cf) * stop_fraction
    exit_reason = "time_exit"; exit_ts = target_ts
    for ts in range(entry_ts + 60, target_ts + 1, 60):
        if not all(ts in price[leg.symbol] for leg in entry_quotes):
            continue
        exit_cf = sum((1 if leg.side == "buy" else -1) * price[leg.symbol][ts] for leg in entry_quotes) * multiplier
        pnl = entry_cf + exit_cf
        mark_pnl.append(pnl)
        if pnl <= -stop_value:
            exit_reason = "stop"; exit_ts = ts; break
        if strategy == "long_gamma" and pnl >= stop_value:
            exit_reason = "profit_target"; exit_ts = ts; break
    final = []
    for leg in entry_quotes:
        q = price_at(price[leg.symbol], exit_ts, 120)
        if not q:
            return None
        final.append(Leg(leg.symbol, leg.side, q[1] * (1 + SLIP if leg.side == "buy" else 1 - SLIP), leg.contract_value, leg.strike))
    exit_cf = sum((1 if leg.side == "buy" else -1) * leg.price for leg in final) * multiplier
    gross = entry_cf + exit_cf
    rates = [getattr(c, "taker_rate", 0.0001) for c, _ in legs]
    fees = sum(fee(client, spot, leg, entry_ts, lots, max(rates)) for leg in entry_quotes)
    fees += sum(fee(client, spot, leg, exit_ts, lots, max(rates)) for leg in final)
    return Result(strategy, asset, day.isoformat(), datetime.fromtimestamp(entry_ts, IST).isoformat(),
                  expiry.isoformat(), direction, ";".join(f"{x.side}:{x.symbol}" for x in entry_quotes),
                  abs(entry_cf), abs(exit_cf), gross, fees, gross - fees, exit_reason,
                  min(mark_pnl, default=0), max(mark_pnl, default=0))


def run(start: date, end: date, output: Path, lots: int, slippage_bps: float) -> None:
    global SLIP
    SLIP = slippage_bps / 10_000
    client = DeltaClient(); rows: list[Result] = []; skips: dict[str, int] = {}
    known = catalog(client.expired_options(max(30, (datetime.now(IST).date() - start).days + 30)),
                    max(30, (datetime.now(IST).date() - start).days + 30))
    for asset in ASSETS:
        # Fetch one continuous spot series per asset. Refetching overlapping
        # multi-day windows for every signal date makes positional research
        # needlessly slow and can amplify transient API failures.
        spot_all = spot_series(client, asset, start - timedelta(days=1), end + timedelta(days=2))
        day = start
        while day <= end:
            # Signals are computed from spot candles available before entry.
            spot = spot_all
            entry_ts = minute_ts(day, 15 * 60)
            eq = price_at(spot, entry_ts, 120)
            if not eq:
                day += timedelta(days=1); continue
            s = eq[1]
            prior = [v for ts, v in sorted(spot.items()) if entry_ts - 240 * 60 <= ts < entry_ts]
            last60 = [v for ts, v in sorted(spot.items()) if entry_ts - 60 * 60 <= ts < entry_ts]
            prior60 = [v for ts, v in sorted(spot.items()) if entry_ts - 120 * 60 <= ts < entry_ts - 60 * 60]
            prevday = [v for ts, v in sorted(spot.items()) if minute_ts(day - timedelta(days=1), 0) <= ts < minute_ts(day, 0)]
            if len(prior) < 20 or len(last60) < 20:
                day += timedelta(days=1); continue
            path60 = sum(abs(b-a) for a,b in zip(last60, last60[1:]))
            efficiency = abs(s - last60[0]) / path60 if path60 else 0
            trend_up = s > max(prior60 or prior) * 1.0005 and efficiency > .35
            trend_down = s < min(prior60 or prior) * .9995 and efficiency > .35
            compression = (max(last60) - min(last60)) / s < .006 and (max(prior60 or prior) - min(prior60 or prior)) / s < .008
            prior_high, prior_low = (max(prevday), min(prevday)) if prevday else (None, None)
            # Fixed intraday liquidity-sweep definition: the previous hour's
            # range is breached, then spot closes back inside that range.
            break_high = max(prior60 or prior)
            break_low = min(prior60 or prior)
            sweep_up = s < break_high and max(last60) > break_high * 1.0005
            sweep_down = s > break_low and min(last60) < break_low * .9995
            signals = []
            if trend_up or trend_down: signals.append(("trend_vertical", "up" if trend_up else "down", day + timedelta(days=2)))
            if sweep_up or sweep_down: signals.append(("failed_breakout", "up_rejection" if sweep_up else "down_rejection", day + timedelta(days=1)))
            if compression: signals.append(("long_gamma", "compression", day + timedelta(days=1)))
            for strategy, direction, expiry in signals:
                contracts = strike_contracts(client, asset, expiry, s, known)
                inc = 100 if asset == "BTC" else 10
                if strategy == "trend_vertical":
                    if direction == "up" and ("C", 0) in contracts and ("C", 2) in contracts:
                        legs = [(contracts[("C", 0)], "buy"), (contracts[("C", 2)], "sell")]
                    elif direction == "down" and ("P", 0) in contracts and ("P", -2) in contracts:
                        legs = [(contracts[("P", 0)], "buy"), (contracts[("P", -2)], "sell")]
                    else: continue
                elif strategy == "failed_breakout":
                    if direction == "up_rejection" and ("C", 1) in contracts and ("C", 3) in contracts:
                        legs = [(contracts[("C", 1)], "sell"), (contracts[("C", 3)], "buy")]
                    elif direction == "down_rejection" and ("P", -1) in contracts and ("P", -3) in contracts:
                        legs = [(contracts[("P", -1)], "sell"), (contracts[("P", -3)], "buy")]
                    else: continue
                else:
                    if ("C", 0) not in contracts or ("P", 0) not in contracts: continue
                    legs = [(contracts[("C", 0)], "buy"), (contracts[("P", 0)], "buy")]
                symbols = {c.symbol for c, _ in legs}
                prices = {sym: close_map(client.candles("MARK:" + sym, minute_ts(day, 14 * 60), minute_ts(expiry, 17 * 60 + 25))) for sym in symbols}
                result = simulate(client, strategy, asset, day, entry_ts, expiry, legs, prices, spot, lots, direction, 1.0)
                if result: rows.append(result)
            day += timedelta(days=1)
    write_csv(output / "trades.csv", [asdict(r) for r in rows])
    summary = [{"strategy": k, **metrics(v)} for k in sorted({r.strategy for r in rows}) for v in [[x for x in rows if x.strategy == k]]]
    write_csv(output / "summary.csv", summary)
    (output / "manifest.json").write_text(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "lots": lots, "slippage_bps": slippage_bps, "rules_frozen": True}, indent=2))
    print(json.dumps({"output": str(output), "trades": len(rows), "summary": summary}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1)); p.add_argument("--end", type=date.fromisoformat, default=date(2026, 3, 31)); p.add_argument("--lots", type=int, default=200); p.add_argument("--slippage-bps", type=float, default=50); p.add_argument("--output", type=Path, default=OUT / "development_2026_q1")
    a = p.parse_args(); run(a.start, a.end, a.output, a.lots, a.slippage_bps)
