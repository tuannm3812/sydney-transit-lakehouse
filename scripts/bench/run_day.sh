#!/usr/bin/env bash
# The benchmark day on the on-demand instance (docs/13_spark_benchmark.md).
#   scripts/bench/run_day.sh <bucket> <day, e.g. 2026-12-01> [speed]
# Run A: the Spark job on the day's raw hours paced at real time
# (speed 1, about 24 h). Run B: the same day flat out into a fresh
# folder (throughput). Run C: the light worker's replay of the same day
# on the same box, then `worker verify` of A and B against it.
# Never run this on the collector host.
set -euo pipefail
bucket="$1"
day="$2"
speed="${3:-1}"
start="${day}T00"
end="$(date -u -d "${day} + 1 day" +%Y-%m-%d)T00"
feeds=$(python3 - <<'PY'
import tomllib
raw = tomllib.load(open("config/feeds.toml", "rb"))
kinds = {"trip_updates", "vehicle_positions", "service_alerts"}
print(" ".join(
    f"--feed {f['name']}" for f in raw["feeds"]
    if f.get("enabled", True) and f["kind"] in kinds
))
PY
)
log() { echo "$(date -u +%FT%TZ) $*" | tee -a data-bench/run_day.log; }
compose() { docker compose --profile bench "$@"; }

mkdir -p data-bench
# The decoder's error text comes from protobuf, and bronze.snapshot
# stores it, so both images must decode with the same protobuf or
# verify differs on partial decodes (seen in the 27 Nov rehearsal).
pb() { docker compose --profile "$1" run --rm --no-deps --entrypoint \
  python "$2" -c "import google.protobuf as p; print(p.__version__)"; }
compose build
docker compose --profile worker build worker
a="$(pb worker worker)" b="$(pb bench bench-produce)"
if [ "$a" != "$b" ]; then
  log "protobuf differs: worker $a, bench $b; pin it and rebuild"
  exit 1
fi
scripts/bench/sample_stats.sh data-bench/stats.jsonl 30 &
sampler=$!
trap 'kill $sampler 2>/dev/null || true' EXIT

run_stream() {  # $1 = folder under data-bench, $2 = speed
  local dir="$1" pace="$2"
  log "run $dir: speed $pace, $start to $end"
  compose down -v --remove-orphans
  compose up -d redpanda
  compose run --rm -e BENCH_DATA_DIR="/bench/$dir" bench-produce topics
  compose run -d --name "bench-stream-$dir" --rm \
    -e BENCH_DATA_DIR="/bench/$dir" bench-stream \
    stream --trigger "${BENCH_TRIGGER_S:-15}" --idle-stop 300
  compose run --rm -e BENCH_DATA_DIR="/bench/$dir" bench-produce \
    produce --bucket "$bucket" --start "$start" --end "$end" \
    --speed "$pace" | tee -a data-bench/run_day.log
  docker wait "bench-stream-$dir" || true
  compose run --rm -e BENCH_DATA_DIR="/bench/$dir" bench-produce \
    report --stats /bench/stats.jsonl > "data-bench/report-$dir.md"
  log "run $dir done: data-bench/report-$dir.md"
}

run_stream paced "$speed"
run_stream flat 0
compose down -v

log "run worker: replay $start to $end"
# Wall time here; the worker container's memory and CPU are in the
# docker stats samples (the 900 MB cap applies, as on the server).
SECONDS=0
docker compose run --rm --entrypoint python \
  -v "$PWD/data-bench:/bench" worker -m transit_lakehouse.worker \
  --data-dir /bench/worker replay --bucket "$bucket" $feeds \
  --start "$start" --end "$end" | tee -a data-bench/run_day.log
log "worker replay took $SECONDS s"
for dir in paced flat; do
  log "verify worker vs $dir"
  docker compose run --rm --entrypoint python \
    -v "$PWD/data-bench:/bench" worker -m transit_lakehouse.worker \
    --data-dir /bench/worker verify --other "/bench/$dir" $feeds \
    --start "$start" --end "$end" | tee -a data-bench/run_day.log
done
log "all done"
