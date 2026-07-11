#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://api.india.delta.exchange"
TESTNET_URL = "https://cdn-ind.testnet.deltaex.org"
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "raw"
RESULTS = ROOT / "results"
ASSETS = ("BTC", "ETH")
ENTRY_MINUTES = tuple(range(13 * 60, 17 * 60 + 1, 15))
STOP_PCTS = (50, 100, 150)


class APIError(RuntimeError):
    pass


class DeltaClient:
    def __init__(self, base_url: str = BASE_URL, retries: int = 6):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "delta-0dte-research/0.1"})

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(self.base_url + path, params=params, timeout=45)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise APIError(f"GET {path} failed: {type(exc).__name__}") from exc
                time.sleep(min(20, 0.5 * 2**attempt) + random.random() / 4)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.retries:
                    raise APIError(f"GET {path} failed with HTTP {response.status_code}")
                reset = response.headers.get("X-RATE-LIMIT-RESET", "")
                delay = float(reset) / 1000 if reset.isdigit() else min(20, 0.5 * 2**attempt)
                time.sleep(delay + random.random() / 4)
                continue
            if not response.ok:
                raise APIError(f"GET {path} failed with HTTP {response.status_code}: {response.text[:160]}")
            payload = response.json()
            if not payload.get("success"):
                raise APIError(f"GET {path} returned success=false")
            return payload
        raise AssertionError("unreachable")

    def expired_options(self, days: int, max_pages: int = 100) -> list[dict[str, Any]]:
        cache = CACHE / f"expired_options_{days}d.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < 12 * 3600:
            return json.loads(cache.read_text())
        params: dict[str, Any] = {
            "states": "expired", "contract_types": "call_options,put_options",
            "underlying_asset_symbols": "BTC,ETH", "page_size": 100,
        }
        rows: list[dict[str, Any]] = []
        cursor = None
        cutoff = datetime.now(IST).date() - timedelta(days=days + 7)
        for _ in range(max_pages):
            if cursor:
                params["after"] = cursor
            payload = self.get("/v2/products", params)
            page = payload.get("result") or []
            rows.extend(page)
            page_dates = []
            for row in page:
                try:
                    page_dates.append(datetime.fromisoformat(str(row["settlement_time"]).replace("Z", "+00:00")).astimezone(IST).date())
                except (KeyError, TypeError, ValueError):
                    pass
            # Products are returned newest first. Stop after a complete page is
            # older than the requested research window instead of pulling the
            # exchange's entire 10,000-contract history.
            if page_dates and max(page_dates) < cutoff:
                break
            new_cursor = (payload.get("meta") or {}).get("after")
            if not new_cursor or new_cursor == cursor:
                break
            cursor = new_cursor
            time.sleep(0.06)
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows))
        return rows

    def candles(self, symbol: str, start: int, end: int, resolution: str = "1m") -> list[dict[str, Any]]:
        safe = symbol.replace(":", "_")
        cache = CACHE / "candles" / f"{safe}_{resolution}_{start}_{end}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        payload = self.get("/v2/history/candles", {"symbol": symbol, "resolution": resolution, "start": start, "end": end})
        rows = sorted(payload.get("result") or [], key=lambda x: int(x["time"]))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows))
        time.sleep(0.06)
        return rows

    def product(self, symbol: str) -> dict[str, Any] | None:
        """Resolve a product directly, including contracts beyond list pagination."""
        cache = CACHE / "products_by_symbol" / f"{symbol}.json"
        if cache.exists():
            value = json.loads(cache.read_text())
            return value or None
        response = self.session.get(self.base_url + "/v2/products/" + symbol, timeout=45)
        # Delta currently returns either 400 or 404 for an unknown symbol.
        if response.status_code in {400, 404}:
            value = None
        elif response.ok:
            payload = response.json()
            value = payload.get("result") if payload.get("success") else None
        else:
            raise APIError(f"GET /v2/products/{symbol} failed with HTTP {response.status_code}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(value))
        time.sleep(0.03)
        return value


@dataclass(frozen=True)
class Contract:
    symbol: str
    asset: str
    side: str
    strike: float
    expiry_day: date
    settlement: datetime
    contract_value: float
    taker_rate: float


def parse_contract(row: dict[str, Any]) -> Contract | None:
    symbol = str(row.get("symbol", ""))
    parts = symbol.split("-")
    if len(parts) != 4 or parts[0] not in {"C", "P"} or parts[1] not in ASSETS:
        return None
    try:
        settlement = datetime.fromisoformat(str(row["settlement_time"]).replace("Z", "+00:00"))
        return Contract(symbol, parts[1], parts[0], float(row.get("strike_price") or parts[2]),
                        datetime.strptime(parts[3], "%d%m%y").date(), settlement,
                        float(row.get("contract_value") or 0), float(row.get("taker_commission_rate") or 0))
    except (KeyError, TypeError, ValueError):
        return None


def catalog(rows: Iterable[dict[str, Any]], days: int) -> dict[tuple[str, date], dict[float, dict[str, Contract]]]:
    cutoff = datetime.now(IST).date() - timedelta(days=days + 7)
    out: dict[tuple[str, date], dict[float, dict[str, Contract]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        c = parse_contract(row)
        if c and c.expiry_day >= cutoff:
            out[(c.asset, c.expiry_day)][c.strike][c.side] = c
    return out


def minute_ts(day: date, minute: int) -> int:
    local = datetime.combine(day, dt_time(minute // 60, minute % 60), IST)
    return int(local.timestamp())


def close_map(rows: Iterable[dict[str, Any]]) -> dict[int, float]:
    return {int(r["time"]): float(r["close"]) for r in rows if r.get("close") is not None}


def price_at(prices: dict[int, float], timestamp: int, tolerance: int = 120) -> tuple[int, float] | None:
    for offset in range(0, tolerance + 1, 60):
        if timestamp + offset in prices:
            return timestamp + offset, prices[timestamp + offset]
    return None


def common_atm(chain: dict[float, dict[str, Contract]], spot: float) -> tuple[Contract, Contract] | None:
    strikes = [strike for strike, legs in chain.items() if "C" in legs and "P" in legs]
    if not strikes:
        return None
    strike = min(strikes, key=lambda x: (abs(x - spot), x))
    return chain[strike]["C"], chain[strike]["P"]


def resolve_atm_pair(client: DeltaClient, asset: str, day: date, spot: float,
                     known_chain: dict[float, dict[str, Contract]] | None = None) -> tuple[Contract, Contract] | None:
    """Resolve nearest paired strike without relying on the capped product list."""
    if known_chain:
        pair = common_atm(known_chain, spot)
        # A page at the 10,000-product catalogue boundary can contain only a
        # partial expiry. Do not accept a far-away "nearest" strike as ATM.
        max_atm_distance = 150 if asset == "BTC" else 15
        if pair and abs(float(pair[0].strike) - spot) <= max_atm_distance:
            return pair
    increment = 100 if asset == "BTC" else 10
    centre = round(spot / increment) * increment
    candidates = [centre]
    for distance in range(1, 7):
        candidates.extend((centre - distance * increment, centre + distance * increment))
    suffix = day.strftime("%d%m%y")
    for strike in candidates:
        call_row = client.product(f"C-{asset}-{int(strike)}-{suffix}")
        put_row = client.product(f"P-{asset}-{int(strike)}-{suffix}")
        call = parse_contract(call_row) if call_row else None
        put = parse_contract(put_row) if put_row else None
        if call and put:
            return call, put
    return None


@dataclass
class Trade:
    asset: str
    expiry: str
    entry_time: str
    interval_group: str
    strike: float
    call_symbol: str
    put_symbol: str
    lots: int
    contract_value: float
    method: str
    stop_pct: int
    entry_call: float
    entry_put: float
    exit_call: float
    exit_put: float
    call_exit_time: str
    put_exit_time: str
    exit_reason: str
    gross_pnl: float
    fees: float
    net_pnl: float
    mae: float
    mfe: float


def simulate(asset: str, day: date, entry_minute: int, call: Contract, put: Contract,
             call_px: dict[int, float], put_px: dict[int, float], spot_px: dict[int, float], lots: int,
             method: str, stop_pct: int, slippage_bps: float) -> Trade | None:
    start, forced = minute_ts(day, entry_minute), minute_ts(day, 17 * 60 + 25)
    ec, ep = price_at(call_px, start), price_at(put_px, start)
    if not ec or not ep:
        return None
    actual_start = max(ec[0], ep[0])
    ec2, ep2 = price_at(call_px, actual_start, 0), price_at(put_px, actual_start, 0)
    if not ec2 or not ep2 or ec2[1] <= 0 or ep2[1] <= 0:
        return None
    slip = slippage_bps / 10_000
    # We sell at entry and buy at exit: move both fills adversely away from mark.
    entry_mark_c, entry_mark_p = ec2[1], ep2[1]
    entry_c, entry_p = entry_mark_c * (1 - slip), entry_mark_p * (1 - slip)
    threshold = 1 + stop_pct / 100
    exit_c = exit_p = None
    exit_ct = exit_pt = None
    call_open = put_open = True
    reason = "time_exit"
    path_pnl: list[float] = []
    last_c = last_p = None
    for ts in range(actual_start + 60, forced + 1, 60):
        if ts in call_px: last_c = call_px[ts]
        if ts in put_px: last_p = put_px[ts]
        if last_c is None or last_p is None:
            continue
        live_c = last_c if call_open else exit_c
        live_p = last_p if put_open else exit_p
        path_pnl.append((entry_c + entry_p - float(live_c) - float(live_p)) * call.contract_value * lots)
        if method == "combined" and call_open and put_open and last_c + last_p >= (entry_mark_c + entry_mark_p) * threshold:
            exit_c, exit_p, exit_ct, exit_pt, call_open, put_open = last_c * (1 + slip), last_p * (1 + slip), ts, ts, False, False
            reason = "combined_stop"; break
        if method == "leg_both" and call_open and put_open and (last_c >= entry_mark_c * threshold or last_p >= entry_mark_p * threshold):
            exit_c, exit_p, exit_ct, exit_pt, call_open, put_open = last_c * (1 + slip), last_p * (1 + slip), ts, ts, False, False
            reason = "call_stop_both" if last_c >= entry_mark_c * threshold else "put_stop_both"; break
        if method == "leg_independent":
            if call_open and last_c >= entry_mark_c * threshold:
                exit_c, exit_ct, call_open, reason = last_c * (1 + slip), ts, False, "call_stop"
            if put_open and last_p >= entry_mark_p * threshold:
                exit_p, exit_pt, put_open, reason = last_p * (1 + slip), ts, False, "both_legs_stopped" if not call_open else "put_stop"
            if not call_open and not put_open: break
    if call_open:
        final = price_at(call_px, forced, 120)
        if not final: return None
        exit_ct, exit_c = final[0], final[1] * (1 + slip)
    if put_open:
        final = price_at(put_px, forced, 120)
        if not final: return None
        exit_pt, exit_p = final[0], final[1] * (1 + slip)
    multiplier = call.contract_value * lots
    gross = (entry_c + entry_p - float(exit_c) - float(exit_p)) * multiplier
    fee_rate = max(call.taker_rate, put.taker_rate)
    # Delta India: fee is based on underlying notional but capped at 3.5%
    # of option premium; GST is then applied. Calculate each leg/fill because
    # independent stops happen at different underlying prices.
    def fee(premium: float, ts: int) -> float:
        spot_quote = price_at(spot_px, ts, 120)
        if not spot_quote:
            return premium * multiplier * 0.035 * 1.18
        notional_fee = spot_quote[1] * multiplier * fee_rate
        premium_cap = premium * multiplier * 0.035
        return min(notional_fee, premium_cap) * 1.18
    fees = fee(entry_c, actual_start) + fee(entry_p, actual_start) + fee(float(exit_c), int(exit_ct)) + fee(float(exit_p), int(exit_pt))
    fmt = lambda ts: datetime.fromtimestamp(int(ts), IST).isoformat()
    return Trade(asset, day.isoformat(), fmt(actual_start), "30m" if entry_minute % 30 == 0 else "15m_only",
                 call.strike, call.symbol, put.symbol, lots, call.contract_value, method, stop_pct,
                 entry_c, entry_p, float(exit_c), float(exit_p), fmt(exit_ct), fmt(exit_pt), reason,
                 gross, fees, gross - fees, min(path_pnl, default=0), max(path_pnl, default=0))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text(""); return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def performance_metrics(rows: list[Trade]) -> dict[str, Any]:
    """Metrics for one daily strategy, ordered chronologically by expiry."""
    ordered = sorted(rows, key=lambda r: r.expiry)
    pnl = [r.net_pnl for r in ordered]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    equity = peak = max_dd = 0.0
    peak_date = date.fromisoformat(ordered[0].expiry)
    underwater_start: date | None = None
    underwater_trades = 0
    max_duration_days = max_duration_trades = 0
    max_dd_date = recovery_date = None
    current_win_streak = current_loss_streak = max_wins = max_losses = 0
    for row, value in zip(ordered, pnl):
        day = date.fromisoformat(row.expiry)
        equity += value
        if equity >= peak:
            peak = equity
            peak_date = day
            if underwater_start is not None:
                max_duration_days = max(max_duration_days, (day - underwater_start).days)
                max_duration_trades = max(max_duration_trades, underwater_trades)
                if recovery_date is None and max_dd_date is not None and day >= max_dd_date:
                    recovery_date = day
            underwater_start = None
            underwater_trades = 0
        else:
            if underwater_start is None:
                underwater_start = peak_date
                underwater_trades = 1
            else:
                underwater_trades += 1
            duration_days = (day - underwater_start).days
            max_duration_days = max(max_duration_days, duration_days)
            max_duration_trades = max(max_duration_trades, underwater_trades)
            drawdown = peak - equity
            if drawdown > max_dd:
                max_dd = drawdown
                max_dd_date = day
                recovery_date = None
        if value > 0:
            current_win_streak += 1; current_loss_streak = 0; max_wins = max(max_wins, current_win_streak)
        elif value < 0:
            current_loss_streak += 1; current_win_streak = 0; max_losses = max(max_losses, current_loss_streak)
        else:
            current_win_streak = current_loss_streak = 0
    mean = statistics.mean(pnl)
    stdev = statistics.stdev(pnl) if len(pnl) > 1 else 0.0
    tail_n = max(1, math.ceil(len(pnl) * 0.05))
    return {
        "trades": len(rows), "gross_pnl": round(sum(r.gross_pnl for r in rows), 2),
        "fees": round(sum(r.fees for r in rows), 2), "net_pnl": round(sum(pnl), 2),
        "avg_pnl": round(mean, 4), "median_pnl": round(statistics.median(pnl), 4),
        "win_rate": round(len(wins) / len(pnl), 4),
        "avg_profitable_trade": round(statistics.mean(wins), 4) if wins else None,
        "avg_losing_trade": round(statistics.mean(losses), 4) if losses else None,
        "payoff_ratio": round(statistics.mean(wins) / abs(statistics.mean(losses)), 4) if wins and losses else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else None,
        "sharpe_daily_annualized": round(mean / stdev * math.sqrt(365), 4) if stdev else None,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_duration_days": max_duration_days,
        "max_drawdown_duration_trades": max_duration_trades,
        "max_drawdown_date": max_dd_date.isoformat() if max_dd_date else None,
        "recovery_date": recovery_date.isoformat() if recovery_date else None,
        "recovered": recovery_date is not None or max_dd == 0,
        "max_consecutive_wins": max_wins, "max_consecutive_losses": max_losses,
        "best_trade": round(max(pnl), 2), "worst_trade": round(min(pnl), 2),
        "cvar_95": round(statistics.mean(sorted(pnl)[:tail_n]), 2),
    }


def summarize(trades: list[Trade]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Trade]] = defaultdict(list)
    for t in trades:
        local = datetime.fromisoformat(t.entry_time)
        groups[(t.asset, local.strftime("%H:%M"), t.method, t.stop_pct)].append(t)
    output = []
    for key, rows in sorted(groups.items()):
        output.append({"asset": key[0], "entry_time": key[1], "method": key[2], "stop_pct": key[3],
                       **performance_metrics(rows)})
    return output


def audit(client: DeltaClient, days: int) -> tuple[dict[tuple[str, date], dict[float, dict[str, Contract]]], list[dict[str, Any]]]:
    products = client.expired_options(days)
    chains = catalog(products, days)
    rows = []
    cutoff = datetime.now(IST).date() - timedelta(days=days)
    for asset in ASSETS:
        asset_days = sorted(d for a, d in chains if a == asset and d >= cutoff)
        settlement_ok = sum(chains[(asset,d)][s][next(iter(chains[(asset,d)][s]))].settlement.astimezone(IST).strftime("%H:%M") == "17:30" for d in asset_days for s in [next(iter(chains[(asset,d)]))])
        paired = sum(1 for d in asset_days if any(set(x) >= {"C","P"} for x in chains[(asset,d)].values()))
        rows.append({"asset": asset, "requested_calendar_days": days, "expiry_days_found": len(asset_days),
                     "days_with_paired_strike": paired, "days_settling_1730_ist": settlement_ok,
                     "first_expiry": min(asset_days).isoformat() if asset_days else None,
                     "last_expiry": max(asset_days).isoformat() if asset_days else None})
    return chains, rows


def backtest(client: DeltaClient, days: int, lots: int, output: Path, slippage_bps: float,
             start_date: date | None = None, end_date: date | None = None) -> tuple[list[Trade], list[dict[str, Any]]]:
    chains, audit_rows = audit(client, days)
    trades: list[Trade] = []
    skipped = defaultdict(int)
    cutoff = start_date or datetime.now(IST).date() - timedelta(days=days)
    last_day = end_date or datetime.now(IST).date() - timedelta(days=1)
    for asset in ASSETS:
        expiry_days = [cutoff + timedelta(days=n) for n in range((last_day - cutoff).days + 1)]
        for day in expiry_days:
            chain = chains.get((asset, day), {})
            start, end = minute_ts(day, 12 * 60 + 55), minute_ts(day, 17 * 60 + 28)
            spot = close_map(client.candles(asset + "USD", start, end))
            selected: dict[int, tuple[Contract, Contract]] = {}
            for minute in ENTRY_MINUTES:
                quote = price_at(spot, minute_ts(day, minute))
                pair = resolve_atm_pair(client, asset, day, quote[1], chain) if quote else None
                if pair: selected[minute] = pair
                else: skipped[(asset, "no_spot_or_pair")] += 1
            price_cache: dict[str, dict[int, float]] = {}
            unique = {leg.symbol: leg for pair in selected.values() for leg in pair}
            # Each symbol is independent and the public quota comfortably
            # supports a small pool. Caching makes retries/resumed runs cheap.
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(client.candles, "MARK:" + symbol, start, end): symbol for symbol in unique}
                for future in as_completed(futures):
                    symbol = futures[future]
                    price_cache[symbol] = close_map(future.result())
            for minute, (call, put) in selected.items():
                for method in ("combined", "leg_independent", "leg_both"):
                    for stop in STOP_PCTS:
                        trade = simulate(asset, day, minute, call, put, price_cache[call.symbol], price_cache[put.symbol], spot, lots, method, stop, slippage_bps)
                        if trade: trades.append(trade)
                        else: skipped[(asset, "missing_option_prices")] += 1
    write_csv(output / "trades.csv", [asdict(x) for x in trades])
    summary = summarize(trades)
    write_csv(output / "summary.csv", summary)
    coverage_rows = []
    for asset in ASSETS:
        completed = sorted({date.fromisoformat(t.expiry) for t in trades if t.asset == asset})
        catalog_row = next((r for r in audit_rows if r["asset"] == asset), {})
        coverage_rows.append({
            "asset": asset, "requested_start": cutoff.isoformat(), "requested_end": last_day.isoformat(),
            "requested_expiry_days": (last_day - cutoff).days + 1,
            "completed_expiry_days": len(completed),
            "first_completed_expiry": completed[0].isoformat() if completed else None,
            "last_completed_expiry": completed[-1].isoformat() if completed else None,
            "catalog_expiry_days": catalog_row.get("expiry_days_found", 0),
            "direct_symbol_resolution_used": len(completed) > int(catalog_row.get("expiry_days_found", 0)),
        })
    write_csv(output / "data_audit.csv", coverage_rows)
    (output / "skips.json").write_text(json.dumps({f"{a}:{r}": n for (a,r),n in skipped.items()}, indent=2))
    return trades, summary


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only Delta BTC/ETH 0DTE straddle research")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("audit", "backtest"):
        q = sub.add_parser(name); q.add_argument("--days", type=int, default=30)
        if name == "backtest":
            q.add_argument("--lots", type=int, default=200); q.add_argument("--output", type=Path)
            q.add_argument("--slippage-bps", type=float, default=50.0, help="Adverse premium slippage per fill (default: 50 bps)")
            q.add_argument("--start-date", type=date.fromisoformat)
            q.add_argument("--end-date", type=date.fromisoformat)
    args = p.parse_args()
    if args.days < 1: p.error("--days must be positive")
    client = DeltaClient()
    if args.cmd == "audit":
        _, rows = audit(client, args.days); print(json.dumps(rows, indent=2)); return 0
    if args.lots < 1 or int(args.lots) != args.lots: p.error("--lots must be a positive whole number")
    if bool(args.start_date) != bool(args.end_date): p.error("provide both --start-date and --end-date")
    if args.start_date and args.start_date > args.end_date: p.error("--start-date must not follow --end-date")
    # Preserve the explicitly reserved 2025 holdout until the candidate set is frozen.
    if args.start_date and args.start_date < date(2026, 1, 1): p.error("dates before 2026-01-01 are reserved as unseen holdout")
    effective_days = (datetime.now(IST).date() - args.start_date).days if args.start_date else args.days
    output = args.output or RESULTS / datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    if args.slippage_bps < 0: p.error("--slippage-bps cannot be negative")
    trades, summary = backtest(client, effective_days, args.lots, output, args.slippage_bps, args.start_date, args.end_date)
    print(json.dumps({"output": str(output), "trades": len(trades), "summary_rows": len(summary)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
