#!/usr/bin/env python3
"""Descriptive premium/balance analysis for the frozen BTC 17:00 combined-50% rule."""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from delta_0dte import IST, Trade, performance_metrics, write_csv

ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/"results"/"btc_1700_premium_analysis"
SOURCES=(
    ROOT/"results"/"final_holdout_2025_jan_aug"/"trades.csv",
    ROOT/"results"/"validation_2025_sep_dec_frozen"/"trades.csv",
    ROOT/"results"/"research_2026_jan01_jul10_50bps"/"trades.csv",
)


def load_spot() -> dict[int,float]:
    result={}
    for path in (ROOT/"data"/"raw"/"candles").glob("BTCUSD_1m_*.json"):
        for row in json.loads(path.read_text()):
            if row.get("close") is not None: result[int(row["time"])]=float(row["close"])
    return result


def convert_trade(row: dict[str,str]) -> Trade:
    for key in ("strike","contract_value","entry_call","entry_put","exit_call","exit_put","gross_pnl","fees","net_pnl","mae","mfe"):
        row[key]=float(row[key])
    for key in ("lots","stop_pct"): row[key]=int(row[key])
    return Trade(**row)  # type: ignore[arg-type]


def wilson(wins:int,n:int,z:float=1.96)->tuple[float,float]:
    if not n:return 0,0
    p=wins/n; d=1+z*z/n
    centre=(p+z*z/(2*n))/d
    margin=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return centre-margin,centre+margin


def pearson(xs:list[float],ys:list[float])->float|None:
    if len(xs)<3 or statistics.pstdev(xs)==0 or statistics.pstdev(ys)==0:return None
    mx,my=statistics.mean(xs),statistics.mean(ys)
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))


def max_drawdown(values:list[tuple[str,float]])->float:
    equity=peak=dd=0.0
    for _,value in sorted(values):
        equity+=value;peak=max(peak,equity);dd=max(dd,peak-equity)
    return dd


def group_metrics(label:str,group_type:str,rows:list[dict[str,Any]])->dict[str,Any]:
    pnl=[float(r["net_pnl"]) for r in rows];wins=[x for x in pnl if x>0];losses=[x for x in pnl if x<0]
    lo,hi=wilson(len(wins),len(rows))
    years=defaultdict(list)
    for r in rows:years[r["expiry"][:4]].append(float(r["net_pnl"]))
    return {"group_type":group_type,"group":label,"observations":len(rows),
        "mean_premium":statistics.mean(float(r["combined_premium"]) for r in rows) if rows else None,
        "median_premium":statistics.median(float(r["combined_premium"]) for r in rows) if rows else None,
        "mean_premium_pct_spot":statistics.mean(float(r["premium_pct_spot"]) for r in rows) if rows else None,
        "mean_smaller_leg_share":statistics.mean(float(r["smaller_leg_share"]) for r in rows) if rows else None,
        "wins":len(wins),"win_probability":len(wins)/len(rows) if rows else None,
        "win_probability_ci95_low":lo if rows else None,"win_probability_ci95_high":hi if rows else None,
        "net_pnl":sum(pnl),"expectancy":statistics.mean(pnl) if pnl else None,
        "median_pnl":statistics.median(pnl) if pnl else None,
        "avg_win":statistics.mean(wins) if wins else None,"avg_loss":statistics.mean(losses) if losses else None,
        "profit_factor":sum(wins)/abs(sum(losses)) if losses else None,
        "max_drawdown":max_drawdown([(r["expiry"],float(r["net_pnl"])) for r in rows]),
        "stop_rate":statistics.mean(r["exit_reason"]=="combined_stop" for r in rows) if rows else None,
        "mean_return_on_credit":statistics.mean(float(r["return_on_credit"]) for r in rows) if rows else None,
        "pnl_2025":sum(years.get("2025",[])),"avg_pnl_2025":statistics.mean(years["2025"]) if years.get("2025") else None,
        "win_rate_2025":statistics.mean(x>0 for x in years["2025"]) if years.get("2025") else None,
        "pnl_2026":sum(years.get("2026",[])),"avg_pnl_2026":statistics.mean(years["2026"]) if years.get("2026") else None,
        "win_rate_2026":statistics.mean(x>0 for x in years["2026"]) if years.get("2026") else None}


def main()->None:
    spot=load_spot(); rows=[]; seen=set()
    for source in SOURCES:
        for raw in csv.DictReader(source.open()):
            if not (raw["asset"]=="BTC" and raw["entry_time"][11:16]=="17:00" and raw["method"]=="combined" and raw["stop_pct"]=="50"):continue
            if raw["expiry"] in seen:raise RuntimeError(f"duplicate expiry {raw['expiry']}")
            seen.add(raw["expiry"]);t=convert_trade(raw)
            ts=int(datetime.fromisoformat(t.entry_time).timestamp());s=spot.get(ts)
            if s is None:raise RuntimeError(f"missing entry spot {t.expiry}")
            premium=t.entry_call+t.entry_put;small=min(t.entry_call,t.entry_put)/premium
            credit=premium*t.contract_value*t.lots
            rows.append({"expiry":t.expiry,"spot":s,"strike":t.strike,"call_premium":t.entry_call,"put_premium":t.entry_put,
                "combined_premium":premium,"premium_pct_spot":premium/s,"smaller_leg_share":small,
                "absolute_imbalance":abs(t.entry_call-t.entry_put)/premium,"balanced_35pct":small>=.35,
                "lots":t.lots,"contract_value":t.contract_value,"credit_usd":credit,"fees":t.fees,"net_pnl":t.net_pnl,
                "return_on_credit":t.net_pnl/credit if credit else None,"exit_reason":t.exit_reason,
                "profitable":t.net_pnl>0})
    rows.sort(key=lambda r:r["expiry"])
    if len(rows)!=556:raise RuntimeError(f"expected 556 rows, got {len(rows)}")
    groups=[]
    def add(group_type:str,label:str,pred:Callable[[dict[str,Any]],bool]):
        selected=[r for r in rows if pred(r)];groups.append(group_metrics(label,group_type,selected))
    add("all","all",lambda r:True)
    add("premium_50","lte_50",lambda r:r["combined_premium"]<=50)
    add("premium_50","gt_50",lambda r:r["combined_premium"]>50)
    fixed=((0,50,"0_50"),(50,75,"50_75"),(75,100,"75_100"),(100,150,"100_150"),(150,200,"150_200"),(200,float('inf'),"gte_200"))
    for lo,hi,label in fixed:add("premium_fixed",label,lambda r,lo=lo,hi=hi:lo<r["combined_premium"]<=hi)
    premiums=sorted(float(r["combined_premium"]) for r in rows)
    cuts=[premiums[round((len(premiums)-1)*q/5)] for q in range(1,5)]
    bounds=[-float('inf'),*cuts,float('inf')]
    for i,(lo,hi) in enumerate(zip(bounds,bounds[1:]),1):add("premium_quintile",f"Q{i}_{lo:.2f}_{hi:.2f}",lambda r,lo=lo,hi=hi:lo<r["combined_premium"]<=hi)
    normalized=sorted(float(r["premium_pct_spot"]) for r in rows)
    normalized_cuts=[normalized[round((len(normalized)-1)*q/5)] for q in range(1,5)]
    normalized_bounds=[-float('inf'),*normalized_cuts,float('inf')]
    for i,(lo,hi) in enumerate(zip(normalized_bounds,normalized_bounds[1:]),1):
        add("premium_pct_spot_quintile",f"Q{i}_{lo:.6f}_{hi:.6f}",lambda r,lo=lo,hi=hi:lo<r["premium_pct_spot"]<=hi)
    balance=((0,.2,"small_leg_lt_20pct"),(.2,.3,"small_leg_20_30pct"),(.3,.4,"small_leg_30_40pct"),(.4,.500001,"small_leg_40_50pct"))
    for lo,hi,label in balance:add("balance",label,lambda r,lo=lo,hi=hi:lo<=r["smaller_leg_share"]<hi)
    add("balance_simple","unbalanced_lt_35pct",lambda r:not r["balanced_35pct"])
    add("balance_simple","balanced_gte_35pct",lambda r:r["balanced_35pct"])
    for premium_label,premium_pred in (("lte50",lambda r:r["combined_premium"]<=50),("gt50",lambda r:r["combined_premium"]>50)):
        for bal_label,bal_pred in (("unbalanced",lambda r:not r["balanced_35pct"]),("balanced",lambda r:r["balanced_35pct"])):
            add("premium_x_balance",f"{premium_label}_{bal_label}",lambda r,a=premium_pred,b=bal_pred:a(r) and b(r))
    summary={"observations":len(rows),"first_expiry":rows[0]["expiry"],"last_expiry":rows[-1]["expiry"],
        "premium_min":min(r["combined_premium"] for r in rows),"premium_q25":statistics.quantiles([r["combined_premium"] for r in rows],n=4)[0],
        "premium_median":statistics.median(r["combined_premium"] for r in rows),"premium_mean":statistics.mean(r["combined_premium"] for r in rows),
        "premium_q75":statistics.quantiles([r["combined_premium"] for r in rows],n=4)[2],"premium_max":max(r["combined_premium"] for r in rows),
        "smaller_leg_share_mean":statistics.mean(r["smaller_leg_share"] for r in rows),
        "smaller_leg_share_median":statistics.median(r["smaller_leg_share"] for r in rows),
        "balanced_35pct_frequency":statistics.mean(r["balanced_35pct"] for r in rows),
        "premium_pnl_correlation":pearson([r["combined_premium"] for r in rows],[r["net_pnl"] for r in rows]),
        "premium_pct_spot_pnl_correlation":pearson([r["premium_pct_spot"] for r in rows],[r["net_pnl"] for r in rows]),
        "balance_pnl_correlation":pearson([r["smaller_leg_share"] for r in rows],[r["net_pnl"] for r in rows]),
        "quintile_cut_points":cuts,"premium_pct_spot_quintile_cut_points":normalized_cuts}
    OUTPUT.mkdir(parents=True,exist_ok=True)
    write_csv(OUTPUT/"observations.csv",rows);write_csv(OUTPUT/"group_metrics.csv",groups)
    (OUTPUT/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(summary,indent=2))


if __name__=="__main__":main()
