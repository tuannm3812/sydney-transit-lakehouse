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
        select * from public_hotspot
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
        from public_hotspot
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


# The two bunching tables read ``public_hotspot``: the hot-spot mart
# as built, or, for a windowed publish, the same aggregate over the
# window's days only (the mart sums every day built, and I-21 makes a
# build start a day early). Keep it in step with
# models/products/mart_bunching_hotspot.sql.
WINDOWED_HOTSPOT = """
    with h as (
        select
            h.*,
            case when isodow(h.service_date) >= 6 then 'weekend'
                else 'weekday' end as day_type,
            hour(timezone('Australia/Sydney', h.observed_arrival_utc))
                as local_hour
        from gold.fct_headway h
        where h.scheduled_headway_s <> 0{window}
    ),
    agg as (
        select
            mode,
            region,
            route_id,
            any_value(route_short_name) as route_short_name,
            direction_id,
            stop_id,
            day_type,
            local_hour,
            count(distinct service_date) as service_days,
            count(*) as headways,
            count(*) filter (bunched) as bunched,
            count(*) filter (overtaken) as overtaken,
            round(avg(bunched::int), 4) as bunched_share,
            round(median(observed_headway_s / abs(scheduled_headway_s)), 3)
                as median_gap_ratio,
            round(median(abs(scheduled_headway_s))) as median_scheduled_s,
            round(median(observed_headway_s)) as median_observed_s
        from h
        group by mode, region, route_id, direction_id, stop_id, day_type,
            local_hour
    ),
    stops as (
        select
            stop_id,
            any_value(stop_name) as stop_name,
            any_value(stop_lat) as stop_lat,
            any_value(stop_lon) as stop_lon
        from gold.mart_bunching_hotspot
        group by stop_id
    )
    select a.*, s.stop_name, s.stop_lat, s.stop_lon
    from agg a
    left join stops s using (stop_id)
"""

# Tables with one row per service day or finer; ``date_from`` and
# ``date_to`` keep only those days. ``quality`` is the build's own test
# run and is published whole.
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
    window = _window(date_from, date_to)
    hotspot = (
        WINDOWED_HOTSPOT.format(
            window=window.replace("service_date", "h.service_date")
        )
        if window
        else "select * from gold.mart_bunching_hotspot"
    )
    con.execute(f"create or replace temp view public_hotspot as {hotspot}")
    rows = {}
    for name, sql in TABLES.items():
        if name in DATED and window:
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
