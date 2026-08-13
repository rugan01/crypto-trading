# PythonAnywhere deployment procedure — Delta BTC 0DTE

## Deployment decision

Use a **paid PythonAnywhere account** for this executor. Paid accounts have unrestricted outbound Internet access and can use scheduled or always-on tasks. A new free account is not suitable for production trading because outbound access is proxy/allowlist restricted and scheduled-task availability is limited.

The largest unresolved blocker is Delta's API IP allowlist. PythonAnywhere does not provide a fixed outbound IP for ordinary tasks. Do not switch production to PythonAnywhere until authenticated Delta REST calls and the public WebSocket both work from the PythonAnywhere task environment. If Delta requires a fixed source IP, use a static-IP proxy/tunnel that supports both HTTPS and WebSockets, or choose a VPS with a dedicated IP instead.

## Recommended architecture

- One paid PythonAnywhere scheduled task at 16:55 IST each calendar day.
- Command runs `scripts/run_cloud_btc_0dte.sh` from the repository virtual environment.
- The task performs read-only preflight, waits until 17:00, executes one campaign, exits by 17:24:30 and reconciles flat.
- Do not run the local Mac and PythonAnywhere executors live at the same time. Broker flat checks are not a sufficient distributed lock because two instances can pass them concurrently.
- During migration, run PythonAnywhere in read-only shadow mode for at least three sessions while the Mac remains the only live executor.

## 1. Create and secure the account

1. Create a paid PythonAnywhere account in the preferred region.
2. Enable two-factor authentication.
3. Add an SSH key to GitHub and clone the private repository using SSH. Do not place a GitHub token in a task command.
4. Confirm the account timezone shown by the Tasks page. The runner also exports `TZ=Asia/Kolkata`, and the Python strategy uses an explicit IST timezone.

## 2. Install the project

In a PythonAnywhere Bash console:

```bash
cd ~
git clone <private-github-ssh-url> Delta
cd Delta
python3.13 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
chmod 700 scripts/run_cloud_btc_0dte.sh
mkdir -p outputs/live
```

If Python 3.13 is not offered on the selected system image, use an available supported Python version and run the complete unit suite before proceeding.

## 3. Configure secrets

Create `/home/<username>/Delta/.env` using the PythonAnywhere file editor or a secure shell session. Include only the required production Delta and Telegram variables. Then protect it:

```bash
chmod 600 /home/<username>/Delta/.env
```

Never commit `.env`, print its contents into task logs, or paste credentials into the scheduled-task command.

## 4. Prove network compatibility

From a fresh paid-account Bash console, run:

```bash
cd ~/Delta
DELTA_ENV=production DELTA_DRY_RUN=true .venv/bin/python -m delta_live.cli doctor --asset BTC --size 200
DELTA_ENV=production DELTA_DRY_RUN=true .venv/bin/python -m delta_live.production_preflight --asset BTC --size 200
```

The preflight must authenticate, find no open positions/orders, read leverage and wallet data, discover the current ATM pair, and send Telegram. Separately run a short WebSocket quote test using the same `websocket-client` dependency. If Delta returns an IP-whitelist error, stop: PythonAnywhere's ordinary outbound IP is not fixed.

## 5. Test without production orders

Run the unit suite:

```bash
cd ~/Delta
.venv/bin/python -m unittest -q
```

Then run at least three daily shadow sessions. Use a cloud-specific dry-run wrapper that performs discovery, margin/liquidity checks, quote streaming and Telegram reporting but never sets `DELTA_DRY_RUN=false` or `DELTA_PRODUCTION_ACK`. Compare timestamps, selected strikes, quotes and availability against the Mac logs.

## 6. Create the scheduled task

On the PythonAnywhere **Tasks** page, create a daily task for 16:55 IST with:

```bash
/home/<username>/Delta/scripts/run_cloud_btc_0dte.sh
```

Paid scheduled tasks can run for up to 12 hours, so the roughly 30-minute process fits comfortably. An always-on task is unnecessary for a single fixed daily window, though it can be used for a separate heartbeat/watchdog if desired.

## 7. Production cutover

1. Confirm the latest code commit is identical on the Mac and PythonAnywhere.
2. Confirm three successful shadow sessions and Telegram delivery.
3. Disable the Mac live LaunchAgent before enabling the PythonAnywhere live task.
4. Run the first cloud production session at 100 contracts, even if the target configuration is 200. Scale only after a fully reconciled cloud session.
5. Confirm the cloud task writes entry, stop, exit and reconciliation events and that the account finishes flat.

## 8. Monitoring and rollback

- Telegram events required: scheduler started, preflight pass/fail, entry filled, stop armed, stop triggered, exit and final reconciliation.
- Review `outputs/live/cloud-scheduler-YYYYMMDD.log` and `outputs/live/events-YYYYMMDD.jsonl` after every session.
- Keep the Mac executor disabled but ready as rollback; never enable both live schedulers together.
- If the cloud task starts late, cannot authenticate, loses WebSocket recovery, or cannot verify flat state, it must fail closed and notify—never attempt a delayed discretionary entry.
