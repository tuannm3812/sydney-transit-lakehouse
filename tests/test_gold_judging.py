"""Gold trip judging on the two critical findings of the 25 Nov review.

1. Coverage applies to every outcome (D-048): a trip that ran or was
   cancelled before its trip-update feed was covered is not judged,
   like a missing one in the same gap.
2. Stops to go uses the D-028 shift: a late bus whose stops all move by
   the same delay writes only a new trip delay, so its stored stop times
   are stale. Read unshifted, a late trip that vanished mid-route looks
   like it reached its end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from google.transit import gtfs_realtime_pb2

from tests.test_timetable import gtfs_zip
from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.gold.__main__ import build, gold_paths
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import open_catalog
from transit_lakehouse.worker.timetable import run_timetable

TU_FEED = Feed("buses_tripupdates", "trip_updates", "https://x/tu", 30)
# Wednesday 18 Nov 2026 starts at 13:00 UTC on 17 Nov (AEDT).
ORIGIN = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
# Polls run 07:15 to 09:00 Sydney, so coverage starts at 07:15.
FIRST_POLL = datetime(2026, 11, 17, 20, 15, tzinfo=UTC)
LAST_POLL = datetime(2026, 11, 17, 22, 0, tzinfo=UTC)
TRIPS = {
    "E1": ["07:20:00", "07:30:00", "07:40:00"],  # ran, before coverage
    "C1": ["07:22:00", "07:32:00", "07:42:00"],  # cancelled, same gap
    "R1": ["08:00:00", "08:10:00", "08:20:00"],  # ran, on time
    "L1": ["08:00:00", "08:10:00", "08:20:00"],  # 15 min late, vanished
}
LATE_FROM = "07:50:00"
LATE_S = 900


def at(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return ORIGIN + timedelta(hours=h, minutes=m, seconds=s)


LISTED_UNTIL = {
    "E1": at("07:40:30"),
    "C1": at("07:42:30"),
    "R1": at("08:20:30"),
    "L1": at("08:05:00"),
}


def tu_snapshot(t: datetime) -> bytes:
    """Every stop stays listed, so stops to go comes from predictions."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    for trip, times in TRIPS.items():
        first = at(times[0]) - timedelta(minutes=30)
        if not first <= t < LISTED_UNTIL[trip]:
            continue
        ent = msg.entity.add()
        ent.id = trip
        tu = ent.trip_update
        tu.trip.trip_id = trip
        tu.trip.start_date = "20261118"
        if trip == "C1":
            tu.trip.schedule_relationship = (
                gtfs_realtime_pb2.TripDescriptor.CANCELED
            )
            continue
        delay = LATE_S if trip == "L1" and t >= at(LATE_FROM) else 0
        for i, hms in enumerate(times, start=1):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = f"S{i}"
            stu.arrival.delay = delay
            stu.arrival.time = int(at(hms).timestamp()) + delay
    return msg.SerializeToString()


@pytest.fixture(scope="module")
def gold_db(tmp_path_factory) -> Path:
    data_dir = tmp_path_factory.mktemp("judging") / "data"
    lander = Lander(data_dir)
    lander.land(
        "static_gtfs",
        datetime(2026, 11, 17, 7, 45, tzinfo=UTC),
        gtfs_zip(TRIPS),
        compress=False,
    )
    t = FIRST_POLL
    while t <= LAST_POLL:
        lander.land(TU_FEED.name, t, tu_snapshot(t))
        t += timedelta(seconds=60)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [TU_FEED])
    assert build(data_dir, None, {"outcome_grace_minutes": 20}, None) == 0
    return gold_paths(data_dir)["db"]


def outcomes(db: Path) -> dict[str, tuple]:
    with duckdb.connect(str(db)) as con:
        rows = con.execute(
            "select trip_id, outcome, tu_covered, min_stops_left"
            " from gold.fct_trip_outcome"
            " where service_date = date '2026-11-18'"
        ).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_the_late_trip_really_moved_only_its_trip_delay(
    gold_db: Path,
) -> None:
    """Guards the scenario: after the delay rise no new stop rows."""
    with duckdb.connect(str(gold_db)) as con:
        [(rows,)] = con.execute(
            "select count(*) from silver.stop_prediction"
            " where trip_id = 'L1' and fetched_at >= ?",
            [at(LATE_FROM)],
        ).fetchall()
    assert rows == 0


def test_ran_and_cancelled_outside_coverage_are_not_judged(
    gold_db: Path,
) -> None:
    got = outcomes(gold_db)
    assert got["E1"][:2] == ("not_judged", False)
    assert got["C1"][:2] == ("not_judged", False)
    assert got["R1"][:2] == ("ran", True)


def test_late_trip_that_vanished_is_partial(gold_db: Path) -> None:
    assert outcomes(gold_db)["L1"] == ("partial", True, 3)
