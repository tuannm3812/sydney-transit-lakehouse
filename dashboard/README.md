# Dashboard

The public, read-only site for the Phase 3 products (D-044, D-045): the timetable honesty tracker, the bunching radar, the map replay and the data-quality page. It is an [Evidence](https://evidence.dev) project (the open-source framework, pinned in `package.json`) that builds to static files.

## What it reads

Only the public extract, never the gold database or silver:

| Step | Command | Writes |
|---|---|---|
| Gold build | `python -m transit_lakehouse.gold build` | `data/gold/transit.duckdb` |
| Public extract | `python -m transit_lakehouse.gold publish` | `data/gold/public/*.parquet`, the tables listed in `transit_lakehouse/gold/publish.py` |
| Map replay | `python -m transit_lakehouse.gold replay --day <Sydney date> --out dashboard/static/replay/data` | one JSON per local hour and the day's bunches |

The extract is Parquet because Evidence bundles an older DuckDB than the Python package and cannot open the gold file (D-045).

## Build

```bash
cd dashboard
npm ci
npm run sources        # reads ../data/gold/public/*.parquet
npm run build          # copies the map libraries, then builds to build/
npm run dev            # local preview at http://localhost:3000
```

`npm run sources` runs DuckDB in WebAssembly, which downloads its Parquet extension from `extensions.duckdb.org`. The cloud sessions block that host, so the build there runs in CI; it works on a Mac or a GitHub runner.

## Synthetic data in CI

CI builds the site against the synthetic route in `tests/test_gold_products.py` to check every page compiles. That build is never published: only a build from real gold data is.

## Pages

| Page | Shows | From |
|---|---|---|
| `/` | trips checked, share that ran, never seen and vanished, by mode and service day | `service_days`, `route_honesty`, `bunching_events` |
| `/ghosts` | ghost trips by hour, route and operator, and every ghost trip | `ghost_trips`, `route_honesty` |
| `/bunching` | hot-spot map, bunched share by hour, routes that bunch most, every bunch | `bunching_hotspots`, `bunching_by_hour`, `bunching_events` |
| `/replay` | the map replay (`static/replay/index.html`, deck.gl on MapLibre) | replay JSON |
| `/quality` | the last build's checks and trip match by mode | `quality`, `trip_match` |
| `/about` | the data, its licence, the method and its limits | `service_days`, `quality` |

## Colours

Charts use Viridis, set as the default palette and colour scale in `evidence.config.yaml`. A series that is a transport mode uses TfNSW's mode colours instead (Bus `#00B5EF`, Trains `#F6891F`, Metro `#00959A`, Light rail `#EE343F`, Ferries `#5AB031`), the same values as the decks and the poster. Each source query in `sources/transit/` adds `mode_name`, the public name the colours are keyed on.

Wording follows docs/7_data_model.md: "never seen" is a ghost candidate, not proof a trip did not run.
