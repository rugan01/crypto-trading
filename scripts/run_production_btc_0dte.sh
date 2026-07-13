#!/usr/bin/env bash
set -euo pipefail

cd /Users/rugan/Projects/Delta
mkdir -p outputs/live
exec >> outputs/live/production-scheduler-$(date +%Y%m%d).log 2>&1

echo "scheduler_started $(date '+%Y-%m-%dT%H:%M:%S%z')"
if ! DELTA_ENV=production /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.cli telegram-notify \
  --message "Delta BTC 0DTE production scheduler started at 16:55 IST; preflight and paired entry are scheduled for 17:00 IST."; then
  echo "telegram_startup_notification_failed"
fi

set +e
DELTA_ENV=production DELTA_DRY_RUN=true \
  /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.production_preflight --asset BTC --size 100
preflight_status=$?
set -e
if [ "$preflight_status" -ne 0 ]; then
  DELTA_ENV=production /Users/rugan/Projects/Delta/.venv/bin/python \
    -m delta_live.cli telegram-notify \
    --message "URGENT: Delta BTC 16:55 production preflight failed with status $preflight_status. No live entry will be attempted." || true
  exit "$preflight_status"
fi

set +e
env \
  DELTA_ENV=production \
  DELTA_DRY_RUN=false \
  DELTA_PRODUCTION_ACK=LIVE_ORDERS_AUTHORIZED \
  /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.session \
  --asset BTC \
  --size 100 \
  --minutes 25 \
  --start-at 17:00:00 \
  --exit-at 17:24:30 \
  --slice-size 25 \
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
