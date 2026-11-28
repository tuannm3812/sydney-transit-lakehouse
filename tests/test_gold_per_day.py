"""A windowed build replaces its own service days and keeps the rest.

The nightly build (`gold build --day`, D-060) reads one day of silver and
rebuilds only that day in the per-day models, so the history it built
on earlier nights must survive it unchanged, and building a day twice
must not double it. Uses the bus day of test_gold_capped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from tests.test_gold_capped import TU_FEED, at, gtfs, snapshot
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.gold.__main__ import build_days, gold_paths
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import open_catalog
from transit_lakehouse.worker.timetable import run_timetable

DAY = date(2026, 11, 18)
PER_DAY = ("fct_trip_outcome", "fct_stop_arrival", "dq_trip_match")


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("per_day") / "data"
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
    return data_dir


def counts(data_dir) -> dict[str, list[tuple]]:
    db = gold_paths(data_dir)["db"]
    with duckdb.connect(str(db)) as con:
        out = {
            m: con.execute(
                f"select service_date::varchar, count(*) from gold.{m}"  # noqa: S608
                " group by 1 order by 1"
            ).fetchall()
            for m in PER_DAY
        }
        # the hot spots read every day built, not the latest window
        out["mart_bunching_hotspot"] = con.execute(
            "select sum(headways), max(service_days)"
            " from gold.mart_bunching_hotspot"
        ).fetchall()
    return out


def test_a_later_day_keeps_the_earlier_one(data_dir) -> None:
    grace = {"outcome_grace_minutes": 20}
    build_days(data_dir, [DAY], grace, None)
    first = counts(data_dir)
    assert all(
        rows and rows[0][0] == "2026-11-18"
        for m, rows in first.items()
        if m in PER_DAY
    )
    assert first["mart_bunching_hotspot"][0][0] > 0
    # the next two nights: days with no data of their own (the second
    # one's window no longer reaches the first day at all)
    build_days(
        data_dir,
        [DAY + timedelta(days=1), DAY + timedelta(days=2)],
        grace,
        None,
    )
    assert counts(data_dir) == first
    # the same day again replaces it rather than adding to it
    build_days(data_dir, [DAY], grace, None)
    assert counts(data_dir) == first
    db = gold_paths(data_dir)["db"]
    with duckdb.connect(str(db)) as con:
        windows = con.execute(
            "select distinct service_date_from::varchar,"
            " service_date_to::varchar from gold.dq_results order by 1"
        ).fetchall()
    assert windows == [
        ("2026-11-18", "2026-11-18"),
        ("2026-11-19", "2026-11-19"),
        ("2026-11-20", "2026-11-20"),
    ]
