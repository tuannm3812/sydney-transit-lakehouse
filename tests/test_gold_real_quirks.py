"""Gold on two quirks found on real hours (R3-01, D-047).

1. TfNSW can publish a day's trips only during that day: the 22 Nov
   2026 ferries were in no timetable fetched before the day started.
   The service day must take the version that carries its trips.
2. The metro, light rail and ferry feeds keep passed stops listed until
   the trip leaves the feed, so the stop count never shrinks. A trip
   that ran to its end must still read as ran, and one that left before
   its first stop as partial.
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
# Fetched before the day: the day's trips are not in it yet.
EARLY = {"OLD1": ["08:00:00", "08:10:00", "08:20:00"]}
# Fetched during the day (09:30 Sydney), with the day's trips.
DURING = {
    "K1": ["08:00:00", "08:10:00", "08:20:00"],  # runs to the end
    "K2": ["08:05:00", "08:15:00", "08:25:00"],  # leaves before stop 1
}


def at(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return ORIGIN + timedelta(hours=h, minutes=m, seconds=s)


def listed_until(trip: str) -> datetime:
    if trip == "K1":
        return at("08:20:30")
    return at("08:03:00")


def tu_snapshot(t: datetime) -> bytes:
    """Every stop of a trip stays listed, passed or not, until it leaves."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    for trip, times in DURING.items():
        if not at(times[0]) - timedelta(minutes=30) <= t < listed_until(trip):
            continue
        ent = msg.entity.add()
        ent.id = trip
        tu = ent.trip_update
        tu.trip.trip_id = trip
        tu.trip.start_date = "20261118"
        for i, hms in enumerate(times, start=1):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = f"S{i}"
            stu.arrival.delay = 0
            stu.arrival.time = int(at(hms).timestamp())
    return msg.SerializeToString()


@pytest.fixture(scope="module")
def gold_db(tmp_path_factory) -> Path:
    data_dir = tmp_path_factory.mktemp("quirks") / "data"
    lander = Lander(data_dir)
    for fetched, times in (
        (datetime(2026, 11, 17, 7, 45, tzinfo=UTC), EARLY),
        (datetime(2026, 11, 17, 22, 30, tzinfo=UTC), DURING),
    ):
        lander.land("static_gtfs", fetched, gtfs_zip(times), compress=False)
    t = datetime(2026, 11, 17, 20, 15, tzinfo=UTC)
    while t <= datetime(2026, 11, 17, 22, 0, tzinfo=UTC):
        lander.land(TU_FEED.name, t, tu_snapshot(t))
        t += timedelta(seconds=60)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [TU_FEED])
    assert build(data_dir, None, {"outcome_grace_minutes": 20}, None) == 0
    return gold_paths(data_dir)["db"]


def query(db: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_service_day_takes_the_version_with_its_trips(gold_db: Path) -> None:
    [(version, fallback)] = query(
        gold_db,
        "select version, version_fallback from staging.stg_service_day"
        " where service_date = date '2026-11-18'",
    )
    assert version.startswith("20261117T223000")
    assert not fallback


def test_a_feed_that_keeps_passed_stops(gold_db: Path) -> None:
    got = query(
        gold_db,
        "select trip_id, outcome, min_stops_left from gold.fct_trip_outcome"
        " where service_date = date '2026-11-18' order by trip_id",
    )
    assert got == [("K1", "ran", 0), ("K2", "partial", 3)]
