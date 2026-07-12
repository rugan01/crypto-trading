# BTC manual live campaign: 12 July 2026

Environment: Delta India production

Campaign: 100-contract BTC 0DTE short straddle

Expiry: `12-07-2026`

Status: Closed manually; account reconciled flat

## Broker-reconciled fills

| Leg | Entry | Entry time IST | Exit | Exit time IST | Gross P&L | Commission |
|---|---:|---|---:|---|---:|---:|
| Short `P-BTC-64000-120726` ×100 | 55.10 | 17:00:55 | 5.00 | 17:19:28 | 5.01 | 0.227563 |
| Short `C-BTC-64000-120726` ×100 | 10.00 | 17:04:36 | 7.00 | 17:20:45 | 0.30 | 0.041300 |

- Combined entry credit: 65.10 points.
- Combined exit debit: 12.00 points.
- Gross P&L: USD 5.31.
- Total commissions: USD 0.318423.
- Net P&L: USD 4.991577.
- Maximum observed combined executable ask while both legs were open: about 39 points; combined stop threshold was 97.65 points.
- No stop breach occurred.
- The put was exited approximately 76 seconds before the call.
- The call entry lagged the put by approximately 3 minutes 41 seconds.

## Review

Thesis: sell the 5 PM BTC 0DTE ATM straddle and capture rapid final-expiry decay.

Execution: both legs filled, but not simultaneously. The put was a taker fill and the call a maker fill. Manual exit was profitable and the account finished flat.

Risk: the planned combined 50% stop was 97.65 points. No broker stop order was attached; monitoring was read-only. When the put was manually closed, the structure became a single call and the combined-straddle stop was no longer conceptually valid.

Learning: the strategy’s gross decay was positive, but asynchronous fills and manual leg closure change the risk definition. A combined stop must not be recomputed from the remaining leg.

Next rule: if one leg is manually closed, mark the campaign `STRUCTURE_CHANGED`, stop using the combined threshold, and require a fresh explicit decision for the remaining leg.

## Evidence

- Broker source: Delta production fills, positions, active orders and wallet snapshot.
- Read-only monitor: `outputs/live/monitor-20260712.jsonl`.
- Scheduled automation: did not start and was deleted after the missed window.
- No screenshot was supplied, so no chart attachment is claimed.
