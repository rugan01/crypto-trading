# Crypto Trading — Delta 0DTE Options Research

An auditable Python research project for BTC and ETH same-day-expiry options on
Delta Exchange India. It develops, backtests, validates, and diagnoses short ATM
straddles while keeping historical research, prospective paper trading, and live
execution strictly separated.

## What this project demonstrates

- Point-in-time historical option discovery beyond Delta's 10,000-product list.
- Resumable one-minute spot and option-mark candle downloads.
- ATM contract selection using only information available at entry.
- Combined, independent-leg, and close-both stop-loss simulations.
- Historical contract values, fees, GST, configurable slippage, and whole lots.
- Chronological research, frozen validation, and final holdout testing.
- Win probability with confidence intervals, Sharpe, profit factor, CVaR,
  drawdown size/duration, streaks, and cost sensitivity.
- Premium-level and call/put-balance diagnostics without retrofitting a filter.
- A documented transition from mark-based backtesting to executable bid/ask and
  depth-aware prospective paper testing.

## Headline finding

Across 556 BTC 17:00 expiries from January 2025 through July 2026, the combined
50% stop strategy produced a modeled 70.3% win rate after costs. Call/put balance
was more informative than a simple premium threshold: entries where the smaller
leg contributed at least 35% of combined premium had an 83.1% historical win
rate, versus 64.6% below 35%. This is descriptive, not a promoted live filter.

See the [premium study](results/btc_1700_premium_analysis/REPORT.md) and
[methodology](docs/METHODOLOGY.md) for limitations and interpretation.

## Repository map

| File | Purpose |
|---|---|
| `delta_0dte.py` | Public API client, product/candle cache, ATM resolver, simulator, performance metrics, research CLI |
| `validate_2025.py` | Frozen four-month validation and final eight-month holdout runner with point-in-time diagnostics |
| `analyze_btc_premium.py` | Full-history BTC premium, balance, confidence-interval, and bucket analysis |
| `test_delta_0dte.py` | Core unit tests for ATM selection, quote timing, metrics, and catalogue-boundary handling |
| `docs/ARCHITECTURE.md` | Detailed explanation of the code and data flow |
| `docs/RUNBOOK.md` | Installation, commands, inputs, outputs, and troubleshooting |
| `docs/METHODOLOGY.md` | Research hypothesis, chronology, cost model, bias controls, and interpretation |
| `docs/LIVE_EXECUTION_PLAN.md` | Assisted-execution design and controls; no enabled live-order engine |
| `results/**/REPORT.md` | Curated research reports; bulky reproducible datasets are gitignored |

## Quick start

```bash
git clone https://github.com/rugan01/crypto-trading.git
cd crypto-trading
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest -q
```

Public historical endpoints do not require an API key.

```bash
# Audit recent expired products
python delta_0dte.py audit --days 30

# Run a recent 30-day research matrix
python delta_0dte.py backtest \
  --days 30 --lots 200 --slippage-bps 50

# Run an exact date window
python delta_0dte.py backtest \
  --start-date 2026-01-01 --end-date 2026-07-10 \
  --lots 200 --slippage-bps 50 \
  --output results/research_2026_jan01_jul10_50bps
```

See [RUNBOOK.md](docs/RUNBOOK.md) for validation and diagnostics commands.

## Sandbox execution engine

The repository now contains a testnet-locked execution foundation in `delta_live/`. It includes signed REST access, WebSocket quote transport, liquidity gating, combined executable-premium stop logic, Telegram alerts, event logs, and safety tests. It does not yet run unattended or permit production orders.

See [the sandbox execution runbook](docs/SANDBOX_EXECUTION_RUNBOOK.md) for the strategy, controls, configuration, commands, and remaining validation stages.

The private execution evidence and post-trade learning loop follow the [Delta trade journal schema](docs/JOURNAL_SCHEMA.md).

Additional option-only strategy hypotheses and the frozen walk-forward protocol are in [STRATEGY_CANDIDATES.md](docs/STRATEGY_CANDIDATES.md).

The first non-straddle research results are summarized in [results/hypotheses/REPORT.md](results/hypotheses/REPORT.md).

The higher-timeframe positional study is specified in [POSITIONAL_MTF_SPEC.md](docs/POSITIONAL_MTF_SPEC.md); [positional_mtf.py](positional_mtf.py) generates the daily/4h/30m pivot signals.

The first three-month option-leg results are in [results/positional_option_backtest/REPORT.md](results/positional_option_backtest/REPORT.md).

Production execution, monitoring, scheduler failure analysis, and cloud-hosting constraints are documented in [AUTOMATION.md](docs/AUTOMATION.md).

## Important interpretation boundary

Historical option marks are fair-value/risk marks, not guaranteed executable
prices. One-minute marks cannot reconstruct bid/ask spread, depth, tick-level
stop crossings, partial fills, or 200-contract market impact. Results remain
research estimates until prospective order-book paper fills validate them.

This software is educational research, not investment advice. Options selling
can lose substantially more than the premium collected. Nothing in this repo
authorizes unattended live trading.

## License

[MIT](LICENSE)
