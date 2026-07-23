#!/usr/bin/env bash
set -euo pipefail

cd /Users/rugan/Projects/Delta
mkdir -p outputs/live
exec >> outputs/live/production-scheduler-$(date +%Y%m%d).log 2>&1

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
