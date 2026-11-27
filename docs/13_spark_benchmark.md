# 13. Redpanda + Spark benchmark

*2026-11-27. Decisions: D-023 (the benchmark), D-041 (one on-demand day, outside the Phase 1 gate), D-056 (this design).*

The benchmark answers one question for the long-term host decision (D-015, due before the AWS credits end in early March 2027): what do Redpanda and Spark Structured Streaming cost in latency, money and operations compared with the light worker, on the same feeds? It replays offloaded S3 hours (D-026), so it never calls TfNSW and never touches the collector host.

## What runs

```
S3 hour tars ──bench produce──▶ Redpanda ──Spark Structured Streaming──▶ Iceberg (data-bench/<run>/lakehouse)
  raw/feed=…/hour=HH.tar        raw.trip_updates      group by feed, state = last file
                                raw.vehicle_positions  the worker's decoder and differ
                                raw.service_alerts     foreachBatch → Spark's Iceberg writer
```

| Piece | Code | What it does |
|---|---|---|
| Producer | `bench produce` (bench/source.py, producer.py) | Reads one hour of every realtime feed at a time from the hour tars, exactly as `worker replay` does, and sends each landed file's stored bytes (gzipped) to `raw.<kind>`, keyed by feed so a feed's files keep their order. Each feed starts with one seed message, the last readable file of the hour before the window (D-049). `--speed 1` sends each file when its fetch time comes round again, standing in for the collector; `--speed 0` sends flat out. |
| Stream | `bench stream` (bench/stream.py, diff.py) | Kafka source → `groupBy(feed).applyInPandasWithState` → `foreachBatch`. The per-feed step runs the worker's own `FeedDiffer` on each message, with the feed's last readable file as Spark state, so a restart or a batch retried on another Python worker rebuilds the same baseline. The sink appends each table through Spark's Iceberg writer and logs, per snapshot, when the broker took it and when its batch committed. Each data hour, once a later fetch time has committed, is compacted with Iceberg's `rewrite_data_files` filtered to that hour's `fetched_at` (never the whole `(obs_date, mode)` partition), and snapshots older than 24 h are expired with `expire_snapshots`, as `run --loop` does with its own code (D-032). In a paced run that is once a wall-clock hour; flat out it comes round faster. |
| Catalog | Iceberg JDBC catalog on SQLite | Same file layout and catalog name as the worker (`data-bench/<run>/lakehouse/catalog.db`). PyIceberg creates the tables with the worker's schemas, partitioning and properties; Spark only appends. So `worker verify`, `worker status` and the gold build open the result unchanged. |
| Report | `bench report` (bench/report.py) | Rows per table, batch times, compaction time, and latency per feed (p50, p95, max of broker → commit), plus peak memory and CPU per container from `docker stats` samples. |

The static timetable stays with the worker: one ~300 MB zip a few times a day is not a stream. The Spark job builds the other six tables: `bronze.snapshot`, `silver.stop_prediction`, `silver.trip_status`, `silver.vehicle_position`, `silver.alert` and `silver.alert_scope`.

Versions: Spark 4.1.4 (Scala 2.13), Iceberg 1.12.0, Redpanda 25.2.10, Java 21 (Temurin). The image fetches the jars at build time (`Dockerfile.bench`), so a run does not depend on Maven Central.

## Rehearsal in the cloud sandbox, 27 Nov

Evidence: [docs/reports/bench-rehearsal-20261127.md](reports/bench-rehearsal-20261127.md). Real S3 hours, 25 Nov 21:00 to 22:00 UTC (8 to 9 am Sydney, Wednesday peak), all 11 realtime feeds, on 4 cores and 15 GB. Spark ran `local[*]` from a virtualenv; Redpanda in Docker.

| Check | Result |
|---|---|
| Messages | 1,729 (11 seeds), 308 MB gzipped, produced flat out in 41 s including the S3 download |
| Spark, flat out | 1,718 snapshots and 6,779,294 rows in 5 micro-batches of up to 400 messages, about 4.5 minutes (13× real time) |
| Worker replay, same hour, same box | the same 6,779,294 rows in 176 s including the S3 download |
| `worker verify`, worker replay vs Spark | **28 of 28 table-hours identical** (row count and order-free digest per table, feed and hour) |
| Spark at 5× real time, 10 s trigger | the same hour in 12.6 minutes, 77 micro-batches of 5.3 s median (7.7 s max), so it kept up with room to spare. Broker → commit per feed: p50 8.6 to 11.5 s, **p95 13.4 to 16.0 s**, max 17.3 s, against the worker's 60 s SLA. Peak memory about 4.0 GB (Spark JVM about 2.0 GB plus Python workers), against the worker's 900 MB cap. `verify` against the worker replay: 28 of 28 identical |
| Bench image in Compose | `Dockerfile.bench` builds; Redpanda, the stream and the producer ran from Compose on 4 feeds of the same hour (522 messages, 155,492 rows). `verify` against the worker replay: 10 of 11 table-hours identical; the 11th, `bronze.snapshot` for Service Alerts, differed only in `decode_error` text, because the image had protobuf 7.36 and the worker run 6.33 (`…FeedMessage': Wire format was corrupt` vs `…FeedMessage'`). `run_day.sh` now builds the bench image with the worker image's protobuf (`PROTOBUF_VERSION` build argument) and refuses to start if they still differ. |
| After the 27 Nov review fixes | Flat out on 4 feeds over two hours (21:00 to 23:00 UTC): the closed hour 21:00 was compacted once (5.7 s) and `verify` found 22 of 22 table-hours identical. |

Flat-out latency means nothing (every file waits behind the backlog), so latency comes from the paced run. Unit tests (`tests/test_bench.py`) cut a replayed window into random micro-batches, drop the per-worker cache at random, and check the rows still equal the live worker's under `verify`.

## Benchmark day runbook (about 3 Dec)

Mike starts and stops the instance (D-041). Nothing here runs on the collector host, and the collector's IAM role is not reused.

1. **Instance (Mike).** EC2 in ap-southeast-2, Ubuntu 24.04, `m7i-flex.large` (2 vCPU, 8 GB; free-tier eligible, so it runs on the credits, D-016) or `m7i-flex.xlarge` (16 GB) if the 8 GB run swaps; 40 GB gp3. Attach a role with the read-only S3 policy (ListBucket and GetObject on the raw bucket only). Instance settings → metadata: IMDSv2 required, hop limit 2, so containers can read the role (as in docs/8_offload.md). Security group: SSH from Mike's IP only. Same region as the bucket, so the download is free.
2. **Setup.** Install Docker (`curl -fsSL https://get.docker.com | sudo sh`, then `sudo usermod -aG docker ubuntu` and log in again). Do **not** run `scripts/bootstrap_vm.sh`: it asks for the TfNSW key and starts a collector. Clone the repo at the commit named in the approval, `touch .env` (left empty: the benchmark needs no TfNSW key, and the key never goes on this box), then `docker compose --profile bench build`.
3. **Pick the day.** A full weekday, e.g. Tuesday 2 Dec UTC. `docker compose --profile bench run --rm bench-produce produce --bucket B --start 2026-12-02T00 --end 2026-12-03T00 --check` lists any hour tar the window (and its seed hour) lacks. A missing hour is not always a loss: when TfNSW freezes a feed, every snapshot of the hour is a duplicate and nothing lands (I-22, ferries on 25 Nov at 17:00 UTC). Check it against the manifest.
4. **Run.** `nohup scripts/bench/run_day.sh sydney-transit-lakehouse-raw-7342 2026-12-02 &`. It runs, in order:
   - **A, paced:** the day at real time (`--speed 1`, about 24 h) into `data-bench/paced`, for latency and steady-state memory and CPU;
   - **B, flat out:** the same day into `data-bench/flat`, for backfill throughput (about 2 h at the rehearsal's 13×);
   - **C, worker:** `worker replay` of the same day under its 900 MB cap, timed, in 4-hour chunks with `worker expire --older-than 0h` between them, because replay does not expire as it goes yet (I-20, open; the workaround R3-01 used). Then `worker verify` of A and B against it.

   It first builds the worker image, builds the bench image with the same protobuf, and stops if they still differ. If the stream container exits part-way, the producer is killed, the stream's log is kept in `data-bench/stream-<run>.log` and the script stops with its exit code.

   `docker stats` is sampled every 30 s into one file per run (`data-bench/stats-paced.jsonl`, `stats-flat.jsonl`, `stats-worker.jsonl`). To cut the day short, pass a speed above 1 (for example 4 for about 6 h) and say so in the report.
5. **Results.** `data-bench/report-paced.md` and `report-flat.md`, the worker's wall time and the verify lines in `data-bench/run_day.log`, and the `stats-*.jsonl` files (the worker's memory is in `stats-worker.jsonl`). Copy them off the box (`scp`), then write report R1-02 (docs/reports/README.md): latency p50/p95 per feed against the 60 s SLA, peak memory against the 900 MB worker cap, throughput, instance-hours and cost, ops notes (moving parts, restarts, what broke).
6. **Stop (Mike).** Terminate the instance and delete its volume once the files are copied. Nothing else to clean up: the bucket is only read.

**Cost.** About 27 instance-hours. At on-demand prices for an 8 GB instance in Sydney that is a few US dollars, paid from the credits; check the current hourly price in the EC2 console before starting. S3 GETs for a day are about 300 requests, and same-region transfer is free.

## Running it by hand

```bash
pip install -e ".[bench]"                       # Spark needs Java 17 or 21
docker compose --profile bench up -d redpanda   # or any Kafka API broker
export BENCH_DATA_DIR=data-bench/try BENCH_BOOTSTRAP=localhost:9092
python -m transit_lakehouse.bench topics
python -m transit_lakehouse.bench stream --idle-stop 120 &
python -m transit_lakehouse.bench produce --bucket B --start 2026-11-25T21 --end 2026-11-25T22 --speed 0
python -m transit_lakehouse.bench report
python -m transit_lakehouse.worker --data-dir data-bench/worker replay --bucket B --feed … --start … --end …
python -m transit_lakehouse.worker --data-dir data-bench/worker verify --other data-bench/try --feed … --start … --end …
```

In Compose, Redpanda listens only on the Compose network (`redpanda:9092`); the stream and producer services reach it there.

## Known limits

- **At-least-once across tables.** Each table is its own Iceberg commit. A crash between two tables' writes in one micro-batch duplicates the first table's rows when the batch is retried; a batch whose writes all landed is skipped on retry (`metrics/batches.jsonl`). The worker avoids this by keeping rows and watermarks in one commit per table (D-027). Good enough for a benchmark; it would need Iceberg's write-audit-publish or per-table batch ids to go live.
- **Latency is measured from the broker**, not from TfNSW's fetch: in a replay, the broker time stands in for the collector landing the file. The worker's figure (Phase 1 item 4) is fetch → `ingested_at`; the report shows both commit and `ingested_at` times.
- **One partition per feed key** keeps order; the topics have 4 partitions and 11 feeds, so parallelism is per feed, not within a feed. The bus trip-update feed is the long pole either way.
