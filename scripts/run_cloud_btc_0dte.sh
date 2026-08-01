#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
export TZ=Asia/Kolkata
export PYTHONUNBUFFERED=1

cd "$PROJECT_ROOT"
mkdir -p outputs/live
exec >> "outputs/live/cloud-scheduler-$(date +%Y%m%d).log" 2>&1

echo "cloud_scheduler_started $(date '+%Y-%m-%dT%H:%M:%S%z')"
if ! DELTA_ENV=production "$PYTHON" -m delta_live.cli telegram-notify \
  --message "Delta BTC cloud scheduler started at 16:55 IST; preflight and paired entry are scheduled for 17:00 IST."; then
  echo "telegram_startup_notification_failed"
fi

set +e
DELTA_ENV=production DELTA_DRY_RUN=true \
  "$PYTHON" -m delta_live.production_preflight --asset BTC --size 200
preflight_status=$?
set -e
if [ "$preflight_status" -ne 0 ]; then
  DELTA_ENV=production "$PYTHON" -m delta_live.cli telegram-notify \
    --message "URGENT: Delta BTC cloud preflight failed with status $preflight_status. No live entry will be attempted." || true
  exit "$preflight_status"
fi

set +e
env \
  DELTA_ENV=production \
  DELTA_DRY_RUN=false \
  DELTA_PRODUCTION_ACK=LIVE_ORDERS_AUTHORIZED \
  "$PYTHON" -m delta_live.session \
  --asset BTC \
  --size 200 \
  --minutes 25 \
  --start-at 17:00:00 \
  --exit-at 17:24:30 \
  --slice-size 50 \
  --entry-window 20 \
  --unmatched-grace 10 \
  --confirm-production-orders
session_status=$?
set -e

if [ "$session_status" -ne 0 ]; then
  DELTA_ENV=production "$PYTHON" -m delta_live.cli telegram-notify \
    --message "URGENT: Delta BTC cloud session exited with status $session_status; emergency flat reconciliation is starting." || true
  env \
    DELTA_ENV=production \
    DELTA_DRY_RUN=false \
    DELTA_PRODUCTION_ACK=LIVE_ORDERS_AUTHORIZED \
    "$PYTHON" -m delta_live.emergency_flatten \
    --asset BTC \
    --confirm-production-orders
fi

exit "$session_status"
