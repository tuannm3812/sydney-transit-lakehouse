#!/usr/bin/env bash
# Sample `docker stats` for the benchmark containers every N seconds as
# JSON lines, for `bench report --stats` (docs/13_spark_benchmark.md).
#   scripts/bench/sample_stats.sh data-bench/stats.jsonl 30 &
set -euo pipefail
out="${1:-data-bench/stats.jsonl}"
every="${2:-30}"
mkdir -p "$(dirname "$out")"
while true; do
  docker stats --no-stream --format '{{json .}}' >> "$out" || true
  sleep "$every"
done
