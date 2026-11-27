"""The public extract the dashboard reads (Phase 3, D-045).

``python -m transit_lakehouse.gold publish`` writes one Parquet file
per dashboard table to ``DATA_DIR/gold/public/``. The dashboard
(``dashboard/``, Evidence) reads only these files, so:

- only what is listed here is ever public: no trip-level silver, no
  manifest, no capture internals beyond the quality results;
- the dashboard does not depend on the gold database's DuckDB storage
  version, which Evidence's bundled DuckDB may not read.

Each query is the whole definition of a public table; the dashboard
pages only filter and aggregate them.
"""

# The queries are fixed text in this module:
# ruff: noqa: S608

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

TABLES: dict[str, str] = {
    # One row per service day with judged trips, newest first.
    "service_days": """
        select
            service_date,
            day_type,
            sum(trips_scheduled) as trips_scheduled,
            sum(trips_judged) as trips_judged,
            sum(trips_ran) as trips_ran,
            sum(trips_missing) as trips_missing,
            sum(trips_vanished) as trips_vanished,
            sum(trips_cancelled) as trips_cancelled
        from gold.mart_route_honesty
        group by all
        having sum(trips_judged) > 0
        order by service_date desc
    """,
    # Route x service day, judged trips only (D-038).
    "route_honesty": """
        select * from gold.mart_route_honesty where trips_judged > 0
    """,
    # Ghost candidates: missing or vanished.
    "ghost_trips": """
        select
            service_date, trip_id, mode, region, route_id,
            route_short_name, agency_name, trip_headsign, ghost_kind,
            confidence,
            strftime(sched_start_local, '%H:%M') as sched_start,
            local_hour, day_type, stop_count, stops_done, share_done,
            route_trips_seen
        from gold.fct_ghost_trip
    """,
    # Route x service day x local hour (Phase 2 mart).
    "route_hour": """
        select
            service_date, mode, region, route_id, route_short_name,
            agency_name, local_hour, trips_scheduled, trips_judged,
            trips_ran, trips_never_seen, trips_partial, on_time_share,
            mean_delay_s, p90_delay_s, headways, bunched_share
        from gold.mart_route_reliability
        where trips_judged > 0 or headways > 0
    """,
    # Hot spots with enough gaps to judge (the map only).
    "bunching_hotspots": """
        select * from gold.mart_bunching_hotspot
        where headways >= 3 and stop_lat is not null
    """,
    # Every gap, for the by-hour chart: the map's filter would drop
    # quiet stop-hours and bias the share (D-051).
    "bunching_by_hour": """
        select
            mode, day_type, local_hour,
            sum(headways) as headways,
            sum(bunched) as bunched,
            sum(overtaken) as overtaken
        from gold.mart_bunching_hotspot
        group by all
    """,
    "bunching_events": """
        select
            service_date, mode, region, route_id, route_short_name,
            direction_id, lead_trip_id, follow_trip_id, stops_paired,
            stops_bunched, stops_overtaken, share_of_stops, min_gap_s,
            scheduled_gap_s,
            strftime(first_bunched_local, '%H:%M') as first_bunched,
            local_hour, day_type
        from gold.fct_bunching_event
    """,
    # The latest build's models and tests (the quality page).
    "quality": """
        select run_id, run_at, resource_type, node, status, failures,
            seconds
        from gold.dq_results
        where run_id = (select arg_max(run_id, run_at) from gold.dq_results)
    """,
    "trip_match": "select * from gold.dq_trip_match",
}


# Tables with one row per service day or finer; ``date_from`` and
# ``date_to`` keep only those days. The bunching hot spots and the
# by-hour chart sum every day built, so a build for the site should
# already end on the last complete day (``service_date_to``).
DATED = {
    "service_days",
    "route_honesty",
    "ghost_trips",
    "route_hour",
    "bunching_events",
    "trip_match",
}


def publish(
    con: duckdb.DuckDBPyConnection,
    out: Path,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, int]:
    """Write every public table; returns rows per table.

    ``date_from`` and ``date_to`` (inclusive Sydney service days) drop
    the partly captured days at the edges of a build from the dated
    tables, so the site never reports a half-collected day.
    """
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, sql in TABLES.items():
        if name in DATED and (date_from or date_to):
            window = _window(date_from, date_to)
            sql = f"select * from ({sql}) where true{window}"
        path = out / f"{name}.parquet"
        tmp = path.with_suffix(".parquet.tmp")
        con.execute(f"copy ({sql}) to {_lit(tmp)} (format parquet)")
        tmp.replace(path)
        [(n,)] = con.execute(
            f"select count(*) from read_parquet({_lit(path)})"
        ).fetchall()
        rows[name] = n
    return rows


def _window(date_from: date | None, date_to: date | None) -> str:
    out = ""
    if date_from:
        out += f" and service_date >= date '{date_from.isoformat()}'"
    if date_to:
        out += f" and service_date <= date '{date_to.isoformat()}'"
    return out


def _lit(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"
