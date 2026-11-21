"""Gold layer: silver views, dq results and a dbt build end to end."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from google.transit import gtfs_realtime_pb2

from tests.test_timetable import gtfs_zip
from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.gold import dq
from transit_lakehouse.gold.__main__ import build, gold_paths
from transit_lakehouse.gold.views import refresh_views, view_sql
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import ensure_tables, open_catalog
from transit_lakehouse.worker.timetable import run_timetable

TU_FEED = Feed("buses_tripupdates", "trip_updates", "https://x/tu", 30)
# Wednesday 18 Nov 2026: the GTFS clock starts at local midnight, which
# is 13:00 UTC on 17 Nov (AEDT, UTC+11).
ORIGIN = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
TIMETABLE = {
    "T1": ["08:00:00", "08:10:00", "08:20:00"],
    "T2": ["08:02:00", "08:11:00", "08:22:00"],
    "T3": ["08:30:00", "08:40:00", "08:50:00"],  # never in the feed
    # Timetabled past 24:00 of Tuesday's service day; TfNSW dates it by
    # the calendar day it runs on, Wednesday 20261118.
    "T4": ["32:05:00", "32:15:00", "32:25:00"],
}
LIVE = ("T1", "T2", "T4")


def at(hms: str, trip: str = "T1") -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    origin = ORIGIN - timedelta(days=1) if trip == "T4" else ORIGIN
    return origin + timedelta(hours=h, minutes=m, seconds=s)


def delay_of(trip: str, t: datetime) -> int:
    if trip == "T1":
        return 120
    if trip == "T4":
        return 0
    # T2 runs on time, then the whole trip slips a minute at 21:05 UTC:
    # under D-028 that is one trip_status row, not a row per stop.
    return 60 if t >= datetime(2026, 11, 17, 21, 5, tzinfo=UTC) else 0


def tu_snapshot(t: datetime) -> bytes:
    """Trips T1 and T2 with only their stops not yet passed."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    for trip in LIVE:
        delay = delay_of(trip, t)
        left = [
            (i, at(hms, trip) + timedelta(seconds=delay))
            for i, hms in enumerate(TIMETABLE[trip], start=1)
            if t < at(hms, trip) + timedelta(seconds=delay + 15)
        ]
        if not left:
            continue  # a finished trip leaves the feed
        ent = msg.entity.add()
        ent.id = trip
        tu = ent.trip_update
        tu.trip.trip_id = trip
        tu.trip.start_date = "20261118"
        tu.trip.route_id = "A1_R1"  # realtime route ids differ
        for i, when in left:
            stu = tu.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = f"S{i}"
            stu.arrival.delay = delay
            stu.arrival.time = int(when.timestamp())
    return msg.SerializeToString()


@pytest.fixture(scope="module")
def gold_db(tmp_path_factory) -> Path:
    data_dir = tmp_path_factory.mktemp("gold") / "data"
    lander = Lander(data_dir)
    lander.land(
        "static_gtfs",
        datetime(2026, 11, 17, 7, 45, tzinfo=UTC),
        gtfs_zip(TIMETABLE),
        compress=False,
    )
    t = datetime(2026, 11, 17, 20, 45, tzinfo=UTC)
    while t <= datetime(2026, 11, 17, 22, 15, tzinfo=UTC):
        lander.land(TU_FEED.name, t, tu_snapshot(t))
        t += timedelta(seconds=60)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [TU_FEED])
    assert build(data_dir, None, {"outcome_grace_minutes": 20}, None) == 0
    return gold_paths(data_dir)["db"]


def query(db: Path, sql: str) -> list[tuple]:
    # Not read_only: dbt-duckdb keeps its own connection open in this
    # process, and DuckDB refuses a second one with other settings.
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_stop_arrivals_rebuild_the_shifted_prediction(gold_db: Path) -> None:
    got = query(
        gold_db,
        "select trip_id, stop_sequence, delay_s, observed_arrival_utc,"
        " schedule_disagreement_s, service_date::varchar"
        " from gold.fct_stop_arrival order by all",
    )
    # T2's stop 2 has no stop row after the 21:05 slip: its 60 s comes
    # from the trip delay. Each trip's last stop is never seen to drop.
    assert [(r[0], r[1], r[2]) for r in got] == [
        ("T1", 1, 120),
        ("T1", 2, 120),
        ("T2", 1, 0),
        ("T2", 2, 60),
        ("T4", 1, 0),
        ("T4", 2, 0),
    ]
    assert got[3][3] == at("08:12:00").astimezone(got[3][3].tzinfo)
    # T4 is matched to Tuesday's service day, so its times agree.
    assert got[4][5] == "2026-11-17"
    assert {r[4] for r in got} == {0}


def test_trip_outcomes(gold_db: Path) -> None:
    got = query(
        gold_db,
        "select service_date::varchar, trip_id, outcome, confidence,"
        " region, mode, route_id from gold.fct_trip_outcome"
        " where outcome <> 'not_judged' order by all",
    )
    assert got == [
        ("2026-11-17", "T4", "ran", "high", "sydney", "bus", "R1"),
        ("2026-11-18", "T1", "ran", "high", "sydney", "bus", "R1"),
        ("2026-11-18", "T2", "ran", "high", "sydney", "bus", "R1"),
        # no vehicle-position feed captured, so not "high"
        ("2026-11-18", "T3", "never_seen", "medium", "sydney", "bus", "R1"),
    ]
    # Every other scheduled trip of the two days is outside capture.
    [(n,)] = query(
        gold_db,
        "select count(*) from gold.fct_trip_outcome"
        " where outcome = 'not_judged'",
    )
    assert n == 4


def test_headway_and_route_mart(gold_db: Path) -> None:
    [(prev, trip, observed, scheduled, bunched)] = query(
        gold_db,
        "select prev_trip_id, trip_id, observed_headway_s,"
        " scheduled_headway_s, bunched from gold.fct_headway"
        " where stop_id = 'S2'",
    )
    # T1 (2 min late) and T2 (1 min late) reach S2 together, 1 min
    # apart on the timetable: bunched.
    assert (prev, trip, observed, scheduled, bunched) == (
        "T1",
        "T2",
        0,
        60,
        True,
    )
    [row] = query(
        gold_db,
        "select trips_scheduled, trips_covered, trips_ran,"
        " trips_never_seen, never_seen_share, stop_arrivals"
        " from gold.mart_route_reliability"
        " where service_date = '2026-11-18' and local_hour = 8",
    )
    # T4 also runs on Wednesday's service day, at 08:05 on Thursday:
    # scheduled, but outside capture.
    assert row == (4, 3, 2, 1, pytest.approx(1 / 3), 4)


def test_quality_tables_and_results(gold_db: Path) -> None:
    shares = query(gold_db, "select match_share from gold.dq_trip_match")
    assert shares == [(1.0,), (1.0,)]
    statuses = dict(
        query(
            gold_db,
            "select node, status from gold.dq_results"
            " where resource_type = 'test'",
        )
    )
    assert statuses and set(statuses.values()) == {"pass"}


def test_view_over_an_empty_table_keeps_its_schema(tmp_path: Path) -> None:
    catalog = open_catalog(tmp_path, env={})
    ensure_tables(catalog)
    with duckdb.connect() as con:
        views = refresh_views(con, catalog)
        assert {v.name for v in views} >= {"silver.trip_status"}
        cols = con.execute("describe silver.trip_status").fetchall()
        assert ("fetched_at", "TIMESTAMP WITH TIME ZONE") in [
            (c[0], c[1]) for c in cols
        ]
        assert con.execute("select count(*) from silver.alert").fetchone() == (
            0,
        )


def test_view_sql_quotes_paths() -> None:
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    schema = Schema(NestedField(1, "trip_id", StringType(), required=False))
    sql = view_sql("silver.t", schema, ["/a/it's.parquet"])
    assert "'/a/it''s.parquet'" in sql
    assert sql.startswith('CREATE OR REPLACE VIEW "silver"."t"')


def test_dq_record_appends_every_node(tmp_path: Path) -> None:
    results = tmp_path / "run_results.json"
    results.write_text(
        json.dumps(
            {
                "metadata": {"invocation_id": "abc"},
                "results": [
                    {
                        "unique_id": "model.transit_gold.fct_x",
                        "status": "success",
                        "execution_time": 1.5,
                    },
                    {
                        "unique_id": "test.transit_gold.share_at_least_x",
                        "status": "fail",
                        "failures": 2,
                        "message": "Got 2 results",
                        "execution_time": 0.1,
                    },
                ],
            }
        )
    )
    with duckdb.connect() as con:
        assert dq.record(con, results) == 2
        assert dq.record(con, results) == 2
        rows = con.execute(
            "select resource_type, status, failures from gold.dq_results"
            " order by all"
        ).fetchall()
    assert rows == [
        ("model", "success", None),
        ("model", "success", None),
        ("test", "fail", 2),
        ("test", "fail", 2),
    ]
