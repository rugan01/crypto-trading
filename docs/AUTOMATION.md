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
  --asset BTC --size 100 --minutes 25 --start-at 17:00:00 \
  --confirm-production-orders
```

The session refuses entry unless the account is flat, both products report 200x leverage, projected free margin is at least USD 30, planned stop risk plus fee buffer is no more than USD 25, spreads are within 15%, and executable credit is at least 95% of combined mid.

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
- Wrapper: `scripts/run_production_btc_0dte.sh`
- Schedule: 13 July 2026 at 16:55 IST
- Working directory: `/Users/rugan/Projects/Delta`
- Logs: `outputs/live/production-scheduler-YYYYMMDD.log`, plus launchd stdout/stderr files
- Command: production overrides, 100 BTC contracts per leg, wait until 17:00, 25-minute session

Verification:

```bash
launchctl print gui/$(id -u)/com.rugan.delta-btc-0dte
launchctl list | rg 'com.rugan.delta-btc-0dte'
```

The job is currently loaded and `not running`, which is expected before 16:55. It is not duplicated with a Codex automation.

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
