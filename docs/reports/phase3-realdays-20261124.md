---
id: R3-01
title: Ghost trips and bunching on two real days
subtitle: The Phase 3 products built from two Sydney service days of raw hours replayed from object storage in the cloud, with the first hand-check sheets.
phase: 3
status: Interim
verdict: Info
date: 2026-11-25
window: 21 Nov 2026 11:00 to 24 Nov 2026 00:00 UTC (raw hours); service days Sunday 22 and Monday 23 Nov 2026 (Sydney) reported
commit: 8ae76fa (gold, hand-check); 0db0ada (replay)
environment: Cloud Linux container (x86, 4 cores, 15 GB), Python 3.13, DuckDB 2.0.1, dbt-core 1.12.7, pyiceberg 0.12.0; gold run with GOLD_MEMORY_LIMIT 8GB and 4 threads. Nothing ran on the collector host.
decisions: D-026, D-038, D-042, D-045, D-046, D-047, D-048, D-052
evidence: hour tars from the offload bucket (data/_downloads in the cloud session, gitignored); docs/reports/handcheck-20261122.md and handcheck-20261123.md; gold database and public extract rebuilt by the commands in the appendix (gitignored)
---

# Ghost trips and bunching on two real days

## Summary

> The ghost tracker and the bunching radar work on real data. On Monday 23 Nov **166 of 37,336 judged trips (0.44%)** never appeared in the feed, and 161 of them were one bus operator's. The run also exposed three judging rules that turned finished metro, light rail and ferry trips into ghosts; they are fixed (D-047).

- **The pipeline kept every trip.** None of the 40 hand-check trips is a `pipeline_miss`: no raw snapshot in a trip's window carried a trip that gold called missing (section 3.7).
- **Ghosts are rare and concentrated.** On 23 Nov Busways North West had 161 of the 166 missing trips, on routes 292, 293, 513, 523 and 524; route 292 lost 66 of 98 judged trips (section 3.5).
- **Three rules were wrong on real feeds.** Before D-047 the first build flagged 2,819 trips as vanished over the two days; after it, 55 (section 3.3).
- **Bunching peaks in the afternoon (provisional, D-051 pending).** 2,844 bus bunches on Monday, 1,198 of them starting between 3 and 7 pm; route 309 bunched at 7 of 8 gaps along Botany Road at 4 pm (section 3.6).
- **Two days replayed in about 2 hours.** 102,864 snapshots became 126.7 million stop predictions and 14.2 million positions; the gold build took 3.4 minutes (sections 3.1 and 3.2).

## Scope and method

The raw hour tars (D-026) for 21 Nov 11:00 to 24 Nov 00:00 UTC, 9.1 GB, were downloaded read-only from the offload bucket, together with the five timetables published from 20 Nov 19:45 UTC on. That covers Sydney service days 22 and 23 Nov in full, plus the evening of 21 Nov and the morning of 24 Nov, which are built but not reported. The worker loaded the timetables with `run --once`, then `replay` rebuilt the realtime tables feed by feed into an empty lakehouse. The gold build ran without a service-day window (incident I-21), then `gold handcheck`, `gold publish` and `gold replay`. This edition, of 25 Nov, rebuilds gold on `main` with D-048, which judges a trip only when its feed was captured around its whole run, and regenerates the sheets with the hand-check fixes of D-052. The ghost counts did not change; the number of judged trips did (section 3.4).

```flow
store | Hour tars | 2.5 days, 11 realtime feeds, from object storage
raw | Raw snapshots | 102,864 files unpacked one hour at a time
silver | Silver tables | rebuilt by worker replay
gold | Gold products | ghost trips, route honesty, bunches, hot spots
check | Hand-check | 20 sampled ghost trips a day, raw bytes searched
```

The window holds TfNSW slowdown I-18 (23 Nov 02:35 to 02:52 UTC). Out of scope: the Phase 3 gate itself, which runs on backfilled history (D-040, D-046); human verdicts on the hand-check sheets; the dashboard build, which CI checks (D-044); and any run on the collector host.

## Results

### Replay

At 0db0ada. Rows are for the whole 2.5-day window.

| Table | Rows | Source feeds |
|---|---:|---|
| `silver.stop_prediction` | **126,672,884** | 5 trip-update feeds |
| `silver.vehicle_position` | 14,228,250 | 5 vehicle-position feeds |
| `silver.trip_status` | 8,496,485 | 5 trip-update feeds |
| `bronze.snapshot` | 102,864 | 11 realtime feeds |
| `silver.alert_scope` | 5,249 | `service_alerts` |
| `silver.alert` | 1,286 | `service_alerts` |

No hour tar was missing. The replay started at about 40 s per bus trip-update hour and slowed to 6 minutes as Iceberg snapshots piled up (I-20); expiring snapshots every 4 hours brought a peak hour back to about 1.7 minutes. The container also restarted once during the run, and the replay resumed from its last commit with no lost or repeated rows.

### Gold build and quality tests

At 8ae76fa.

| Service day | Mode | Realtime trips | *Matched to the timetable* | Arrivals checked |
|---|---|---:|---:|---:|
| 22 Nov | Bus | 23,858 | 100.0% | 894,130 |
| 22 Nov | Ferries | 526 | 98.9% | 479 |
| 22 Nov | Light rail | 284 | 100.0% | – |
| 22 Nov | Metro | 274 | 100.0% | 807 |
| 23 Nov | Bus | 44,150 | 99.5% | 1,700,505 |
| 23 Nov | Ferries | 706 | 100.0% | 553 |
| 23 Nov | Light rail | 282 | 100.0% | – |
| 23 Nov | Metro | 433 | 100.0% | 984 |

42 of 43 models and tests pass. The one failure is ferry trip match on the partial edge day of 21 Nov (20 of 39), whose timetable was published before that window starts. The full build took 3.4 minutes at 8ae76fa (89 s at 9928028, before D-048's delay-shifted stops to go); with DuckDB allowed 8 GB, the container peaked at about 9.2 GB in use at 9928028, so this is not a measure of the 2 GB host.

### Three rules fixed on real feeds

At 9928028 (D-047). Vanished trips, 22 and 23 Nov together.

| Rule | What the feed does | First build | After the fix |
|---|---|---:|---:|
| Timetable for a day | TfNSW published the 22 Nov ferry trips only during 22 Nov | 2% of 22 Nov ferry trips matched | 98.9% |
| Stops to go | Metro, light rail and ferry updates keep passed stops listed | 2,819 vanished | 282 |
| Leaving at the scheduled end | Most trips left the feed within 10 minutes of their scheduled end | 282 vanished | **55** |
| Another start date (hand-check) | Bus trip ids repeat every day | 2 of 40 read as `start_date_mismatch` | 0 |

Before the second fix, 98% of metro trips and 85% of light rail trips "vanished". Of the 279 bus trips still flagged after it, 225 left the feed within 10 minutes of their scheduled end or later, and no vehicle position of any of them came after it left.

### Ghost trips

At 8ae76fa. Judged trips only, for operators with realtime that day (D-038), and only when the feed was captured around the trip's whole run (D-048).

| Service day | Mode | Scheduled | Judged | Ran | Cancelled | Missing | Vanished | *Ghost share* |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 Nov | Bus | 24,544 | 23,792 | 23,648 | 108 | 3 | 33 | 0.15% |
| Sun 22 Nov | Ferries | 778 | 594 | 593 | 1 | 0 | 0 | 0.00% |
| Sun 22 Nov | Light rail | 861 | 284 | 241 | 43 | 0 | 0 | 0.00% |
| Sun 22 Nov | Metro | 274 | 274 | 274 | 0 | 0 | 0 | 0.00% |
| Mon 23 Nov | Bus | 53,109 | 36,135 | 35,449 | 502 | 163 | 21 | **0.51%** |
| Mon 23 Nov | Ferries | 1,122 | 649 | 644 | 2 | 3 | 0 | 0.46% |
| Mon 23 Nov | Light rail | 1,397 | 237 | 221 | 15 | 0 | 1 | 0.42% |
| Mon 23 Nov | Metro | 433 | 315 | 313 | 2 | 0 | 0 | 0.00% |

Trains are scheduled (5,144 trips) but not judged (O-04). Light rail and ferries are judged only for the operators in their feeds: Inner West light rail and Sydney Ferries. Cancelled trips are ones TfNSW itself marked cancelled; they are not ghosts. Under D-048, slowdown I-18 (13:35 to 13:52 Sydney) and a 2-minute metro gap left 8,229 trips of covered operators unjudged on 23 Nov, against 44 before: a trip seen to run inside a capture gap is no longer counted as ran while a missing one in the same gap is not judged. So Monday's ghost share rests on the trips outside that stretch of the day, and Sunday is unchanged.

```chart
title: Ghost trips by mode and day at 8ae76fa
unit: trips
Bus, Sun 22 Nov: 36
Bus, Mon 23 Nov: 184
Ferries, Mon 23 Nov: 3
Light rail, Mon 23 Nov: 1
```

### Where the ghosts are

At 8ae76fa. Missing trips on Monday 23 Nov, by route.

| Route | Operator | Judged | Missing | *Missing share* |
|---|---|---:|---:|---:|
| 292 | Busways North West | 98 | **66** | 67% |
| 293 | Busways North West | 25 | 23 | 92% |
| 524 | Busways North West | 46 | 23 | 50% |
| 523 | Busways North West | 43 | 23 | 53% |
| 513 | Busways North West | 30 | 15 | 50% |
| 551 | Busways North West | 6 | 5 | 83% |
| F3 | Sydney Ferries | 104 | 3 | 3% |
| Total, all routes | all operators | 37,336 | 166 | 0.44% |

Busways North West had 2,831 judged trips that day, and 161 of them never appeared. On route 292 all 66 belong to one service in the timetable (`TA+6E#1+6`), and 121 other trips of that service ran that day. 148 of the 161 were still in the next timetable publish, so TfNSW did not withdraw them. On Sunday only 3 trips were missing, network-wide.

### Bunching radar

At 8ae76fa; unchanged from 9928028. A bunch is two consecutive buses of a route and direction bunched at 3 stops or more (D-045). **Provisional:** D-051 (pending) also counts pairs that overtake and changes the by-hour count, so these numbers will be redone when it is on `main`.

| Service day | Bus gaps measured | Gaps bunched | *Bunches* | Top route (bunches) |
|---|---:|---:|---:|---|
| Sun 22 Nov | 848,491 | 15,784 (1.86%) | 1,293 | 333 (239) |
| Mon 23 Nov | 1,482,434 | 33,511 (2.26%) | **2,844** | 333 (157) |

A bunch lasts a median 9 stops, about half the stops the two buses share. On Monday the next routes were 309 (134), 390X (101) and 392 (94). The strongest hot spots are route 309 along Botany Road and Bunnerong Road around 4 pm, where 6 or 7 of every 8 or 9 gaps were bunched at 12 stops. Ferries (940 gaps) and metro (1,669 gaps) had no bunched gap.

```chart
title: Bus bunches by starting hour, Monday 23 Nov, at 8ae76fa
unit: bunches
6 am: 109
7 am: 301
8 am: 294
9 am: 169
10 am: 105
11 am: 78
12 pm: 108
1 pm: 104
2 pm: 148
3 pm: 287
4 pm: 338
5 pm: 331
6 pm: 242
7 pm: 103
```

### Hand-check sheets

At 8ae76fa (D-052). 20 ghost trips a day, drawn by seeded hash (seed `phase3`), each searched for in every raw trip-update and vehicle-position file of its mode from 30 minutes before its start to 30 minutes after its end. A hit counts only when an entity names exactly that trip id, and a raw file that is not on disk is counted as missing.

| Service day | Ghost trips | Sampled | Raw files searched | Raw files missing | `pipeline_miss` | `consistent_ghost` | `consistent_vanish` |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sun 22 Nov | 36 | 20 | 17,532 | 0 | **0** | 3 | 17 |
| Mon 23 Nov | 187 | 20 | 12,945 | 0 | **0** | 15 | 5 |

The sheets are `handcheck-20261122.md` and `handcheck-20261123.md` in this folder, with the verdict column empty for Mike. A first read of the Monday sheet: 10 of its 15 missing trips are Busways North West's and 3 are Sydney Ferries F3 trips on a route with 126 other trips seen. 6 of the 15 are low-confidence trips on school or special routes (for example 610w, 619w, S089) that had no trip at all in the feed that day, which points to an untracked service rather than a ghost. Under the gate's bar (D-046) those 6 would need a verdict each. The 5 vanished trips left with 3 to 28 stops to go and did not come back. The same 40 trips were drawn as at 9928028 and every reading is the same; D-048's delay shift moved two trips' stops done by one.

### Dashboard extract and map replay

At 8ae76fa.

| Output | Size | Contents |
|---|---:|---|
| Public extract (`gold publish`) | 10 MB | 8 tables; 326,901 hot-spot rows, 7,580 route-day rows, 238 ghost trips |
| Map replay (`gold replay`) | 175 MB | 48 Sydney hours, positions every 30 s, 4,149 bunches drawn |

## Findings

- **The products answer the brief's questions on real data.** Monday's ghost share is 0.51% for buses, and it points at one operator and five routes; Sunday's is 0.15% (section 3.4, section 3.5).
- **A feed's habits decide the rules.** Each of the three D-047 fixes came from a TfNSW feed behaviour that synthetic tests had not modelled; `tests/test_gold_real_quirks.py` now builds two of them (section 3.3, D-047).
- **"Vanished" is now rare and believable.** 55 trips over two days left the feed more than 10 minutes before their scheduled end with stops to go; none was seen again in the raw files (section 3.3, section 3.7).
- **The hand-check finds no lost trips, but not every flag is a ghost.** No `pipeline_miss` in 40 trips, so the D-046 hard rule holds; the 18 of 20 bar will turn on how low-confidence trips on routes absent from the feed are judged (section 3.7, D-046).
- **Bunching is a peak-hour bus problem (provisional).** 42% of Monday's bunches started between 3 and 7 pm, and none happened on ferries or metro (section 3.6).

## Limits

- **Two days are not a pattern.** One Sunday and one Monday cannot rank routes; the Busways result may be a one-day tracking fault. Hot spots rest on 8 or 9 gaps.
- **Trains and light rail are partly out.** No train trip is judged and light rail has no headways (O-04).
- **A missing trip is a candidate, not proof.** Nothing here shows whether a bus ran untracked; that is what the hand-check verdicts are for.
- **A capture gap now removes hours of judged trips.** Under D-048 the 17-minute I-18 slowdown took 8,229 trips out of Monday's judgement, because each trip needs capture to 180 minutes after its end (section 3.4).
- **The build ran with more memory than the host has.** Peak memory on the 2 GB host is not measured here.
- **A windowed build is wrong at its first day** (I-21), so this report used an unwindowed one.

## Next steps

- **Mike:** fill the verdict column of both sheets, or say how low-confidence trips on routes absent from the feed should count toward the gate (D-046).
- **Mike:** the worker's container limit is 900 MB in `docker-compose.yml` but 700 MB in D-040; which should it be?
- Redo the bunching numbers when D-051 is on `main`.
- Make `replay` expire and compact as it goes (I-20) before the overnight S3 backfill (D-040), and fix the window edge (I-21).
- Judge trains from their trip ids (O-04) and take light rail arrivals from passing predictions.
- Watch Busways North West over more days to see whether its missing trips repeat.
- The Phase 3 gate: 20 trips on backfilled history once the worker is live (D-046).

## Appendix

Commands, in the cloud session (S3 read-only keys from the environment, never printed):

```bash
pip install -e ".[dev,offload,worker,gold,report]"
# hour tars for 21 Nov 11:00 to 24 Nov 00:00 UTC, plus 5 timetables,
# copied with boto3 into data/_downloads laid out like the bucket
python -m transit_lakehouse.worker --data-dir data run --once --feed static_gtfs   # x5
python -m transit_lakehouse.worker --data-dir data replay --tars data/_downloads \
    --feed <feed> --start 2026-11-21T12 --end 2026-11-24T00
python -m transit_lakehouse.worker --data-dir data expire --older-than 0h
python -m transit_lakehouse.worker --data-dir data compact
GOLD_MEMORY_LIMIT=8GB GOLD_THREADS=4 python -m transit_lakehouse.gold build
python -m transit_lakehouse.gold handcheck --service-date 2026-11-23
python -m transit_lakehouse.gold handcheck --service-date 2026-11-22
python -m transit_lakehouse.gold publish
python -m transit_lakehouse.gold replay --day 2026-11-22 2026-11-23 --out data/gold/replay
```

Timetable versions loaded: fetched 20 Nov 19:45, 21 Nov 23:38, 22 Nov 23:38, 23 Nov 11:38 and 23 Nov 23:38 UTC (195,992 to 207,948 trips each). Service day 22 Nov took the 21 Nov 23:38 version under D-047; under the old rule it took the 20 Nov one.
