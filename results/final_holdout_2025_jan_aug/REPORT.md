# Final holdout: January–August 2025

## Protocol

- Period: 1 January–31 August 2025, 243 daily expiries.
- Candidates: exactly three rules promoted after September–December validation.
- No alternate entry times, stop widths, or replacements were searched.
- Size: 200 contracts per leg.
- Base costs: 50 bps adverse premium slippage per fill, historical option fee with premium cap, and GST.
- Complete observations: 243/243 for each candidate; 729 total candidate-days.
- Missing contract, mark, spot, or diagnostic rows: zero.
- This consumes the final historical holdout. Future confirmation must be prospective/paper-traded.

## Final holdout results

| Candidate | Net P&L | Avg trade | Win rate | Avg win | Avg loss | Profit factor | Sharpe | Max DD | DD duration | CVaR 95% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC 17:00 combined 50% | $2,848.81 | $11.72 | 76.1% | $19.73 | -$13.82 | 4.56 | 10.99 | $79.97 | 10 days | -$32.89 |
| BTC 17:00 independent 100% | $2,968.17 | $12.21 | 76.5% | $19.38 | -$11.18 | 5.66 | 12.40 | $48.46 | 10 days | -$23.50 |
| ETH 15:15 independent 150% | $42.10 | $0.17 | 54.7% | $17.77 | -$21.11 | 1.02 | 0.13 | $321.55 | 72 days, unrecovered | -$58.24 |

Sharpe is annualized daily net-USD-P&L Sharpe using `sqrt(365)`, not margin-adjusted Sharpe.

## Monthly stability

| Candidate | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Positive months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC combined 50% | $468.92 | $454.95 | $481.18 | $290.34 | $230.53 | $238.73 | $337.19 | $346.98 | 8/8 |
| BTC independent 100% | $590.25 | $466.24 | $480.40 | $278.34 | $293.71 | $217.28 | $344.17 | $297.79 | 8/8 |
| ETH independent 150% | -$156.88 | -$33.68 | $180.14 | -$49.39 | $229.87 | $20.00 | -$69.63 | -$78.33 | 3/8 |

BTC passed decisively. ETH failed the final holdout despite passing September–December validation.

## Slippage sensitivity

| Candidate | 0 bps | 50 bps | 100 bps |
|---|---:|---:|---:|
| BTC combined 50% | $2,928.84 | $2,848.81 | $2,768.79 |
| BTC independent 100% | $3,047.60 | $2,968.17 | $2,888.74 |
| ETH independent 150% | $133.17 | $42.10 | -$48.96 |

The BTC edge is insensitive to this mark-slippage range. ETH becomes negative at 100 bps and has no defensible margin of safety.

## Premium and movement behaviour

| Setup | Mean entry premium | Mean move to 17:25 | Mean move/premium | Premium exceeded move |
|---|---:|---:|---:|---:|
| BTC 17:00 family | $203.18 | $146.06 | 0.79 | 77.4% of days |
| ETH 15:15 | $19.78 | $17.81 | 1.01 | 65.0% of days |

This directly contradicts a universal claim that Delta option premium is always too low for sellers. In this holdout, late BTC premium systematically exceeded subsequent movement often enough to support sellers, while ETH premium was approximately equal to subsequent movement before costs and tail effects.

The premium-to-60-minute expected-move richness ratio had very weak linear correlation with P&L: about 0.05 for BTC and 0.03 for ETH. It remains a descriptive state variable, not a validated trading filter.

## Stops and concentration

- BTC combined 50% stopped on 16/243 days; independent 100% stopped one leg on 38/243 days.
- BTC top 10 days contributed only 22.0% and 21.1% of total profit respectively. Results were broadly distributed rather than driven by a few outliers.
- ETH stopped at least one leg on 99/243 days.
- ETH's top profitable days exceeded its tiny total net result many times over because the remaining distribution lost almost all of those gains.

## Combined evidence through 10 July 2026

Combining the complete 2025 history with 1 January–10 July 2026 at the same 50 bps model:

| Strategy | Expiries | Net P&L | Win rate | Profit factor | Sharpe | Max DD | CVaR 95% |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC 17:00 combined 50% | 556 | $4,332.31 | 70.1% | 3.13 | 8.03 | $122.96 | -$31.71 |
| BTC 17:00 independent 100% | 556 | $4,237.82 | 67.6% | 3.27 | 8.20 | $122.80 | -$28.26 |
| ETH 15:15 independent 150% | 556 | $1,215.69 | 60.3% | 1.28 | 1.76 | $327.94 | -$58.89 |

The combined ETH number must not override its final-holdout failure. The correct decision rule is based on the untouched test, not the pooled in-sample total.

## Interpretation of exchange pricing

Delta is an exchange venue, not normally the counterparty taking the opposite side of every option trade. Premium is formed by buyers, sellers, and market makers in the order book. Delta supplies the mark methodology, price bands, margin/risk engine, and collects fees.

The evidence supports an asset- and horizon-specific conclusion:

- Late BTC 0DTE premium was rich relative to the following 25-minute move across both validation and final holdout periods.
- ETH 15:15 premium did not provide a stable post-cost volatility risk premium.
- Fees are material: across the 556-day combined evidence they consumed about $1,341 for BTC combined and $1,346 for BTC independent at 200 contracts.
- Mark-price profitability still does not prove that a 200-contract order can execute at the modeled prices.

## Final research verdict

Promote to prospective paper testing:

1. BTC 17:00 combined 50%.
2. BTC 17:00 independent-leg 100% as a challenger; do not trade both simultaneously because their economic exposure is highly correlated.

Reject for current deployment:

- ETH 15:15 independent-leg 150%.
- All other fixed-time rules previously screened.

No historical data remains genuinely unseen. The next evidence must come from prospective bid/ask and depth collection, simulated 200-contract fills, and paper-trade monitoring.
