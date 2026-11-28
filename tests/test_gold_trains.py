"""Sydney Trains trip updates, read the way the feed sends them (D-059).

The train feed differs from the others in four ways, all seen on
25-26 Nov 2026 (O-04): no start_date on any trip, only a stop id on each
stop (no stop_sequence), never a negative delay (an early train reads
0), and an origin delay that is a stale departure estimate.

T1 runs four stops from 08:00. It is listed with a 60 s delay at its
origin, 0 at S2 (it was early) and 90 s at S3 and S4. Gold must date it
from the timetable, place each stop by its stop id, and keep the origin
and the zero-delay stop as arrivals with no time. X9 is a train the
feed reports that no timetable lists: trip match counts it apart.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.gold.__main__ import build, gold_paths
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import open_catalog
from transit_lakehouse.worker.timetable import run_timetable

TU_FEED = Feed("sydneytrains_tripupdates", "trip_updates", "https://x/ttu", 30)
# Wednesday 18 Nov 2026 starts at 13:00 UTC on 17 Nov (AEDT).
ORIGIN = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
START = "08:00"
STOPS = 4
DELAY = {1: 60, 2: 0, 3: 90, 4: 90}


def at(hm: str, plus_s: float = 0) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return ORIGIN + timedelta(hours=h, minutes=m, seconds=plus_s)


def sched(seq: int) -> datetime:
    return at(START, 180 * (seq - 1))


def gtfs() -> bytes:
    def hms(t: datetime) -> str:
        s = int((t - ORIGIN).total_seconds())
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "SydneyTrains,Sydney Trains,https://x,Australia/Sydney\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_type\n"
        "T1R,SydneyTrains,T1,2\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(
            f"S{i},Station {i},-33.87,{151.20 + 0.02 * i}\n"
            for i in range(1, STOPS + 1)
        ),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,"
        "friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20261101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\nT1R,WK,T1,0\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
        "stop_sequence\n"
        + "".join(
            f"T1,{hms(sched(i))},{hms(sched(i))},S{i},{10 * i}\n"
            for i in range(1, STOPS + 1)
        ),
        "feed_info.txt": "feed_publisher_name,feed_version,"
        "feed_start_date,feed_end_date\nTfNSW,v1,20261117,20261231\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def listed(t: datetime) -> list[int]:
    """Stops still listed at t: each drops 30 s after its arrival."""
    return [
        i
        for i in range(1, STOPS + 1)
        if t < sched(i) + timedelta(seconds=DELAY[i] + 30)
    ]


def snapshot(t: datetime) -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    stops = listed(t)
    if t >= at(START, -600) and stops:
        ent = msg.entity.add()
        ent.id = "T1"
        tu = ent.trip_update
        tu.trip.trip_id = "T1"  # no start_date, as Sydney Trains send
        for i in stops:
            stu = tu.stop_time_update.add()
            stu.stop_id = f"S{i}"  # and no stop_sequence
            stu.arrival.delay = DELAY[i]
            stu.arrival.time = int(sched(i).timestamp()) + DELAY[i]
    if at("07:50") <= t <= at("08:20"):
        ent = msg.entity.add()
        ent.id = "X9"
        tu = ent.trip_update
        tu.trip.trip_id = "X9"  # in no published timetable
        stu = tu.stop_time_update.add()
        stu.stop_id = "S2"
        stu.arrival.delay = 0
    return msg.SerializeToString()


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("trains") / "data"
    lander = Lander(data_dir)
    lander.land(
        "static_gtfs",
        datetime(2026, 11, 17, 7, 45, tzinfo=UTC),
        gtfs(),
        compress=False,
    )
    t = at("07:30")
    while t <= at("08:30"):
        lander.land(TU_FEED.name, t, snapshot(t))
        t += timedelta(seconds=30)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [TU_FEED])
    build(data_dir, None, {"outcome_grace_minutes": 20}, None)
    return gold_paths(data_dir)["db"]


def query(db, sql: str) -> list[tuple]:
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_a_train_run_is_dated_from_the_timetable(db) -> None:
    assert query(
        db,
        "select trip_id, start_date, timetable_dated"
        " from staging.stg_rt_run order by 1",
    ) == [("T1", "20261118", True), ("X9", "20261118", False)]
    assert query(
        db,
        "select distinct mode, start_date, service_date::varchar"
        " from staging.stg_rt_trip_state",
    ) == [("train", "20261118", "2026-11-18")]
    assert query(
        db,
        "select outcome from gold.fct_trip_outcome"
        " where trip_id = 'T1' and service_date = '2026-11-18'",
    ) == [("ran",)]


def test_stops_are_placed_by_stop_id(db) -> None:
    rows = query(
        db,
        "select stop_id, stop_sequence from gold.fct_stop_arrival"
        " order by stop_sequence",
    )
    assert rows == [(f"S{i}", 10 * i) for i in range(1, STOPS + 1)]


def test_origin_and_zero_delay_arrivals_have_no_time(db) -> None:
    rows = query(
        db,
        "select stop_id, delay_s, early_hidden, time_known"
        " from gold.fct_stop_arrival order by stop_sequence",
    )
    assert rows == [
        ("S1", 60, False, False),  # origin: a stale departure estimate
        ("S2", 0, True, False),  # on time or early, by how much unknown
        ("S3", 90, False, True),
        ("S4", 90, False, True),
    ]


def test_a_hidden_early_stop_still_counts_as_on_time(db) -> None:
    # S2 at its ceiling of 0, S3 and S4 at 90 s; the origin is left out
    assert query(
        db,
        "select stop_arrivals, on_time_share"
        " from gold.mart_route_reliability"
        " where route_id = 'T1R' and service_date = '2026-11-18'",
    ) == [(3, 1.0)]


def test_a_train_no_timetable_lists_is_counted_apart(db) -> None:
    assert query(
        db,
        "select mode, realtime_trips, matched_trips, unlisted_trips"
        " from gold.dq_trip_match",
    ) == [("train", 1, 1, 1)]
