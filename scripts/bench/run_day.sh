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
# bronze.snapshot stores protobuf's error text, so the benchmark image is
# built with the worker image's protobuf, then checked (27 Nov rehearsal).
pb() { docker compose --profile "$1" run --rm --no-deps --entrypoint \
  python "$2" -c "import google.protobuf as p; print(p.__version__)"; }
docker compose --profile worker build worker
a="$(pb worker worker)"
compose build --build-arg PROTOBUF_VERSION="$a"
b="$(pb bench bench-produce)"
if [ "$a" != "$b" ]; then
  log "protobuf differs: worker $a, bench $b"
  exit 1
fi
log "protobuf $a in both images"

sampler=""
sample() {  # one stats file per run, so runs do not mix
  [ -n "$sampler" ] && kill "$sampler" 2>/dev/null || true
  scripts/bench/sample_stats.sh "data-bench/stats-$1.jsonl" 30 &
  sampler=$!
}
trap '[ -n "$sampler" ] && kill $sampler 2>/dev/null || true' EXIT

run_stream() {  # $1 = folder under data-bench, $2 = speed
  local dir="$1" pace="$2" name="bench-stream-$1" code
  log "run $dir: speed $pace, $start to $end"
  compose down -v --remove-orphans
  docker rm -f "$name" >/dev/null 2>&1 || true
  sample "$dir"
  compose up -d redpanda
  compose run --rm -e BENCH_DATA_DIR="/bench/$dir" bench-produce topics
  # No --rm: the container must outlive a crash so its exit code and
  # logs can be read.
  compose run -d --name "$name" \
    -e BENCH_DATA_DIR="/bench/$dir" bench-stream \
    stream --trigger "${BENCH_TRIGGER_S:-15}" --idle-stop 300
  # Stop the producer at once if the stream dies part-way.
  ( while [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ]
    do sleep 30; done
    docker ps -q --filter label=com.docker.compose.service=bench-produce \
      | xargs -r docker kill >/dev/null ) &
  local watch=$!
  compose run --rm -e BENCH_DATA_DIR="/bench/$dir" bench-produce \
    produce --bucket "$bucket" --start "$start" --end "$end" \
    --speed "$pace" | tee -a data-bench/run_day.log
  code="$(docker wait "$name")"
  kill "$watch" 2>/dev/null || true
  docker logs "$name" > "data-bench/stream-$dir.log" 2>&1
  docker rm "$name" >/dev/null
  if [ "$code" != 0 ]; then
    log "run $dir: stream exited $code, see data-bench/stream-$dir.log"
    exit 1
  fi
  compose run --rm -e BENCH_DATA_DIR="/bench/$dir" bench-produce \
    report --stats "/bench/stats-$dir.jsonl" > "data-bench/report-$dir.md"
  log "run $dir done: data-bench/report-$dir.md"
}

worker() {  # the light worker under its 900 MB cap, as on the server
  docker compose --profile worker run --rm --entrypoint python \
    -v "$PWD/data-bench:/bench" worker -m transit_lakehouse.worker \
    --data-dir /bench/worker "$@"
}

run_stream paced "$speed"
run_stream flat 0
compose down -v

# replay does not expire as it goes (I-20, open), so a whole day slows
# to minutes per hour; replay in 4-hour chunks with expiry between, the
# workaround used for R3-01. Chunks continue from the tables'
# watermarks, so the rows are the same as one replay.
log "run worker: replay $start to $end in 4-hour chunks"
sample worker
SECONDS=0
chunk="$start"
while [ "$chunk" \< "$end" ]; do
  next="$(python3 -c "import sys, datetime as d
f = '%Y-%m-%dT%H'
n = d.datetime.strptime(sys.argv[1], f) + d.timedelta(hours=4)
print(min(n.strftime(f), sys.argv[2]))" "$chunk" "$end")"
  worker replay --bucket "$bucket" $feeds --start "$chunk" --end "$next" \
    | tee -a data-bench/run_day.log
  worker expire --older-than 0h >> data-bench/run_day.log
  chunk="$next"
done
log "worker replay took $SECONDS s"
for dir in paced flat; do
  log "verify worker vs $dir"
  worker verify --other "/bench/$dir" $feeds --start "$start" \
    --end "$end" | tee -a data-bench/run_day.log
done
log "all done"
