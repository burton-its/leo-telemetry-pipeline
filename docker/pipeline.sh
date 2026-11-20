#!/bin/sh
# Re-runs the one-shot stages (decode -> store) for each satellite on a timer.
# ingest runs as its own service and writes data/raw/<id>.jsonl and data/leo.db.
#
#   NORAD_IDS                  space-separated satellites (default: ISS)
#   PIPELINE_INTERVAL_SECONDS  pause between passes (default: 900 = 15 min)
set -u

IDS="${NORAD_IDS:-25544}"
INTERVAL="${PIPELINE_INTERVAL_SECONDS:-900}"

while true; do
  for id in $IDS; do
    echo "[pipeline] decode + store for NORAD $id"
    python -m decode "$id" && python -m store "$id" \
      || echo "[pipeline] NORAD $id skipped (see message above)"
  done
  sleep "$INTERVAL"
done
