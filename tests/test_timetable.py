from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.worker.tables import (
    WATERMARK_PREFIX,
    open_catalog,
)
from transit_lakehouse.worker.timetable import (
    PATTERN_TABLE,
    TRIP_TABLE,
    VERSION_TABLE,
    ensure_timetable_tables,
    run_timetable,
)

T0 = datetime(2026, 11, 17, 7, 45, tzinfo=UTC)


def gtfs_zip(times: dict[str, list[str]], with_info: bool = True) -> bytes:
    """A tiny GTFS feed: one route, trips given as {trip: [hh:mm:ss]}."""
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "A1,Test Buses,https://x,Australia/Sydney\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_type\n"
        "R1,A1,333,700\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        "S1,One,-33.87,151.21\nS2,Two,-33.88,151.22\n"
        "S3,Three,-33.89,151.23\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,"
        "friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20261101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\n"
        + "".join(f"R1,WK,{t},0\n" for t in times),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
        "stop_sequence\n"
        + "".join(
            f"{t},{hms},{hms},S{i},{i}\n"
            for t, stops in times.items()
            for i, hms in enumerate(stops, start=1)
        ),
    }
    if with_info:
        files["feed_info.txt"] = (
            "feed_publisher_name,feed_version,feed_start_date,feed_end_date\n"
            "TfNSW,v1,20261117,20261231\n"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


BASE = {
    "T1": ["08:00:00", "08:10:00", "08:20:00"],
    "T2": ["09:00:00", "09:10:00", "09:20:00"],
    "T3": ["24:30:00", "24:40:00", "24:50:00"],  # after midnight
}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def land(data_dir: Path, payload: bytes, hours: int = 0) -> str:
    return Lander(data_dir).land(
        "static_gtfs", T0 + timedelta(hours=hours), payload, compress=False
    )


def scan(catalog, name: str) -> list[dict]:
    return catalog.load_table(name).scan().to_arrow().to_pylist()


def test_first_version_stores_everything(data_dir: Path) -> None:
    land(data_dir, gtfs_zip(BASE))
    catalog = open_catalog(data_dir, env={})
    [stats] = run_timetable(data_dir, catalog)
    assert (stats.trips, stats.patterns, stats.new_patterns) == (3, 3, 3)
    assert stats.stop_times == 9

    [version] = scan(catalog, VERSION_TABLE)
    assert version["feed_version"] == "v1"
    assert version["routes"] == 1 and version["stops"] == 3
    trips = {r["trip_id"]: r for r in scan(catalog, TRIP_TABLE)}
    assert trips["T3"]["first_departure_s"] == 24 * 3600 + 30 * 60
    assert trips["T1"]["stop_count"] == 3
    assert len(scan(catalog, PATTERN_TABLE)) == 9
    stops = scan(catalog, "silver.timetable_stop")
    assert {s["stop_lat"] for s in stops} == {-33.87, -33.88, -33.89}


def test_republish_stores_only_new_patterns(data_dir: Path) -> None:
    land(data_dir, gtfs_zip(BASE))
    changed = dict(BASE, T2=["09:05:00", "09:15:00", "09:25:00"])
    land(data_dir, gtfs_zip(changed), hours=6)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    [second] = run_timetable(data_dir, catalog)
    assert (second.trips, second.patterns, second.new_patterns) == (3, 3, 1)
    assert len(scan(catalog, PATTERN_TABLE)) == 9 + 3
    assert len(scan(catalog, "silver.timetable_pattern")) == 4
    assert len(scan(catalog, TRIP_TABLE)) == 6
    assert run_timetable(data_dir, catalog) == []  # nothing left


def test_identical_trips_share_a_pattern(data_dir: Path) -> None:
    land(data_dir, gtfs_zip(dict(BASE, T4=BASE["T1"])))
    catalog = open_catalog(data_dir, env={})
    [stats] = run_timetable(data_dir, catalog)
    assert (stats.trips, stats.patterns) == (4, 3)
    trips = {r["trip_id"]: r for r in scan(catalog, TRIP_TABLE)}
    assert trips["T4"]["pattern_id"] == trips["T1"]["pattern_id"]


def test_redoing_a_version_does_not_duplicate(data_dir: Path) -> None:
    rel = land(data_dir, gtfs_zip(BASE))
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    # Simulate a crash before the watermark moved: wind it back.
    table = ensure_timetable_tables(catalog)[VERSION_TABLE]
    with table.transaction() as tx:
        tx.remove_properties(WATERMARK_PREFIX + "static_gtfs")
    [again] = run_timetable(data_dir, catalog)
    assert again.new_patterns == 3  # its own patterns are rewritten
    assert len(scan(catalog, PATTERN_TABLE)) == 9
    assert len(scan(catalog, TRIP_TABLE)) == 3
    assert len(scan(catalog, VERSION_TABLE)) == 1
    assert rel.endswith(".zip")


def test_missing_optional_files_and_columns(data_dir: Path) -> None:
    land(data_dir, gtfs_zip(BASE, with_info=False))
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    [version] = scan(catalog, VERSION_TABLE)
    assert version["feed_version"] is None
    assert scan(catalog, "silver.timetable_calendar_date") == []
    [route] = scan(catalog, "silver.timetable_route")
    assert route["route_long_name"] is None and route["route_type"] == 700


def test_worker_run_builds_one_timetable_per_run(data_dir: Path) -> None:
    from transit_lakehouse.collector.config import Feed
    from transit_lakehouse.worker.runner import run_once

    land(data_dir, gtfs_zip(BASE))
    land(data_dir, gtfs_zip(BASE), hours=6)
    feed = Feed("static_gtfs", "static_gtfs", "https://x/gtfs", 21600)
    catalog = open_catalog(data_dir, env={})
    first = run_once(data_dir, catalog, [feed])
    assert len(first.timetables) == 1 and "timetable" in first.summary()
    second = run_once(data_dir, catalog, [feed])
    assert second.timetables[0].new_patterns == 0
    assert run_once(data_dir, catalog, [feed]).timetables == []
