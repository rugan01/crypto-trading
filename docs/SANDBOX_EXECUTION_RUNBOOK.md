# Sandbox execution engine runbook

This release is deliberately locked to Delta India testnet for order submission. Production order submission is not enabled.

## Execution hypothesis

- Sell the nearest complete ATM BTC or ETH 0DTE call/put pair at 17:00 IST.
- Treat the actual volume-weighted fills, not marks, as the entry credit.
- Arm the combined 50% stop at `1.50 × actual combined entry credit`.
- Evaluate the stop using executable buyback prices for the remaining size. A mark-price touch alone is informational.
- Require two fresh consecutive breaches at least one second apart before exiting.
- Start a paired, reduce-only forced exit at 17:24:30 and finish by 17:25.

## Architecture

WebSocket is the primary quote/event transport. It subscribes to Delta's `ticker` and `system_status` channels and reconnects with exponential backoff. REST is used for option discovery, product specifications, startup reconciliation, orders, and as a stale-stream recovery feed.

The runtime state machine is:

`STARTING → READY → ENTERING → OPEN → EXITING → CLOSED`

Any failed gate becomes `NO_TRADE`; stale data, breached daily risk, ambiguous positions, or an operator kill becomes `HALTED`. Restarting must reconcile active orders and real-time positions before taking action.

## Liquidity and entry policy

1. Freeze the common ATM strike immediately before entry.
2. Require two-sided quotes on both legs.
3. Reject a leg whose bid/ask spread exceeds 15% of its mid.
4. Require combined executable sell credit of at least 95% of combined mid.
5. Require intended size at the quoted top level. Later depth-aware slicing will use L2 VWAP; this initial sandbox build fails closed when top-level size is insufficient.
6. Submit matched IOC limit slices with a bounded minimum credit. Never use an unbounded market order.
7. If one leg fills without the other, cancel remaining entry orders and flatten the unmatched quantity with a bounded reduce-only limit.

## Risk policy

- `combined executable asks >= combined fill credit × 1.50` for two observations triggers the stop.
- Exit orders are `reduce_only=true` and use ask prices rounded upward to the tick.
- A configurable daily-loss cap is independent of the strategy stop.
- Quotes older than two seconds or a disconnected stream cause entry rejection; an open position switches to one-second REST recovery polling and alerts Telegram.
- Every intent, quote decision, order response, fill, stop observation, and error is appended to `outputs/live/events-YYYYMMDD.jsonl`.
- Client order IDs prevent accidental duplication and are reconciled at startup.

## Configuration

Create separate Delta India demo-account keys; production keys do not work on testnet. Add these to the local `.env` without committing it:

```dotenv
DELTA_ENV=testnet
DELTA_DRY_RUN=true
DELTA_TESTNET_API_KEY=...
DELTA_TESTNET_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

The Telegram values can be copied from the existing private trading-system environment. Do not commit either environment file.

## Install and verify

```bash
cd /Users/rugan/Projects/Delta
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 -m unittest -v
python3 -m delta_live.cli doctor
python3 -m delta_live.cli discover --asset BTC
python3 -m delta_live.cli dry-run --asset BTC --size 200
python3 -m delta_live.cli telegram-test
```

`dry-run` discovers the expiry and ATM pair and applies the liquidity gate, but cannot place an order. A liquidity rejection is a successful safety outcome, not a software failure.

## Testnet activation boundary

Only after authenticated reads, Telegram, reconciliation, and dry-run evidence pass:

```dotenv
DELTA_ENV=testnet
DELTA_DRY_RUN=false
```

The code still refuses production orders. Before the first demo order session, set an explicit maximum daily loss, validate contract value and required margin, and use a small size rather than 200 contracts. Production requires a separate reviewed release and explicit user approval.

## Remaining work before unattended testnet trading

- Finish the continuous scheduler around 17:00–17:25.
- Integrate authenticated order/fill WebSocket channels and startup reconciliation.
- Add L2 depth VWAP and matched slice execution.
- Implement partial-fill repair and emergency flatten integration tests.
- Exercise several demo sessions and review their event logs.
- Add heartbeat/dead-man protection if supported for the account.
