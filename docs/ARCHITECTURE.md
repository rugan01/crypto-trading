# Architecture and code walkthrough

## Data flow

```text
Delta public REST API
  -> expired product metadata / direct product-by-symbol resolution
  -> one-minute BTCUSD/ETHUSD and MARK:option candles
  -> local resumable cache (data/raw, not committed)
  -> point-in-time ATM selection
  -> stop/exit simulator and cost model
  -> trade ledger
  -> grouped performance and diagnostic reports
```

No private endpoint is required for historical research. Account reconciliation
uses authenticated endpoints operationally, but private account exports are not
part of the public repository.

## `delta_0dte.py`

This is the core engine.

### Configuration

- `BASE_URL` and `TESTNET_URL`: Delta India endpoints.
- `IST`: all strategy clocks use `Asia/Kolkata`.
- `ENTRY_MINUTES`: 15-minute entry grid from 13:00 through 17:00.
- `STOP_PCTS`: 50%, 100%, and 150% premium-stop variants.

### `DeltaClient`

- `get`: read-only GET wrapper with timeouts, retries, exponential backoff, and
  `X-RATE-LIMIT-RESET` handling.
- `expired_options`: cursor-paginates expired calls/puts and caches metadata.
- `candles`: downloads and caches one-minute candles per symbol/time window.
- `product`: resolves an exact historical symbol. This is crucial because the
  expired-product list has an effective 10,000-row ceiling.

### Contract discovery

- `parse_contract`: converts product JSON into a typed `Contract`.
- `catalog`: groups contracts by asset, expiry, strike, and option side.
- `common_atm`: chooses the nearest strike that has both call and put.
- `resolve_atm_pair`: uses the catalog when complete, rejects a suspiciously
  distant catalogue strike, and falls back to exact symbol probing on the
  historical BTC $100 / ETH $10 search grid.

The distance guard was added after an audit found a partial catalogue page on
11–12 April 2026. Without it, a far-away strike could be mislabeled ATM.

### Simulation

`simulate` receives synchronized spot, call, and put maps and models one short
straddle:

- adverse entry and exit slippage is applied to mark;
- `combined` closes both legs when combined mark reaches the stop multiple;
- `leg_independent` closes each leg separately;
- `leg_both` closes both when either individual stop is hit;
- the historical research exit is 17:25 IST;
- missing prices cause a skip rather than unlimited forward filling;
- P&L uses `contract_value × whole contract count`;
- fees use the product's historical rate against notional, the option-premium
  cap, and GST.

The simulator contains no order-placement method.

### Metrics

`performance_metrics` produces gross/net P&L, fees, expectancy, win rate,
average winner/loser, payoff ratio, profit factor, annualized daily-P&L Sharpe,
maximum drawdown, drawdown duration, recovery state, streaks, best/worst trade,
and 95% CVaR. `summarize` groups these metrics by asset, entry time, stop method,
and stop percentage.

### CLI

- `audit`: confirms expiry/product coverage.
- `backtest`: runs the complete BTC/ETH time × stop matrix.
- Exact date arguments are supported. A historical guard prevents accidentally
  reusing the original holdout through the general research command.

## `validate_2025.py`

This runner encodes research decisions before reading each validation period.

- `validation`: the five candidates frozen for September–December 2025.
- `final_holdout`: the three candidates promoted into January–August 2025.
- It reuses the core simulator but does not search alternate rules.
- `diagnostic_row` separates pre-entry features from post-entry labels.

Pre-entry diagnostics include premium/spot, call-put imbalance, breakevens,
15/30/60/120-minute momentum, range, trend efficiency, expected movement,
richness, premium decay, and quote coverage. Post-entry labels include movement,
payoff proxy, excursion, stop reason, fees, MAE/MFE, and P&L.

## `analyze_btc_premium.py`

This script joins the frozen BTC combined-50% observations across 2025 and 2026.
It computes:

- fixed and quintile premium buckets;
- premium normalized by BTC spot;
- smaller-leg share and balance buckets;
- joint premium × balance groups;
- Wilson 95% confidence intervals for win probability;
- expectancy, average win/loss, profit factor, drawdown, stop rate, and
  year-by-year consistency;
- correlations as descriptive diagnostics.

It intentionally does not emit a live trading rule from the discovered buckets.

## `test_delta_0dte.py`

The unit tests cover paired ATM selection, forward-only quote tolerance,
performance metric calculations, candle parsing, and rejection of a far-away
strike from a partial catalogue.

## Generated data

- `data/raw/`: API cache; large and not committed.
- `results/`: reproducible trade ledgers and summaries. Public Git keeps curated
  reports and compact final summaries; large ledgers remain ignored.
- `outputs/`: authenticated daily journals/fills; private and ignored.
