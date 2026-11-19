# Light worker dry run on real data, 19 Nov 2026

Run on Mike's Mac (Apple silicon, Python 3.14, pyiceberg 0.12, pyarrow 26) on branch `claude/project-thread-un0e23` at 4afecb6, with three raw hours downloaded from S3 (`sydney-transit-lakehouse-raw-7342`). The worker was `python -m transit_lakehouse.worker run --once`, at its defaults then (`--max-rows 100000`, Arrow's default memory pool). Read-only for the server: nothing ran on EC2.

## Input

| Feed | UTC hour (Sydney) | Files | Raw on disk |
|---|---|---:|---:|
| buses_tripupdates | 17 Nov 21:00 (8 am peak) | 120 | 236 MB |
| buses_vehiclepos | 17 Nov 21:00 (8 am peak) | 240 | 40 MB |
| buses_tripupdates | 17 Nov 16:00 (3 am) | 120 | 6.6 MB |

## Result

| | All three hours | 3 am trip updates alone |
|---|---:|---:|
| Seconds (wall) | 56.8 | 1.6 |
| Peak memory (max RSS) | **941 MB** | 363 MB |
| Iceberg commits | 202 | 6 |
| `bronze.snapshot` rows / Parquet | 480 / 0.4 MB | 120 / 0.02 MB |
| `silver.stop_prediction` rows / Parquet | 10,071,845 / 84.6 MB | 124,976 / 1.0 MB |
| `silver.trip_status` rows / Parquet | 142,828 / 2.8 MB | 2,488 / 0.04 MB |
| `silver.vehicle_position` rows / Parquet | 701,312 / 7.8 MB | n/a |

A second `run --once` processed 0 files in 0.2 s, so reruns are no-ops. The sample rows checked by hand (one bus 15 s apart, one trip's stops 21–23 with 211–250 s delays) were correct.

## Reading

- **Bus stop predictions dominate.** The 8 am hour wrote about 9.9 million change rows (about 83,000 per 30 s snapshot), 80 times the 3 am hour. That is 84.6 MB of Parquet against 236 MB of gzipped raw. Scaled to the 3.38 GB/day of bus trip-update raw (D-024), it comes to roughly **1.2 GB and 100+ million rows a day**. A per-trip delay encoding (docs/9_phase1.md item 7) is worth testing before the worker runs live.
- **Memory was too high for the 900 MB cap.** Fixed in the same PR: the flush threshold is now 50,000 rows (one peak snapshot), and the worker uses the system allocator instead of Arrow's default pool. On a synthetic peak backlog in the cloud test box, peak memory fell from 664 MB to 421 MB. **The real-data rerun at dc91b4d peaked at 772 MB** (from 941 MB), with identical row counts, 62 s wall time, and 396 commits instead of 202 (smaller flushes, so Parquet grew about 12% until compaction). That fits under the 900 MB cap but with little headroom, so per-trip delay encoding and compaction (item 7) come before deployment.
- **Bug found:** with a space in the repo path (`3. Personal`), the warehouse went to a literal `3.%20Personal` folder beside the repo, because the default warehouse was a percent-encoded `file://` URI. Now it is a plain path, with a test; the rerun wrote inside the repo. The server path has no space, so EC2 was not affected.
- Throughput: about 0.45 s per peak trip-update snapshot, well inside the 30 s poll interval.
