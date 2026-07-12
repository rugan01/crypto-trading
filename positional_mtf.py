#!/usr/bin/env python3
"""Generate frozen multi-timeframe spot signals for positional option research."""
from __future__ import annotations

import argparse
import csv
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from delta_0dte import DeltaClient, close_map, minute_ts, IST


def candles_chunked(client: DeltaClient, symbol: str, start: date, end: date, resolution: str) -> list[dict]:
    limits = {"30m": 35, "4h": 300, "1d": 900}
    out: dict[int, dict] = {}
    day = start
    while day <= end:
        chunk_end = min(end, day + timedelta(days=limits[resolution]))
        rows = client.candles(symbol, minute_ts(day, 0), minute_ts(chunk_end, 23 * 60 + 59), resolution)
        for row in rows:
            out[int(row["time"])] = row
        day = chunk_end + timedelta(days=1)
    return [out[k] for k in sorted(out)]


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    value = sum(values[:period]) / period
    for x in values[period:]:
        value = alpha * x + (1 - alpha) * value
    return value


def pivots(high: float, low: float, close: float) -> dict[str, float]:
    p = (high + low + close) / 3
    return {"P": p, "R1": 2*p-low, "S1": 2*p-high, "R2": p+high-low, "S2": p-high+low,
            "R3": high+2*(p-low), "S3": low-2*(high-p),
            "CR1": close+(high-low)*1.1/12, "CS1": close-(high-low)*1.1/12,
            "CR2": close+(high-low)*1.1/6, "CS2": close-(high-low)*1.1/6,
            "CR3": close+(high-low)*1.1/4, "CS3": close-(high-low)*1.1/4,
            "CR4": close+(high-low)*1.1/2, "CS4": close-(high-low)*1.1/2}


def run(asset: str, start: date, end: date, output: Path) -> None:
    client = DeltaClient(); symbol = asset + "USD"
    d1 = candles_chunked(client, symbol, start - timedelta(days=70), end, "1d")
    h4 = candles_chunked(client, symbol, start - timedelta(days=20), end, "4h")
    m30 = candles_chunked(client, symbol, start - timedelta(days=3), end, "30m")
    daily = {}
    for row in d1:
        day = datetime.fromtimestamp(int(row["time"]), IST).date()
        daily[day] = row
    rows=[]
    emitted=set()
    previous_close = None
    for idx, bar in enumerate(m30):
        ts=int(bar["time"]); day=datetime.fromtimestamp(ts, IST).date()
        if day < start or day > end or day not in daily:
            continue
        prior_day = day-timedelta(days=1)
        if prior_day not in daily:
            continue
        daily_history=[x for x in d1 if int(x["time"]) < ts]
        h4_history=[x for x in h4 if int(x["time"]) < ts]
        daily_closes=[float(x["close"]) for x in daily_history]
        h4_closes=[float(x["close"]) for x in h4_history]
        d20,d50=ema(daily_closes,20),ema(daily_closes,50)
        h20,h50=ema(h4_closes,20),ema(h4_closes,50)
        if None in (d20,d50,h20,h50):
            previous_close=float(bar["close"]); continue
        close=float(bar["close"]); high=float(bar["high"]); low=float(bar["low"])
        regime="bull" if close>d20 and d20>d50 and close>h20 and h20>h50 else "bear" if close<d20 and d20<d50 and close<h20 and h20<h50 else "neutral"
        pv=pivots(float(daily[prior_day]["high"]),float(daily[prior_day]["low"]),float(daily[prior_day]["close"]))
        prev=previous_close if previous_close is not None else close
        signal=None; mode=None; level=None
        if regime=="bull" and prev<=pv["R1"]<close:
            signal="bullish_breakout"; mode="debit_call_vertical"; level=pv["R1"]
        elif regime=="bear" and prev>=pv["S1"]>close:
            signal="bearish_breakout"; mode="debit_put_vertical"; level=pv["S1"]
        elif high>=pv["CR3"] and close<pv["CR3"]:
            signal="upper_camarilla_rejection"; mode="credit_call_vertical"; level=pv["CR3"]
        elif low<=pv["CS3"] and close>pv["CS3"]:
            signal="lower_camarilla_rejection"; mode="credit_put_vertical"; level=pv["CS3"]
        if signal:
            key=(day,signal)
            if key in emitted:
                previous_close=close
                continue
            emitted.add(key)
            rows.append({"asset":asset,"signal_time":datetime.fromtimestamp(ts,IST).isoformat(),"signal":signal,"mode":mode,"regime":regime,"close":close,"level":level,"daily_ema20":d20,"daily_ema50":d50,"h4_ema20":h20,"h4_ema50":h50,"R1":pv["R1"],"S1":pv["S1"],"CR3":pv["CR3"],"CS3":pv["CS3"]})
        previous_close=close
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ["asset","signal_time","signal"]); w.writeheader(); w.writerows(rows)
    print({"asset":asset,"signals":len(rows),"output":str(output)})


if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--asset",choices=("BTC","ETH"),required=True); p.add_argument("--start",type=date.fromisoformat,required=True); p.add_argument("--end",type=date.fromisoformat,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args(); run(a.asset,a.start,a.end,a.output)
