# Methodology

## Hypothesis

For BTC and ETH 0DTE options, short ATM straddles may earn a volatility risk
premium when combined option premium exceeds the movement remaining before the
17:30 IST settlement. The hypothesis is falsified when post-cost expectancy and
tail behavior fail outside the research window.

## Research chronology

1. Initial 30-expiry screen across 17 entry times and nine stop variants.
2. Ninety-expiry expansion to test timing stability.
3. Full 1 Jan–10 Jul 2026 research matrix: 191 expiries per asset.
4. Five candidates frozen before Sep–Dec 2025 validation.
5. Three candidates promoted before Jan–Aug 2025 final holdout.
6. BTC passed; ETH failed the final holdout.
7. Full-history premium/balance diagnostics were performed descriptively only.

No historical period remains genuinely unseen. Future evidence must be
prospective.

## Point-in-time controls

- ATM strike uses contemporaneous spot and actual historical symbols.
- Expiry, contract value, tick size, and fee rates come from product metadata.
- Missing historical marks are skipped, not filled from future data.
- Pre-entry diagnostic features use timestamps at or before entry.
- Post-entry movement and P&L are labels only.
- Validation runners search no alternate entry times or stops.

## Execution and cost model

- Historical marks are adjusted adversely by configurable premium slippage.
- Base model: 50 bps per option fill; 0 and 100 bps sensitivities.
- Size: 200 whole contracts per leg in research.
- Fees: historical option rate against underlying notional, capped by the
  applicable percentage of option premium, plus GST.
- Stop checks use synchronized one-minute marks.
- Historical forced exit: 17:25 IST.

## Metrics

Primary: net expectancy, maximum drawdown/duration, profit factor, annualized
daily-P&L Sharpe, 95% CVaR, average winner/loser, payoff ratio, worst trade,
year/month stability, and cost sensitivity.

Secondary: win probability and confidence interval, streaks, stop rate,
premium/spot, implied-versus-expected movement, premium balance, and profit
concentration.

Win rate alone never promotes a strategy.

## Final evidence

The final Jan–Aug 2025 holdout produced:

- BTC 17:00 combined 50%: $2,848.81 net, PF 4.56, positive 8/8 months.
- BTC 17:00 independent 100%: $2,968.17 net, PF 5.66, positive 8/8 months.
- ETH 15:15 independent 150%: $42.10 net, PF 1.02, failed.

The full-history balance study found a strong descriptive association between
more balanced entry premium and performance. It is not a validated live filter
because the threshold was discovered after examining all history.

## Limitations

1. Mark price is not executable bid/ask.
2. Historical order-book depth and partial fills are unavailable.
3. One-minute data cannot reproduce tick-level stop order behavior.
4. Fixed contracts are not equal-risk or margin-normalized sizing.
5. Sharpe uses daily dollar P&L, not return on capital.
6. Hundreds of configurations were screened before validation, so selection
   bias is reduced—not eliminated—by holdouts.
7. Exchange and fee rules can change.
8. A short straddle has severe gap/gamma risk beyond modeled stops.

## Research-to-live boundary

```text
historical hypothesis
  -> point-in-time backtest
  -> frozen validation
  -> final holdout
  -> prospective executable paper fills
  -> testnet order mechanics
  -> separately approved limited live use
```

This repository currently stops before unattended live execution.
