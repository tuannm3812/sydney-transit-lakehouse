# 11. Phase 2 plan: reliability marts

*2026-11-21. Decision: D-035. First report: R2-01 (docs/reports/marts-dryrun-20261121.md).*

Phase 2 turns the silver tables into small gold answer tables with dbt on DuckDB, and puts data-quality tests behind them that anyone can see. Work starts now on branches, on silver tables replayed from real hour tars, while the Phase 0 capture run finishes. Nothing here touches the collector host.

**Gate (from the plan):** the data-quality page is green for 7 straight days.

## How it runs

```bash
pip install -e ".[gold]"
python -m transit_lakehouse.gold build      # views over silver, then dbt build
python -m transit_lakehouse.gold dq         # last build's results
python -m transit_lakehouse.gold views      # views only, for ad hoc DuckDB
```

It reads the lakehouse under `DATA_DIR` as the worker writes it and writes `DATA_DIR/gold/transit.duckdb`, with dbt's target and logs beside it. `--vars` takes dbt variables as JSON (service-day window, grace, thresholds; see `dbt_project.yml`). `GOLD_MEMORY_LIMIT` and `GOLD_THREADS` set DuckDB's limits (300 MB, 1). On the host it runs as `docker compose --profile gold run --rm gold build`.

The dbt project lives in the package (`src/transit_lakehouse/gold/dbt/`), so the Docker image carries it.

## Gate check

The gate is the quality page green for 7 straight service days, counted from the worker go-live once the first full day of every mode is green (D-039). `gold gate` decides "green" per Sydney service day from the gold database alone, read-only (D-057):

```bash
python -m transit_lakehouse.gold build --day 2026-11-29   # nightly, one service day (D-060)
python -m transit_lakehouse.gold gate --since 2026-11-29 --json docs/reports/gold-gate.json
python -m transit_lakehouse.gold gate-report --gate-json docs/reports/gold-gate.json \
    --commit <sha> --out docs/reports/phase2-gate-<yyyymmdd>.md
```

It exits 0 only when the last green run reaches `--days` (7). On the host: `docker compose --profile gold run --rm gold gate ...`, after the nightly build and never during it.

| Check | Green when | Decision |
|---|---|---|
| `build` | a build covering the day, run after its data closed, has every model and structural test passing | D-035 |
| `bar:<model>.<column>` | each `share_at_least` bar in `models/quality/schema.yml` holds on the day's row, per mode | D-035 |
| `modes` | every mode with a trip-update feed has realtime trips | D-039 |
| `capture` | at least 95% of each mode's scheduled trips are judged on capture | D-048 |
| `operators:rule`, `operators:covered`, `operators:mart` | no uncovered operator's trip is `never_seen` or `partial`; each mode has a covered operator; the route mart agrees with the outcomes | D-038 |
| `operators:dropped` | warning only: an operator covered the day before is not covered this day | D-038 |
| `trip_left:rows`, `trip_left:share`, `trip_left:burst` | `REMOVED` rows exist with stop count 0; at least 90% of trips seen to run left the feed; no snapshot ended over half the trips | D-036, D-049 |

A day is `open` until trip updates were captured past its last trip's end plus the grace, so the newest day never counts early. The draft report fills R2-02's tables from the JSON; Findings are written by hand before it is set to Final, as for R0-02.

**Rehearsal, 27 Nov (cloud, raw hours 24 Nov 12:00 to 26 Nov 23:00 UTC replayed from object storage, gold built unwindowed).** 41 of 43 dbt nodes passed; the two failures were share tests on the partial edge days 24 and 27 Nov, which the gate judges per day and so leaves out of 25 and 26 Nov. Every day was red on one thing: **trains have no realtime trip state** (379,784 train `trip_status` rows, none with a `start_date`, so `stg_rt_trip_state` drops them and no train operator is covered). With trains left out (`--mode bus --mode metro --mode lightrail --mode ferry`), 26 Nov is green on all 53 checks (4 warnings: light rail has no arrival rows yet, O-04) and 25 Nov is red only on metro capture (394 of 433 trips, 91.0%): the metro trip-update files have gaps of 150 to 210 s between 14:04 and 14:36 UTC (about 1 am Sydney), over the 120 s coverage limit. Trip left the feed: 100% of trips seen to run left it on both days, and no snapshot ended more than 3.5% of the bus trips.

**Rehearsal with trains, 28 Nov (same replay, D-059).** With train runs dated from the timetable, 3,001 train trips a day are judged and every train bar passes on 25 and 26 Nov: trip match 96%, arrivals within 150 m of a position 97%. Train timetable agreement is a warning (14 to 87 timed rows a day, under the 100-row minimum), and so is the nightly empty train feed at about 01:00 Sydney (`trip_left:empty`). 26 Nov is green on all 67 checks; 25 Nov is still red only on metro capture (91.0%, above). A full build took 4.1 min at 8 GB in the cloud; at the host's 300 MB it ran out of memory, on main too (I-23), so gold now builds one day at a time (D-060).

## Models

| Layer | Model | One row per | Notes |
|---|---|---|---|
| staging | `stg_service_day` | service day | UTC origin of the GTFS clock; timetable version in effect |
| staging | `stg_scheduled_trip` | scheduled trip × service day | calendar and exceptions applied; mode, region, UTC start and end |
| staging | `stg_rt_trip_state` | realtime trip state | service day fixed for after-midnight trips (D-035) |
| staging | `stg_pattern_stop` | pattern stop | position from the end, to tell which stops a trip has passed |
| staging | `stg_capture_window` | stretch of capture per feed | outcomes are judged only inside one |
| gold | `fct_stop_arrival` | realtime trip × passed stop | scheduled and observed time, delay, method |
| gold | `fct_trip_outcome` | scheduled trip × service day | ran, partial, cancelled, never_seen or not_judged, with confidence; never_seen only for an operator with realtime that day (D-038) |
| gold | `fct_headway` | consecutive trips at a stop | observed and scheduled gap, bunched flag |
| gold | `mart_route_reliability` | route × service day × local hour | outcomes, on-time share, delay, bunched share |
| quality | `dq_trip_match`, `dq_stop_arrival`, `dq_arrival_position`, `dq_position_plausibility` | service day × mode | shares tested against the bars in D-035 |
| quality | `gold.dq_results` | dbt node × build | every model and test result, for the quality page |

## Work items

| # | Item | Status | Waits for |
|---|---|---|---|
| 1 | **dbt on DuckDB over silver** (`transit_lakehouse.gold`, D-035) | built, R2-01 | nothing |
| 2 | **Stop arrivals and headways** (`fct_stop_arrival`, `fct_headway`) | built; arrivals checked against positions (R2-01) | nothing |
| 3 | **Trip outcomes** (`fct_trip_outcome`) | built; judged on 283 trips only | a full day of every mode |
| 4 | **Route reliability mart** | built without excess wait time | item 9 |
| 5 | **Quality tests**: match rate, delay sanity, timetable agreement, arrival position, plausibility | built, all pass on two real hours | nothing |
| 6 | **`dq_results` table** | built | nothing |
| 7 | **Trip left the feed** in silver (`REMOVED` row in `trip_status`, D-036), for last-stop arrivals and when a trip vanished | built (Mike, 22 Nov); R2-01 section 3.7 | nothing |
| 8 | **Operator coverage**, so ghost rates count only operators that publish realtime | built (D-038): same operator and mode seen in trip updates that service day | nothing |
| 9 | **Excess wait time** from headways | planned | nothing |
| 10 | **Incremental builds by service day** (`gold build --day`, D-060) and a nightly run, scheduled away from the worker's hourly compaction | built; 25 to 27 Nov each built at 300 MB (peak 842 MB); the nightly schedule waits | the worker live on the host |
| 11 | **Volume test**: rows within 3 standard deviations of the same hour over the last 4 weeks | planned | four weeks of silver |
| 12 | **Public quality page** from `gold.dq_results`, capture rate and freshness | planned | the public dashboard host |
| 13 | **A full-day run** of every mode with the plan's 3-hour grace, memory and time measured | planned | the worker live, after the Phase 0 gate (about 28 Nov) |
| 14 | **Gate check** (`gold gate`, D-057) and the R2-02 draft (`gold gate-report`) | built; rehearsed on 25 and 26 Nov replayed from S3 (below) | the worker live, then 7 green days |

## Open risks

- **Other modes join the timetable differently.** Trains needed their own reading (D-059); metro, light rail and ferry trip ids are checked per mode by `dq_trip_match`.
- **The build is near the cap.** A one-day build peaks at about 840 MB of the 900 MB container cap (D-060). Bus days with more trips than late November, or `mart_bunching_hotspot`, which reads every day built, may need the next step down in memory.
- **Gold and the worker share 2 GB with the collector.** Each is capped at 900 MB; they should not run at the same time (R2-01).
- **Expiry can delete a file a build is reading.** The worker's hourly expiry removes files that compaction replaced. A build reads the file list at its start, so it should run well clear of the compaction hour, or under a shared lock (item 10).
- **One feed per mode is not the whole mode.** The light rail feed is the Inner West line only and the ferry feed is Sydney Ferries only, so L2, L3, L4 and private-ferry trips are `not_judged` (D-038) until their feeds are added.
- **"Never seen" is not "did not run".** Public wording stays "timetable honesty" (docs/7_data_model.md); most never-seen trips in R2-01 belong to operators without realtime at all.
