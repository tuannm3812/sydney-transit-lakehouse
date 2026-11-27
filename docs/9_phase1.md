# 9. Phase 1 plan: build the lakehouse

*2026-11-18. Decisions: D-023 (runtime), D-027 (worker design).*

Phase 1 turns the landed raw snapshots into Iceberg tables with a small always-on worker, then measures Redpanda + Spark Structured Streaming on the same feeds as a benchmark. Work starts now on branches, against tests and captured data, while the Phase 0 capture run finishes. Nothing in Phase 1 touches the running collector.

**Gate:** `worker replay` of a full day from S3 into an empty lakehouse, then `worker verify` against the live tables, reports every table-hour identical (ignoring the ingest timestamp), and p95 latency from fetch to committed row is within 60 s on the live worker.

## Work items

| # | Item | Status | Waits for |
|---|---|---|---|
| 1 | **Light worker core** (`transit_lakehouse.worker`): tolerant decode, `bronze.snapshot`, `silver.vehicle_position`, `silver.stop_prediction`, `silver.trip_status`, watermarks, snapshot expiry | PR open | nothing |
| 2 | **Real-data dry run**: run item 1 on a captured peak hour and an overnight hour; record rows, Parquet size, memory and seconds per snapshot | done 19 Nov (docs/reports/worker-dryrun-20261119.md); rerun after the memory fix | nothing |
| 3 | **Replay from S3** (`worker replay --bucket … --start … --end …` reads the hourly tars one hour at a time, D-026) and `worker verify --other …` (row count and order-free digest per table, feed and hour) for the gate | built 19 Nov, tested against tars made by the offload code | the worker live on the server, then a day replayed from S3 |
| 4 | **Latency report**: fetch → committed row, p50/p95 per feed, against the 60 s SLA | planned | the worker running live |
| 5 | **Static timetable versions** (`silver.timetable_*`, D-029): one version per landed zip, stop times stored once per distinct trip pattern | built 19 Nov, synthetic tests; real zip to measure | a static zip from S3 for a real run |
| 6 | **Service Alerts** (`silver.alert`, `silver.alert_scope`, type-2 history by content hash; TfNSW's field 7 repaired, D-030) | done: real 17 Nov 20:00 to 22:00 UTC replayed, 314 alerts, replay identical, 159 MB | |
| 7 | **Compaction** of small files by closed hour (D-032), and per-trip delay encoding to shrink bus `stop_prediction` (D-028) | done | |
| 8 | **Deploy the worker** on the collector host: S3 backfill into the live lakehouse with `scripts/backfill.sh`, then `docker compose --profile worker up -d --no-deps worker` (D-056, runbook in docs/4_next_steps.md) | ready; dry-run in Docker on 27 Nov | **the Phase 0 gate (about 28 Nov, D-033)**; pre-approved runbook (D-043), SSH approval names the commit |
| 9 | **Iceberg warehouse on S3** (prefix `lakehouse/` in the raw bucket; the role needs delete on that prefix only, for expiry and compaction) | proposed | Mike approves the IAM change |
| 10 | **Redpanda + Spark benchmark**: compose profile that replays raw into Redpanda and runs Spark into a separate `bench` namespace; compare latency, cost and ops load with the worker | planned | Mike approves an on-demand 8–16 GB instance for the run hours |
| 11 | **Re-decide the long-term host** before the AWS credits end (D-015) | open | Mike |

## Tables built by item 1

All tables are partitioned by `obs_date` (the UTC date of the poll, D-006) and `mode`, written as zstd Parquet, with every column optional.

| Table | One row per | Rule |
|---|---|---|
| `bronze.snapshot` | landed raw file | Always written. Fetch time, header time, entity count, failed entities, decode error, rows emitted, ingest time. The lineage and latency table. |
| `silver.stop_prediction` | stop × change | Written when a stop's prediction changes *relative to its trip's delay* (or its status or scheduled time changes), or the stop is new. A whole trip running later is one `trip_status` row, not a row per remaining stop (D-028). `trip_delay_s` is the trip delay the row was written against. |
| `silver.trip_status` | trip × change | Written when the trip's status, route, vehicle, current delay (`current_delay_s`, its first listed stop's delay), start time or stop count changes, and as a `REMOVED` row with stop count 0 when the trip leaves the feed (D-036). |
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

- **Deploying the worker on the collector host.** It shares 2 GB of memory and the disk with the collector. A real 8 am bus backlog peaked at 941 MB before the memory fix (docs/reports/worker-dryrun-20261119.md), so the worker now flushes every 50,000 rows and uses the system allocator, and the compose service is capped at 900 MB and one CPU, and sits behind the `worker` profile so a routine `docker compose up` does not start it. Starting it during the capture run would put the gate at risk for no gain: the raw files are kept, so the worker can catch up afterwards.
- **Anything on the server**, including a one-off run, needs Mike's approval naming the action, target and commit.

## Open risks

- **Bus `stop_prediction` volume.** At peak about half of all bus stop predictions change every 30 s (docs/reports/profile-20261118.md), measured at about 83,000 rows a snapshot and 9.9 million rows (84.6 MB of Parquet) in the 8 am hour, roughly 1.2 GB a day (docs/reports/worker-dryrun-20261119.md); item 7 tests storing a per-trip delay change instead of every downstream stop.
- **Small files.** One commit a minute writes about 1,440 small files per table partition a day; hourly compaction (D-032) merges each closed hour into one file, and the hourly `expire` deletes the replaced files and the old manifests; tables keep 20 metadata JSON files. On the real 8 am hour the four bus tables went from 282 files to 13 with rows unchanged. Hours with fewer than 4 files are left alone.
- **Seeding after a prune.** Each batch diffs against the file before it. If that file was pruned locally (D-026 keeps 3 days), the first file of the batch is written in full and the run logs a warning. This only happens if the worker falls more than 3 days behind.
- **The bus feed is statewide.** The dry run's sample bus (route `2449_S303`) was at latitude −32.7, near Newcastle. TfNSW's `buses` feeds include regional NSW operators, so silver keeps everything, and the gold models for the Sydney products filter by agency or area (to decide in Phase 2 from the static timetable's `agency.txt`). The first real timetable has 688 agency ids; route `2449_S303` is agency 2449, Port Stephens Coaches (docs/reports/worker-dryrun-20261119.md).
