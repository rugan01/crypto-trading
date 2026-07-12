#!/usr/bin/env bash
set -euo pipefail

cd /Users/rugan/Projects/Delta
mkdir -p outputs/live
exec >> outputs/live/production-scheduler-$(date +%Y%m%d).log 2>&1

echo "scheduler_started $(date -Is)"
exec env \
  DELTA_ENV=production \
  DELTA_DRY_RUN=false \
  DELTA_PRODUCTION_ACK=LIVE_ORDERS_AUTHORIZED \
  /Users/rugan/Projects/Delta/.venv/bin/python \
  -m delta_live.session \
  --asset BTC \
  --size 100 \
  --minutes 25 \
  --start-at 17:00:00 \
  --confirm-production-orders
