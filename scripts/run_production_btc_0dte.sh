#!/usr/bin/env bash
set -euo pipefail

cd /Users/rugan/Projects/Delta
mkdir -p outputs/live
exec >> outputs/live/production-scheduler-$(date +%Y%m%d).log 2>&1

# TARGET IS 150. Running 125 until the account is funded to support it.
#
# The binding constraint is base margin PLUS premium margin, not base alone:
#   base    = spot * 0.001 * size / 200 * 2
#   premium = combined_credit * 0.001 * size
# Both must fit inside available balance or the exchange rejects the order.
# Premium margin scales with the day's credit, so the same size needs more
# margin on a high-premium session.
#
# 2026-08-08 at spot 64,996 and a 96 credit:
#   150 -> base $97.49 + premium $14.40 = $111.89  vs $106.43 available  FAILS
#   125 -> base $81.25 + premium $12.00 =  $93.25  vs $106.43 available  OK
#
# Raise to 150 once available balance clears roughly $120 on a normal-credit
# day. Re-run the arithmetic above first - do not assume, since a high-credit
# session can push the requirement well past base margin alone.
BTC_SIZE="${DELTA_BTC_SIZE:-125}"
BTC_SLICE_SIZE="${DELTA_BTC_SLICE_SIZE:-125}"
PERMISSIVE_ENTRY="${DELTA_PERMISSIVE_ENTRY:-true}"

echo "scheduler_started $(date '+%Y-%m-%dT%H:%M:%S%z')"
if ! DELTA_ENV=production /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.cli telegram-notify \
  --message "Delta BTC 0DTE production scheduler started at 16:55 IST; target is ${BTC_SIZE} contracts per leg in one matched submission, paired entry is scheduled for 17:00 IST, and market-quality checks are telemetry-only."; then
  echo "telegram_startup_notification_failed"
fi

set +e
DELTA_ENV=production DELTA_DRY_RUN=true DELTA_PERMISSIVE_ENTRY="$PERMISSIVE_ENTRY" \
  /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.production_preflight --asset BTC --size "$BTC_SIZE"
preflight_status=$?
set -e
if [ "$preflight_status" -ne 0 ]; then
  DELTA_ENV=production /Users/rugan/Projects/Delta/.venv/bin/python \
    -m delta_live.cli telegram-notify \
    --message "URGENT: Delta BTC 16:55 production preflight failed for ${BTC_SIZE} contracts per leg with status $preflight_status. No live entry will be attempted." || true
  exit "$preflight_status"
fi

set +e
env \
  DELTA_ENV=production \
  DELTA_DRY_RUN=false \
  DELTA_PERMISSIVE_ENTRY="$PERMISSIVE_ENTRY" \
  DELTA_PRODUCTION_ACK=LIVE_ORDERS_AUTHORIZED \
  /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.session \
  --asset BTC \
  --size "$BTC_SIZE" \
  --minutes 25 \
  --start-at 17:00:00 \
  --exit-at 17:24:30 \
  --slice-size "$BTC_SLICE_SIZE" \
  --entry-window 20 \
  --unmatched-grace 10 \
  --confirm-production-orders
session_status=$?
set -e

if [ "$session_status" -ne 0 ]; then
  DELTA_ENV=production /Users/rugan/Projects/Delta/.venv/bin/python \
    -m delta_live.cli telegram-notify \
    --message "URGENT: Delta BTC production session exited with status $session_status; emergency flat reconciliation is starting." || true
  env \
    DELTA_ENV=production \
    DELTA_DRY_RUN=false \
    DELTA_PRODUCTION_ACK=LIVE_ORDERS_AUTHORIZED \
    /Users/rugan/Projects/Delta/.venv/bin/python \
    -m delta_live.emergency_flatten \
    --asset BTC \
    --confirm-production-orders
fi

exit "$session_status"
