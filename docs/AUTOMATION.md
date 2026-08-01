# Execution and monitoring automation

## Current scripts

- `delta_live/session.py`: time-bounded execution session. Discovers the current expiry/ATM pair, validates leverage and margin, enters matched legs, monitors executable asks, exits by time, and reconciles the account.
- `delta_live/monitor.py`: read-only monitor for an already-open campaign. It polls positions and ticker asks, writes JSONL observations, alerts Telegram on start/structure changes/stop proximity/flat state, and never submits orders.
- `delta_live/client.py`: HMAC-signed REST client with retries, fresh timestamps per retry, product/ticker/position/order/fill/wallet access, and leverage reads.
- `delta_live/stream.py`: reconnectable public ticker WebSocket. The session uses REST recovery when the stream is stale or incomplete.
- `delta_live/engine.py`: state machine, combined executable-premium stop calculation, event logging and Telegram dispatch.
- `delta_live/liquidity.py`: spread, visible size and executable-credit gates.
- `scripts/bootstrap.sh`: creates `.venv` with Homebrew Python/OpenSSL and installs dependencies.

## Manual production command

The repository `.env` remains testnet-by-default. Production is enabled only for one shell invocation:

```bash
cd /Users/rugan/Projects/Delta
source .venv/bin/activate
DELTA_ENV=production \
DELTA_DRY_RUN=false \
DELTA_PRODUCTION_ACK=LIVE_ORDERS_AUTHORIZED \
python -m delta_live.session \
  --asset BTC --size 150 --minutes 25 --start-at 17:00:00 \
  --slice-size 150 \
  --confirm-production-orders
```

The current production target is 150 contracts per leg, submitted as one concurrent matched 150-contract call/put pair. `DELTA_PERMISSIVE_ENTRY=true` makes the 15% spread, displayed depth, 95% credit, projected-free-margin and modelled-loss checks telemetry-only so they are recorded for later analysis without cancelling the daily entry. Entry uses bounded IOC limits up to 10% through the displayed bid. Credentials, duplicate/open campaign protection, valid two-sided quotes, paired-leg recovery, stop monitoring, forced exit and emergency flatten remain mandatory. The 20-second entry window and up to 10 seconds of missing-leg recovery remain active so a partial or missing leg is completed or flattened without retaining unmatched exposure.

At exit, the session first reconciles the current broker size of each campaign leg. It reads cumulative L2 ask depth for the remaining quantity and submits every live leg concurrently as reduce-only IOC limits. Options priced at USD 5 or less receive a meaningful tick-aware cushion—up to twice the depth price on the first attempt—because a 2% ladder can round back to the same penny-option tick. Larger premiums start with a 5% cushion. Limits remain bounded, widen on subsequent rounds, and fall back to the ticker ask if L2 is unavailable. Final broker positions are reconciled again before the session may report success; emergency flatten remains the outer recovery layer.

## Monitoring command

```bash
DELTA_ENV=production .venv/bin/python -m delta_live.monitor \
  --asset BTC --until 17:30:00 --interval 10 \
  --log outputs/live/monitor-YYYYMMDD.jsonl
```

This process is read-only. It is not a replacement for an attached broker stop order. If a leg is manually closed, it records `structure_changed` and does not invent a new combined stop from the remaining leg.

## macOS production schedule

The active local schedule is a single `launchd` job:

- Plist: `~/Library/LaunchAgents/com.rugan.delta-btc-0dte.plist`
- Keep-awake plist: `~/Library/LaunchAgents/com.rugan.delta-btc-awake.plist`
- Wrapper: `scripts/run_production_btc_0dte.sh`
- Schedule: every calendar day at 16:55 IST, including weekends
- Working directory: `/Users/rugan/Projects/Delta`
- Logs: `outputs/live/production-scheduler-YYYYMMDD.log`, plus launchd stdout/stderr files
- Command: production overrides, one concurrent matched submission of 150 BTC contracts per leg, wait until 17:00, begin forced exit at 17:24:30
- The wrapper runs a read-only production preflight at 16:55 and sends the result to Telegram.
- A nonzero session exit triggers an urgent Telegram message and emergency flat reconciliation.

Verification:

```bash
launchctl print gui/$(id -u)/com.rugan.delta-btc-0dte
launchctl list | rg 'com.rugan.delta-btc-0dte'
```

The job is loaded and normally shows `not running` outside the 16:55-17:25 session. It is not duplicated with a Codex automation. The plist deliberately has no `Day`, `Month` or weekday restriction. A persistent `caffeinate -i` LaunchAgent prevents idle sleep while the user is logged in. The Mac must still remain powered and logged in; this setup cannot start after shutdown or logout.

## What failed today

The Codex automation was configured against `/Users/rugan/Documents/New project`, not the Delta project. It did not start. A manual production session then reached the entry point but failed closed because the CLI confirmation was not propagated into the client order method. That propagation is now fixed. The account remained flat until the user manually executed and closed the trade.

## Why a free cloud host needs care

Before moving production execution to PythonAnywhere or another free host, verify:

- outbound HTTPS and WebSocket support;
- a stable outbound IP that Delta can whitelist;
- a process that stays alive for the entire 16:55–17:30 IST window;
- correct Asia/Kolkata timezone handling;
- secret environment variables, never a committed `.env`;
- persistent logs and a restart policy;
- a separate testnet deployment and kill switch;
- no duplicate scheduler instances;
- a heartbeat/Telegram alert if the process stops.

Many free tiers sleep, restrict long-running workers, restrict WebSockets, or do not provide a stable outbound IP. A free cron task that wakes late is not suitable for unattended production orders. The safe order is: testnet on the host, paper monitor, several reconciled sessions, then a small production session with an operator present.

## Recommended deployment shape

Use one process per campaign, guarded by a lock file or unique campaign key. Run a preflight at 16:55, wait until 17:00 inside the same process, execute only once, exit by 17:25, reconcile fills, and send the journal at 18:00. A separate read-only watchdog may alert on heartbeat failure, but it must not place a second order.
