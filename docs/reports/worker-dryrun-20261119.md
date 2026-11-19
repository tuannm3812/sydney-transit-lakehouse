---
id: R1-01
title: Light worker dry run on real data
subtitle: The Phase 1 worker built, replayed and verified bronze and silver tables from three real hours of Sydney bus data and one real timetable.
phase: 1
status: Final
verdict: Pass with actions
date: 2026-11-19
commit: 09e726f
environment: Mike's Mac (Apple silicon, macOS), Python 3.14, pyiceberg 0.12, pyarrow 26, DuckDB 1.x. Nothing ran on the collector host.
decisions: D-023, D-027, D-028, D-029, D-030
evidence: hour tars from the offload bucket (D-026); worker output in data/lakehouse on the Mac (gitignored); this report
highlights:
  - 30 / 30 | table-hours identical after replay
  - 5.6 M | stop-prediction rows for the 8 am hour, down from 10.1 M
  - 757 MB | peak memory, under the 900 MB cap
  - 9.8 s | to load a 200,836-trip timetable
---

# Light worker dry run on real data

## Summary

> The light worker is ready to deploy after the Phase 0 gate. On real data it builds the tables in under a minute, a rerun adds nothing, a replay from the hour tars gives **identical tables**, and every run fits the 900 MB container cap.

- **Replay matches the live build.** Rebuilding 17 Nov 16:00 to 22:00 UTC from the hour tars gave 30 of 30 table-hours identical to the worker's own build (section 3.4).
- **Storage fell by 29%.** Storing stop predictions relative to the trip delay (D-028) cut `silver.stop_prediction` from 10,071,845 to 5,632,675 rows for the same hours, and all tables from 108.1 MB to 76.6 MB of Parquet (section 3.3).
- **Memory fits, with modest headroom.** Peak memory was 757 MB for the rebuild and at most 743 MB for a replay, down from 941 MB on the first run (sections 3.2 and 3.6).
- **A real timetable loads in 9.8 s.** 200,836 trips and 5,424,421 stop times were stored as 127,457 distinct stop patterns (D-029, section 3.5).
- **99.6% of live trips join the timetable.** That holds for both trip updates and vehicle positions, so the realtime and static tables line up (section 3.5).

## Scope and method

The worker (`python -m transit_lakehouse.worker`, D-027) ran on Mike's Mac against hours copied from the offload bucket. Each run started from an empty warehouse unless stated. The code moved through five commits during the day; each table names the commit its numbers came from.

| Feed | UTC hour (Sydney) | Files | Raw on disk |
|---|---|---:|---:|
| `buses_tripupdates` | 17 Nov 21:00 (8 am peak) | 120 | 236 MB |
| `buses_vehiclepos` | 17 Nov 21:00 (8 am peak) | 240 | 40 MB |
| `buses_tripupdates` | 17 Nov 16:00 (3 am) | 120 | 6.6 MB |
| `static_gtfs` | 17 Nov 07:00 (6 pm) | 1 | 287 MB |

The commands were `run --once` (build), `replay --tars` then `verify --other` (replay check), and `run --once --feed static_gtfs` (timetable). Peak memory is the process's maximum resident set size from `/usr/bin/time -l`.

Out of scope: the other ten feeds, Service Alerts on real data, compaction, and any run on the Linux host.

## Results

### First run (4afecb6)

| | All three hours | 3 am trip updates alone |
|---|---:|---:|
| Seconds (wall) | 56.8 | 1.6 |
| Peak memory | **941 MB** | 363 MB |
| Iceberg commits | 202 | 6 |
| `bronze.snapshot` rows | 480 | 120 |
| `silver.stop_prediction` rows | 10,071,845 | 124,976 |
| `silver.trip_status` rows | 142,828 | 2,488 |
| `silver.vehicle_position` rows | 701,312 | n/a |

A second `run --once` processed 0 files in 0.2 s. Sample rows checked by hand (one bus 15 s apart, one trip's stops 21 to 23 with 211 to 250 s delays) matched the raw feed.

### Memory fix (dc91b4d)

The flush threshold fell from 100,000 to 50,000 rows (about one peak snapshot) and Arrow now uses the system allocator.

| | 4afecb6 | dc91b4d |
|---|---:|---:|
| Peak memory | 941 MB | **772 MB** |
| Wall time | 56.8 s | 62.1 s |
| Iceberg commits | 202 | 396 |
| Row counts | 10.9 M | identical |

### Stop predictions relative to the trip delay (D-028, 4f151a2)

| | dc91b4d | 4f151a2 |
|---|---:|---:|
| `silver.stop_prediction` rows | 10,071,845 | 5,632,675 |
| `silver.stop_prediction` Parquet | 95.0 MB | 59.3 MB |
| `silver.trip_status` rows | 142,828 | 443,100 |
| All tables, Parquet | 108.1 MB | **76.6 MB** |
| Wall time | 62.1 s | 54.8 s |
| Peak memory | 772 MB | 765 MB |

The rebuild at 09e726f gave the same row counts in 57.8 s at 757 MB.

### Replay from hour tars and verify (09e726f)

| Window | Files | Wall | Peak memory | Verify |
|---|---:|---:|---:|---|
| 17 Nov 21:00 to 22:00 UTC | 360 | 53.9 s | 710 MB | 2 of 5 table-hours differ |
| 17 Nov 16:00 to 22:00 UTC | 480 | 58.3 s | 743 MB | **30 of 30 identical** |

The short window differs only in its first trip-update file. The live build had compared it with the last 16:59 snapshot; the replay had no earlier hour, so it wrote that file in full: 42 more `trip_status` rows (7,238 against 7,196) and the matching `bronze.snapshot.rows_emitted`. Every later file, and `stop_prediction` for that first file, matched. With the same starting snapshot on both sides the lakehouses are identical.

### Static timetable (D-029, 09e726f)

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

The zip has no `feed_info.txt`, so publisher and feed-version columns are empty; the version id is the landed file name. The join rate was measured on 9,776 distinct trips in `silver.trip_status` and 7,435 in `silver.vehicle_position`.

### Peak memory across runs

| Run | Commit | Peak memory |
|---|---|---:|
| First run | 4afecb6 | 941 MB |
| Memory fix | dc91b4d | 772 MB |
| Rebuild | 09e726f | 757 MB |
| Replay, 16:00 to 22:00 | 09e726f | 743 MB |
| Replay, 21:00 to 22:00 | 09e726f | 710 MB |
| Timetable | 09e726f | 638 MB |

```chart
title: Peak memory by run at 09e726f (MB)
unit: MB
limit: 900 | container cap
Rebuild: 757
Replay 16:00 to 22:00: 743
Replay 21:00 to 22:00: 710
Timetable: 638
```

## Findings

- **Bus stop predictions dominate the volume.** Even after D-028 they are 5,632,675 of the 6,777,087 silver rows for these hours, nearly all from the 8 am hour. Scaled to the 3.38 GB a day of bus trip-update raw (D-024), that is roughly 0.75 GB of Parquet a day (estimated), against 1.2 GB before D-028.
- **D-028 moves rows, it does not lose them.** `trip_status` grew from 142,828 to 443,100 rows because one trip-delay change is now one trip row instead of a row per remaining stop. The encoding is lossless (D-028, tests in `tests/test_worker.py`).
- **The replay gate needs a matching start.** `verify` only matches when both sides start from the same previous snapshot, or both from none (section 3.4). On the host the offload keeps every hour, so a replay of any window can start from the hour before it.
- **The bus feed is statewide.** The sample bus on route `2449_S303` was at latitude −32.7, near Newcastle. Agency 2449 is Port Stephens Coaches, and 2448, 2450, 2457 and 7024 are Hunter Valley Buses. Silver keeps everything; the gold Sydney products filter by `agency_id`.
- **One agency can carry several ids.** 688 agency ids map to 462 names (Transit Systems NSW is 2503 and 7063), so gold groups operators by name, not id.
- **A bug was found and fixed.** With a space in the repo path (`3. Personal`), the warehouse went to a literal `3.%20Personal` folder, because the default was a percent-encoded `file://` URI. It is now a plain path, with a test (dc91b4d). The host path has no space.

## Limits

- All runs were on macOS. Peak memory on the Linux host may differ and is measured before the worker is turned on.
- Three hours of two bus feeds and one timetable. The other realtime feeds are far smaller (D-024) but have not been run on real data.
- Service Alerts history (D-030) is tested only on generated protobufs; no alert hour was replayed.
- Parquet sizes are before compaction. Smaller flushes mean more small files until compaction is added.
- The 0.75 GB a day figure scales one peak hour and one quiet hour to a day; the real figure comes from the first live day.

## Next steps

- **Before deploying:** add compaction, replay one Service Alerts hour, and measure memory on Linux (docs/9_phase1.md).
- **After the Phase 0 gate (24 to 25 Nov), with Mike's approval:** deploy the worker on the collector host, run `replay` and `verify` on a full day, and report latency from fetch to silver.
- **Needs Mike:** review and merge PR #14; approve the on-demand instance for the Redpanda and Spark benchmark (D-023).
