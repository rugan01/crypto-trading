# Delta 0DTE trade journal

Delta orders, fills, positions, and wallet charges are the source of truth. Strategy events explain what the engine knew and why it acted. Human notes explain intent and psychology. These layers must not be silently mixed or overwritten.

## Campaign key

Every attempt uses a stable key:

`DELTA-{environment}-{YYYYMMDD}-{asset}-{entry HHMM}-{strategy}`

Example: `DELTA-PROD-20260712-BTC-1700-0DTE`.

Zero-fill attempts remain journalled as cancelled/no-trade campaigns. Retries use an attempt suffix and remain linked to the same campaign.

## Storage

- `outputs/live/events-YYYYMMDD.jsonl`: append-only engine events and risk observations.
- `outputs/live/journals/YYYY-MM-DD/<campaign-key>.json`: machine-readable reconciled campaign.
- `outputs/live/journals/YYYY-MM-DD/<campaign-key>.md`: readable daily review.
- `docs/SESSIONS/`: public-safe, redacted research reports selected for GitHub.

Private files under `outputs/` are gitignored because they can contain account-specific data, order IDs, IP metadata, and balances.

## Reconciled journal fields

### Identity

- Campaign key, environment, account scope, engine version and git commit.
- Trade date, timezone, underlying, strategy and expiry.
- Call/put symbols, product IDs, strikes, contract values and tick sizes.

### Plan

- Scheduled and actual entry time.
- Requested quantity and supported quantity from depth.
- Required leverage per leg.
- Combined stop percentage and exact trigger level.
- Maximum daily loss and minimum free-margin floor.
- Entry window, minimum credit, forced-exit time and emergency conditions.

### Preflight market and account state

- Spot, marks, last prices, bid/ask, spread, bid/ask size and L2 VWAP.
- Quote timestamps and freshness.
- Combined mid, executable credit and credit-to-mid ratio.
- Wallet balance, available balance, blocked margin and projected post-entry free margin.
- Existing positions/orders, API status, WebSocket status and rate-limit state.
- Gate results and precise `NO_TRADE` reasons.

### Execution

- Every client/order ID, intent timestamp, acknowledgement latency and state.
- Requested/filled/unfilled size, side, limit, average fill and maker/taker role.
- Partial-fill repair, cancellations, retries and unmatched exposure duration.
- Entry slippage versus bid/mid/L2 VWAP.
- Entry call, put and combined actual credits.

### Monitoring and risk

- One-second executable combined buyback, marks and stop-breach counter.
- Maximum adverse/favourable excursion and timestamps.
- WebSocket message age, disconnects, reconnects and REST recovery ticks.
- Wallet/free-margin snapshots and hard-loss checks.
- Telegram notification attempts and failures.
- Stop reason, persistence evidence and decision latency.

### Exit and reconciliation

- Exit reason and time.
- Reduce-only order attempts, fills and residual quantities.
- Exit slippage and combined debit.
- Delta-reported commission per fill, GST-inclusive total, gross and net P&L.
- Final positions, active orders and wallet balance.
- Reconciliation status and any discrepancy.

### Review and learning

- Thesis: what the trade was intended to capture.
- Strategy outcome: P&L and whether the signal behaved as expected.
- Execution quality: spread, slippage, latency, partial fills and fallback usage.
- Risk quality: planned versus actual exposure and rule adherence.
- Data quality: missing/stale observations and broker discrepancies.
- Psychology/manual intervention: user-supplied notes only.
- Learning: one evidence-based conclusion.
- Next rule: one specific operational change for the next session.
- Confidence: provisional until repeated across multiple sessions.

## Daily learning loop

1. At startup, create the campaign and record the frozen plan.
2. During the session, append every event; never overwrite raw evidence.
3. After exit, fetch Delta order history, fills, positions and wallet data.
4. Reconcile quantities, prices, commissions and flat-account status.
5. Compute P&L, MAE/MFE, slippage, fallback ratio and rule adherence.
6. Compare live executable behavior with the backtest assumptions.
7. Write one learning and one next rule. Do not optimize parameters from one day.
8. At 18:00 IST, send the summary and any anomaly to Telegram.
9. Weekly, aggregate win rate, net expectancy, fee drag, stop frequency, liquidity failures and execution quality without changing the frozen production rule midweek.

## Current production rule

- BTC, 150 contracts per leg, common ATM 0DTE call and put, entered as one concurrent matched pair.
- Entry decision at 17:00 IST; mandatory exit begins 17:24:30 and completes by 17:25.
- Both products must report exactly 200x leverage.
- Combined executable stop is 1.5 times actual combined fill, persisted twice.
- Hard daily-loss cap is USD 25; minimum post-entry free margin is USD 30.
- Any failed liquidity, margin, leverage, data-freshness or reconciliation check is `NO_TRADE`.

### Unmatched leg: retain, never round-trip (effective 1 August 2026)

If one leg fills and the other cannot, the filled leg is **kept** and traded
single-sided. It is never bought back to restore symmetry.

Closing a good fill to repair a broken pair is a guaranteed loss taken to avoid
an uncertain one: it pays two lots of commission plus the spread and surrenders
the entry price, while a single short leg still carries a defined stop. In the
session that prompted this rule the abort cost more in commission and slippage
than the whole day's edge, and the same contract was re-sold minutes later at a
materially worse price than the one that had just been closed.

The retained leg keeps the normal protections:

- stop at **1.5x the credit of the leg actually held** — the unfilled leg
  contributes nothing to entry credit and nothing to the buyback comparison;
- the same 17:24:30 forced exit;
- normal broker reconciliation at exit.

`entry_unfilled` is now raised only when **both** legs are empty. A single-sided
fill emits `single_leg_session` and is a live position, not a failed entry.

This **supersedes** the 14 July 2026 session note, which recommended cancelling
the whole campaign when a leg's minimum-credit gate failed. That earlier
recommendation is retained in `docs/SESSIONS/` as a historical record only.

### Strike selection

The selector takes the **nearest** strike to spot, which maximises extrinsic
value; this is correct and unchanged. With 200-point strike spacing, spot sits
50-100 points from the nearest strike roughly half the time, and at that
distance one leg is worth almost nothing. Moving to the next strike out does not
help — it lowers extrinsic value and simply moves the worthless leg to the other
side. Those days are single-leg directional sessions by nature, which is what
the retain rule above is for.

A `market_context` event is recorded every session — spot, strike, distance,
per-leg bid/ask and spread, combined credit, intrinsic, **extrinsic**, mark vol
and open interest — so any future skip/size rule is derived from data rather
than from a small sample. No gate is currently driven by extrinsic value: across
the sessions logged so far the relationship to outcome is not established, and
the sample is far too small to justify one.
