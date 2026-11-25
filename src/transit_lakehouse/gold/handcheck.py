"""Hand-check sheet for ghost trips (the Phase 3 gate, D-045).

The gate is that flagged trips hold up when checked by hand against the
raw positions. This module picks a sample of ``gold.fct_ghost_trip``
for one service day and gathers, for each trip, the evidence a person
checks it against:

- **raw**: every landed trip-update and vehicle-position file of the
  trip's mode in its window, decompressed and searched for the trip id
  as bytes. This does not use the worker's decoding, so it catches a
  trip the pipeline lost on the way to silver.
- **silver, any date**: trip-update and position rows with the trip
  id on any ``start_date`` inside the trip's window, which catches a
  start-date mismatch (ids repeat daily, so other days do not count).
- **untracked vehicles** (missing trips): positions without a trip id
  within ``near_m`` of the trip's first stop around its scheduled
  start, a bus that may have run without its trip logged on.
- **alerts**: service alerts that name the trip or its route.
- **route siblings**: other trips of the route seen that day.

Each trip gets an automatic reading from that evidence. The person
doing the check confirms or overrules it in the sheet's verdict
column; the agreement is what the gate report quotes.
"""

# SQL text is built from fixed fragments; values are bound parameters:
# ruff: noqa: S608

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
from google.protobuf.message import DecodeError

from transit_lakehouse.worker.decode import _envelope, read_payload

SYDNEY = "Australia/Sydney"


@dataclass
class Evidence:
    trip_id: str
    start_date: str
    service_date: str
    mode: str
    route: str
    agency: str
    headsign: str
    ghost_kind: str
    confidence: str | None
    sched_start_local: str
    sched_start_utc: datetime
    sched_end_utc: datetime
    vanished_at: datetime | None
    stop_count: int | None
    stops_done: int | None
    route_trips_seen: int
    raw_files_scanned: int = 0
    raw_files_missing: int = 0
    raw_files_with_trip: int = 0
    raw_after_vanish: int = 0
    raw_first_at: datetime | None = None
    raw_last_at: datetime | None = None
    silver_trip_rows: int = 0
    silver_position_rows: int = 0
    other_start_dates: list[str] = field(default_factory=list)
    untracked_nearby: int = 0
    alerts: list[str] = field(default_factory=list)
    reading: str = ""
    why: str = ""


def sample(
    con: duckdb.DuckDBPyConnection,
    service_date: date,
    n: int = 20,
    seed: str = "phase3",
) -> list[dict]:
    """Pick ``n`` ghost trips, spread over kind, confidence and mode.

    Within each stratum trips are ordered by a hash of the seed and the
    trip id, so the same seed always draws the same sample and nobody
    picks the trips. Strata take turns, so a sample of 20 is not all
    one kind.
    """
    rows = con.execute(
        "select * from gold.fct_ghost_trip where service_date = ?",
        [service_date],
    )
    cols = [d[0] for d in rows.description]
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for values in rows.fetchall():
        rec = dict(zip(cols, values, strict=True))
        key = (rec["ghost_kind"], rec["confidence"] or "", rec["mode"])
        strata[key].append(rec)
    for recs in strata.values():
        recs.sort(key=lambda r: _hash(seed, r["trip_id"]))
    picked: list[dict] = []
    # missing before vanished, high confidence first: the headline
    # claim is the missing trip, so it gets the first turns
    order = sorted(
        strata,
        key=lambda k: (k[0] != "missing", _conf_rank(k[1]), k[2]),
    )
    while len(picked) < n and any(strata[k] for k in order):
        for key in order:
            if strata[key] and len(picked) < n:
                picked.append(strata[key].pop(0))
    return picked


def _hash(seed: str, trip_id: str) -> str:
    return hashlib.sha256(f"{seed}:{trip_id}".encode()).hexdigest()


def _conf_rank(conf: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(conf, 3)


def gather(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path,
    trips: list[dict],
    pad_minutes: int = 30,
    near_m: float = 300.0,
) -> list[Evidence]:
    """Collect the evidence for each sampled trip."""
    out = []
    for t in trips:
        ev = Evidence(
            trip_id=t["trip_id"],
            start_date=t["start_date"],
            service_date=str(t["service_date"]),
            mode=t["mode"],
            route=t["route_short_name"] or t["route_id"],
            agency=t["agency_name"] or t["agency_id"] or "",
            headsign=t["trip_headsign"] or "",
            ghost_kind=t["ghost_kind"],
            confidence=t["confidence"],
            sched_start_local=t["sched_start_local"].strftime("%H:%M"),
            sched_start_utc=t["sched_start_utc"],
            sched_end_utc=t["sched_end_utc"],
            vanished_at=t["vanished_at"],
            stop_count=t["stop_count"],
            stops_done=t["stops_done"],
            route_trips_seen=t["route_trips_seen"] or 0,
        )
        lo = ev.sched_start_utc - timedelta(minutes=pad_minutes)
        hi = ev.sched_end_utc + timedelta(minutes=pad_minutes)
        _raw(con, data_dir, ev, lo, hi)
        _silver(con, ev, lo, hi)
        if ev.ghost_kind == "missing":
            _untracked(con, ev, near_m)
        _alerts(con, ev, lo, hi)
        ev.reading, ev.why = reading(ev)
        out.append(ev)
    return out


def _raw(con, data_dir: Path, ev: Evidence, lo, hi) -> None:
    files = con.execute(
        "select source_file, fetched_at from bronze.snapshot"
        " where mode = ? and kind in ('trip_updates', 'vehicle_positions')"
        " and fetched_at between ? and ? order by fetched_at",
        [ev.mode, lo, hi],
    ).fetchall()
    needle = ev.trip_id.encode()
    for rel, fetched in files:
        path = data_dir / rel
        if not path.exists():
            # Pruned (D-026) or not downloaded: counted, so the reading
            # can say the evidence is incomplete instead of clean.
            ev.raw_files_missing += 1
            continue
        ev.raw_files_scanned += 1
        if carries_trip(read_payload(path), needle):
            ev.raw_files_with_trip += 1
            ev.raw_first_at = ev.raw_first_at or fetched
            ev.raw_last_at = fetched
            if ev.vanished_at and fetched > ev.vanished_at + timedelta(
                minutes=2
            ):
                ev.raw_after_vanish += 1


def carries_trip(payload: bytes, trip_id: bytes) -> bool:
    """True when an entity's trip update or vehicle names ``trip_id``.

    A byte search alone is not enough: a short numeric trip id such as
    ``1234`` also occurs inside ``12345`` or in an unrelated field. So a
    hit is confirmed by walking the protobuf by hand (FeedMessage.entity
    2, then trip_update 3 or vehicle 4, then trip 1, then trip_id 1),
    which does not use the worker's decoder. A payload that cannot be
    walked keeps the byte search's answer, so a broken file is shown to
    the person checking rather than hidden.
    """
    if trip_id not in payload:
        return False
    try:
        for field_no, entity in _envelope(payload):
            if field_no != 2:
                continue
            for kind, body in _envelope(entity):
                if kind not in (3, 4):
                    continue
                for part, trip in _envelope(body):
                    if part != 1:
                        continue
                    for key, value in _envelope(trip):
                        if key == 1 and value == trip_id:
                            return True
    except (DecodeError, IndexError):
        return True
    return False


def _silver(con, ev: Evidence, lo, hi) -> None:
    # Bus trip ids repeat every day the trip runs, so the same id under
    # another start date is only a mismatch inside this trip's window
    # (R3-01): rows of yesterday's or tomorrow's run are not.
    [(rows, dates)] = con.execute(
        "select count(*), list(distinct start_date)"
        " from silver.trip_status where trip_id = ?"
        " and fetched_at between ? and ?",
        [ev.trip_id, lo, hi],
    ).fetchall()
    ev.silver_trip_rows = rows
    [(pos, pdates)] = con.execute(
        "select count(*), list(distinct start_date)"
        " from silver.vehicle_position where trip_id = ?"
        " and fetched_at between ? and ?",
        [ev.trip_id, lo, hi],
    ).fetchall()
    ev.silver_position_rows = pos
    seen = {d for d in (dates or []) + (pdates or []) if d}
    ev.other_start_dates = sorted(seen - {ev.start_date})


def _untracked(con, ev: Evidence, near_m: float) -> None:
    first = con.execute(
        "select s.stop_lat, s.stop_lon"
        " from staging.stg_scheduled_trip t"
        " join silver.timetable_pattern_stop p"
        "   on p.pattern_id = t.pattern_id"
        " join silver.timetable_stop s"
        "   on s.version = t.version and s.stop_id = p.stop_id"
        " where t.trip_id = ? and t.service_date = ?"
        " order by p.stop_sequence limit 1",
        [ev.trip_id, ev.service_date],
    ).fetchall()
    if not first or first[0][0] is None:
        return
    lat, lon = first[0]
    [(count,)] = con.execute(
        "select count(distinct vehicle_id) from silver.vehicle_position"
        " where mode = ? and trip_id is null"
        " and not coalesce(quarantined, false)"
        " and fetched_at between ? and ?"
        " and 2 * 6371000 * asin(sqrt("
        "   pow(sin(radians(latitude - ?) / 2), 2)"
        "   + cos(radians(?)) * cos(radians(latitude))"
        "   * pow(sin(radians(longitude - ?) / 2), 2))) <= ?",
        [
            ev.mode,
            ev.sched_start_utc - timedelta(minutes=10),
            ev.sched_start_utc + timedelta(minutes=10),
            lat,
            lat,
            lon,
            near_m,
        ],
    ).fetchall()
    ev.untracked_nearby = count


def _alerts(con, ev: Evidence, lo, hi) -> None:
    # silver.alert holds a row per version of an alert (new, changed,
    # removed). A version counts only while it was in the feed: not a
    # removal, and not replaced or removed before the trip's window.
    rows = con.execute(
        "with versions as ("
        "   select *, lead(fetched_at) over ("
        "       partition by alert_id order by fetched_at) as until_at"
        "   from silver.alert"
        ")"
        " select distinct a.alert_id, a.effect, a.header_text"
        " from silver.alert_scope s"
        " join versions a"
        "   on a.alert_id = s.alert_id and a.content_hash = s.content_hash"
        " join staging.stg_scheduled_trip t"
        "   on t.trip_id = ? and t.service_date = ?"
        " where (s.trip_id = t.trip_id or s.route_id = t.route_id)"
        " and a.event <> 'removed'"
        " and a.fetched_at <= ?"
        " and (a.until_at is null or a.until_at >= ?)"
        " and coalesce(a.active_start, ?) <= ?"
        " and coalesce(a.active_end, ?) >= ?"
        " order by 1",
        [ev.trip_id, ev.service_date, hi, lo, lo, hi, hi, lo],
    ).fetchall()
    ev.alerts = [
        f"{aid} ({effect or 'no effect'}): {(text or '')[:80]}"
        for aid, effect, text in rows
    ]


def reading(ev: Evidence) -> tuple[str, str]:
    """The automatic reading of the evidence, and why."""
    if ev.raw_files_scanned == 0:
        return "no_raw", "no raw files on disk for the trip's window"
    if ev.ghost_kind == "missing" and ev.raw_files_with_trip:
        return (
            "pipeline_miss",
            f"trip id in {ev.raw_files_with_trip} raw files, "
            "so gold lost a trip the feed carried",
        )
    if ev.ghost_kind == "vanished" and ev.raw_after_vanish:
        return (
            "pipeline_miss",
            f"trip id in {ev.raw_after_vanish} raw files after it "
            "vanished from silver",
        )
    if ev.raw_files_missing:
        return (
            "raw_incomplete",
            f"{ev.raw_files_missing} of "
            f"{ev.raw_files_missing + ev.raw_files_scanned} raw files "
            "are not on disk; extract the hour tars from object storage "
            "and run the check again before a verdict",
        )
    if ev.other_start_dates:
        return (
            "start_date_mismatch",
            "the feed carried this trip id under start date "
            + ", ".join(ev.other_start_dates),
        )
    if ev.alerts:
        return "alert_explains", f"{len(ev.alerts)} alert(s) name it"
    if ev.untracked_nearby:
        return (
            "untracked_vehicle",
            f"{ev.untracked_nearby} vehicle(s) without a trip near its "
            "first stop at its start",
        )
    if ev.ghost_kind == "missing":
        return (
            "consistent_ghost",
            f"absent from all {ev.raw_files_scanned} raw files; "
            f"{ev.route_trips_seen} other trips of the route seen",
        )
    return (
        "consistent_vanish",
        f"left the feed after {ev.stops_done} of {ev.stop_count} stops "
        "and did not come back in raw",
    )


def to_markdown(evidence: list[Evidence], service_date: date) -> str:
    """The sheet: one row per trip, with an empty verdict column."""
    head = (
        f"# Ghost trip hand-check, service day {service_date}\n\n"
        "Generated by `python -m transit_lakehouse.gold handcheck`. "
        "Fill **verdict** with `agree` or `disagree` and a note, after "
        "checking the trip against the raw positions and the evidence "
        "here.\n\n"
        "| # | trip | route | operator | start (Sydney) | kind | "
        "confidence | raw files with trip / scanned / missing | "
        "silver rows, "
        "any date | untracked near start | alerts | route trips seen | "
        "reading | verdict |\n"
        "|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|\n"
    )
    lines = []
    for i, ev in enumerate(evidence, start=1):
        lines.append(
            f"| {i} | `{ev.trip_id}` | {ev.route} | {ev.agency} | "
            f"{ev.sched_start_local} | {ev.ghost_kind} | "
            f"{ev.confidence or ''} | "
            f"{ev.raw_files_with_trip} / {ev.raw_files_scanned} / "
            f"{ev.raw_files_missing} | "
            f"{ev.silver_trip_rows + ev.silver_position_rows} | "
            f"{ev.untracked_nearby} | {len(ev.alerts)} | "
            f"{ev.route_trips_seen} | {ev.reading}: {ev.why} | |"
        )
    counts: dict[str, int] = defaultdict(int)
    for ev in evidence:
        counts[ev.reading] += 1
    tally = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    return head + "\n".join(lines) + f"\n\nReadings: {tally}.\n"


def to_json(evidence: list[Evidence]) -> str:
    return json.dumps([asdict(e) for e in evidence], default=str, indent=1)
