---
id: R1-01
title: Light worker dry run on real data
subtitle: The Phase 1 worker built, replayed and verified the bronze and silver tables from three real hours of bus data and one timetable.
phase: 1
status: Final
verdict: Pass with actions
date: 2026-11-19
window: 17 Nov 2026, 16:00 to 17:00 and 21:00 to 22:00 UTC (bus feeds); timetable published 07:45 UTC
commit: 09e726f
environment: Mike's Mac (Apple silicon, macOS), Python 3.14, pyiceberg 0.12, pyarrow 26, DuckDB. Nothing ran on the collector host.
decisions: D-023, D-027, D-028, D-029, D-030
evidence: hour tars from the offload bucket (D-026); worker output in data/lakehouse on the Mac (gitignored)
---

# Light worker dry run on real data

## Summary

> The light worker is ready to deploy after the Phase 0 gate. On real data it builds the tables in under a minute, a rerun adds nothing, a replay gives **identical tables**, and every run fits the 900 MB memory cap.

- **Replay matches the live build.** Rebuilding 16:00 to 22:00 UTC from the hour tars gave 30 of 30 table-hours identical to the worker's own build (section 3.4).
- **Storage fell by 29%.** Storing stop predictions relative to the trip delay (D-028) cut them from 10,071,845 to 5,632,675 rows, and all tables from 108.1 MB to 76.6 MB (section 3.3).
- **Memory fits the cap.** Peak memory was 757 MB for a full rebuild and 743 MB for a replay, down from 941 MB on the first run (sections 3.2 and 3.6).
- **A real timetable loads in 9.8 s.** Its 200,836 trips and 5,424,421 stop times were stored as 127,457 distinct stop patterns (D-029, section 3.5).
- **99.6% of live trips are in the timetable,** for both trip updates and vehicle positions, so realtime and timetable tables join cleanly (section 3.5).

## Scope and method

The worker (`python -m transit_lakehouse.worker`, D-027) reads raw snapshots, compares each with the one before, and writes only the changes:

```flow
raw | Hour tars | from object storage, one per feed and hour
bronze | bronze.snapshot | one row per raw file
silver | Silver tables | vehicle positions, stop predictions, trip status, timetable
check | Verify | replayed tables compared with the live build
```

It ran on Mike's Mac against these inputs, from an empty warehouse each time unless stated. The code changed five times during the day, so each table names the commit its numbers came from.

| Feed | UTC hour (Sydney) | Files | Raw on disk |
|---|---|---:|---:|
| `buses_tripupdates` | 21:00 (8 am peak) | 120 | 236 MB |
| `buses_vehiclepos` | 21:00 (8 am peak) | 240 | 40 MB |
| `buses_tripupdates` | 16:00 (3 am) | 120 | 6.6 MB |
| `static_gtfs` | 07:00 (6 pm) | 1 | 287 MB |

Commands: `run --once` to build, `replay --tars` then `verify --other` to check a replay, and `run --once --feed static_gtfs` for the timetable. Peak memory is the maximum resident set size reported by `/usr/bin/time -l`.

Out of scope: the other ten feeds, Service Alerts on real data, compaction, and any run on Linux.

## Results

### First run

At 4afecb6.

| | All three hours | 3 am trip updates alone |
|---|---:|---:|
| Wall time | 56.8 s | 1.6 s |
| Peak memory | **941 MB** | 363 MB |
| Iceberg commits | 202 | 6 |
| `bronze.snapshot` rows | 480 | 120 |
| `silver.stop_prediction` rows | 10,071,845 | 124,976 |
| `silver.trip_status` rows | 142,828 | 2,488 |
| `silver.vehicle_position` rows | 701,312 | n/a |

A second `run --once` found 0 new files in 0.2 s. Rows checked by hand (one bus 15 s apart; one trip's stops 21 to 23, delayed 211 to 250 s) matched the raw feed.

### Memory fix

At dc91b4d the worker writes to Iceberg every 50,000 rows instead of 100,000 (about one peak snapshot) and uses the system memory allocator.

| | 4afecb6 | dc91b4d |
|---|---:|---:|
| Peak memory | 941 MB | **772 MB** |
| Wall time | 56.8 s | 62.1 s |
| Iceberg commits | 202 | 396 |
| Rows written | 10,916,465 | 10,916,465 |

### Stop predictions relative to the trip delay

D-028, at 4f151a2. Each stop now stores its delay minus the trip's delay, so a whole trip running late changes one trip row instead of every remaining stop.

| | dc91b4d | 4f151a2 |
|---|---:|---:|
| `silver.stop_prediction` rows | 10,071,845 | 5,632,675 |
| `silver.stop_prediction` Parquet | 95.0 MB | 59.3 MB |
| `silver.trip_status` rows | 142,828 | 443,100 |
| All tables, Parquet | 108.1 MB | 76.6 MB |
| Wall time | 62.1 s | 54.8 s |
| Peak memory | 772 MB | 765 MB |

```chart
title: Stop-prediction rows for the same three hours
unit: rows
Before D-028: 10071845
After D-028: 5632675
```

### Replay and verify

At 09e726f. `verify` compares each table, feed and hour by row count and a content digest.

| Window (17 Nov, UTC) | Files | Wall time | Peak memory | Table-hours | Result |
|---|---:|---:|---:|---|---|
| 21:00 to 22:00 | 360 | 53.9 s | 710 MB | 2 of 5 differ | Differs |
| 16:00 to 22:00 | 480 | 58.3 s | 743 MB | 0 of 30 differ | Identical |

The short window differs only in its first trip-update file. The live build compared that file with the last 16:59 snapshot; the replay had no earlier hour, so it wrote the file in full: 42 more `silver.trip_status` rows (7,238 against 7,196) and the matching count in `bronze.snapshot`. Every later file matched. With the same starting snapshot on both sides, the tables are identical.

### Static timetable

D-029, at 09e726f.

| | |
|---|---:|
| Wall time | 9.8 s |
| Peak memory | 638 MB |
| Agencies / routes / stops | 688 / 10,191 / 171,316 |
| Trips | 200,836 |
| Stop times | 5,424,421 |
| Distinct stop patterns | **127,457** |
| Parquet, all timetable tables | about 40 MB |
| Live trips found in the timetable | 99.6% |

The zip has no `feed_info.txt`, so the publisher and feed-version columns are empty and the version id is the landed file name. The join rate was measured on 9,776 distinct trips in `silver.trip_status` and 7,435 in `silver.vehicle_position`.

### Peak memory

| Run | Commit | *Peak memory* | Under the cap |
|---|---|---:|---|
| First run | 4afecb6 | 941 MB | No |
| Memory fix | dc91b4d | 772 MB | Yes |
| Rebuild | 09e726f | 757 MB | Yes |
| Replay, 16:00 to 22:00 | 09e726f | 743 MB | Yes |
| Replay, 21:00 to 22:00 | 09e726f | 710 MB | Yes |
| Timetable | 09e726f | 638 MB | Yes |

```chart
title: Peak memory by run at 09e726f
unit: MB
limit: 900 | container cap
Rebuild: 757
Replay 16:00 to 22:00: 743
Replay 21:00 to 22:00: 710
Timetable: 638
```

## Findings

- **Bus stop predictions are most of the volume.** Even after D-028 they are 5,632,675 of the 6,777,087 silver rows, nearly all from the 8 am hour. Scaled to the 3.38 GB a day of bus trip-update raw data (D-024), that is roughly 0.75 GB of Parquet a day (estimated), against 1.2 GB before D-028.
- **D-028 moves rows, it does not lose them.** `silver.trip_status` grew from 142,828 to 443,100 rows, because a trip-delay change is now one trip row. The encoding is lossless and tested (`tests/test_worker.py`).
- **Replay needs a matching start.** `verify` matches only when both sides start from the same previous snapshot, or both from none (section 3.4). The offload keeps every hour, so a replay on the host can always start from the hour before.
- **The bus feed covers all of NSW.** The sample bus on route `2449_S303` was near Newcastle; agency 2449 is Port Stephens Coaches, and 2448, 2450, 2457 and 7024 are Hunter Valley Buses. Silver keeps everything, and the Sydney products filter by `agency_id` in gold.
- **One operator can have several agency ids.** 688 ids map to 462 names (Transit Systems NSW is 2503 and 7063), so gold groups operators by name.
- **A path bug was found and fixed.** With a space in the repo path (`3. Personal`), the warehouse landed in a `3.%20Personal` folder. It is now a plain path, with a test (dc91b4d). The host path has no space.

## Limits

- All runs were on macOS. Memory on the Linux host may differ, and is measured before the worker goes live.
- Three hours of two bus feeds and one timetable. The other realtime feeds are much smaller (D-024) but have not run on real data.
- Service Alerts history (D-030) is tested only on generated data.
- Parquet sizes are before compaction; smaller writes mean more small files until compaction is added.
- The 0.75 GB a day estimate scales one peak hour and one quiet hour; the first live day gives the real figure.

## Next steps

- **Before deploying:** add compaction, replay one Service Alerts hour, and measure memory on Linux (docs/9_phase1.md).
- **After the Phase 0 gate (24 to 25 Nov), with Mike's approval:** deploy the worker on the collector host, replay and verify a full day, and report the delay from fetch to silver.
- **Needs Mike:** review and merge PR #14, and approve an on-demand instance for the Redpanda and Spark benchmark (D-023).
