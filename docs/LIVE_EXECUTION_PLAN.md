# BTC 17:00 0DTE assisted execution plan

Version: draft 1, dated 11 July 2026. This is an assistive/manual plan, not authorization for unattended live execution.

## Strategy selected

- Underlying: BTC.
- Contract: nearest common ATM call and put expiring at 17:30 IST.
- Signal time: 17:00:00 IST.
- Historical size reference: 200 contracts per leg (`0.2 BTC` when contract value is `0.001`). Live size must be explicitly confirmed against available capital and daily loss cap.
- Primary historical rule: combined-premium 50% stop, forced exit at 17:25.
- Operational challenger: independent-leg 100% stop, with every surviving leg forced out at 17:25.
- Trade only one rule on a given day; never stack both.

## Mandatory pre-trade inputs by 16:59:30

1. Current API/system status and timestamp freshness.
2. Exact expiry, strike, product IDs, contract values and tick sizes.
3. Spot, mark, last trade, best bid/ask and first several depth levels for both legs.
4. Depth-adjusted executable sell VWAP for the intended quantity on both legs.
5. Combined mark/mid and combined executable sell credit.
6. Available balance, initial margin, post-trade free margin and liquidation stress.
7. User-approved quantity and maximum daily dollar/INR loss.

If quantity or maximum daily loss is missing, status is `NO TRADE`.

## Liquidity gate

Do not force a trade simply because the clock reaches 17:00.

- Full intended size must be visible or plausibly executable within the recorded depth snapshot.
- Combined depth-adjusted credit must be at least 95% of combined mark/mid. A lower ratio means historical mark-price results are not comparable to the live fill.
- No individual leg may have a spread wider than 15% of mid without explicit manual approval and smaller size.
- Quotes must be fresh within two seconds and market/system status must be normal.
- If the gate fails, reduce size once to the quantity supported by depth or record `NO TRADE`; do not chase.

## Entry sequence: 17:00:00–17:00:20

1. Freeze the ATM strike at 16:59:55 using contemporaneous spot.
2. Submit both sell limits as close together as the interface/API permits, using marketable limits with a predeclared minimum credit—not unrestricted market orders.
3. Slice large orders into matched call/put quantities; never allow total filled call and put quantities to diverge beyond one slice.
4. Allow a maximum 20-second entry window. Reprice only within the minimum combined-credit constraint.
5. If only one leg fills and the other cannot fill within five seconds, cancel the remainder and flatten the unmatched exposure with a bounded marketable limit.
6. Record mark, last trade, bid/ask, depth, order IDs and fills at every attempt.

“Exactly 17:00” means the decision and first paired submissions occur at 17:00; it does not mean accepting an unlimited spread.

## Stop design after the mark-trigger anomaly

Delta supports `mark_price`, `last_traded_price`, and `spot_price` as stop trigger methods. Never accept an unspecified/default trigger.

### Recommended primary: combined executable-premium monitor

- Threshold: 1.5 times the actual combined entry credit for the combined-50% strategy.
- Trigger input: depth-adjusted buyback VWAP for the full remaining call and put quantity, not mark alone.
- Persistence: threshold must be met in two consecutive fresh observations at least one second apart.
- Action: paired reduce-only marketable limit buybacks, sliced and matched by remaining quantity.
- If one leg closes first, immediately close the residual leg; do not convert a failed combined exit into a directional position.

### If using independent 100% stops manually

- Set `stop_trigger_method=last_traded_price`, not `mark_price`, and verify the returned order object.
- Threshold: two times that leg's actual sell fill.
- Stop order must be reduce-only.
- Because last trade can be stale in an illiquid option, run a parallel executable-ask monitor: if full-size buyback VWAP exceeds the threshold persistently, close even without a qualifying last trade.
- Record any divergence among mark, last trade, best ask and actual buyback fill.

### Emergency protection

- Maintain an account-level hard loss/low-margin kill condition independent of leg stops.
- If API/data becomes stale, system status degrades, or margin approaches the approved floor, close both legs with bounded marketable limits.
- Never rely on cancellation/re-entry as a repair strategy.

## Exit

- Begin paired reduce-only exit at 17:24:30 and complete by 17:25:00.
- Use bounded marketable limits and record mark, last trade, bid/ask, depth-adjusted VWAP and actual fills.
- Close every surviving leg; do not rely on 17:30 settlement during prospective validation.
- Reason: this matches the historical test and avoids the final five minutes of gamma/TWAP-settlement risk. It is not based on a claim that settlement fees are always higher.
- Fee note: current manual option closes and nonzero settlement use the same 0.010% notional rate with a 3.5% premium cap, plus GST. A worthless option settles with no fee. The dollar amount can differ because the option value differs.

## Daily evidence package at 18:00

- Orders and fills with trigger methods, fees and roles.
- One-second or best-available spot, mark, last trade, bid/ask and depth from 16:55–17:30.
- Entry/exit depth-adjusted VWAP versus mark.
- Gross/net P&L, maximum adverse/favourable excursion, stop events and latency.
- Combined premium versus realized movement.
- Rule adherence, anomalies and one next-session rule.

## Controls still required before unattended execution

- Confirm live quantity; do not assume 200 contracts after the user reported capital constraints.
- Confirm maximum loss per trade and per day in account currency.
- Confirm minimum free-margin floor and emergency flatten rule.
- Whitelist the current execution machine IP and validate authenticated reads on testnet/production as appropriate.
- Run at least several days of assisted/manual execution with complete depth capture.
- Implement dry-run, idempotent client order IDs, duplicate-order prevention, heartbeat, kill switch and reconciliation before live automation.
