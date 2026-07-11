# Frozen validation: September–December 2025

## Protocol and data integrity

- Validation period: 1 September–31 December 2025, 122 daily expiries.
- Still-unseen period: 1 January–31 August 2025. No symbol or candle from this period was requested.
- Five candidates were frozen from the 2026 research before accessing validation data.
- Size: 200 contracts per leg.
- Base model: 50 bps adverse premium slippage per fill, historical option fees with premium cap, and GST.
- Complete observations: 122/122 for every candidate; 610 total candidate-days.
- Missing data skips: zero; synchronized option-mark coverage between entry and 17:25 was 100%.

This is backward temporal validation because the strategies were researched on later 2026 data. It is useful regime evidence, but it is not a prospective forward test.

## Validation results

| Candidate | Net P&L | Avg trade | Win rate | Avg win | Avg loss | Profit factor | Sharpe | Max DD | DD duration | CVaR 95% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC 17:00 combined 150% | $1,000.78 | $8.20 | 72.1% | $15.67 | -$11.11 | 3.65 | 8.40 | $121.38 | 19 days | -$34.45 |
| BTC 17:00 independent 100% | $872.29 | $7.15 | 67.2% | $15.65 | -$10.27 | 3.12 | 7.42 | $122.80 | 22 days | -$32.18 |
| ETH 15:15 independent 150% | $716.36 | $5.87 | 64.8% | $23.54 | -$26.58 | 1.63 | 3.83 | $327.94 | 44 days | -$65.01 |
| ETH 16:45 combined 150% | $69.07 | $0.57 | 57.4% | $10.98 | -$13.45 | 1.10 | 0.70 | $124.03 | 97 days, unrecovered | -$37.45 |
| BTC 17:00 combined 50% challenger | $999.88 | $8.20 | 71.3% | $15.81 | -$10.72 | 3.66 | 8.66 | $95.75 | 16 days | -$28.05 |

Sharpe is annualized daily net-USD-P&L Sharpe using `sqrt(365)`, not margin-adjusted Sharpe.

## Monthly validation

| Candidate | Sep | Oct | Nov | Dec | Positive months |
|---|---:|---:|---:|---:|---:|
| BTC combined 150% | $65.24 | $293.66 | $359.19 | $282.69 | 4/4 |
| BTC independent 100% | $25.67 | $218.37 | $382.29 | $245.97 | 4/4 |
| ETH 15:15 independent 150% | $222.13 | $88.72 | $245.50 | $160.01 | 4/4 |
| ETH 16:45 combined 150% | $87.96 | -$28.22 | -$8.63 | $17.95 | 2/4 |
| BTC combined 50% | $67.10 | $275.39 | $374.71 | $282.69 | 4/4 |

## Slippage sensitivity: net P&L

| Candidate | 0 bps | 50 bps | 100 bps |
|---|---:|---:|---:|
| BTC combined 150% | $1,039.01 | $1,000.78 | $962.54 |
| BTC independent 100% | $911.17 | $872.29 | $833.42 |
| ETH 15:15 independent 150% | $766.65 | $716.36 | $666.07 |
| ETH 16:45 combined 150% | $98.36 | $69.07 | $39.79 |
| BTC combined 50% | $1,038.12 | $999.88 | $961.64 |

## Option-behaviour diagnostics

The diagnostics file contains only entry-time information in feature columns; subsequent movement and P&L are kept as labels.

For each candidate-day it records:

- ATM strike distance and call/put premium split;
- combined premium in USD and as a percentage of spot;
- upper/lower premium breakevens;
- trailing 15/30/60/120-minute momentum, close range, trend efficiency, and quote coverage;
- expected remaining absolute move from each fixed trailing realized-volatility window;
- premium-to-expected-move richness ratios;
- preceding 15-minute option-premium change and decay;
- subsequent 17:25 absolute move, maximum up/down excursion, strike payoff proxy, premium capture, stop behaviour, fees, MAE/MFE, and P&L.

`Call + put premium` is an implied-move proxy, not model-implied volatility. Historical Greeks, executable spread, and depth cannot be truthfully reconstructed from mark candles.

### Implied-versus-realized observations

| Setup | Mean entry premium | Mean move to 17:25 | Mean actual/premium | Premium exceeded move |
|---|---:|---:|---:|---:|
| BTC 17:00 family | $185.66 | $146.11 | 0.80 | 67.2% of days |
| ETH 15:15 independent | $23.07 | $15.03 | 0.65 | 81.1% of days |
| ETH 16:45 combined | $12.77 | $10.24 | 0.83 | 70.5% of days |

The premium-to-60-minute expected-move ratio had only weak linear correlation with P&L: approximately 0.17–0.18 for BTC and 0.06–0.09 for ETH. Richness alone is therefore not a sufficient entry rule.

Fixed bucket results are suggestive but must not be optimized on this validation set:

- BTC generally improved when richness exceeded 1.25, but the relationship was not monotonic.
- ETH 15:15 lost money when richness was below 0.75, performed best around 1.25–1.50, and became negative again above 1.50. Very high richness may be identifying high-risk volatility regimes rather than free premium.
- ETH 16:45 showed no clean or economically meaningful richness relationship.

## Stop and concentration findings

- BTC combined 150% never stopped during the final 25 minutes; it was effectively a time-exit strategy in this period.
- BTC combined 50% stopped on 10/122 days and produced almost identical profit with lower drawdown and CVaR. For the remaining unseen test, 50% is the better representative of this economic idea.
- BTC independent 100% stopped one leg on 31/122 days and retained strong performance.
- ETH 15:15 independent stopped at least one leg on 33/122 days. Its top 10 days supplied 60.3% of total profit, so its edge is more concentrated than BTC's 46.8%.
- ETH 16:45's top profitable days exceeded its total net result because the remaining days lost money. It fails the robustness test despite a slightly positive aggregate.

## Decision after validation

Pass to the still-unseen January–August 2025 test:

1. BTC 17:00 combined 50% — primary BTC rule; strongest tail-adjusted version of the same final-half-hour edge.
2. BTC 17:00 independent-leg 100% — distinct stop-management challenger.
3. ETH 15:15 independent-leg 150% — passes, but with a smaller allocation hypothesis because of drawdown and profit concentration.

Do not pass:

- ETH 16:45 combined 150% — weak profit factor, only two profitable months, 97-day unrecovered drawdown.
- BTC combined 150% as a separate candidate — it duplicates the 50% rule's economic exposure and supplied no useful stop protection in validation.

No richness threshold should be added before the final holdout. The feature dataset is for understanding and for designing a future, separately validated regime model—not for retroactively improving these frozen rules.
