"""A run of stops capped at one instant is not an arrival (D-054).

On 23 Nov 2026 a bus's trip update could list several passed stops with
a stale, late prediction, then drop them all in one poll. Each stop's
observed time was capped at that poll, so a run of stops shared one
instant, and the gaps to the next bus at those stops were made up.

C1 (08:00) carries a 30-minute-late prediction for S1 to S5 and drops
them all at 08:09; D1 (08:06) runs on time. C1's five capped stops keep
their arrival rows but are not times: no headway uses them. A trip that
drops one stop at a time keeps every arrival.
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

TU_FEED = Feed("buses_tripupdates", "trip_updates", "https://x/tu", 30)
# Wednesday 18 Nov 2026 starts at 13:00 UTC on 17 Nov (AEDT).
ORIGIN = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
STOPS = 6
FIRST = {"C1": "08:00", "D1": "08:06"}
CAPPED_AT = "08:09"  # C1 drops S1 to S5 here
STALE_S = 1800


def at(hm: str, plus_min: float = 0) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return ORIGIN + timedelta(hours=h, minutes=m + plus_min)


def sched(trip: str, seq: int) -> datetime:
    return at(FIRST[trip], 2 * (seq - 1))


def gtfs() -> bytes:
    def hms(t: datetime) -> str:
        s = int((t - ORIGIN).total_seconds())
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "A1,Test Buses,https://x,Australia/Sydney\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_type\n"
        "R1,A1,333,700\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(
            f"S{i},Stop {i},-33.87,{151.20 + 0.01 * i}\n"
            for i in range(1, STOPS + 1)
        ),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,"
        "friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20261101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\n"
        + "".join(f"R1,WK,{t},0\n" for t in FIRST),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
        "stop_sequence\n"
        + "".join(
            f"{t},{hms(sched(t, i))},{hms(sched(t, i))},S{i},{i}\n"
            for t in FIRST
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


def listed(trip: str, t: datetime) -> list[int]:
    """Stops still listed at poll time t."""
    if trip == "C1":
        return list(range(1, STOPS + 1)) if t < at(CAPPED_AT) else [STOPS]
    # D1 drops each stop 30 s after its scheduled time
    return [
        i
        for i in range(1, STOPS + 1)
        if t < sched("D1", i) + timedelta(seconds=30)
    ]


def snapshot(t: datetime) -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    for trip in FIRST:
        if t < at(FIRST[trip], -20):
            continue
        stops = listed(trip, t)
        if not stops or t >= at(FIRST[trip], 2 * STOPS + 2):
            continue
        ent = msg.entity.add()
        ent.id = trip
        tu = ent.trip_update
        tu.trip.trip_id = trip
        tu.trip.start_date = "20261118"
        late = STALE_S if trip == "C1" and t < at(CAPPED_AT) else 0
        for i in stops:
            stu = tu.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = f"S{i}"
            stu.arrival.delay = late
            stu.arrival.time = int(sched(trip, i).timestamp()) + late
    return msg.SerializeToString()


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("capped") / "data"
    lander = Lander(data_dir)
    lander.land(
        "static_gtfs",
        datetime(2026, 11, 17, 7, 45, tzinfo=UTC),
        gtfs(),
        compress=False,
    )
    t = at("07:30")
    while t <= at("08:40"):
        lander.land(TU_FEED.name, t, snapshot(t))
        t += timedelta(seconds=30)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [TU_FEED])
    build(data_dir, None, {"outcome_grace_minutes": 20}, None)
    db = gold_paths(data_dir)["db"]
    # 5 of C1's 12 arrivals are capped, which the before-exit quality
    # test rightly flags; nothing else may fail.
    failed = query(
        db,
        "select node from gold.dq_results"
        " where status not in ('pass', 'success')",
    )
    assert [n for (n,) in failed if "before_exit" not in n] == []
    return db


def query(db, sql: str) -> list[tuple]:
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_a_capped_run_of_stops_has_no_arrival_time(db) -> None:
    rows = query(
        db,
        "select trip_id, stop_sequence, method, time_known"
        " from gold.fct_stop_arrival order by trip_id, stop_sequence",
    )
    capped = [r for r in rows if r[0] == "C1" and r[1] < STOPS]
    assert [r[2:] for r in capped] == [("capped_at_exit", False)] * 5
    assert all(r[3] for r in rows if r[0] == "D1")


def test_headways_skip_the_capped_stops(db) -> None:
    rows = query(
        db,
        "select stop_sequence from gold.fct_headway"
        " where 'C1' in (trip_id, prev_trip_id) order by 1",
    )
    assert all(seq == STOPS for (seq,) in rows)
