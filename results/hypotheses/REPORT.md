# New option hypotheses: development and walk-forward results

Status: exploratory research; no candidate is approved for live trading.

## Fixed rules tested

1. `trend_vertical`: at 15:00 IST, buy a two-day call or put debit spread after a fixed one-hour breakout and trend-efficiency condition; 100% debit stop and target-expiry exit.
2. `failed_breakout`: at 15:00 IST, sell a one-day OTM call or put credit spread after a fixed one-hour range breach and close back inside; 100% credit stop and target-expiry exit.
3. `long_gamma`: at 15:00 IST, buy the next-day ATM call and put after fixed range compression; 100% debit stop/target and target-expiry exit.

All results use 200 contracts, 50 bps adverse fill slippage, 0.01% historical option fee capped at 3.5% of premium, and GST. They are mark-based and do not prove executable depth.

## Chronological results

| Fold | Strategy | Trades | Net P&L | Win rate | Profit factor | Sharpe | Max DD |
|---|---|---:|---:|---:|---:|---:|---:|
| Jan–Mar 2026 development | trend vertical | 5 | -83.39 | 20.0% | 0.13 | -19.58 | 95.65 |
| Jan–Mar 2026 development | failed breakout | 16 | -111.19 | 37.5% | 0.41 | -8.26 | 149.65 |
| Jan–Mar 2026 development | long gamma | 40 | 63.26 | 47.5% | 1.02 | 0.19 | 1,112.35 |
| Apr–May 2026 validation | trend vertical | 0 | n/a | n/a | n/a | n/a | n/a |
| Apr–May 2026 validation | failed breakout | 11 | -9.80 | 54.5% | 0.89 | -1.03 | 50.16 |
| Apr–May 2026 validation | long gamma | 30 | -981.00 | 30.0% | 0.46 | -5.79 | 1,215.98 |
| Jun–Jul 10 2026 confirmation | trend vertical | 0 | n/a | n/a | n/a | n/a | n/a |
| Jun–Jul 10 2026 confirmation | failed breakout | 11 | -21.20 | 54.5% | 0.77 | -2.27 | 58.66 |
| Jun–Jul 10 2026 confirmation | long gamma | 21 | 491.72 | 52.4% | 1.83 | 4.28 | 347.59 |

## Interpretation

- The trend vertical is not yet testable as a stable strategy because the fixed signal generated only five trades in development and none in either validation fold. It is not evidence of an edge; the signal is too sparse.
- The failed-breakout spread failed both walk-forward folds. Reject this fixed version.
- Long gamma is strongly regime-dependent: approximately breakeven in development, materially negative in April–May, and positive in June–July. It is not robust enough for promotion.
- No hypothesis passes the predeclared promotion gates. The current ATM BTC short-premium strategy remains the only historically validated family, subject to prospective execution evidence.

## Engineering notes

The positional runner now fetches spot candles in daily chunks to respect Delta's 2,000-candle response cap and uses the expired-product catalogue for historical target expiries. It falls back to deterministic symbol resolution only when no catalogue is available. Smoke tests and the complete existing test suite pass.

## Next research decision

Do not tune these three rules on the observed results. A better next iteration should use a predeclared higher-timeframe signal with a larger expected sample, or collect prospective L2/order-flow data before designing another microstructure rule. Any replacement must restart the chronological protocol and reserve a new prospective paper period as the genuine unseen test.
