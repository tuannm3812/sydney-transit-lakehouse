# Spark benchmark rehearsal, 27 Nov 2026

Generated evidence for docs/13_spark_benchmark.md and D-056: `bench report` output and `worker verify` lines from the cloud sandbox (4 cores, 15 GB), on real S3 hours of 25 Nov 2026. Raw metrics stayed in the sandbox.

## Run 1: flat out, 21:00-22:00 UTC, 11 realtime feeds

- Window: first message 2026-11-27T13:10:58.137000+00:00, last commit 2026-11-27T13:17:38.122325+00:00 (0.11 h)
- 1,718 snapshots in 5 micro-batches; 6,779,294 rows (16,949 rows/s overall)
- Batch time p50 / p95 / max: 59.7 s / 76.9 s / 76.9 s

| table | rows |
|---|---:|
| bronze.snapshot | 1,718 |
| silver.alert | 28 |
| silver.alert_scope | 56 |
| silver.stop_prediction | 5,595,783 |
| silver.trip_status | 443,017 |
| silver.vehicle_position | 738,692 |

| feed | snapshots | p50 commit | p95 commit | max commit | p95 ingested_at |
|---|---:|---:|---:|---:|---:|
| buses_tripupdates | 120 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| buses_vehiclepos | 240 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| ferries_tripupdates | 120 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| ferries_vehiclepos | 120 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| lightrail_innerwest_tripupdates | 120 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| lightrail_innerwest_vehiclepos | 240 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| metro_tripupdates | 120 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| metro_vehiclepos | 240 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| service_alerts | 38 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| sydneytrains_tripupdates | 120 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |
| sydneytrains_vehiclepos | 240 | 327.3 s | 399.8 s | 399.8 s | 396.3 s |

`worker verify` (worker replay of the same hour vs Spark): 28 table-hours compared, 0 differ: identical

## Run 2: 5x real time, 10 s trigger, same hour and feeds

- Window: first message 2026-11-27T13:24:01.133000+00:00, last commit 2026-11-27T13:36:11.451720+00:00 (0.20 h)
- 1,718 snapshots in 77 micro-batches; 6,779,294 rows (9,283 rows/s overall)
- Batch time p50 / p95 / max: 5.3 s / 7.0 s / 7.7 s

| table | rows |
|---|---:|
| bronze.snapshot | 1,718 |
| silver.alert | 28 |
| silver.alert_scope | 56 |
| silver.stop_prediction | 5,595,783 |
| silver.trip_status | 443,017 |
| silver.vehicle_position | 738,692 |

| feed | snapshots | p50 commit | p95 commit | max commit | p95 ingested_at |
|---|---:|---:|---:|---:|---:|
| buses_tripupdates | 120 | 9.0 s | 15.1 s | 16.2 s | 13.7 s |
| buses_vehiclepos | 240 | 10.3 s | 15.8 s | 17.3 s | 14.5 s |
| ferries_tripupdates | 120 | 11.0 s | 13.5 s | 14.7 s | 12.3 s |
| ferries_vehiclepos | 120 | 8.6 s | 14.7 s | 15.8 s | 13.3 s |
| lightrail_innerwest_tripupdates | 120 | 11.5 s | 14.0 s | 15.2 s | 12.8 s |
| lightrail_innerwest_vehiclepos | 240 | 9.8 s | 15.2 s | 16.7 s | 13.9 s |
| metro_tripupdates | 120 | 10.9 s | 13.4 s | 16.6 s | 12.1 s |
| metro_vehiclepos | 240 | 10.3 s | 15.1 s | 16.3 s | 13.8 s |
| service_alerts | 38 | 9.9 s | 14.6 s | 15.4 s | 13.1 s |
| sydneytrains_tripupdates | 120 | 9.9 s | 16.0 s | 17.1 s | 14.6 s |
| sydneytrains_vehiclepos | 240 | 10.0 s | 15.4 s | 16.9 s | 14.1 s |

Peak resident memory of the Spark JVM and its Python workers, sampled every 15 s with `ps`: about 4,022 MB.

`worker verify`: 28 table-hours compared, 0 differ: identical

## Run 3: flat out, 21:00-23:00 UTC, 4 feeds, after the review fixes

- Window: first message 2026-11-27T14:38:00.545000+00:00, last commit 2026-11-27T14:39:12.534751+00:00 (0.02 h)
- 1,036 snapshots in 7 micro-batches; 286,719 rows (3,983 rows/s overall)
- Batch time p50 / p95 / max: 2.9 s / 9.5 s / 9.5 s
- Hourly compaction and expiry: 1 runs, mean 5.7 s, max 5.7 s

| table | rows |
|---|---:|
| bronze.snapshot | 1,036 |
| silver.alert | 45 |
| silver.alert_scope | 117 |
| silver.stop_prediction | 243,349 |
| silver.trip_status | 23,889 |
| silver.vehicle_position | 18,283 |

| feed | snapshots | p50 commit | p95 commit | max commit | p95 ingested_at |
|---|---:|---:|---:|---:|---:|
| metro_tripupdates | 240 | 42.2 s | 71.9 s | 71.9 s | 70.7 s |
| metro_vehiclepos | 480 | 42.2 s | 71.9 s | 71.9 s | 70.7 s |
| service_alerts | 76 | 42.2 s | 71.9 s | 71.9 s | 70.7 s |
| sydneytrains_tripupdates | 240 | 42.2 s | 71.9 s | 71.9 s | 70.7 s |

Compaction of the closed hour 21:00 ran once (`maintenance.jsonl`: 5.7 s). `worker verify`: 22 table-hours compared, 0 differ: identical
