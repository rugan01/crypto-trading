# BTC 17:00 combined-50% premium and balance analysis

## Scope and controls

- Strategy: BTC 17:00 ATM short straddle, combined-premium 50% stop, historical 17:25 exit.
- Period: 1 January 2025–10 July 2026.
- Observations: 556 daily expiries.
- Costs: 50 bps adverse slippage per fill plus historical fees/GST.
- Entry premium: modeled executable sell credit after slippage, not raw mark.
- Purpose: descriptive understanding only. No premium or balance threshold is promoted from this same dataset.

During this analysis, two catalogue-boundary dates (11–12 April 2026) were found to use a far-away strike because Delta's capped product catalogue contained an incomplete expiry. The resolver now rejects a catalogue strike more than one normal interval from spot and resolves the true ATM symbol directly. The 2026 research and this report were regenerated after the fix.

## Overall result

- Win probability: 70.3% (95% Wilson interval 66.4%–74.0%).
- Net P&L: $4,360.27 for 200 historical contracts.
- Expectancy: $7.84 per expiry.
- Average winner/loss: $16.32 / -$12.24.
- Profit factor: 3.16.
- Maximum drawdown: $122.96.
- Combined stop frequency: 10.3%.
- Median combined premium: $158.99; mean $172.84.

The edge was stronger in 2025 than 2026: average P&L $10.54 and win rate 74.5% in 2025 versus $2.68 and 62.3% in 2026.

## Is premium above $50 better?

The data cannot answer `<= $50` reliably: only 2/556 historical entries had modeled credit at or below $50. Their 100% observed win rate has an extremely wide 95% interval of approximately 34%–100%.

| Combined premium | N | Win probability | 95% CI | Expectancy | Profit factor | 2025 avg | 2026 avg |
|---|---:|---:|---:|---:|---:|---:|---:|
| ≤$50 | 2 | 100.0% | 34.2%–100% | $4.53 | n/a | n/a | $4.53 |
| $50–75 | 22 | 68.2% | 47.3%–83.6% | $0.04 | 1.01 | $4.41 | -$0.65 |
| $75–100 | 58 | 72.4% | 59.8%–82.2% | $4.40 | 3.49 | $3.14 | $4.84 |
| $100–150 | 167 | 68.3% | 60.8%–74.8% | $3.93 | 2.16 | $5.52 | $2.39 |
| $150–200 | 143 | 64.3% | 56.2%–71.7% | $4.26 | 1.91 | $5.81 | -$3.05 |
| >$200 | 164 | 76.8% | 69.8%–82.6% | $17.26 | 5.59 | $18.03 | $10.61 |

The lowest economically meaningful band, $50–75, was approximately breakeven after costs. Premium above $200 performed best and remained positive in both years. The relationship is not monotonic: $150–200 was weaker than both $100–150 and >$200, and it was negative during 2026.

Using equal-sized premium quintiles produced the same broad observation: the highest quintile, above approximately $223, had an 80.2% win rate and $22.69 expectancy. This threshold is descriptive and must not be retrofitted as a live entry filter.

## Premium normalized by BTC spot

Absolute premium naturally rises with BTC price and volatility. Premium as a percentage of spot is more comparable across regimes.

| Premium/spot quintile | Approximate range | N | Win probability | Expectancy | Profit factor |
|---|---|---:|---:|---:|---:|
| Q1 | <0.123% | 112 | 71.4% | $3.46 | 2.29 |
| Q2 | 0.123%–0.156% | 111 | 70.3% | $4.39 | 2.42 |
| Q3 | 0.156%–0.190% | 111 | 66.7% | $4.96 | 2.16 |
| Q4 | 0.190%–0.244% | 111 | 73.9% | $9.19 | 3.87 |
| Q5 | >0.244% | 111 | 69.4% | $17.26 | 4.49 |

Higher normalized premium increased dollar expectancy, but did not monotonically increase win probability. Premium/spot correlation with P&L was about 0.33; useful context, not a standalone predictor.

## Are the legs normally balanced?

Define `smaller-leg share = min(call premium, put premium) / combined premium`. A perfectly balanced straddle is 50%.

- Median smaller-leg share: 24.1%.
- Mean: 24.8%.
- Only 30.9% of entries had the smaller leg at least 35% of combined premium.

The legs were therefore usually not balanced at 17:00, despite selecting the nearest listed ATM strike. Fast spot movement, strike spacing, skew and mark behaviour can create a dominant leg during the entry/fill window.

| Smaller-leg share | N | Win probability | 95% CI | Expectancy | Profit factor |
|---|---:|---:|---:|---:|---:|
| <20% | 235 | 58.7% | 52.3%–64.8% | $2.68 | 1.63 |
| 20%–30% | 93 | 71.0% | 61.0%–79.2% | $6.82 | 2.69 |
| 30%–40% | 106 | 80.2% | 71.6%–86.7% | $11.24 | 4.35 |
| 40%–50% | 122 | 83.6% | 76.0%–89.1% | $15.61 | 7.50 |

This is the clearest descriptive relationship in the study. Entries with smaller-leg share at least 35% had:

- N = 172;
- win probability 83.1% (95% CI 76.8%–88.0%);
- expectancy $14.95;
- profit factor 7.94;
- maximum drawdown $62.32;
- positive average P&L in both 2025 and 2026.

Below 35%, N = 384, win probability fell to 64.6%, expectancy to $4.66, profit factor to 2.08, and drawdown rose to $244.12.

Balance-P&L correlation was about 0.29. The bucket effect is economically meaningful, but the 35% cut was examined on the full history and is not a validated trading threshold.

## Combined premium and balance

For premium above $50:

- balanced ≥35%: N=171, win probability 83.0%, expectancy $15.00, profit factor 7.93;
- unbalanced <35%: N=383, win probability 64.5%, expectancy $4.66, profit factor 2.08.

There is insufficient evidence for the same comparison at or below $50 because only two observations existed.

## Interpretation of the 11 July live trade

The reconciled BTC entry credit was exactly $50: put $35 and call $15. The smaller-leg share was `15/50 = 30%`.

- The absolute-premium history has almost no observations at or below $50, so no credible win probability can be assigned from that threshold.
- A 30% smaller-leg share sits at the boundary of the historically stronger 30%–40% balance band, but the actual legs filled 279 seconds apart. It should not be treated as a clean balanced-entry observation.
- The profitable result is encouraging execution evidence, not proof of the bucket probability.

## Prospective use without overfitting

Do not skip trades yet based on $200 premium or 35% balance. For every future 17:00 observation, record these predeclared tags:

- premium band: `≤75`, `75–100`, `100–150`, `150–200`, `>200`;
- premium/spot quintile using the frozen cut points in `summary.json`;
- smaller-leg share and balance tag: `<20%`, `20–30%`, `30–40%`, `40–50%`;
- executable combined credit versus mark, leg-fill delay, spread and depth.

After at least 30–60 prospective expiries, compare the frozen descriptive probabilities with live executable outcomes. Only then decide whether balance belongs in a no-trade or position-sizing rule.
