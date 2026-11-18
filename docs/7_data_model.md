# 7. Data model and storage plan

*Draft, 2026-11-18. Numbers marked "measure" are open until `checks profile` runs on the server.*

## The question the data answers

TfNSW publishes two things about every trip. The **static timetable** says a trip will run and when it is due at each stop: that is the promise. The **realtime feeds** say where the vehicle is and when it is now expected: that is what happened. Every product here is a comparison of the two.

| Product | Question | Built from |
|---|---|---|
| Timetable honesty (ghost buses) | Did each scheduled trip show up in the live feed, by route and hour? | static trips × trip updates × vehicle positions |
| Bunching radar | Are buses on a route arriving in clumps instead of evenly spaced? | vehicle positions along route shapes, timetabled headways |
| Prediction accuracy | How good is "due in 5 minutes"? | every trip-update prediction × the arrival that followed |
| Ask the network | Plain-English questions over the gold tables | gold tables, with the dbt docs as the SQL agent's catalogue |

## Sources (what the collector lands)

| Feed | Cadence | Unit inside | Size per snapshot (17 Nov) |
|---|---|---|---|
| Vehicle positions (bus, trains, metro) | 15 s | one entity per vehicle: trip, position, timestamp, occupancy | bus p50 162 kB |
| Trip updates (bus, trains, metro) | 30 s | one entity per trip, holding one stop-time update per remaining stop | bus p50 2.7 MB, p95 8.6 MB |
| Service alerts | 60 s | one entity per alert, with the routes, stops and trips it affects | 466 kB |
| Static GTFS | 6 h, conditional (D-021) | zip of routes, trips, stops, stop_times, calendar, shapes | 287 MB |

**The join key** between static and realtime is `(trip_id, start_date)`. `trip_id` alone repeats every service day. Vehicles join through the trip descriptor in each position.

**TfNSW-specific quirks:** a string in `TripDescriptor` field 7 breaks the standard bindings (D-020), so silver needs a tolerant proto. Trains use v2 paths.

## Layers

### Bronze: raw, immutable

This is what the collector writes today: one gzipped protobuf per changed snapshot under `raw/feed=/date=/hour=`, plus the poll manifest. It is the replay source for every later fix, and nothing is parsed or filtered at ingest.

### Silver: one row per fact, changes only

| Table | Grain | Key | Notes |
|---|---|---|---|
| `vehicle_position` | vehicle × report | (vehicle_id, vehicle timestamp) | Kept in full; bunching needs every point. Drop rows whose vehicle timestamp did not advance. |
| `stop_prediction` | trip × stop × change | (trip_id, start_date, stop_id, observed_at) | Write a row only when the predicted arrival or departure changes. This is the big saving (measure). |
| `trip_status` | trip × change | (trip_id, start_date, observed_at) | Scheduled, added, cancelled, as reported. |
| `alert` / `alert_scope` | alert × version | (alert_id, version) | Scope rows link to route, stop or trip. |
| `timetable_*` | per published version | version = static zip sha256 | Every daily version is kept, so each service day joins to the timetable that was live then. |

Iceberg tables are partitioned by service date and mode, sorted by trip and stop, with zstd Parquet.

### Gold: small answer tables

| Table | Grain | Holds |
|---|---|---|
| `trip_outcome` | scheduled trip × day | **ran** (seen live), **cancelled** (feed said so), **never seen** (scheduled, no live record), plus a confidence level and first and last seen times |
| `route_honesty` | route × hour × day | share of trips in each outcome |
| `headway` | consecutive buses × stop | observed gap against the timetabled gap; bunched when observed < ⅓ of scheduled |
| `prediction_error` | trip × stop × lead time | predicted vs actual, at 0–2, 2–5, 5–10 and 10–20 minutes out |

**"Never seen" is not "did not run".** A vehicle's tracker can fail. The confidence level uses the evidence available: no trip update *and* no position for the whole trip, other trips on the route reporting normally, and the run's length. Public wording is "timetable honesty", never "the bus did not run".

## Storage plan

| Stage | Rule | Why |
|---|---|---|
| Collector disk | Raw for at most 7 days, then removed after a verified upload | 80 GB fills in about 17 days at 4.4 GB/day |
| Object storage, raw | Every raw file, hourly bundles, moving to a cheaper class after 30 days | Replay and audit; cheap at rest |
| Iceberg silver and gold | Kept | Small after change-only encoding (measure) |
| Raw retention beyond 90 days | Decide once silver is trusted (decisions-log entry) | Raw is the only way to fix a silver bug later |

## Open measurements

- **Change share per feed.** This is the share of trip-update rows and positions that change between consecutive snapshots. Measure it with `python -m transit_lakehouse.checks profile --feed buses_tripupdates --feed buses_vehiclepos --hour <UTC hour>` on a weekday peak hour and an overnight hour. It sets the silver size and whether 30 s is the right trip-update cadence.
- **Timetable republish rate.** This is how often the static zip really changes. The answer comes from D-021 polls returning 304 or a new file.
