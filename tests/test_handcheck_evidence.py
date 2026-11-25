"""Hand-check evidence rules (review finding 7, D-052).

Each test builds the few tables the check reads in an in-memory DuckDB,
so it pins one rule: pruned raw files are counted, a byte hit must be
the trip's own id, a removed or replaced alert does not explain a
ghost, and a quarantined position is not an untracked vehicle.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.gold import handcheck

START = datetime(2026, 11, 17, 21, 0, tzinfo=UTC)  # 8 am Sydney
END = START + timedelta(minutes=30)
LO, HI = START - timedelta(minutes=30), END + timedelta(minutes=30)


def evidence(trip_id: str = "1234", kind: str = "missing"):
    return handcheck.Evidence(
        trip_id=trip_id,
        start_date="20261118",
        service_date="2026-11-18",
        mode="bus",
        route="R1",
        agency="A",
        headsign="",
        ghost_kind=kind,
        confidence="high",
        sched_start_local="08:00",
        sched_start_utc=START,
        sched_end_utc=END,
        vanished_at=None,
        stop_count=4,
        stops_done=None,
        route_trips_seen=3,
    )


def feed(trip_ids: list[str], label: str = "") -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(START.timestamp())
    for i, trip in enumerate(trip_ids):
        ent = msg.entity.add()
        ent.id = f"e{i}"
        ent.vehicle.trip.trip_id = trip
        ent.vehicle.vehicle.label = label
    return msg.SerializeToString()


@pytest.fixture
def con():
    c = duckdb.connect()
    for schema in ("bronze", "silver", "staging"):
        c.execute(f"create schema {schema}")
    c.execute(
        "create table bronze.snapshot (source_file varchar,"
        " fetched_at timestamptz, mode varchar, kind varchar)"
    )
    yield c
    c.close()


def land(con, data_dir: Path, name: str, payload: bytes | None, minute: int):
    rel = f"raw/feed=buses_vehiclepos/{name}.pb.gz"
    if payload is not None:
        path = data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(payload))
    con.execute(
        "insert into bronze.snapshot"
        " values (?, ?, 'bus', 'vehicle_positions')",
        [rel, START + timedelta(minutes=minute)],
    )


def test_a_longer_trip_id_is_not_the_trip(con, tmp_path: Path) -> None:
    # "1234" is inside "12345" and inside a vehicle label, but no
    # entity names trip 1234: a byte search alone made it a pipeline miss.
    land(con, tmp_path, "a", feed(["12345"], label="bus 1234"), 0)
    ev = evidence("1234")
    handcheck._raw(con, tmp_path, ev, LO, HI)
    assert ev.raw_files_scanned == 1
    assert ev.raw_files_with_trip == 0
    land(con, tmp_path, "b", feed(["12345", "1234"]), 1)
    ev = evidence("1234")
    handcheck._raw(con, tmp_path, ev, LO, HI)
    assert ev.raw_files_with_trip == 1


def test_carries_trip_reads_trip_updates_too() -> None:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    ent = msg.entity.add()
    ent.id = "x"
    ent.trip_update.trip.trip_id = "T9"
    payload = msg.SerializeToString()
    assert handcheck.carries_trip(payload, b"T9")
    assert not handcheck.carries_trip(payload, b"T")


def test_pruned_raw_files_block_a_clean_reading(con, tmp_path: Path) -> None:
    land(con, tmp_path, "a", feed(["9"]), 0)
    land(con, tmp_path, "gone", None, 1)  # in bronze, pruned from disk
    ev = evidence("1234")
    handcheck._raw(con, tmp_path, ev, LO, HI)
    assert (ev.raw_files_scanned, ev.raw_files_missing) == (1, 1)
    assert handcheck.reading(ev)[0] == "raw_incomplete"
    # a hit in the files that are there still reads as a pipeline miss
    ev.raw_files_with_trip = 1
    assert handcheck.reading(ev)[0] == "pipeline_miss"


def alert_tables(con) -> None:
    con.execute(
        "create table staging.stg_scheduled_trip as"
        " select '1234' as trip_id, date '2026-11-18' as service_date,"
        " 'R1' as route_id"
    )
    con.execute(
        "create table silver.alert (alert_id varchar, content_hash varchar,"
        " event varchar, effect varchar, header_text varchar,"
        " active_start timestamptz, active_end timestamptz,"
        " fetched_at timestamptz)"
    )
    con.execute(
        "create table silver.alert_scope (alert_id varchar,"
        " content_hash varchar, trip_id varchar, route_id varchar)"
    )


def add_alert(con, alert_id, chash, event, fetched, route="R1") -> None:
    con.execute(
        "insert into silver.alert values (?, ?, ?, 'NO_SERVICE', 'x',"
        " null, null, ?)",
        [alert_id, chash, event, fetched],
    )
    con.execute(
        "insert into silver.alert_scope values (?, ?, null, ?)",
        [alert_id, chash, route],
    )


def test_removed_alerts_do_not_explain_a_ghost(con) -> None:
    alert_tables(con)
    day_before = START - timedelta(days=1)
    # Open-ended alert on the route, removed the day before the trip.
    add_alert(con, "A1", "h1", "new", day_before)
    add_alert(con, "A1", "h1", "removed", day_before + timedelta(hours=2))
    # Replaced before the window by a version scoped to another route.
    add_alert(con, "A2", "h2", "new", day_before)
    add_alert(con, "A2", "h3", "changed", LO - timedelta(minutes=5), "R2")
    ev = evidence("1234")
    handcheck._alerts(con, ev, LO, HI)
    assert ev.alerts == []
    # Still in the feed during the window: it counts.
    add_alert(con, "A3", "h4", "new", day_before)
    handcheck._alerts(con, ev, LO, HI)
    assert [a.split(" ")[0] for a in ev.alerts] == ["A3"]


def test_quarantined_positions_are_not_untracked_vehicles(con) -> None:
    con.execute(
        "create table staging.stg_scheduled_trip as"
        " select '1234' as trip_id, date '2026-11-18' as service_date,"
        " 'P1' as pattern_id, 'v1' as version"
    )
    con.execute(
        "create table silver.timetable_pattern_stop as"
        " select 'P1' as pattern_id, 1 as stop_sequence, 'S1' as stop_id"
    )
    con.execute(
        "create table silver.timetable_stop as select 'v1' as version,"
        " 'S1' as stop_id, -33.87 as stop_lat, 151.21 as stop_lon"
    )
    con.execute(
        "create table silver.vehicle_position (mode varchar,"
        " vehicle_id varchar, trip_id varchar, latitude double,"
        " longitude double, fetched_at timestamptz, quarantined boolean)"
    )
    con.execute(
        "insert into silver.vehicle_position values"
        " ('bus', 'B1', null, -33.87, 151.21, ?, true)",
        [START],
    )
    ev = evidence("1234")
    handcheck._untracked(con, ev, 300.0)
    assert ev.untracked_nearby == 0
    con.execute(
        "insert into silver.vehicle_position values"
        " ('bus', 'B2', null, -33.8701, 151.2101, ?, null)",
        [START],
    )
    handcheck._untracked(con, ev, 300.0)
    assert ev.untracked_nearby == 1
