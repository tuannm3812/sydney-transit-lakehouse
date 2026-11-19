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

## Rerun with stop predictions relative to the trip delay (D-028, 4f151a2)

Same three hours, same Mac, clean warehouse.

| | dc91b4d | 4f151a2 |
|---|---:|---:|
| `silver.stop_prediction` rows / Parquet | 10,071,845 / 95.0 MB | **5,632,675 / 59.3 MB** |
| `silver.trip_status` rows / Parquet | 142,828 / 3.4 MB | 443,100 / 7.9 MB |
| All tables, Parquet | 107.1 MB | **76.6 MB (−28%)** |
| Wall time | 62.1 s | 54.8 s |
| Peak memory (max RSS) | 772 MB | 765 MB |

`trip_status` grows because each trip-delay change is now one trip row (about 3,500 a snapshot at peak) in place of a row for every remaining stop. Scaled to a day, bus stop predictions come to roughly 0.75 GB instead of 1.2 GB. Peak memory is unchanged and sits at about 85% of the 900 MB container cap on macOS; it is re-measured on the Linux host before the worker is turned on.

## Replay, verify and a real timetable (09e726f)

Same Mac, clean warehouse. The rebuild matched 4f151a2 row for row (57.8 s, peak 757 MB).

### Replay from hour tars, then verify against the live build

| Window | Hours | Files | Wall | Peak RSS | Verify |
|---|---:|---:|---:|---:|---|
| 17 Nov T21–T22 | 2 feed-hours | 360 | 53.9 s | 710 MB | 2 of 5 table-hours differ |
| 17 Nov T16–T22 | 3 feed-hours | 480 | 58.3 s | 743 MB | **30 table-hours, 0 differ: identical** |

The T21-only difference comes from the seed, not from replay. The live build had diffed the first 21:00 trip-update snapshot against the last 16:59 one. The replay had no hour 20 tar to seed from, so it wrote that snapshot in full: 42 extra `trip_status` rows (7,238 against 7,196) and the matching `bronze.snapshot.rows_emitted`. Every later file, and `stop_prediction` for that first file, matched exactly. With the same seed on both sides (the T16–T22 window) the two lakehouses are identical. The gate check therefore needs both sides to start from the same previous file; on the live host it will, because the offload keeps every hour.

### Static timetable (D-029)

One real zip, `20261117T074509.770654Z_40c41e270064.zip` (from the 287 MB hour tar).

| | |
|---|---:|
| Wall time / peak RSS | 9.8 s / 638 MB |
| Agencies / routes / stops | 688 / 10,191 / 171,316 |
| Trips / stop times | 200,836 / 5,424,421 |
| Distinct stop patterns | 127,457 |
| Parquet, all timetable tables | ~40 MB (pattern stops 27 MB) |

The zip has no `feed_info.txt`, so the publisher and feed-version columns are null; the version id is the landed file stem.

### Agencies and the Sydney filter

688 agency ids carry 462 distinct names; many operators have several ids (Transit Systems NSW is 2503 and 7063). Regional operators are in the same feed: the dry run's Newcastle bus (route `2449_S303`) belongs to agency 2449, Port Stephens Coaches, and 2448/2450/2457/7024 are Hunter Valley Buses. `agency_id` from the timetable is a clean key for the gold Sydney filter.

### Memory

All runs fit under the 900 MB container cap: rebuild 757 MB, replay 710–743 MB, timetable 638 MB (macOS max RSS; Linux still to measure).
