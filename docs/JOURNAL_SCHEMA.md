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

- BTC, **target 150** contracts per leg, common ATM 0DTE call and put, entered as one concurrent
  matched pair. **Size is chosen automatically each session** - see below - so 150 is a ceiling,
  not a fixed quantity.
- **Auto-sizing (effective 2026-08-08).** `delta_live.size_check` computes the largest size the
  account can margin and clamps the target down to it. It can only reduce, never raise above the
  target, so the worst case is a smaller position rather than a rejected or partially filled entry.

      base_per_contract    = spot * 0.001 / 200 * 2
      premium_per_contract = combined_credit * 0.001
      budget               = (available - 5 fee buffer) * 0.95 safety
      size                 = min(target, floor(budget / per_contract) rounded down to 25)

  Returns 0 - a hard NO TRADE with a Telegram alert - when it cannot afford the 50-contract
  minimum, rather than opening a token position that cannot cover its own commission.

  The reason this exists: sizing failed three times in three days because the requirement was
  checked against base margin alone. The exchange needs base PLUS premium, and premium scales with
  the day's credit, so the same size fits one day and is rejected the next on an identical balance.
  On 2026-08-08 base margin alone looked comfortably affordable; adding premium margin took the
  real requirement ABOVE the available balance.

  Distinct from the `projected_free` telemetry warning, which was reviewed on 2026-08-01 and
  accepted as not applicable to a 20-minute defined-stop book. That one is advisory; this is hard.

  Verified against every sizing decision actually taken: 6 Aug (credit 52) -> 100, 8 Aug
  (credit 96) -> 125, and 5 Aug (credit 83.1) -> 125 where the session in fact ran 150 and
  printed a NEGATIVE `projected_free`.
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

### Orders are priced from the L2 book, never the ticker (effective 12 August 2026)

`/v2/tickers` is a **cached snapshot**. A 30-sample probe on 12 August put its
median age at **4.21s** (p90 6.61s, max 8.19s) against **0.61s** for
`/v2/l2orderbook` (p90 2.00s, max 2.28s). Polling the ticker faster returns the
identical snapshot with the identical timestamp — it does not refresh on demand.

On 12 August this produced a single-leg session. The ticker reported the put bid
pinned at exactly **28.00** — and the ask at exactly 30.00 — for the entire
8.5-second retry window while the executable bid fell to 22, where the put was
later sold manually. Six IOC sells priced 27.16 down to 25.76 all cancelled
unfilled. The old ladder stepped down from the **entry-time** bid to a floor of
0.90x, i.e. 25.20, so it was arithmetically incapable of reaching the market
however many times it retried. The same staleness made the call fill at 35
against its own quoted bid of 28, which looked like a good fill and was really
the same defect pointing the other way.

Three changes:

- `Quote.from_l2` and `pricing_quote()` price every entry and retry order from
  the L2 book, falling back to the ticker only when L2 fails or is empty.
- The retry ladder **chases the live bid** — `live_bid × (0.995 − 0.005×placed)`,
  floored at 0.97 — instead of stepping down from a fixed entry-time anchor.
  The cushion now only has to absorb read-to-fill latency.
- A quote older than **3s**, or carrying no timestamp at all, is refused rather
  than traded on (`entry_retry_stale_quote`). 3s was chosen from the probe
  above; a 2s ceiling refused 10% of healthy L2 reads for no benefit.

`MAX_CHASE_OF_ENTRY_BID` (0.75) stops the chase if the live bid has collapsed
below three-quarters of the entry bid, at which point the retain rule takes
over. That is a circuit breaker; `minimum_combined_credit` remains the designed
control for credit quality. `entry_leg_retry` now records `quote_source`,
`quote_age_s` and `cushion` so this class of failure is diagnosable from the log
alone rather than needing a live probe to reconstruct.

Covered by `StaleQuotePricingTests`.

### Every exit reports realised P&L (effective 12 August 2026)

Both exit paths — time exit and stop — emit a `session_pnl` event and send a
Telegram message carrying entry credit, exit debit, gross, commission and
**net**, with the reason named in the header (`TIME EXIT` / `STOP LOSS`).

Commission comes from authenticated fills restricted to **this engine's own
order ids**. Attributing by symbol would be wrong: manual trades on the session's
own contract are common — 12 August had 125 puts sold by hand on the very
symbol the bot was trading — and a symbol-level sum would silently absorb their
fees into the automated result.

The lookup is best-effort and runs **after** the position is flat, so it can
neither delay risk reduction nor change the exit status. If it fails, the
message reports gross and says `net unavailable` rather than presenting gross as
net. Covered by `ExitPnlTests`.

### Commission, and why there is no minimum credit (13 August 2026)

Delta charges a flat **4.130% of premium traded**, identical on buys and sells,
with **no fixed floor** — verified across all 262 BTC option fills in the book,
where a $0.0008 premium paid $0.000033 at exactly the same rate. Large premiums
are occasionally charged less where a notional cap binds, so 4.130% is the worst
case and the right planning number.

A round trip pays it twice. Breakeven is buying back at
`(1−r)/(1+r) = 92.07%` of the credit, so **the structure must give back 7.93% of
its entry credit just to cover fees.**

That is a **ratio, not a dollar amount.** Because the fee is perfectly
proportional to premium, a 30-point credit is no more fee-burdened than a
300-point one, and **no absolute minimum credit follows from commission.** The
data agrees: across 23 completed sessions (11 July excluded as the size-1
shakedown) the correlation between entry credit and net outcome is **+0.06**,
and **−0.05** once normalised by credit. A 50-point credit floor would have
blocked four sessions worth about a **THIRD** of the book's lifetime net.

8 August is the case that looks like a small-credit loss and is not: credit
34.50, bought back 34.20, so 0.87% of decay against a 7.93% hurdle. It lost
because spot walked away, not because 34.50 was small.

What must actually pay the 7.93% is **extrinsic** value. Intrinsic does not
decay; selling it is a directional bet, not a theta trade. So the recorded
metric is fee **coverage**:

    hurdle_points  = combined_bid × 0.0793
    coverage_ratio = extrinsic_points / hurdle_points

`fee_coverage` is written every session and `fee_coverage_warning` (plus a
Telegram alert) fires below a coverage of **1.5**. Below 1.0 the trade cannot
cover its own commission from decay at all.

**This gates nothing.** Replayed over the 11 sessions that carry
`market_context`, it fires 5 times — and the flagged and cleared groups have the
*same* mean outcome, a small loss each. It has no demonstrated predictive power yet;
it exists to accumulate the sample. Revisit after three flagged sessions.

Covered by `FeeHurdleTests`.

### Failure handling and alerting (effective 15 August 2026)

On 15 August the 16:55 scheduler lost DNS. Every Delta call failed with
`Failed to resolve 'api.india.delta.exchange'`, the session never started, and
the account was untouched — correct. Three things around it were not.

**1. A failed margin check fell back to the MAXIMUM size.** The script did
`size_check_failed ... falling back to target 150`. That is backwards: a failed
margin check is *missing information*, and the safe response to missing
information is the smallest position, not the largest. Measured against the real
balance that evening — spot ~63,000, credit ~100 — 125 lots were affordable and
the 150-lot fallback demanded **MORE MARGIN THAN THE ACCOUNT HELD**. Preflight failed too, so nothing traded, but had the network recovered in
the seconds between the two steps it would have attempted an unaffordable size.
**A failed size_check is now NO TRADE.**

**2. The alert shared its failure mode with the outage.** Telegram needs the
same network that had just died, so nothing was delivered. `alert()` in
`delta_live/alerting.py` now fans out to Telegram, a macOS desktop notification
and an append-only `outputs/live/ALERTS.log`; the last two need no network, so
an outage cannot silence the report of that outage. `ERROR` and `NO_TRADE` also
drop a dated `ALERT-YYYYMMDD.txt` marker so an unnoticed failure is visible in a
directory listing. `INFO` deliberately does not raise a desktop popup — routine
startup notices would train the alert to be ignored.

**3. The log blamed the wrong thing.** `TelegramNotifier.send()` returned a bare
`False` both when credentials were missing and when the request failed, and the
CLI printed "Telegram is not configured" for both. The credentials were present
and correct. `deliver()` now returns `Delivery.SENT / NOT_CONFIGURED / FAILED`
and the CLI names the actual cause.

**Retries.** `DeltaRESTClient` already retried five times, but its backoff spans
only ~12 seconds and the outage outlasted it. The scheduler now retries the
margin check `DELTA_SIZE_ATTEMPTS` times (default 4) at `DELTA_SIZE_RETRY_SLEEP`
seconds apart (default 45), which fits inside the ~5 minutes between 16:55 and
the 17:00 entry. If every attempt fails it is NO TRADE with an ERROR alert.

Covered by `AlertingTests` and `SizeCheckFallbackTests`.

### Entry guards are scoped to the traded asset (effective 6 August 2026)

`production_preflight` and `run_session` both refuse to start when leftover
state exists. That check is now **asset-scoped** via
`DeltaRESTClient.active_orders_for(asset)`, not account-wide.

On 6 August a reduce-only protective stop on `P-XAUT-4200-070826` aborted both
gates while the BTC book was completely flat. A stop on an unrelated underlying
is not leftover state for a BTC straddle, and blocking on it means an unrelated
hedge elsewhere in the account silently cancels the day's session.

Matching is on the asset appearing anywhere in the product symbol, so it covers
both options (`C-BTC-64600-060826`) and perpetuals (`BTCUSD`). A BTC perp
carries delta on the same underlying and **is** genuine leftover state, even
though it does not use the hyphenated option format. `client.positions(asset)`
was already correctly scoped and is unchanged.

Covered by `ActiveOrderScopeTests` — unrelated asset does not block, same-asset
option blocks, same-asset perpetual blocks, empty book does not block, and
null/missing symbols are ignored rather than raising.

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
