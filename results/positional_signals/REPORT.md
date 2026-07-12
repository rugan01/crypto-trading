# Positional multi-timeframe signal coverage

Signal generation stage only; option P&L has not been calculated yet.

Period: 1 January 2025–30 June 2026. Daily regime, 4-hour confirmation, 30-minute execution, classic R1/S1 and Camarilla CR3/CS3. Repeated signals of the same type are deduplicated to one per asset/day.

| Asset | Total signals | 2025 backtest | 2026 forward | Bullish breakout | Bearish breakout | Upper rejection | Lower rejection |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | 857 | 573 | 284 | 52 | 76 | 352 | 377 |
| ETH | 832 | 555 | 277 | 45 | 71 | 346 | 370 |

The high rejection count is intentional: the Camarilla reversal mode is allowed in neutral, bullish and bearish regimes. The option stage must enforce one-position-at-a-time, minimum signal spacing, and margin/liquidity gates before treating every signal as tradable.

The generated CSV files are private/reproducible outputs and remain gitignored. The next step is to resolve target-expiry option contracts at each frozen signal, simulate the debit/credit vertical, and report P&L separately for 2025 and January–June 2026.
