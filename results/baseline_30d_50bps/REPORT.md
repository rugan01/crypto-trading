# BTC/ETH 0DTE Straddle — 30-expiry pilot

## Scope

- Period: 11 June–10 July 2026 (30 daily expiries per asset).
- Assets: BTC and ETH, evaluated separately.
- Size: 200 contracts per leg (BTC exposure 0.2 BTC; ETH exposure 2 ETH).
- Entry grid: every 15 minutes, 13:00–17:00 IST.
- Exit: stop or 17:25 IST; all audited contracts settled at 17:30 IST.
- Prices: one-minute historical option mark closes.
- Costs: 50 bps adverse premium slippage per fill; historical product taker rate (0.01%) on underlying notional, capped at 3.5% of premium per fill, plus 18% GST.
- Complete simulations: 9,180; data skips: zero.

Each entry time/stop combination is an alternative strategy. P&L must not be summed across them as though all 153 variants were traded simultaneously.

## Best in-sample configurations

| Asset | Entry | Stop method | Stop | Trades | Net P&L (USD) | Avg/trade | Win rate | Max drawdown | 95% CVaR |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | 15:30 | Combined | 100% | 30 | 172.51 | 5.75 | 66.7% | 68.48 | -58.22 |
| BTC | 13:15 | Leg independent | 100% | 30 | 133.77 | 4.46 | 53.3% | 110.49 | -47.49 |
| BTC | 14:30 | Leg independent | 50% | 30 | 103.58 | 3.45 | 70.0% | 74.55 | -43.66 |
| BTC | 17:00 | Combined | 50% | 30 | 99.55 | 3.32 | 66.7% | 43.73 | -16.46 |
| ETH | 13:00 | Leg independent | 100% | 30 | 186.11 | 6.20 | 80.0% | 15.64 | -14.28 |
| ETH | 13:00 | Leg independent | 150% | 30 | 143.10 | 4.77 | 66.7% | 21.69 | -18.44 |
| ETH | 13:00 | Close both on first leg SL | 100% | 30 | 128.01 | 4.27 | 66.7% | 15.64 | -14.94 |
| ETH | 13:15 | Leg independent | 50% | 30 | 116.08 | 3.87 | 70.0% | 18.80 | -15.75 |

The BTC 15:30 combined-100% result had gross P&L of $256.91 and $84.41 of modeled fees. The ETH 13:00 independent-leg-100% result had gross P&L of $219.66 and $33.54 of modeled fees.

## Slippage sensitivity (average USD per expiry)

| Candidate | 0 bps | 50 bps | 100 bps |
|---|---:|---:|---:|
| BTC 15:30 combined 100% | 6.14 | 5.75 | 5.36 |
| BTC 13:15 independent 100% | 5.06 | 4.46 | 3.86 |
| BTC 14:30 independent 50% | 3.94 | 3.45 | 2.97 |
| ETH 13:00 independent 100% | 6.42 | 6.20 | 5.99 |
| ETH 13:00 independent 150% | 4.99 | 4.77 | 4.55 |
| ETH 13:00 close-both 100% | 4.49 | 4.27 | 4.04 |

All listed candidates remain positive under the 100 bps model, but mark-price slippage is only a scenario—not evidence of executable liquidity for 200 contracts.

## Robustness observations

- ETH independent-leg 100% was positive at 14 of 17 entry times. This is the broadest promising pattern in the pilot.
- BTC combined 100% was positive at only 3 of 17 entry times. The 15:30 result is isolated and therefore has a much higher selection/overfitting risk.
- The BTC 17:00 combined 50% candidate has lower profit but materially lower observed tail loss than the 15:30 leaders; it deserves out-of-sample testing because the user specifically suspected the final half-hour.
- Thirty observations per configuration are insufficient for promotion. These rankings are hypothesis generators, not validated edges.

## Critical limitations

1. Mark price is a fair-value/risk price, not a realized bid/ask fill. Delta explicitly notes that realized P&L depends on actual execution price.
2. Historical bid/ask depth for a 200-contract order is unavailable in this dataset. A prospective order-book paper test is mandatory.
3. Stops are evaluated on synchronized one-minute mark closes. Intraminute breaches and latency can worsen real fills.
4. The same 30-expiry sample was used to compare 153 configurations per asset, creating material multiple-testing bias.
5. Margin usage and return on margin are not reported because historical point-in-time portfolio margin was not reconstructed.
6. The testnet endpoint failed TLS negotiation from this local Python environment. Only public production market-data endpoints were used; no private endpoint or API credential was used.

## Next validation gate

- Extend the dataset as far back as daily-expiry and one-minute mark coverage permits.
- Freeze a small candidate set before viewing older results: ETH 13:00 independent 100%, ETH 13:00 close-both 100%, BTC 15:30 combined 100%, and BTC 17:00 combined 50%.
- Use chronological walk-forward/holdout testing rather than re-ranking every period.
- Capture live bid/ask and depth prospectively from 13:00–17:30 IST for at least 20–30 expiries, then simulate 200-contract fills and latency.
- Add portfolio margin snapshots during paper testing before calculating return on capital.
