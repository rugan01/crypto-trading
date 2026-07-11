# BTC/ETH 0DTE Straddle — 1 January to 10 July 2026

## Boundary and coverage

- Research period: 1 January–10 July 2026, inclusive.
- Reserved unseen period: 1 January–31 December 2025. No 2025 contract, symbol, or candle was queried.
- Coverage: 191/191 daily expiries for both BTC and ETH.
- Simulations: 58,446 at the base model; no missing-price or contract skips.
- Size: 200 contracts per leg, equal to 0.2 BTC or 2 ETH using the historical contract values.
- Entry grid: every 15 minutes from 13:00 through 17:00 IST.
- Variants: combined, independent-leg, and close-both-on-first-leg stops at 50%, 100%, and 150%.
- Base costs: 50 bps adverse option-premium slippage per fill, historical 0.01% taker fee capped at 3.5% of premium, plus 18% GST.

Delta's expired-product list stopped near April because of its 10,000-product ceiling. January–early April contracts were resolved directly by deterministic symbol and verified through product metadata. This did not require inspecting 2025.

## Strongest full-period results

| Asset | Entry | Exit rule | Net P&L | Avg win | Avg loss | Win rate | Profit factor | Annualized daily Sharpe | Max DD | DD duration | CVaR 95% |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 17:00 | Combined 150% | $608.08 | $11.39 | -$10.69 | 62.8% | 1.80 | 4.36 | $70.87 | 22 days | -$27.34 |
| BTC | 17:00 | Combined 100% | $557.02 | $11.39 | -$11.41 | 62.8% | 1.69 | 3.83 | $81.24 | 19 days | -$30.40 |
| BTC | 17:00 | Independent 100% | $397.35 | $11.27 | -$9.87 | 56.5% | 1.48 | 2.84 | $98.64 | 24 days | -$29.27 |
| BTC | 15:30 | Independent 50% | $627.41 | $17.54 | -$15.27 | 56.5% | 1.50 | 2.80 | $150.96 | 56 days | -$44.10 |
| ETH | 15:15 | Independent 150% | $457.22 | $11.45 | -$13.98 | 64.4% | 1.48 | 2.66 | $136.39 | 42 days | -$41.47 |
| ETH | 16:15 | Independent 50% | $212.69 | $7.70 | -$6.93 | 55.0% | 1.36 | 2.23 | $87.50 | 114 days | -$18.42 |
| ETH | 16:45 | Combined 150% | $172.28 | $6.00 | -$7.52 | 62.3% | 1.32 | 1.93 | $64.23 | 78 days | -$23.60 |

Sharpe is calculated from one net USD P&L observation per daily expiry and annualized with `sqrt(365)`. It is not margin-adjusted Sharpe, and it remains in-sample.

## Slippage sensitivity: average net P&L per expiry

| Candidate | 0 bps | 50 bps | 100 bps |
|---|---:|---:|---:|
| BTC 17:00 combined 150% | $3.44 | $3.18 | $2.92 |
| BTC 17:00 combined 100% | $3.18 | $2.92 | $2.65 |
| BTC 17:00 independent 100% | $2.35 | $2.08 | $1.81 |
| ETH 15:15 independent 150% | $2.65 | $2.39 | $2.13 |
| ETH 16:15 independent 50% | $1.32 | $1.11 | $0.91 |
| ETH 16:45 combined 150% | $1.07 | $0.90 | $0.73 |

All listed candidates remain positive under 100 bps slippage, but this still does not prove that 200 contracts can execute near mark.

## Subperiod and monthly stability

| Candidate | Jan–Mar P&L | Apr–10 Jul P&L | Positive months |
|---|---:|---:|---:|
| BTC 17:00 combined 150% | $343.83 | $264.25 | 7/7 |
| BTC 17:00 combined 50% | $227.80 | $255.81 | 7/7 |
| BTC 17:00 independent 100% | $223.65 | $173.70 | 6/7 |
| BTC 15:30 independent 50% | $663.10 | -$35.69 | 4/7 |
| ETH 15:15 independent 150% | $212.16 | $245.07 | 6/7 |
| ETH 16:15 independent 50% | $226.53 | -$13.84 | 5/7 |
| ETH 16:45 combined 150% | $91.62 | $80.66 | 6/7 |

The BTC 15:30 and ETH 16:15 results are less robust than their full-period rank suggests because their profits are concentrated in January–March. BTC 17:00 combined and ETH 15:15 independent survived both subperiods.

## What changed versus the 90-expiry study

The early-ETH hypothesis did not survive the longer history:

| Previous candidate | Full-period net P&L | Profit factor | Sharpe | Max DD | DD duration |
|---|---:|---:|---:|---:|---:|
| ETH 13:00 independent 100% | $24.65 | 1.02 | 0.11 | $600.21 | 183 days, unrecovered |
| ETH 13:00 close-both 100% | $122.85 | 1.10 | 0.62 | $431.37 | 170 days |
| ETH 13:15 independent 50% | $171.40 | 1.14 | 0.90 | $488.15 | 156 days, unrecovered |
| BTC 17:00 combined 50% | $483.61 | 1.56 | 3.26 | $122.96 | 21 days |
| BTC 17:00 independent 100% | $397.35 | 1.48 | 2.84 | $98.64 | 24 days |

The recent 90-day sample substantially overstated the early ETH rules. The BTC final-half-hour family survived and became the clearest repeatable pattern.

## Revised candidate recommendation before touching 2025

Primary candidates:

1. BTC 17:00 combined 150% — strongest full-period risk-adjusted result and profitable in all seven calendar months.
2. BTC 17:00 independent 100% — genuinely different stop handling with balanced average win/loss.
3. ETH 15:15 independent 150% — best ETH result and profitable in both broad subperiods.
4. ETH 16:45 combined 150% — lower expectancy but lower drawdown and positive in both subperiods.

Optional fifth challenger:

5. BTC 17:00 combined 50% — robust, but highly correlated with candidate 1. It should be treated as a stop-width sensitivity challenger, not an independent strategy.

Do not carry BTC 15:30 independent 50% or ETH 16:15 independent 50% into the holdout merely because their full-period totals are positive; their subperiod deterioration is material.

## Holdout protocol

- Freeze the four primary candidates and one optional challenger before querying 2025.
- Run exactly those parameters from 1 January–31 December 2025.
- Do not replace failed candidates, change entry times, or optimize stops after seeing holdout results.
- Report missing historical daily expiries or candles explicitly.
- Keep mark-price results provisional until prospective bid/ask depth validates 200-contract fills.
