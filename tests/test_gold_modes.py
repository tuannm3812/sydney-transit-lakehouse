"""Trip outcome keys on mode, and partial needs a covered operator.

Second 25 Nov Antigravity review, finding 3:

1. A trip id two modes share must not mix: the metro feed carrying a
   cancelled trip with the same id and start date as a bus trip that
   ran must not make the bus trip cancelled.
2. A trip seen only in the vehicle positions of an operator that is not
   in the trip-update feed that day is not judged, like a never_seen
   trip of that operator, instead of counting as partial.
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

BUS_TU = Feed("buses_tripupdates", "trip_updates", "https://x/tu", 30)
BUS_VP = Feed("buses_vehiclepos", "vehicle_positions", "https://x/vp", 30)
METRO_TU = Feed("metro_tripupdates", "trip_updates", "https://x/mtu", 30)
# Wednesday 18 Nov 2026 starts at 13:00 UTC on 17 Nov (AEDT).
ORIGIN = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
FIRST_POLL = datetime(2026, 11, 17, 20, 15, tzinfo=UTC)  # 07:15 Sydney
LAST_POLL = datetime(2026, 11, 17, 22, 0, tzinfo=UTC)  # 09:00 Sydney
TIMES = ["08:00:00", "08:10:00", "08:20:00"]
# M1 runs for operator A1; V1 belongs to A2, which has no trip updates.
TRIPS = {"M1": ("R1", TIMES), "V1": ("R2", TIMES)}


def at(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return ORIGIN + timedelta(hours=h, minutes=m, seconds=s)


def gtfs() -> bytes:
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "A1,Covered Buses,https://x,Australia/Sydney\n"
        "A2,Uncovered Buses,https://x,Australia/Sydney\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_type\n"
        "R1,A1,333,700\nR2,A2,444,700\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        "S1,One,-33.87,151.21\nS2,Two,-33.88,151.22\n"
        "S3,Three,-33.89,151.23\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,"
        "friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20261101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\n"
        + "".join(f"{r},WK,{t},0\n" for t, (r, _) in TRIPS.items()),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
        "stop_sequence\n"
        + "".join(
            f"{t},{hms},{hms},S{i},{i}\n"
            for t, (_, stops) in TRIPS.items()
            for i, hms in enumerate(stops, start=1)
        ),
        "feed_info.txt": "feed_publisher_name,feed_version,"
        "feed_start_date,feed_end_date\nTfNSW,v1,20261117,20261231\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def message(t: datetime) -> gtfs_realtime_pb2.FeedMessage:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    return msg


def bus_tu(t: datetime) -> bytes:
    """M1 runs to its end; V1's operator is not in the feed."""
    msg = message(t)
    if at("07:30:00") <= t < at("08:20:30"):
        ent = msg.entity.add()
        ent.id = "M1"
        ent.trip_update.trip.trip_id = "M1"
        ent.trip_update.trip.start_date = "20261118"
        for i, hms in enumerate(TIMES, start=1):
            stu = ent.trip_update.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = f"S{i}"
            stu.arrival.time = int(at(hms).timestamp())
    return msg.SerializeToString()


def bus_vp(t: datetime) -> bytes:
    """V1 is tracked in positions only, until halfway along."""
    msg = message(t)
    if at("08:00:00") <= t < at("08:10:00"):
        ent = msg.entity.add()
        ent.id = "V1"
        ent.vehicle.trip.trip_id = "V1"
        ent.vehicle.trip.start_date = "20261118"
        ent.vehicle.vehicle.id = "bus-v1"
        ent.vehicle.position.latitude = -33.875
        ent.vehicle.position.longitude = 151.215
        ent.vehicle.timestamp = int(t.timestamp())
    return msg.SerializeToString()


def metro_tu(t: datetime) -> bytes:
    """A metro trip with M1's id and date, cancelled after the bus ran.

    trip_status is change-only, so the cancellation is the latest state
    row of that id and date in any mode.
    """
    msg = message(t)
    ent = msg.entity.add()
    ent.id = "M1"
    ent.trip_update.trip.trip_id = "M1"
    ent.trip_update.trip.start_date = "20261118"
    if t >= at("08:30:00"):
        ent.trip_update.trip.schedule_relationship = (
            gtfs_realtime_pb2.TripDescriptor.CANCELED
        )
    return msg.SerializeToString()


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict[str, tuple]:
    data_dir = tmp_path_factory.mktemp("modes") / "data"
    lander = Lander(data_dir)
    lander.land(
        "static_gtfs",
        datetime(2026, 11, 17, 7, 45, tzinfo=UTC),
        gtfs(),
        compress=False,
    )
    t = FIRST_POLL
    while t <= LAST_POLL:
        lander.land(BUS_TU.name, t, bus_tu(t))
        lander.land(BUS_VP.name, t, bus_vp(t))
        lander.land(METRO_TU.name, t, metro_tu(t))
        t += timedelta(seconds=60)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [BUS_TU, BUS_VP, METRO_TU])
    assert build(data_dir, None, {"outcome_grace_minutes": 20}, None) == 0
    with duckdb.connect(str(gold_paths(data_dir)["db"])) as con:
        rows = con.execute(
            "select trip_id, outcome, operator_covered, positions"
            " from gold.fct_trip_outcome"
            " where service_date = date '2026-11-18'"
        ).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_a_trip_id_shared_with_another_mode_does_not_mix(outcomes) -> None:
    assert outcomes["M1"][:2] == ("ran", True)


def test_positions_only_trip_of_an_uncovered_operator_is_not_judged(
    outcomes,
) -> None:
    outcome, covered, positions = outcomes["V1"]
    assert positions > 0  # the scenario: seen in positions only
    assert (outcome, covered) == ("not_judged", False)
