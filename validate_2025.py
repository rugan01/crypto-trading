#!/usr/bin/env python3
"""Frozen 2025 validation and final-holdout runner."""
from __future__ import annotations

import csv
import argparse
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from delta_0dte import (
    ASSETS, IST, DeltaClient, Trade, close_map, minute_ts, performance_metrics,
    price_at, resolve_atm_pair, simulate, write_csv,
)

START = date(2025, 9, 1)
END = date(2025, 12, 31)
LOTS = 200
SLIPPAGE_BPS = 50.0
OUTPUT = Path(__file__).resolve().parent / "results" / "validation_2025_sep_dec_frozen"

# Frozen before any 2025 data was queried. The last item is a correlated
# stop-width challenger, not an independent fifth economic hypothesis.
VALIDATION_CANDIDATES = (
    ("BTC", 17 * 60, "combined", 150, "btc_1700_combined_150"),
    ("BTC", 17 * 60, "leg_independent", 100, "btc_1700_independent_100"),
    ("ETH", 15 * 60 + 15, "leg_independent", 150, "eth_1515_independent_150"),
    ("ETH", 16 * 60 + 45, "combined", 150, "eth_1645_combined_150"),
    ("BTC", 17 * 60, "combined", 50, "btc_1700_combined_50_challenger"),
)

# Frozen after Sep-Dec validation and before Jan-Aug was accessed.
HOLDOUT_CANDIDATES = (
    ("BTC", 17 * 60, "combined", 50, "btc_1700_combined_50"),
    ("BTC", 17 * 60, "leg_independent", 100, "btc_1700_independent_100"),
    ("ETH", 15 * 60 + 15, "leg_independent", 150, "eth_1515_independent_150"),
)


def exact_series(prices: dict[int, float], start: int, end: int) -> list[tuple[int, float]]:
    return [(ts, prices[ts]) for ts in range(start, end + 1, 60) if ts in prices]


def rms_return_move(values: list[float], spot: float, remaining_minutes: int) -> float | None:
    if len(values) < 3 or any(x <= 0 for x in values):
        return None
    returns = [math.log(b / a) for a, b in zip(values, values[1:])]
    mean_sq = statistics.mean(x * x for x in returns)
    return spot * math.sqrt(2 / math.pi) * math.sqrt(remaining_minutes * mean_sq)


def correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys)) / math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))


def diagnostic_row(candidate_id: str, trade: Trade, spot_px: dict[int, float], call_px: dict[int, float],
                   put_px: dict[int, float], entry_minute: int, slippage_bps: float) -> dict[str, Any] | None:
    day = date.fromisoformat(trade.expiry)
    entry_ts = int(datetime.fromisoformat(trade.entry_time).timestamp())
    forced_ts = minute_ts(day, 17 * 60 + 25)
    expiry_ts = minute_ts(day, 17 * 60 + 30)
    spot_entry_q, spot_exit_q = price_at(spot_px, entry_ts, 0), price_at(spot_px, forced_ts, 120)
    call_entry_q, put_entry_q = price_at(call_px, entry_ts, 0), price_at(put_px, entry_ts, 0)
    if not all((spot_entry_q, spot_exit_q, call_entry_q, put_entry_q)):
        return None
    spot_entry, spot_exit = spot_entry_q[1], spot_exit_q[1]
    entry_call, entry_put = call_entry_q[1], put_entry_q[1]
    premium = entry_call + entry_put
    remaining = max(1, (expiry_ts - entry_ts) // 60)
    row: dict[str, Any] = {
        "candidate_id": candidate_id, "asset": trade.asset, "expiry": trade.expiry,
        "entry_time": trade.entry_time[11:16], "method": trade.method, "stop_pct": trade.stop_pct,
        "spot_entry": spot_entry, "spot_1725": spot_exit, "strike": trade.strike,
        "atm_distance_abs": abs(spot_entry - trade.strike),
        "atm_distance_pct": abs(spot_entry - trade.strike) / spot_entry,
        "entry_call_mark": entry_call, "entry_put_mark": entry_put,
        "entry_combined_mark": premium, "premium_pct_spot": premium / spot_entry,
        "upper_breakeven": trade.strike + premium, "lower_breakeven": trade.strike - premium,
        "minutes_to_expiry": remaining, "call_share": entry_call / premium,
        "put_share": entry_put / premium, "premium_imbalance": (entry_call-entry_put)/premium,
        "settlement_proxy_abs_move": abs(spot_exit-spot_entry),
        "settlement_proxy_straddle_payoff": abs(spot_exit-trade.strike),
        "terminal_premium_edge": premium-abs(spot_exit-trade.strike),
        "terminal_edge_ratio": abs(spot_exit-trade.strike)/premium,
        "gross_pnl": trade.gross_pnl, "fees": trade.fees, "net_pnl": trade.net_pnl,
        "profitable": trade.net_pnl > 0, "exit_reason": trade.exit_reason,
        "call_exit_time": trade.call_exit_time, "put_exit_time": trade.put_exit_time,
        "mae": trade.mae, "mfe": trade.mfe,
        "premium_capture_to_exit": (premium-(trade.exit_call+trade.exit_put)/(1+slippage_bps/10_000))/premium,
    }
    post_spot = [v for _,v in exact_series(spot_px, entry_ts, forced_ts)]
    if post_spot:
        row.update({"max_up_move_pct": (max(post_spot)-spot_entry)/spot_entry,
                    "max_down_move_pct": (spot_entry-min(post_spot))/spot_entry,
                    "max_abs_excursion_pct": max(max(post_spot)-spot_entry, spot_entry-min(post_spot))/spot_entry})
    for window in (15, 30, 60, 120):
        series = exact_series(spot_px, entry_ts-window*60, entry_ts)
        values = [v for _,v in series]
        expected = rms_return_move(values, spot_entry, remaining)
        path = sum(abs(b-a) for a,b in zip(values,values[1:])) if len(values)>1 else 0
        row[f"pre_quote_coverage_{window}m"] = len(values)/(window+1)
        row[f"momentum_{window}m"] = math.log(spot_entry/values[0]) if values and values[0]>0 else None
        row[f"close_range_{window}m"] = (max(values)-min(values))/spot_entry if values else None
        row[f"trend_efficiency_{window}m"] = abs(spot_entry-values[0])/path if path else None
        row[f"expected_abs_move_{window}m"] = expected
        row[f"richness_{window}m"] = premium/expected if expected and expected>0 else None
    prior_ts = entry_ts-15*60
    prior_c, prior_p = price_at(call_px, prior_ts, 0), price_at(put_px, prior_ts, 0)
    if prior_c and prior_p:
        prior_premium = prior_c[1]+prior_p[1]
        row["premium_change_15m"] = premium/prior_premium-1 if prior_premium else None
        row["premium_decay_per_min_15m"] = (premium-prior_premium)/15
    else:
        row["premium_change_15m"] = row["premium_decay_per_min_15m"] = None
    combined = [(ts,call_px[ts]+put_px[ts]) for ts in range(entry_ts,forced_ts+1,60) if ts in call_px and ts in put_px]
    expected_points = (forced_ts-entry_ts)//60+1
    row["option_quote_coverage"] = len(combined)/expected_points
    row["premium_min"] = min((v for _,v in combined),default=None)
    row["premium_max"] = max((v for _,v in combined),default=None)
    for multiple in (0.5,1.0,1.5):
        hit = next((ts for ts,v in exact_series(spot_px,entry_ts,forced_ts) if abs(v-spot_entry)>=premium*multiple),None)
        row[f"minutes_to_{str(multiple).replace('.','_')}x_implied_move"] = (hit-entry_ts)//60 if hit else None
    return row


def grouped_diagnostics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = ((0,.75,"lt_0_75"),(.75,1,"0_75_1"),(1,1.25,"1_1_25"),(1.25,1.5,"1_25_1_5"),(1.5,float("inf"),"gte_1_5"))
    out=[]
    for candidate in sorted({r["candidate_id"] for r in rows}):
        cr=[r for r in rows if r["candidate_id"]==candidate]
        richness=[float(r["richness_60m"]) for r in cr if r.get("richness_60m") is not None]
        pnl=[float(r["net_pnl"]) for r in cr if r.get("richness_60m") is not None]
        for lo,hi,label in buckets:
            selected=[r for r in cr if r.get("richness_60m") is not None and lo<=float(r["richness_60m"])<hi]
            out.append({"candidate_id":candidate,"group_type":"richness_60m","group":label,"observations":len(selected),
                        "avg_net_pnl":statistics.mean(float(r["net_pnl"]) for r in selected) if selected else None,
                        "win_rate":statistics.mean(bool(r["profitable"]) for r in selected) if selected else None,
                        "avg_actual_abs_move":statistics.mean(float(r["settlement_proxy_abs_move"]) for r in selected) if selected else None,
                        "richness_pnl_correlation":correlation(richness,pnl)})
        for month in sorted({r["expiry"][:7] for r in cr}):
            selected=[r for r in cr if r["expiry"].startswith(month)]
            out.append({"candidate_id":candidate,"group_type":"month","group":month,"observations":len(selected),
                        "avg_net_pnl":statistics.mean(float(r["net_pnl"]) for r in selected),
                        "win_rate":statistics.mean(bool(r["profitable"]) for r in selected),
                        "avg_actual_abs_move":statistics.mean(float(r["settlement_proxy_abs_move"]) for r in selected),
                        "richness_pnl_correlation":None})
    return out


def main() -> None:
    parser=argparse.ArgumentParser(description="Frozen 2025 validation/holdout runner")
    parser.add_argument("--phase",choices=("validation","final_holdout"),default="validation")
    parser.add_argument("--slippage-bps",type=float,default=SLIPPAGE_BPS)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if args.slippage_bps < 0: parser.error("--slippage-bps cannot be negative")
    if args.phase == "validation":
        start,end,candidates=START,END,VALIDATION_CANDIDATES
        output=args.output or OUTPUT
        unseen="2025-01-01/2025-08-31"
    else:
        start,end=date(2025,1,1),date(2025,8,31)
        candidates=HOLDOUT_CANDIDATES
        output=args.output or Path(__file__).resolve().parent/"results"/"final_holdout_2025_jan_aug"
        unseen="none; final historical holdout consumed"
    client=DeltaClient(); trades=[]; diagnostics=[]; skips=defaultdict(int)
    by_asset=defaultdict(list)
    for row in candidates: by_asset[row[0]].append(row)
    for asset in ASSETS:
        for offset in range((end-start).days+1):
            day=start+timedelta(days=offset)
            start_ts,end_ts=minute_ts(day,11*60),minute_ts(day,17*60+28)
            spot=close_map(client.candles(asset+"USD",start_ts,end_ts))
            for _,entry_minute,method,stop,candidate_id in by_asset[asset]:
                entry_quote=price_at(spot,minute_ts(day,entry_minute),120)
                pair=resolve_atm_pair(client,asset,day,entry_quote[1]) if entry_quote else None
                if not entry_quote or not pair:
                    skips[f"{candidate_id}:missing_spot_or_contract"]+=1; continue
                call,put=pair
                call_px=close_map(client.candles("MARK:"+call.symbol,start_ts,end_ts))
                put_px=close_map(client.candles("MARK:"+put.symbol,start_ts,end_ts))
                trade=simulate(asset,day,entry_minute,call,put,call_px,put_px,spot,LOTS,method,stop,args.slippage_bps)
                if not trade:
                    skips[f"{candidate_id}:missing_option_prices"]+=1; continue
                diagnostic=diagnostic_row(candidate_id,trade,spot,call_px,put_px,entry_minute,args.slippage_bps)
                if not diagnostic:
                    skips[f"{candidate_id}:missing_diagnostics"]+=1; continue
                trades.append(trade); diagnostics.append(diagnostic)
    output.mkdir(parents=True,exist_ok=True)
    write_csv(output/"trades.csv",[asdict(t) for t in trades])
    write_csv(output/"diagnostics.csv",diagnostics)
    summary=[]
    for candidate_id in [x[4] for x in candidates]:
        candidate_trades=[t for t,d in zip(trades,diagnostics) if d["candidate_id"]==candidate_id]
        summary.append({"candidate_id":candidate_id,**performance_metrics(candidate_trades)})
    write_csv(output/"summary.csv",summary)
    write_csv(output/"diagnostic_groups.csv",grouped_diagnostics(diagnostics))
    (output/"skips.json").write_text(json.dumps(skips,indent=2))
    (output/"manifest.json").write_text(json.dumps({"phase":args.phase,"start":start.isoformat(),"end":end.isoformat(),"reserved_unseen":unseen,
        "lots":LOTS,"slippage_bps":args.slippage_bps,"candidates":[{"asset":a,"entry_minute":m,"method":method,"stop_pct":stop,"id":cid} for a,m,method,stop,cid in candidates]},indent=2))
    print(json.dumps({"output":str(output),"trades":len(trades),"diagnostics":len(diagnostics),"skips":dict(skips)},indent=2))


if __name__=="__main__": main()
