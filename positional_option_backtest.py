#!/usr/bin/env python3
"""Apply frozen positional_mtf signals to dated option verticals."""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from delta_0dte import IST, DeltaClient, close_map, minute_ts, parse_contract, price_at
from options_hypotheses import metrics
from positional_mtf import candles_chunked

SLIP = 0.005


@dataclass
class Trade:
    asset: str; signal_date: str; signal: str; mode: str; expiry: str
    legs: str; entry_value: float; exit_value: float; gross_pnl: float; fees: float
    net_pnl: float; exit_reason: str; max_adverse: float; max_favourable: float


def product_legs(client: DeltaClient, asset: str, expiry: date, spot: float, mode: str):
    inc = 100 if asset == "BTC" else 10; centre = round(spot / inc) * inc; suffix = expiry.strftime("%d%m%y")
    needed = {"debit_call_vertical": (("C", 0, "buy"), ("C", 2, "sell")),
              "debit_put_vertical": (("P", 0, "buy"), ("P", -2, "sell")),
              "credit_call_vertical": (("C", 1, "sell"), ("C", 3, "buy")),
              "credit_put_vertical": (("P", -1, "sell"), ("P", -3, "buy"))}[mode]
    out=[]
    for typ, distance, side in needed:
        symbol=f"{typ}-{asset}-{int(centre+distance*inc)}-{suffix}"
        row=client.product(symbol)
        c=parse_contract(row) if row else None
        if not c: return None
        out.append((c,side))
    return out


def fee(spot: dict[int,float], price: float, contract_value: float, lots: int, rate: float, ts: int) -> float:
    sq=price_at(spot,ts,180); notional=(sq[1] if sq else 0)*contract_value*lots*rate
    return min(notional,price*contract_value*lots*.035)*1.18


def simulate(client, signal: dict[str,str], contracts, prices, spot, lots: int) -> Trade | None:
    entry_ts=int(datetime.fromisoformat(signal["signal_time"]).timestamp()); expiry=date.fromisoformat(signal["signal_time"][:10])+timedelta(days=2 if signal["mode"].startswith("debit") else 1); forced=minute_ts(expiry,17*60+25)
    fills=[]
    for c,side in contracts:
        q=price_at(prices[c.symbol],entry_ts,180)
        if not q:return None
        fills.append((c,side,q[1]*(1+SLIP if side=="buy" else 1-SLIP)))
    mult=fills[0][0].contract_value*lots; entry_cf=sum((-1 if side=="buy" else 1)*px for _,side,px in fills)*mult
    target=abs(entry_cf); marks=[]; reason="time_exit"; exit_ts=forced
    for ts in range(entry_ts+30*60,forced+1,30*60):
        if not all(ts in prices[c.symbol] for c,_,_ in fills):continue
        exit_cf=sum((1 if side=="buy" else -1)*prices[c.symbol][ts] for c,side,_ in fills)*mult; pnl=entry_cf+exit_cf; marks.append(pnl)
        stop = pnl <= -target*.5 if signal["mode"].startswith("debit") else pnl <= -target
        target_hit = pnl >= target if signal["mode"].startswith("debit") else pnl >= target*.5
        if stop or target_hit: reason="stop" if stop else "target"; exit_ts=ts; break
    exits=[]
    for c,side,_ in fills:
        q=price_at(prices[c.symbol],exit_ts,180)
        if not q:return None
        exits.append((c,side,q[1]*(1+SLIP if side=="buy" else 1-SLIP)))
    exit_cf=sum((1 if side=="buy" else -1)*px for _,side,px in exits)*mult; gross=entry_cf+exit_cf; rate=max(getattr(c,"taker_rate",.0001) for c,_,_ in fills)
    fees=sum(fee(spot,px,c.contract_value,lots,rate,entry_ts) for c,_,px in fills)+sum(fee(spot,px,c.contract_value,lots,rate,exit_ts) for c,_,px in exits)
    return Trade(signal["asset"],signal["signal_time"][:10],signal["signal"],signal["mode"],expiry.isoformat(),";".join(f"{side}:{c.symbol}" for c,side,_ in fills),abs(entry_cf),abs(exit_cf),gross,fees,gross-fees,reason,min(marks,default=0),max(marks,default=0))


def run(signals: Path, start: date, end: date, output: Path, lots: int):
    client=DeltaClient(); rows=list(csv.DictReader(signals.open())); rows=[r for r in rows if start.isoformat()<=r["signal_time"][:10]<=end.isoformat()]
    assets={r["asset"] for r in rows}; spot_cache={}; out=[]
    for asset in assets:
        spot_cache[asset]=close_map(candles_chunked(client,asset+"USD",start-timedelta(days=2),end+timedelta(days=4),"30m"))
    for i,signal in enumerate(rows,1):
        day=date.fromisoformat(signal["signal_time"][:10]); expiry=day+timedelta(days=2 if signal["mode"].startswith("debit") else 1); s=float(signal["close"])
        legs=product_legs(client,signal["asset"],expiry,s,signal["mode"])
        if not legs:continue
        prices={}
        for c,_ in legs:
            prices[c.symbol]=close_map(client.candles("MARK:"+c.symbol,minute_ts(day,14*60),minute_ts(expiry,17*60+25),resolution="30m"))
        t=simulate(client,signal,legs,prices,spot_cache[signal["asset"]],lots)
        if t:out.append(t)
    output.mkdir(parents=True,exist_ok=True); csv_path=output/"trades.csv"
    with csv_path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(asdict(out[0])) if out else ["asset"]);w.writeheader();w.writerows(asdict(x) for x in out)
    summary=[]
    for key in sorted({(x.asset,x.mode) for x in out}): summary.append({"asset":key[0],"mode":key[1],**metrics([x for x in out if (x.asset,x.mode)==key])})
    (output/"summary.json").write_text(json.dumps(summary,indent=2));print(json.dumps({"trades":len(out),"summary":summary},indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--signals",type=Path,required=True);p.add_argument("--start",type=date.fromisoformat,required=True);p.add_argument("--end",type=date.fromisoformat,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--lots",type=int,default=200);a=p.parse_args();run(a.signals,a.start,a.end,a.output,a.lots)
