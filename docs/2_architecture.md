# 2. Architecture

## Phase 0 (now)

```
            ┌──────────────── collector (one asyncio process) ───────────────┐
TfNSW  ───► │ per-feed schedule (15 s VP / 30 s TU, 1.5 s stagger)            │
Open Data   │   → global limiter (≤ 1 req/s, D-013)                           │
Hub         │   → GET with "Authorization: apikey …"                          │
            │   → classify: ok | backoff (429/5xx/403 rate) | wait (403 quota)│
            │   → sha256: unchanged? → manifest only                          │
            │             new?       → atomic write .pb + manifest            │
            └─────────────────────────────────────────────────────────────────┘
                     │                                   │
     data/raw/feed=…/date=…/hour=…/*.pb.gz     data/manifest/feed=…/date=…/hour=….jsonl
                                                         │
                               checks capture (SLA, exit 1 on breach)
                               checks quota  (24 h quota + size report)
```

### On-disk layout

- `raw/feed=<feed>/date=YYYY-MM-DD/hour=HH/<YYYYMMDDTHHMMSS.ffffffZ>_<sha12>.pb.gz` holds the exact bytes TfNSW returned, gzip-compressed (D-017), only when they differ from the previous snapshot of that feed.
- `manifest/feed=<feed>/date=YYYY-MM-DD/hour=HH.jsonl` has one line per poll attempt: `fetched_at`, `status`, `outcome` (`new`, `unchanged`, `http_error`, `network_error`), `latency_ms`, `size_bytes`, `sha256`, the feed header timestamp, entity count, the landed path, the error, and the limit-related response headers.
- `_heartbeat` holds the last successful poll time and drives the container healthcheck.

The UTC partitioning is explained in D-006, the dedupe rule in D-009 and the capture definition in D-007.

### Why files first, not Redpanda yet

See D-005. The Phase 0 gate is about the source: can we capture it at 99% within quota? Landed raw files are replayable, so Phase 1 can backfill Redpanda from them and nothing collected this week is wasted.

## Phase 1: light worker (D-023, D-027)

`python -m transit_lakehouse.worker run --loop 60` reads new raw files since each table's watermark, diffs each against the snapshot before it, and commits `bronze.snapshot` and the silver tables to Iceberg (SQLite catalog, warehouse under `data/lakehouse/`). The plan and work items are in docs/9_phase1.md.

## Phase 2: gold marts (D-035)

`python -m transit_lakehouse.gold build` points DuckDB views at each silver table's live Parquet files (listed by PyIceberg), runs dbt into `data/gold/transit.duckdb` and records every test in `gold.dq_results`. The plan and work items are in docs/11_phase2.md.

## Target (Phase 1+)

The collector also produces each new snapshot to Redpanda (one topic per feed kind, keyed by feed). Spark Structured Streaming decodes the protobuf into bronze Iceberg tables (vehicle positions, trip-update stop time events) on object storage, and silver/gold models are built incrementally with dbt on DuckDB. The products read gold. The details wait on the plan document (D-012).
