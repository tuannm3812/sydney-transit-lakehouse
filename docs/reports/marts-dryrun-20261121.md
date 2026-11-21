---
id: R2-01
title: Gold marts dry run on real data
subtitle: The first Phase 2 dbt models and quality tests built on DuckDB from the silver tables of two real bus hours and one timetable, replayed off the server.
phase: 2
status: Interim
verdict: Pass with actions
date: 2026-11-21
window: 17 Nov 2026, 16:00 to 17:00 and 21:00 to 22:00 UTC (3 am and 8 am Sydney) for bus trip updates; 21:00 to 22:00 UTC for bus vehicle positions; timetable published 07:45 UTC
commit: 5801cc0 (gold models); silver tables built by the Phase 1 worker at the same commit
environment: Cloud Linux container (x86, 4 cores), Python 3.13, dbt-core 1.12, dbt-duckdb 1.11, DuckDB 2.0, pyiceberg 0.12. Nothing ran on the collector host.
decisions: D-027, D-028, D-029, D-035
evidence: the four hour tars of R1-01 (on Mike's Mac in data/_downloads); gold database rebuilt by the commands in the appendix (gitignored)
---

# Gold marts dry run on real data

## Summary

> The gold layer works on real data: all **33 of 33** dbt models and tests pass, observed stop arrivals agree with the separate vehicle-position feed, and the build fits the worker's memory cap. One TfNSW date quirk was found and fixed before it reached a mart.

- **The timetable join holds.** 99.7% of 9,639 realtime bus trips matched a trip the timetable schedules that service day, above the plan's 95% bar (section 3.2).
- **Stop arrivals are where the buses were.** At 132,153 observed arrivals the nearest vehicle position was a median 48 m from the stop and 95.9% were within 150 m; at the timetabled time it was 568 m and 17.8% (section 3.3).
- **After-midnight trips are dated by calendar day.** TfNSW gives a trip timetabled after 24:00 the next day's `start_date`; moving 129 such trips back to their service day lifted timetable agreement from 98.4% to 99.95% (section 3.4).
- **Never seen is mostly operators without realtime.** In the 8 am window 1 of 184 judged Sydney trips was never seen, against 45 of 99 regional ones, 44 of them from operators with no trip in the feed at all (section 3.5).
- **The build fits the cap.** A full build of two hours took 24.2 s and peaked at 714 MB with DuckDB held to 300 MB (section 3.6).

## Scope and method

`python -m transit_lakehouse.gold build` (D-035) points DuckDB views at the live Parquet files of each bronze and silver Iceberg table, runs `dbt build`, and appends every model and test result to `gold.dq_results`:

```flow
raw | Hour tars | two bus hours and one timetable, from object storage
silver | Silver tables | rebuilt by the Phase 1 worker
gold | Staging views | service days, scheduled trips, trip states
gold | Gold marts | stop arrivals, trip outcomes, headways, route reliability
check | Quality tables | five tables with threshold tests, results in dq_results
```

The silver tables were rebuilt from the same four hour tars as R1-01 with `worker run --once` (480 snapshots and one timetable in 189.6 s). The gold build then ran from an empty gold database.

| Feed | UTC hour (Sydney) | Files |
|---|---|---:|
| `buses_tripupdates` | 21:00 (8 am peak) | 120 |
| `buses_vehiclepos` | 21:00 (8 am peak) | 240 |
| `buses_tripupdates` | 16:00 (3 am) | 120 |
| `static_gtfs` | 07:00 (6 pm) | 1 |

The plan closes a trip 3 hours after its scheduled end (`outcome_grace_minutes: 180`). With one-hour windows that judges no trip at all, so the outcome numbers in section 3.5 come from a build with `--vars '{"outcome_grace_minutes": 20}'`. Every other number is the same in both builds.

Out of scope: trains, metro, light rail and ferries (no hours downloaded yet), Service Alerts in gold, the volume test (it needs four weeks of history), the public quality page, and any run on the collector host.

## Results

### Tables built

At 5801cc0.

| Table | Rows | One row per |
|---|---:|---|
| `gold.fct_stop_arrival` | 142,314 | realtime trip and stop it was seen to pass (6,868 trips) |
| `gold.fct_trip_outcome` | 105,706 | scheduled trip and service day (17 and 18 Nov) |
| `gold.fct_headway` | 60,048 | pair of consecutive trips at a stop |
| `gold.mart_route_reliability` | 47,916 | route, service day and local hour |
| All dbt nodes | 33 | 1 seed, 13 models, 19 tests: **all pass** |

### Timetable join

At 5801cc0, from `gold.dq_trip_match`. A trip matches when its `trip_id` is scheduled by the timetable in effect on its service day.

| Service day | Realtime trips | Matched | *Match share* | Result |
|---|---:|---:|---:|---|
| 18 Nov | 9,639 | 9,611 | **99.71%** | Pass |
| 17 Nov (after midnight) | 129 | 129 | 100.00% | Pass |

Realtime bus route ids (`2449_130`) do not match timetable route ids (`74-490-sj2-1`), so gold takes every trip's route, operator and network from the timetable through `trip_id`, never from the realtime route.

### Stop arrivals against vehicle positions

At 5801cc0, from `gold.dq_stop_arrival` and `gold.dq_arrival_position`. A stop's observed arrival is the last prediction made while it was still listed, rebuilt from the D-028 encoding. TfNSW's bus positions carry no stop or status (0 of 701,312 rows), so they are used as an independent check rather than as the source: at each arrival, take the trip's positions at most 30 s before and after, and measure the nearer one's distance to the stop.

| Check, 18 Nov service day | Value | Bar | Result |
|---|---:|---:|---|
| Arrivals checked against positions | 132,153 | | |
| Median distance to the stop | **48 m** | | |
| 95th percentile distance | 140 m | | |
| Within 150 m of the stop | 95.9% | 90% | Pass |
| Same check at the timetabled time, median | 568 m | | |
| Same check at the timetabled time, within 150 m | 17.8% | | |
| Delay between −30 and +180 minutes | 99.9% | 99% | Pass |
| Realtime scheduled time equals the timetable's (±60 s) | 99.95% | 95% | Pass |
| Last prediction no later than the stop left the feed (+2 min) | 99.1% | 95% | Pass |

The two timetabled-time rows come from an ad hoc query with the same method (appendix), not a dbt table. The last prediction sits a median 34 s before the poll that dropped the stop, about one 30 s trip-update interval. Where it is later than that poll (5.2% of rows, mostly by seconds), the observed time is capped at the poll and `method` is `capped_at_exit`. 4,375 rows (3.1%) are stops dropped three or more at a time, which `dropped_with` records.

### After-midnight trips

Found by the agreement check while building, before 5801cc0. Before the fix, 2,864 Sydney Buses Network arrivals sat about a day after their stop left the feed.

| Read realtime `start_date` D as | Trips | Median first seen minus scheduled start |
|---|---:|---:|
| Service day D, timetabled after 24:00 | 129 | −86,699 s (a day early) |
| Service day D − 1, timetabled after 24:00 | 129 | **−299 s** |
| Service day D, timetabled before 24:00 | 9,611 | −1,198 s |

| Check | Before the fix | After |
|---|---:|---:|
| Scheduled time agrees with the timetable | 98.44% | 99.95% |
| Last prediction no later than the stop left the feed | 97.62% | 99.11% |

### Trip outcomes in the 8 am window

At 5801cc0 with `outcome_grace_minutes: 20`, so a trip is judged when trip updates cover it from 10 minutes before its start to 20 minutes after its end: trips scheduled from 21:10 to 21:39 UTC.

| Region | Judged | Ran | Cancelled | Partial | Never seen | Never seen, low confidence |
|---|---:|---:|---:|---:|---:|---:|
| Sydney | 184 | 178 | 2 | 3 | **1** | 1 |
| Regional | 99 | 54 | 0 | 0 | 45 | 45 |
| All judged | 283 | 232 | 2 | 3 | 46 | 46 |

All 46 never-seen trips are low confidence: no other trip of their route appeared that day. 44 of the 45 regional ones belong to 23 operators with no trip in the feed in either hour. The one Sydney trip is a train replacement bus.

### Build time and memory

Each row is a full build from an empty gold database. The default row is at 5801cc0; the other three ran on the same data an hour earlier, before the after-midnight fix (section 3.4). Peak memory is the build process's maximum resident set size.

| DuckDB memory limit | Threads | Wall time | *Peak memory* | Under the 900 MB cap |
|---|---:|---:|---:|---|
| 600 MB | 4 | 16.3 s | 982 MB | No |
| 600 MB | 1 | 22.3 s | 976 MB | No |
| 400 MB | 2 | 18.8 s | 796 MB | Yes |
| 300 MB, the default | 1 | 24.2 s | **714 MB** | Yes |

```chart
title: Peak memory of a gold build by DuckDB limit
unit: MB
limit: 900 | container cap
600 MB, 4 threads: 982
600 MB, 1 thread: 976
400 MB, 2 threads: 796
300 MB, 1 thread: 714
```

About 400 MB of the peak is dbt and Python, above whatever DuckDB is allowed. The gold database is 21 MB.

## Findings

- **The trip_id join, the top risk on the deck, holds for buses.** 99.7% of realtime trips match their service day's timetable (section 3.2), and the realtime feed's own scheduled times agree with the timetable's for 99.95% of passed stops (section 3.3).
- **Last-prediction arrivals are good enough to build on.** The bus is within 150 m of the stop at 95.9% of them, against 17.8% at the timetabled time (section 3.3), so on-time and headway figures can rest on trip updates alone, as the plan assumed.
- **Service day must come from the timetable, not the feed.** TfNSW dates after-midnight trips by calendar day (section 3.4). `stg_rt_trip_state` moves them back; any later model that joins realtime to the timetable has to go through it (D-035).
- **A ghost rate needs an operator filter.** Most never-seen trips in the window belong to operators that publish no realtime at all (section 3.5); counted as ghosts they would make regional NSW look 45% unreliable. The Phase 3 tracker should report only operators with realtime coverage.
- **Gold fits beside the collector only with DuckDB held down.** 300 MB and one thread keep the build at 714 MB (section 3.6), but the worker uses up to 900 MB too, so the two should not run at the same time on the 2 GB host.

## Limits

- **Two hours of one mode.** Every number is from bus feeds on one weekday morning and one night. Trains, metro, light rail and ferries are untested; their trip ids may not match the complete timetable as well.
- **Outcome rates are not representative.** Only 283 trips could be judged, all short ones starting 21:10 to 21:39 UTC, and with a 20-minute grace instead of the plan's 3 hours (section 3.5).
- **A trip's last stops have no arrival.** A finished trip leaves the feed, and silver does not record when a trip disappears, so the final stop is never seen to drop out. Terminus on-time needs that event.
- **Arrival times are a bound, not a measurement.** They are TfNSW's last prediction before the stop dropped, capped at the drop; the position check shows they are close, not exact.
- **Memory at day scale is estimated.** A day holds about twelve times these rows; DuckDB spills to disk above its limit, so memory should hold and time should grow, but this was not run.
- **Measured off the server.** The container was x86 Linux, not the t3.small, and not in Docker.

## Next steps

- **Mike: review the PR** with the gold package, the models and this report.
- **Silver: record when a trip leaves the feed** (a `removed` row in `silver.trip_status`, as `silver.alert` does, D-030). It is cheapest now, before the worker runs live, and gives terminus arrivals and a cleaner "vanished mid-route" outcome.
- **Run a full day once the worker is live** after the Phase 0 gate (about 28 Nov): every mode, the plan's 3-hour grace, and the memory and time of a day-sized build.
- **Incremental builds by service day**, then a nightly schedule after the worker's compaction hour, not beside it.
- **Operator coverage in gold**, so ghost rates count only operators that publish realtime.
- **Still to build for Phase 2:** the volume test (after four weeks of data), excess wait time in the route mart, and the public quality page from `gold.dq_results`.

## Appendix

```
# silver, from the hour tars laid out as data/raw/feed=…/date=…/hour=…
python -m transit_lakehouse.worker run --once

# gold, as measured in section 3.6 (defaults: 300 MB, 1 thread)
python -m transit_lakehouse.gold build
python -m transit_lakehouse.gold build --vars '{"outcome_grace_minutes": 20}'
python -m transit_lakehouse.gold dq
```

The timetabled-time baseline in section 3.3 is `gold.dq_arrival_position` with `observed_arrival_utc` replaced by `sched_arrival_utc`, run by hand on the gold database.
