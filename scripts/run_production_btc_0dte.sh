#!/usr/bin/env bash
set -euo pipefail

cd /Users/rugan/Projects/Delta
mkdir -p outputs/live
exec >> outputs/live/production-scheduler-$(date +%Y%m%d).log 2>&1

# TARGET size per leg. Auto-sizing clamps this down to whatever margin actually
# supports on the day, so this is a ceiling rather than a fixed quantity.
BTC_TARGET_SIZE="${DELTA_BTC_SIZE:-150}"
PERMISSIVE_ENTRY="${DELTA_PERMISSIVE_ENTRY:-true}"

echo "scheduler_started $(date '+%Y-%m-%dT%H:%M:%S%z')"

# Auto-size. The exchange needs base PLUS premium margin inside available
# balance, and premium margin scales with the day's credit, so a fixed size can
# fit one day and be rejected the next on an identical balance. This only ever
# reduces below the target, never raises above it.
set +e
# Only stdout is captured; the size_check diagnostics go to stderr, which the
# exec at the top of this script already routes into the scheduler log.
BTC_SIZE=$(DELTA_ENV=production /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.size_check --asset BTC --target "$BTC_TARGET_SIZE")
size_status=$?
set -e
if [ "$size_status" -ne 0 ] || [ -z "$BTC_SIZE" ]; then
  echo "size_check_failed status=$size_status - falling back to target $BTC_TARGET_SIZE"
  BTC_SIZE="$BTC_TARGET_SIZE"
fi
if [ "$BTC_SIZE" -eq 0 ]; then
  echo "NO TRADE: margin supports less than the minimum viable size"
  DELTA_ENV=production /Users/rugan/Projects/Delta/.venv/bin/python \
    -m delta_live.cli telegram-notify \
    --message "Delta BTC 0DTE: NO TRADE today. Available margin supports less than the minimum viable size against a target of ${BTC_TARGET_SIZE} contracts per leg." || true
  exit 0
fi
if [ "$BTC_SIZE" -ne "$BTC_TARGET_SIZE" ]; then
  echo "auto_sized target=$BTC_TARGET_SIZE chosen=$BTC_SIZE"
fi
BTC_SLICE_SIZE="${DELTA_BTC_SLICE_SIZE:-$BTC_SIZE}"

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
