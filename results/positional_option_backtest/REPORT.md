# Positional pivot/Camarilla option-leg backtest: January–March 2025

Status: January–March 2025 baseline plus January–March 2026 comparison. This remains a diagnostic study; no mode is promoted.

## Frozen option expressions

- Classic R1/S1 breakout with daily and 4-hour trend confirmation: 2DTE debit vertical.
- Camarilla CR3/CS3 rejection: 1DTE credit vertical with a protective wing.
- 50% adverse fill slippage per leg, historical 0.01% option fee capped at 3.5% of premium, and 18% GST.
- 200 contracts per leg; 30-minute mark candles for the option holding path.
- 50% debit-loss stop / 100% credit-loss stop, fixed profit target, and expiry-day 17:25 exit.

## Results

| Asset | Executable trades | Net P&L | Stops | Targets |
|---|---:|---:|---:|---:|
| BTC | 17 | -$368.40 | 10 | 7 |
| ETH | 15 | -$1.56 | 4 | 11 |

### BTC by mode

| Mode | Trades | Net P&L | Win rate | Profit factor |
|---|---:|---:|---:|---:|
| Credit call vertical | 7 | -$171.86 | 0.0% | 0.00 |
| Credit put vertical | 6 | -$122.03 | 0.0% | 0.00 |
| Debit call vertical | 1 | -$25.21 | 0.0% | 0.00 |
| Debit put vertical | 3 | -$49.30 | 33.3% | 0.14 |

### ETH by mode

| Mode | Trades | Net P&L | Win rate | Profit factor |
|---|---:|---:|---:|---:|
| Credit call vertical | 3 | -$14.84 | 66.7% | 0.37 |
| Credit put vertical | 7 | +$7.90 | 85.7% | 1.31 |
| Debit call vertical | 1 | +$11.37 | 100.0% | n/a |
| Debit put vertical | 4 | -$5.98 | 50.0% | 0.80 |

## Coverage warning

The spot signal generator produced 148 BTC and 135 ETH signals in Q1, but only 17 BTC and 15 ETH had complete, resolvable target-expiry option legs and usable mark paths. This low coverage is caused by historical product/candle availability and must be improved before interpreting the strategy. The results are therefore directional diagnostics, not a promotion decision.

## Decision

Do not tune or deploy the pivot rules from this three-month sample. BTC is clearly negative in this first option expression. ETH credit-put verticals are mildly positive but have only seven observations. The remaining nine months of 2025 and the January–June 2026 forward period should be run only after the contract-resolution coverage audit is improved and the frozen rules are unchanged.
## January–March 2026 comparison

| Asset | Spot signals | Executable trades | Coverage | Net P&L |
|---|---:|---:|---:|---:|
| BTC | 144 | 23 | 16.0% | -$115.49 |
| ETH | 137 | 18 | 13.1% | -$114.43 |

### BTC 2026 Q1 modes

| Mode | Trades | Net P&L | Win rate | Avg win | Avg loss | Sharpe | Max DD | Stop rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Credit call vertical | 9 | -$68.89 | 55.6% | $5.77 | -$24.43 | -9.13 | $89.45 | 44.4% |
| Credit put vertical | 8 | -$21.90 | 75.0% | $3.21 | -$20.59 | -4.72 | $27.07 | 25.0% |
| Debit put vertical | 6 | -$24.70 | 33.3% | $19.44 | -$15.90 | -4.30 | $63.58 | 66.7% |

### ETH 2026 Q1 modes

| Mode | Trades | Net P&L | Win rate | Avg win | Avg loss | Sharpe | Max DD | Stop rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Credit call vertical | 7 | -$19.14 | 57.1% | $8.01 | -$17.06 | -3.85 | $35.81 | 42.9% |
| Credit put vertical | 10 | -$78.64 | 40.0% | $7.88 | -$18.36 | -10.95 | $85.25 | 60.0% |
| Debit call vertical | 1 | -$16.65 | 0.0% | n/a | -$16.65 | n/a | $16.65 | 100.0% |

No fixed pivot/Camarilla vertical mode is profitable across both Q1 samples. The 2026 coverage improved but remains only 13–16%, so the option-resolution pipeline is still incomplete.
