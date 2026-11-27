#!/usr/bin/env bash
# Gate-day S3 history backfill into the live lakehouse (D-040, D-056).
#
#   scripts/backfill.sh 2026-11-17T06        # to now, then catch up
#   scripts/backfill.sh 2026-11-17T06 2026-11-27T21
#
# Run on the collector host from the repo folder, with the worker
# service stopped (the replay takes the live worker's lock, so a
# running worker makes it fail fast rather than collide). Loads the
# timetables first, then replays the realtime feeds one UTC day at a
# time, oldest first: each day starts where the last ended, so the live
# worker carries on from the final watermark (docs/4_next_steps.md).
#
# Without an end hour it replays up to the last hour the hourly offload
# has shipped (the hour before the current one), then keeps catching
# up on the hours shipped meanwhile, so the live worker starts with
# about an hour of local backlog instead of the whole run's.
#
# Before each day it reads the live watch state (D-034) and stops if a
# feed or disk alert is open. Stopping loses nothing: run the same
# command again and every feed resumes after its watermark.
#
# Reads S3 with the instance role (get and list only). Needs no TfNSW
# key and never prints one. Logs to docs/reports/backfill.log.
set -euo pipefail

START=${1:?start hour, e.g. 2026-11-17T06}
FOLLOW=0
shipped() { date -u -d '-1 hour' +%Y-%m-%dT%H; }  # hours before are in S3
if [ -n "${2:-}" ]; then END=$2; else END=$(shipped); FOLLOW=1; fi
BUCKET=${OFFLOAD_BUCKET:-$(grep -E '^OFFLOAD_BUCKET=' .env 2>/dev/null | cut -d= -f2- || true)}
STATE=docs/reports/watch-state.json
LOG=docs/reports/backfill.log

replay() {
  docker compose --profile worker run --rm --no-deps worker \
    --data-dir /data replay --bucket "$BUCKET" --scratch /data/backfill \
    "$@"
}

alert_open() {
  [ -f "$STATE" ] && python3 -c \
    'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("open") else 1)' \
    "$STATE"
}

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

[ -n "$BUCKET" ] || { echo "OFFLOAD_BUCKET is not set" >&2; exit 2; }
if docker compose ps --status running --services | grep -qx worker; then
  echo "the worker service is running; stop it first" >&2
  exit 2
fi

log "backfill $START to $END: timetables"
replay --feed static_gtfs --start "$START" --end "$END" 2>&1 | tee -a "$LOG"

realtime() {  # replay [$1, $2) one UTC day at a time
  local day=$1 next
  while [[ "$day" < "$2" ]]; do
    if alert_open; then
      log "stopped before $day: a live-watch alert is open ($STATE)"
      exit 3
    fi
    next=$(date -u -d "${day%T*} +1 day" +%Y-%m-%dT00)
    [[ "$next" < "$2" ]] || next=$2
    log "realtime $day to $next"
    replay --exclude-feed static_gtfs --start "$day" --end "$next" 2>&1 \
      | tee -a "$LOG"
    day=$next
  done
}

realtime "$START" "$END"
while [ "$FOLLOW" = 1 ] && [[ "$(shipped)" > "$END" ]]; do
  next=$(shipped)
  log "catching up $END to $next"
  replay --feed static_gtfs --start "$END" --end "$next" 2>&1 \
    | tee -a "$LOG"
  realtime "$END" "$next"
  END=$next
done
log "backfill done to $END"
docker compose --profile worker run --rm --no-deps worker status \
  2>&1 | tee -a "$LOG"
