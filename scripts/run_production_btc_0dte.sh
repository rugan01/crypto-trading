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
PY=/Users/rugan/Projects/Delta/.venv/bin/python

# Multi-channel alert. The desktop and logfile channels do not need the network,
# so an outage cannot silence the report of that outage (15 Aug 2026).
alert() {  # alert LEVEL MESSAGE
  DELTA_ENV=production "$PY" -m delta_live.cli alert --level "$1" --message "$2" || true
}

# Retry the margin check. A DNS blip at 16:55 took the whole session out on
# 15 Aug; the client's own backoff spans ~12s, which was not enough. Entry is at
# 17:00, so there are ~5 minutes of slack to spend before giving up.
SIZE_ATTEMPTS="${DELTA_SIZE_ATTEMPTS:-4}"
SIZE_RETRY_SLEEP="${DELTA_SIZE_RETRY_SLEEP:-45}"
BTC_SIZE=""
attempt=1
while [ "$attempt" -le "$SIZE_ATTEMPTS" ]; do
  set +e
  # Only stdout is captured; size_check diagnostics go to stderr, which the
  # exec at the top of this script already routes into the scheduler log.
  BTC_SIZE=$(DELTA_ENV=production "$PY" -m delta_live.size_check --asset BTC --target "$BTC_TARGET_SIZE")
  size_status=$?
  set -e
  if [ "$size_status" -eq 0 ] && [ -n "$BTC_SIZE" ]; then
    [ "$attempt" -gt 1 ] && echo "size_check recovered on attempt $attempt"
    break
  fi
  echo "size_check_failed status=$size_status attempt=$attempt/$SIZE_ATTEMPTS"
  BTC_SIZE=""
  if [ "$attempt" -lt "$SIZE_ATTEMPTS" ]; then
    sleep "$SIZE_RETRY_SLEEP"
  fi
  attempt=$((attempt + 1))
done

# NO TRADE when the margin check never succeeded. It used to fall back to the
# TARGET size, which is exactly backwards: a failed margin check is missing
# information, and the safe response to missing information is the smallest
# position, not the largest. On 15 Aug that fallback would have attempted 150
# lots needing more margin than the account held.
if [ -z "$BTC_SIZE" ]; then
  echo "NO TRADE: size_check failed ${SIZE_ATTEMPTS}x - refusing to fall back to the target size"
  alert ERROR "Delta BTC 0DTE: NO TRADE. The margin check failed ${SIZE_ATTEMPTS} times (likely network/DNS). No order was placed and the account is untouched."
  exit 0
fi
if [ "$BTC_SIZE" -eq 0 ]; then
  echo "NO TRADE: margin supports less than the minimum viable size"
  alert NO_TRADE "Delta BTC 0DTE: NO TRADE today. Available margin supports less than the minimum viable size against a target of ${BTC_TARGET_SIZE} contracts per leg."
  exit 0
fi
if [ "$BTC_SIZE" -ne "$BTC_TARGET_SIZE" ]; then
  echo "auto_sized target=$BTC_TARGET_SIZE chosen=$BTC_SIZE"
fi
BTC_SLICE_SIZE="${DELTA_BTC_SLICE_SIZE:-$BTC_SIZE}"

alert INFO "Delta BTC 0DTE scheduler started 16:55 IST. Size ${BTC_SIZE} per leg, paired entry 17:00 IST, market-quality checks telemetry-only."

set +e
DELTA_ENV=production DELTA_DRY_RUN=true DELTA_PERMISSIVE_ENTRY="$PERMISSIVE_ENTRY" \
  /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.production_preflight --asset BTC --size "$BTC_SIZE"
preflight_status=$?
set -e
if [ "$preflight_status" -ne 0 ]; then
  alert ERROR "Delta BTC preflight FAILED for ${BTC_SIZE} contracts per leg (status $preflight_status). No live entry attempted."
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
  alert ERROR "Delta BTC session exited with status $session_status. Emergency flat reconciliation starting."
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
