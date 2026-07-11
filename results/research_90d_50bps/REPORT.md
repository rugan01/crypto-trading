# BTC/ETH 0DTE Straddle — 90-expiry research window

## Research boundary

- Research period: 12 April–10 July 2026, 90 daily expiries per asset.
- Proposed untouched holdout: 12 April 2025–11 April 2026, subject to Delta historical contract/candle availability.
- The holdout has not been downloaded, ranked, or inspected in this study.
- Size: 200 contracts per leg: 0.2 BTC or 2 ETH using the historical contract values.
- Entry grid: every 15 minutes from 13:00 through 17:00 IST.
- Variants: combined, independent-leg, and close-both-on-first-leg stops at 50%, 100%, and 150%.
- Forced exit: 17:25 IST; all 180 audited asset-expiries settled at 17:30 IST.
- Base execution model: one-minute mark closes, 50 bps adverse premium slippage per fill, historical 0.01% taker fee capped at 3.5% of premium, and 18% GST.
- Simulations: 27,540; missing-data skips: zero.

Each configuration below represents one alternative daily strategy. Results cannot be summed across entry times.

## Leading 90-expiry candidates

| Asset | Entry | Exit rule | Net P&L | Avg win | Avg loss | Win rate | Profit factor | Annualized daily Sharpe | Max DD | DD duration | 95% CVaR |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 17:00 | Combined 50% | $233.02 | $9.20 | -$10.62 | 66.7% | 1.73 | 4.33 | $69.32 | 13 days | -$23.11 |
| BTC | 17:00 | Combined 150% | $227.29 | $9.31 | -$11.74 | 67.8% | 1.67 | 3.92 | $70.87 | 22 days | -$29.97 |
| BTC | 17:00 | Combined 100% | $204.61 | $9.31 | -$12.52 | 67.8% | 1.56 | 3.27 | $81.24 | 17 days | -$34.59 |
| BTC | 17:00 | Independent 100% | $165.25 | $8.63 | -$8.85 | 61.1% | 1.53 | 3.26 | $39.66 | 24 days | -$24.53 |
| ETH | 13:00 | Independent 100% | $502.91 | $12.55 | -$9.83 | 68.9% | 2.83 | 7.74 | $86.38 | 15 days | -$26.56 |
| ETH | 13:00 | Close both 100% | $433.42 | $12.27 | -$8.69 | 64.4% | 2.56 | 6.98 | $51.08 | 14 days | -$21.34 |
| ETH | 13:15 | Independent 50% | $401.15 | $10.61 | -$10.68 | 71.1% | 2.45 | 6.83 | $48.91 | 15 days | -$27.67 |
| ETH | 13:15 | Independent 100% | $419.93 | $11.43 | -$11.12 | 70.0% | 2.40 | 6.58 | $53.96 | 17 days | -$27.03 |

Sharpe uses the mean and sample standard deviation of one net P&L observation per daily expiry, annualized by `sqrt(365)` with zero risk-free rate. It is not return-on-capital Sharpe because historical margin was not reconstructed. The values are also in-sample and subject to selection bias.

## Original 30-expiry leaders after adding 60 expiries

| Candidate | 30-expiry avg | 90-expiry avg | 90-day result |
|---|---:|---:|---|
| BTC 15:30 combined 100% | $5.75 | $2.37 | Still profitable, but max DD expanded to $309.55 and underwater duration to 66 days |
| BTC 17:00 combined 50% | $3.32 | $2.59 | Survived with stronger risk-adjusted behaviour: $69.32 DD and 13 underwater days |
| ETH 13:00 independent 100% | $6.20 | $5.59 | Survived strongly; profit factor 2.83 |
| ETH 13:00 close-both 100% | $4.27 | $4.82 | Improved; lower drawdown than the independent version |

The additional data materially weakened the BTC 15:30 result and strengthened the case for the final-half-hour BTC family. This is exactly why the larger research window was necessary.

## Slippage sensitivity: average net P&L per expiry

| Candidate | 0 bps | 50 bps | 100 bps |
|---|---:|---:|---:|
| BTC 17:00 combined 50% | $2.82 | $2.59 | $2.36 |
| BTC 17:00 combined 150% | $2.75 | $2.53 | $2.30 |
| BTC 17:00 independent 100% | $2.07 | $1.84 | $1.60 |
| ETH 13:00 independent 100% | $5.88 | $5.59 | $5.29 |
| ETH 13:00 close-both 100% | $5.12 | $4.82 | $4.52 |
| ETH 13:15 independent 50% | $4.75 | $4.46 | $4.16 |

## Stability assessment

- ETH independent 100% was profitable at 13 of 17 entry times; close-both 100% was profitable at 12 of 17. The early ETH effect is relatively broad rather than confined to one timestamp.
- BTC combined 50% was profitable at only 2 of 17 entry times. Both cluster near the end of the session, but this remains a narrower and less certain pattern.
- The 17:00 BTC combined variants are very similar. Taking all three into the holdout would count the same underlying idea multiple times. Freeze one primary rule and, at most, one sensitivity challenger.
- The 90-day Sharpe estimates are unusually high for the ETH leaders. They should be treated as provisional because they use mark valuations and were selected from 153 configurations per asset.

## Suggested frozen shortlist for the one-year holdout

1. ETH 13:00 independent-leg 100% — strongest expectancy and profit factor.
2. ETH 13:00 close-both 100% — safer operational alternative and lower observed drawdown.
3. ETH 13:15 independent-leg 50% — neighbouring-time robustness without duplicating the exact 13:00 rule.
4. BTC 17:00 combined 50% — best BTC Sharpe and tail balance.
5. BTC 17:00 independent-leg 100% — distinct stop handling and lowest drawdown among leading BTC rules.

This shortlist should be frozen before querying the reserved year. No replacement or parameter tuning should be allowed after seeing holdout performance. If historical product discovery cannot reach the full reserved year, the limitation must be reported rather than shortening the holdout silently.

## Remaining execution limitations

- Mark prices are valuations, not executable bid/ask fills.
- Historical depth for a 200-contract order is unavailable.
- Stops observe synchronized one-minute closes, not tick-level crossings.
- Margin and return on capital remain unavailable historically.
- A prospective bid/ask and depth paper test remains mandatory before live use.
