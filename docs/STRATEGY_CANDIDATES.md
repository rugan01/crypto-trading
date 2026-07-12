# Additional BTC/ETH option strategy candidates

Status: research design only. No candidate below is approved for live trading.

## Evidence base

The completed research covers 556 daily expiries from 1 January 2025 through 10 July 2026, with one-minute option marks, spot candles, ATM contract resolution, and diagnostic features. Existing results use 200 contracts per leg, 50 bps adverse premium slippage, the historical fee model, GST, and a 17:25 IST forced exit.

The strongest facts are:

- BTC 17:00 premium selling survived the 2025 holdout. The 50% combined-stop rule produced USD 2,848.81, 76.1% wins, 4.56 profit factor, USD 79.97 maximum drawdown and 10-day maximum drawdown duration.
- BTC 17:00 independent 100% was similar: USD 2,968.17, 76.5% wins, 5.66 profit factor, USD 48.46 maximum drawdown.
- ETH 15:15 independent 150% nearly failed the holdout: USD 42.10, 1.02 profit factor, 0.13 Sharpe and 72-day unrecovered drawdown.
- BTC 17:00 combined premium was greater than the subsequent move on 77.4% of 556 observations, but the relationship was not monotonic.
- BTC leg balance was the clearest descriptive feature: smaller-leg share at least 35% had 83.1% wins and USD 14.95 expectancy versus 64.6% and USD 4.66 below 35%. This threshold was discovered on the full history and must not be promoted without a fresh walk-forward test.
- Richness, momentum, trend efficiency and range are useful regime labels, not standalone signals. Their correlations with P&L were weak and non-monotonic.

## Candidate A: balanced late BTC/ETH short straddle

Hypothesis: a late 0DTE straddle is safer when both legs contribute meaningful premium, because extreme skew/imbalance identifies directional or gap risk.

- Entry: 17:00 IST; nearest common ATM call and put.
- Gate: smaller-leg share >=35%; do not use this cut until it is frozen from a development fold.
- Exit: combined executable-premium stop at 50% of credit; paired forced exit at 17:25.
- Assets: BTC and ETH tested separately.
- Why it is different: it tests the market-profile/balance observation as an entry eligibility rule, not another stop-time grid.
- Main risk: selection bias because 35% was observed after the full-history analysis; freeze a training-only cut or test a single prespecified 30% cut.

## Candidate B: one-step OTM short strangle

Hypothesis: moving each short leg one listed strike outside ATM reduces immediate directional sensitivity and may improve execution/margin, at the cost of lower credit and more frequent terminal losses.

- Entry: 17:00 IST; short the nearest paired call above ATM and put below ATM.
- Exit: combined 100% stop on executable buyback, plus 17:25 paired exit.
- Control: compare one fixed strike step only; do not search two, three and four steps in the first pass.
- Assets: BTC and ETH.
- Metrics to emphasize: credit per margin dollar, terminal edge ratio, tail loss, fee drag and percentage of days where one short leg finishes ITM.
- Main risk: the smaller credit may be swallowed by four taker fees across any later hedge or repair.

## Candidate C: defined-risk 0DTE iron fly

Hypothesis: the late BTC short-straddle effect may survive with a long protective wing, reducing tail and margin risk enough to improve return on capital even after two extra fees.

- Entry: 17:00 IST; sell the common ATM call/put and buy one fixed wing two listed strikes away on each side.
- Exit: 100% loss of net credit or 17:25; no discretionary repair.
- Position: one long wing for every short leg; equal contracts.
- Assets: BTC first, ETH only if the wing quotes pass liquidity.
- Metrics to emphasize: net credit, defined maximum loss, return on max risk, fee/gross ratio, max drawdown and stop frequency.
- Main risk: long wings can be wide/illiquid and the extra two fills can make the structure worse than the naked straddle.

## Candidate D: long ATM gamma straddle

Hypothesis: a long straddle should outperform when the realised move exceeds the market's short-dated premium, providing diversification against the short-premium strategy.

- Entry: buy the nearest common ATM call/put at 15:00 IST; one fixed entry time.
- Exit: sell both at 17:25, or a 100% mark-based profit target only if that rule is frozen before scoring. First pass uses time exit only.
- Assets: BTC and ETH.
- Metrics to emphasize: net P&L after four fees, win rate, average loss, premium-to-realised-move ratio, maximum adverse excursion and percentage of premium recovered.
- Main risk: four taker fees and rapid 0DTE theta decay; a mark-price positive result is not enough without executable bid/ask evidence.

## Optional Candidate E: momentum-conditioned vertical credit spread

This is a fifth challenger only if implementation capacity allows it.

- Entry: 15:00 IST; if trailing 60-minute momentum is positive, sell the nearest OTM put and buy the next lower put; if negative, sell the nearest OTM call and buy the next higher call. If momentum is near zero, no trade.
- Exit: 100% of initial net credit, or 17:25.
- Freeze one momentum definition and one strike step before validation.
- This is a directional option strategy with defined risk, not a disguised straddle.
- Main risk: trend-efficiency and momentum labels were weakly predictive in the existing data; this should be treated as a diversification experiment, not an expected winner.

## Research order

1. Candidate A: cheapest extension because the current ATM simulator already contains the needed legs.
2. Candidate B: tests skew/strike selection while retaining short-premium economics.
3. Candidate C: tests whether defined risk improves capital efficiency.
4. Candidate D: tests the opposite volatility exposure and guards against a one-regime conclusion.
5. Candidate E only after A–D are implemented and audited.

## Frozen chronological validation protocol

The old 2025 holdout is no longer genuinely unseen because it has already been inspected for the first strategy family. It may be used as a secondary regime-stress report, not as a clean final test.

For each candidate:

1. Development: 1 January–31 March 2026. Freeze contract step, entry time, stop and all gates here.
2. Walk-forward validation: 1 April–31 May 2026. No replacement or retuning after seeing results.
3. Confirmation: 1 June–10 July 2026. Score the frozen candidate exactly once.
4. Secondary regime stress: September–December 2025 and January–August 2025, labelled as previously exposed historical data.
5. Final evidence: 30–60 prospective paper expiries with live bid/ask/depth capture. This is the only clean unseen stage remaining.

No candidate is promoted on aggregate P&L alone. Promotion requires positive net expectancy in both walk-forward periods, no unrecovered drawdown beyond the test window, profit factor above 1.2 after 100 bps slippage, fee drag below 40% of gross P&L, and no liquidity gate failure rate above 20% at the intended size. These are predeclared research gates, not guarantees.

## Required output for every candidate

Net P&L, expectancy, win rate, average winner/loser, profit factor, annualized daily Sharpe, maximum drawdown, drawdown duration, 95% CVaR, stop frequency, forced-exit frequency, MAE/MFE, premium-to-realised-move ratio, balance/skew buckets, richness buckets, fees as a percentage of gross P&L, return on margin/max risk, slippage sensitivity at 0/50/100 bps, and executable-depth coverage.

The mark-only historical results must remain labelled as provisional until prospective order-book paper fills validate them.
