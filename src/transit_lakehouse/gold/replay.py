"""Map replay export (Phase 3, D-039).

Writes one Sydney day of vehicle positions as small JSON files that the
dashboard's map replay (``dashboard/static/replay/``) plays back:

    <out>/index.json                 days, hours and vehicle counts
    <out>/<day>/<HH>.json            one local hour of tracks
    <out>/<day>/bunches.json         that day's bunches, to highlight

Positions come from ``silver.vehicle_position``, which the worker
builds from the raw snapshots without dropping any that moved, and are
thinned to one point per vehicle per ``step_s`` seconds (the last one
in each step). Coordinates keep 5 decimals, about 1 m. A track is one
vehicle on one trip; a vehicle with no trip is kept with an empty trip.

Hours are Sydney local time for the reader; the data stays UTC
(D-006) and every file says which UTC instant its hour starts at.
"""

# SQL text is fixed; values are bound parameters:
# ruff: noqa: S608

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import duckdb

SYDNEY = "Australia/Sydney"

POSITIONS_SQL = """
with p as (
    select
        v.mode,
        v.vehicle_id,
        coalesce(v.trip_id, '') as trip_id,
        v.start_date,
        v.route_id,
        v.latitude,
        v.longitude,
        coalesce(v.event_ts, v.fetched_at) as ts
    from silver.vehicle_position v
    where not coalesce(v.quarantined, false)
        and v.latitude is not null and v.longitude is not null
        and coalesce(v.event_ts, v.fetched_at) >= ?
        and coalesce(v.event_ts, v.fetched_at) < ?
        {mode_filter}
),
stepped as (
    select
        mode, vehicle_id, trip_id,
        any_value(start_date) as start_date,
        any_value(route_id) as rt_route_id,
        (epoch(ts)::bigint // ?) * ? as bucket,
        arg_max(latitude, ts) as lat,
        arg_max(longitude, ts) as lon
    from p
    group by mode, vehicle_id, trip_id, bucket
)
select
    s.mode, s.vehicle_id, s.trip_id,
    coalesce(t.route_short_name, s.rt_route_id, '') as route,
    coalesce(t.trip_headsign, '') as headsign,
    s.bucket, s.lat, s.lon
from stepped s
left join staging.stg_scheduled_trip t
    on t.trip_id = s.trip_id and t.start_date = s.start_date
order by s.mode, s.vehicle_id, s.trip_id, s.bucket
"""

BUNCHES_SQL = """
select
    route_short_name as route, mode, direction_id,
    lead_trip_id, follow_trip_id,
    epoch(first_bunched_at)::bigint as first_at,
    epoch(last_bunched_at)::bigint as last_at,
    stops_bunched, min_gap_s, scheduled_gap_s
from gold.fct_bunching_event
where timezone(?, first_bunched_at)::date = ?
order by first_at
"""


def hour_bounds(
    con: duckdb.DuckDBPyConnection, day: date
) -> list[tuple[int, datetime, datetime]]:
    """(local hour, UTC start, UTC end) for each hour of a Sydney day.

    Built by DuckDB's time zone rules, so a daylight-saving day has 23
    or 25 hours and no hour is played twice.
    """
    rows = con.execute(
        "select hour(timezone(?, h)), h, h + interval 1 hour"
        " from generate_series("
        "   timezone(?, ?::timestamp),"
        "   timezone(?, (?::date + 1)::timestamp) - interval 1 hour,"
        "   interval 1 hour) g(h)",
        [SYDNEY, SYDNEY, day, SYDNEY, day],
    ).fetchall()
    return [(h, lo, hi) for h, lo, hi in rows]


def export_day(
    con: duckdb.DuckDBPyConnection,
    day: date,
    out: Path,
    step_s: int = 30,
    modes: list[str] | None = None,
) -> dict:
    """Write one Sydney day's hours; returns its index entry."""
    day_dir = out / day.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    hours = []
    for hour, lo, hi in hour_bounds(con, day):
        params: list = [lo, hi]
        mode_filter = ""
        if modes:
            mode_filter = "and list_contains(?::varchar[], v.mode)"
            params.append(modes)
        sql = POSITIONS_SQL.format(mode_filter=mode_filter)
        rows = con.execute(sql, [*params, step_s, step_s]).fetchall()
        if not rows:
            continue
        t0 = int(lo.timestamp())
        tracks: dict[tuple, dict] = {}
        points: dict[tuple, list] = defaultdict(list)
        for mode, vid, trip, route, headsign, bucket, lat, lon in rows:
            key = (mode, vid, trip)
            if key not in tracks:
                tracks[key] = {
                    "v": vid,
                    "m": mode,
                    "t": trip,
                    "r": route,
                    "h": headsign,
                }
            points[key].append(
                [round(lon, 5), round(lat, 5), int(bucket) - t0]
            )
        vehicles = [{**tracks[k], "p": points[k]} for k in tracks]
        payload = {
            "day": day.isoformat(),
            "hour": hour,
            "t0": t0,
            "step": step_s,
            "vehicles": vehicles,
        }
        name = f"{hour:02d}.json"
        (day_dir / name).write_text(json.dumps(payload, separators=(",", ":")))
        hours.append(
            {
                "hour": hour,
                "t0": t0,
                "file": f"{day.isoformat()}/{name}",
                "vehicles": len({(m, v) for m, v, _ in tracks}),
            }
        )
    bunches = [
        dict(zip(_BUNCH_KEYS, r, strict=True))
        for r in con.execute(BUNCHES_SQL, [SYDNEY, day]).fetchall()
    ]
    (day_dir / "bunches.json").write_text(
        json.dumps(bunches, separators=(",", ":"))
    )
    return {"day": day.isoformat(), "hours": hours, "bunches": len(bunches)}


_BUNCH_KEYS = (
    "route",
    "mode",
    "direction_id",
    "lead_trip_id",
    "follow_trip_id",
    "first_at",
    "last_at",
    "stops_bunched",
    "min_gap_s",
    "scheduled_gap_s",
)


def write_index(out: Path, entry: dict) -> dict:
    """Add or replace a day in ``index.json``, newest day first."""
    path = out / "index.json"
    index = {"days": []}
    if path.exists():
        index = json.loads(path.read_text())
    days = [d for d in index["days"] if d["day"] != entry["day"]]
    days.append(entry)
    index["days"] = sorted(days, key=lambda d: d["day"], reverse=True)
    path.write_text(json.dumps(index, indent=1))
    return index
