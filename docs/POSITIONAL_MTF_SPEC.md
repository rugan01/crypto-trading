# Higher-timeframe positional options study

## Proposed historical windows

- Backtest: 1 January–31 December 2025.
- Chronological forward test: 1 January–30 June 2026.
- Prospective confirmation after implementation: minimum 30 signals.

The 2025 data has been viewed in prior strategy research, so it is not a pristine unseen sample. The 2026 first-half period is chronological forward evidence for this new signal family, but the final unseen test must be prospective paper trading.

## Signal architecture

1. Daily regime: close versus EMA20/EMA50 and EMA20 slope.
2. Four-hour confirmation: close versus EMA20/EMA50 in the same direction.
3. Thirty-minute execution: breakout through classic R1/S1 or rejection at Camarilla CR3/CS3.
4. Option expression: next-day or two-day defined-risk vertical spread; never naked directional selling.

The first frozen modes are:

- Bullish R1 breakout → buy call debit spread.
- Bearish S1 breakout → buy put debit spread.
- CR3 rejection → sell call credit spread with a long call wing.
- CS3 rejection → sell put credit spread with a long put wing.

## Fixed risk and exit proposal

- Equal contracts in both legs of the vertical.
- Maximum loss is the debit paid for debit spreads or the vertical width minus credit for credit spreads.
- Initial stop: 50% loss of debit/maximum-risk budget for debit spreads; 100% loss of credit for credit spreads.
- Profit target: 100% gain on debit or 50% credit capture, whichever applies.
- Time exit: 17:25 IST on the target expiry day.
- No rolling, averaging, or replacement after a stop.

## Pivots

Classic levels use the prior daily high, low and close:

`P=(H+L+C)/3`, `R1=2P-L`, `S1=2P-H`.

Camarilla levels use the prior daily range and close. CR3/CS3 are used for reversal signals because they are closer to the active range than CR4/CS4 and should produce more observations. No pivot multiplier will be tuned after seeing results.

## Required option metrics

Net P&L, win rate, average win/loss, profit factor, Sharpe, maximum drawdown and duration, CVaR, signal-to-fill delay, premium/debit, maximum risk, return on risk, fee drag, slippage sensitivity, option quote coverage and whether the signal was executable at intended size.

## Implementation boundary

`positional_mtf.py` currently creates the spot-only signal file using native 30m/4h/1d candles. The next step is to feed those frozen signal timestamps into the multi-expiry vertical simulator, resolve target-expiry contracts, and reconcile entry/exit fees. Spot signal frequency is checked before option-leg results are interpreted.
