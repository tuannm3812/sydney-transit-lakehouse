# 12. Phase 3 plan: the headline products

*2026-11-24. Decisions: D-042 (start now, on replayed S3 hours), D-044 (Evidence), D-045 (product design), D-046 (gate pass mark).*

Phase 3 turns the gold marts into the two headline products of the brief (D-003), a map replay and a public dashboard. It is built in the cloud on raw hours replayed from S3 while the Phase 0 gate runs (D-042), on the same code path the live worker uses. Nothing here touches the collector host.

**Gate (from the plan, bar by D-046):** 20 flagged ghost trips hand-checked against the raw positions, on backfilled history. It passes when at least 18 of the 20 flags hold up and none is a `pipeline_miss`.

## How it runs

```bash
pip install -e ".[gold]"
python -m transit_lakehouse.gold build                      # adds the Phase 3 models
python -m transit_lakehouse.gold handcheck --service-date 2026-11-24
python -m transit_lakehouse.gold publish                    # dashboard extract
python -m transit_lakehouse.gold replay --day 2026-11-24 \
    --out dashboard/static/replay/data
cd dashboard && npm ci && npm run sources && npm run build  # dashboard/README.md
```

## Products

| Product | Built from | One row per | Notes |
|---|---|---|---|
| Ghost trip tracker | `fct_ghost_trip` | judged trip that was never seen (`missing`) or left the feed early (`vanished`) | operators with realtime that day only (D-038); confidence from `fct_trip_outcome` |
| Route honesty | `mart_route_honesty` | route x service day | ran, missing and vanished shares of judged trips |
| Bunching radar: bunches | `fct_bunching_event` | pair of consecutive vehicles bunched at 3 or more stops | `bunch_min_stops` (D-045); overtakes count, pair in timetable order (D-051) |
| Bunching radar: hot spots | `mart_bunching_hotspot` | route x direction x stop x day type x local hour | stop name and position from the day's timetable; the map shows stop-hours with 3 or more gaps, the by-hour chart every gap (`bunching_by_hour`, D-051) |
| Hand-check sheet | `gold handcheck` | sampled ghost trip | raw snapshots searched for the trip id; evidence and an automatic reading; a person writes the verdict |
| Map replay | `gold replay`, `dashboard/static/replay/` | Sydney hour | positions every 30 s per vehicle and trip; bunched pairs drawn larger |
| Dashboard | `gold publish`, `dashboard/` | page | Evidence, static, read-only; reads only the public Parquet extract |

## The hand-check

`gold handcheck` draws a seeded sample of `fct_ghost_trip` for one service day, spread over kind (missing first), confidence and mode, so nobody picks the trips. For each one it collects:

1. **Raw**: every landed trip-update and vehicle-position file of the trip's mode from 30 minutes before its start to 30 minutes after its end, decompressed and searched for the trip id as bytes. This does not use the worker's decoder, so it catches a trip the pipeline lost.
2. **Silver, any date**: rows with the trip id under any `start_date` inside the trip's window, which catches a start-date mismatch. Bus trip ids repeat every day a trip runs, so rows of another day's run do not count (R3-01).
3. **Untracked vehicles** (missing trips): vehicles with no trip within 300 m of the first stop within 10 minutes of the start.
4. **Alerts** naming the trip or its route, and the number of other trips of the route seen that day.

The automatic reading is one of `pipeline_miss`, `raw_incomplete`, `start_date_mismatch`, `alert_explains`, `untracked_vehicle`, `consistent_ghost`, `consistent_vanish` or `no_raw`. `raw_incomplete` means some raw files of the window are not on disk (pruned after 3 days, D-026): extract the hour tars and rerun before a verdict. A raw hit must be the trip's own id in a trip update or vehicle, and only alerts still in the feed during the trip count (D-052). The person checking confirms or overrules each row in the sheet's verdict column, and the Phase 3 report quotes the agreement.

## Work items

| # | Item | Status | Waits for |
|---|---|---|---|
| 1 | Ghost trip tracker and route honesty marts | built, tested on a synthetic route | real hours |
| 2 | Bunches and hot spots | built, tested on a synthetic route | real hours |
| 3 | Hand-check sheet with raw search | built | real hours; the gate run on backfilled history |
| 4 | Map replay export and viewer | built; viewer checked in a headless browser | real hours |
| 5 | Evidence dashboard: overview, ghosts, bunching, replay, quality | pages written; CI builds them | real hours |
| 6 | Run on about 2 days of S3 hours (R3-01, interim) | done 2026-11-24: 22 and 23 Nov; three rules fixed (D-047) | Mike's verdicts on the two hand-check sheets |
| 7 | Live radar: bunches forming now, from the latest trip updates | planned | the worker live on the host |
| 8 | Hosting the dashboard (static host, free tier) and the portfolio link | planned | Mike's choice of host |
| 9 | Hand-check of 20 trips on backfilled history (the gate) | planned | the backfill after the worker deploys (D-040) |

## Open risks

- **Two days are not a pattern.** Ghost shares and hot spots need weeks to rank routes fairly; the dashboard shows how many trips and gaps each figure rests on.
- **Realtime route ids differ from the timetable's for buses** (Phase 2 finding), so every product joins on `trip_id` and `start_date` and takes route names from the timetable.
- **Basemap tiles** come from CARTO's free basemaps, which ask for attribution and allow modest traffic. A busy public site may need its own tiles.
- **The replay files are large.** A full day of every mode at 30 s is tens of MB; the viewer loads one hour at a time, and the step can be raised.
