"""Gold on a network shaped like TfNSW's: several operators, a loop
route, a route whose trips start in two regions, and a stretch of
unreadable files. Each case is one a full day of every mode will meet
(gold review, D-038).
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.gold.__main__ import build, gold_paths
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import open_catalog
from transit_lakehouse.worker.timetable import run_timetable

TU_FEED = Feed("buses_tripupdates", "trip_updates", "https://x/tu", 30)
# Wednesday 18 Nov 2026; local midnight is 13:00 UTC on 17 Nov.
ORIGIN = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)

STOPS = {
    "S1": (-33.87, 151.21),
    "S2": (-33.88, 151.22),
    "X1": (-33.90, 151.20),
    "X2": (-33.91, 151.21),
    "N1": (-32.93, 151.78),  # Newcastle, outside the Sydney box
}
# route: (agency, network)
ROUTES = {
    "R1": ("A1", "Sydney Buses Network"),
    "RL": ("A1", "Sydney Buses Network"),  # a loop
    "RM": ("A1", "Sydney and Surrounds Network"),  # spans regions
    "R2": ("A2", "Sydney Buses Network"),  # operator without realtime
}
# trip: (route, [(stop, local time)])
TRIPS = {
    "L1": ("RL", [("X1", "08:00"), ("X2", "08:10"), ("X1", "08:20")]),
    "L2": ("RL", [("X1", "08:15"), ("X2", "08:25"), ("X1", "08:35")]),
    "M1": ("RM", [("S1", "08:00"), ("S2", "08:10")]),
    "M2": ("RM", [("N1", "08:02"), ("S2", "08:12")]),
    "G1": ("R1", [("S1", "08:05"), ("S2", "08:15")]),  # a ghost
    "Q1": ("R2", [("S1", "08:05"), ("S2", "08:15")]),  # no realtime
    "P1": ("R1", [("S1", "09:00"), ("S2", "09:10")]),  # unreadable hour
}
LIVE = ("L1", "L2", "M1", "M2")
# Readable trip updates until 21:45 UTC, then files that do not decode.
READABLE_UNTIL = datetime(2026, 11, 17, 21, 45, tzinfo=UTC)


def at(hm: str) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return ORIGIN + timedelta(hours=h, minutes=m)


def network_zip() -> bytes:
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "A1,Test Buses,https://x,Australia/Sydney\n"
        "A2,Quiet Buses,https://x,Australia/Sydney\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_desc,"
        "route_type\n"
        + "".join(f"{r},{a},{r},{n},700\n" for r, (a, n) in ROUTES.items()),
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{s},{s},{lat},{lon}\n" for s, (lat, lon) in STOPS.items()),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,"
        "friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20261101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\n"
        + "".join(f"{r},WK,{t},0\n" for t, (r, _) in TRIPS.items()),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
        "stop_sequence\n"
        + "".join(
            f"{t},{hm}:00,{hm}:00,{s},{i}\n"
            for t, (_, calls) in TRIPS.items()
            for i, (s, hm) in enumerate(calls, start=1)
        ),
        "feed_info.txt": "feed_publisher_name,feed_version,feed_start_date,"
        "feed_end_date\nTfNSW,v1,20261117,20261231\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def tu_snapshot(t: datetime) -> bytes:
    """The live trips, on time, each with its stops not yet passed."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    for trip in LIVE:
        calls = TRIPS[trip][1]
        left = [
            (i, s, at(hm))
            for i, (s, hm) in enumerate(calls, start=1)
            if t < at(hm) + timedelta(seconds=15)
        ]
        if not left:
            continue
        ent = msg.entity.add()
        ent.id = trip
        tu = ent.trip_update
        tu.trip.trip_id = trip
        tu.trip.start_date = "20261118"
        for i, stop, when in left:
            stu = tu.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = stop
            stu.arrival.delay = 0
            stu.arrival.time = int(when.timestamp())
    return msg.SerializeToString()


@pytest.fixture(scope="module")
def network_db(tmp_path_factory) -> Path:
    data_dir = tmp_path_factory.mktemp("gold_network") / "data"
    lander = Lander(data_dir)
    lander.land(
        "static_gtfs",
        datetime(2026, 11, 17, 7, 45, tzinfo=UTC),
        network_zip(),
        compress=False,
    )
    t = datetime(2026, 11, 17, 20, 45, tzinfo=UTC)
    while t <= datetime(2026, 11, 17, 22, 40, tzinfo=UTC):
        payload = tu_snapshot(t) if t <= READABLE_UNTIL else b"\xff" * 64
        lander.land(TU_FEED.name, t, payload)
        t += timedelta(seconds=60)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [TU_FEED])
    assert build(data_dir, None, {"outcome_grace_minutes": 20}, None) == 0
    return gold_paths(data_dir)["db"]


def query(db: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_unreadable_files_are_not_capture(network_db: Path) -> None:
    [(bad,)] = query(
        network_db,
        "select count(*) from bronze.snapshot"
        " where decode_error like 'decode_error%'",
    )
    assert bad > 0
    [(covered_to,)] = query(
        network_db,
        "select max(covered_to) from staging.stg_capture_window",
    )
    assert covered_to == READABLE_UNTIL.astimezone(covered_to.tzinfo)
    # P1 runs only while every file was unreadable: not a ghost.
    [row] = query(
        network_db,
        "select outcome, tu_covered from gold.fct_trip_outcome"
        " where trip_id = 'P1' and service_date = '2026-11-18'",
    )
    assert row == ("not_judged", False)


def test_never_seen_needs_an_operator_with_realtime(network_db: Path) -> None:
    got = query(
        network_db,
        "select trip_id, outcome, operator_covered"
        " from gold.fct_trip_outcome where trip_id in ('G1', 'Q1')"
        " and service_date = '2026-11-18' order by trip_id",
    )
    # G1's operator has trips in the feed; Q1's has none that day.
    assert got == [
        ("G1", "never_seen", True),
        ("Q1", "not_judged", False),
    ]
    [(covered, never_seen)] = query(
        network_db,
        "select sum(trips_covered), sum(trips_never_seen)"
        " from gold.mart_route_reliability where route_id in ('R1', 'R2')"
        " and service_date = '2026-11-18'",
    )
    assert (covered, never_seen) == (1, 1)


def test_loop_route_pairs_each_call_with_the_same_call(
    network_db: Path,
) -> None:
    got = query(
        network_db,
        "select visit, prev_trip_id, trip_id, observed_headway_s,"
        " scheduled_headway_s, bunched from gold.fct_headway"
        " where stop_id = 'X1' order by visit",
    )
    assert got == [
        (1, "L1", "L2", 900, 900, False),
        (2, "L1", "L2", 900, 900, False),
    ]


def test_route_mart_keeps_regions_apart(network_db: Path) -> None:
    got = query(
        network_db,
        "select region, trips_scheduled, stop_arrivals, headways"
        " from gold.mart_route_reliability where route_id = 'RM'"
        " and service_date = '2026-11-18' order by region",
    )
    # M2 starts in Newcastle and follows M1 to S2: its arrivals and the
    # gap behind M1 belong to the regional row only.
    assert got == [
        ("regional", 1, 2, 1),
        ("sydney", 1, 2, None),
    ]
