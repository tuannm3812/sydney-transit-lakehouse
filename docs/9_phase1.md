# 9. Phase 1 plan: build the lakehouse

*2026-11-18. Decisions: D-023 (runtime), D-027 (worker design).*

Phase 1 turns the landed raw snapshots into Iceberg tables with a small always-on worker, then measures Redpanda + Spark Structured Streaming on the same feeds as a benchmark. Work starts now on branches, against tests and captured data, while the Phase 0 capture run finishes. Nothing in Phase 1 touches the running collector.

**Gate:** a replay of the raw snapshots for a full day rebuilds every silver table row for row (ignoring the ingest timestamp), and p95 latency from fetch to committed row is within 60 s on the live worker.

## Work items

| # | Item | Status | Waits for |
|---|---|---|---|
| 1 | **Light worker core** (`transit_lakehouse.worker`): tolerant decode, `bronze.snapshot`, `silver.vehicle_position`, `silver.stop_prediction`, `silver.trip_status`, watermarks, snapshot expiry | PR open | nothing |
| 2 | **Real-data dry run**: run item 1 on a captured peak hour and an overnight hour; record rows, Parquet size, memory and seconds per snapshot in `docs/reports/` | next | a copy of a few hours from S3 (Mike) |
| 3 | **Replay from S3** (read the hourly tars, D-026) and `worker verify-replay` for the gate | planned | nothing to build; a real run needs item 2's data |
| 4 | **Latency report**: fetch → committed row, p50/p95 per feed, against the 60 s SLA | planned | the worker running live |
| 5 | **Static timetable versions** (`silver.timetable_*`, one version per zip sha256, streamed in chunks so the 287 MB zip fits the host) | planned | nothing |
| 6 | **Service Alerts** (`silver.alert`, `silver.alert_scope`, SCD2 by alert version) with a TfNSW-tolerant proto (D-020) | planned | nothing |
| 7 | **Compaction** of small files by closed hour, and a test of per-trip delay encoding to shrink bus `stop_prediction` | planned | item 2's measurements |
| 8 | **Deploy the worker** on the collector host (`docker compose --profile worker up -d worker`) | ready after item 2 | **the Phase 0 gate (24–25 Nov)** and Mike's SSH approval |
| 9 | **Iceberg warehouse on S3** (prefix `lakehouse/` in the raw bucket; the role needs delete on that prefix only, for expiry and compaction) | proposed | Mike approves the IAM change |
| 10 | **Redpanda + Spark benchmark**: compose profile that replays raw into Redpanda and runs Spark into a separate `bench` namespace; compare latency, cost and ops load with the worker | planned | Mike approves an on-demand 8–16 GB instance for the run hours |
| 11 | **Re-decide the long-term host** before the AWS credits end (D-015) | open | Mike |

## Tables built by item 1

All tables are partitioned by `obs_date` (the UTC date of the poll, D-006) and `mode`, written as zstd Parquet, with every column optional.

| Table | One row per | Rule |
|---|---|---|
| `bronze.snapshot` | landed raw file | Always written. Fetch time, header time, entity count, failed entities, decode error, rows emitted, ingest time. The lineage and latency table. |
| `silver.stop_prediction` | stop × change | Written when a stop's predicted arrival, departure or status differs from the same stop in the previous snapshot, or the stop is new. |
| `silver.trip_status` | trip × change | Written when the trip's status, route, vehicle, trip delay, start time or stop count changes. |
| `silver.vehicle_position` | vehicle report | Written when the vehicle's own timestamp changes (two polls of one report are one event). Implausible readings are kept with `quarantined = true` and a reason: no position, outside NSW, faster than 150 km/h, or a timestamp more than 10 minutes ahead. |

## Running it

```bash
pip install -e ".[worker]"
python -m transit_lakehouse.worker run --once            # one pass over new raw files
python -m transit_lakehouse.worker run --loop 60         # always-on
python -m transit_lakehouse.worker status                # rows and watermarks
python -m transit_lakehouse.worker expire --older-than 24h
```

It reads `DATA_DIR` (default `data`), writes the catalog and warehouse under `DATA_DIR/lakehouse/`, and never calls the TfNSW API. `LAKEHOUSE_CATALOG_URI` and `LAKEHOUSE_WAREHOUSE` override the locations.

## What must wait for the Phase 0 gate

- **Deploying the worker on the collector host.** It shares 2 GB of memory and the disk with the collector. A bus trip-update backlog peaked at about 600 MB in a synthetic test, so the compose service is capped at 900 MB and one CPU, and sits behind the `worker` profile so a routine `docker compose up` does not start it. Starting it during the capture run would put the gate at risk for no gain: the raw files are kept, so the worker can catch up afterwards.
- **Anything on the server**, including a one-off run, needs Mike's approval naming the action, target and commit.

## Open risks

- **Bus `stop_prediction` volume.** At peak about half of all bus stop predictions change every 30 s (docs/reports/profile-20261118.md), which is roughly 80,000 rows a snapshot and tens of millions of rows a day. Item 2 measures the real Parquet size; item 7 tests storing a per-trip delay change instead of every downstream stop.
- **Small files.** One commit a minute writes about 1,440 small files per table partition a day until compaction (item 7) lands. Reads stay correct, just slower.
- **Seeding after a prune.** Each batch diffs against the file before it. If that file was pruned locally (D-026 keeps 3 days), the first file of the batch is written in full and the run logs a warning. This only happens if the worker falls more than 3 days behind.
