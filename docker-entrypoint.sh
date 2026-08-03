#!/bin/sh
set -e

: "Starting Azure Container App entrypoint"

if [ -z "$SYNC_INTERVAL" ]; then
  SYNC_INTERVAL=60
fi

while true; do
  echo "==== SYNC START: $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===="
  python -u app.py
  echo "==== SYNC END: $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===="
  echo "Waiting $SYNC_INTERVAL seconds until next sync..."
  sleep "$SYNC_INTERVAL"
done
