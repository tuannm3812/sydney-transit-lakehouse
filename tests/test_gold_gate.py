"""Phase 2 gate check (D-056) on small hand-made gold databases.

Each test starts from two green service days and breaks one thing, so
it shows the check that catches it. The last tests run the gate on a
real dbt build (from tests/test_gold.py) and draft the R2-02 report.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tests.test_gold import gold_db  # noqa: F401  (pytest fixture)
from transit_lakehouse.gold import gate, gate_report
from transit_lakehouse.gold.__main__ import main
from transit_lakehouse.reports.render import parse

D1, D2 = date(2026, 11, 25), date(2026, 11, 26)
MODES = ("bus", "ferry")


def _origin(day: date) -> datetime:
    # Sydney local midnight in UTC (AEDT, UTC+11).
    return datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(
        hours=11
    )


def make_db(path: Path, days: tuple[date, ...] = (D1, D2)) -> Path:
    """Gold, staging and bronze tables for green days of two modes.

    Each mode and day has 40 trips of operator A (covered) and 10 of
    operator Q (no realtime, so not_judged under D-038).
    """
    con = duckdb.connect(str(path))
    for schema in ("gold", "staging", "bronze"):
        con.execute(f"create schema {schema}")
    con.execute(
        """
        create table gold.fct_trip_outcome (
            service_date date, trip_id varchar, mode varchar,
            agency_id varchar, sched_start_utc timestamptz,
            sched_end_utc timestamptz, outcome varchar, seen_tu boolean,
            tu_covered boolean, operator_covered boolean,
            left_feed_at timestamptz)
        """
    )
    con.execute(
        """
        create table staging.stg_rt_trip_state (
            mode varchar, feed varchar, trip_id varchar,
            service_date date, trip_status varchar, stop_count integer,
            fetched_at timestamptz)
        """
    )
    con.execute(
        """
        create table bronze.snapshot (
            feed varchar, mode varchar, kind varchar,
            fetched_at timestamptz, entities integer)
        """
    )
    con.execute(
        """
        create table staging.stg_capture_window (
            feed varchar, mode varchar, kind varchar, stretch bigint,
            covered_from timestamptz, covered_to timestamptz)
        """
    )
    first = _origin(days[0])
    last = _origin(days[-1]) + timedelta(days=1, hours=8)
    for mode in MODES:
        feed = f"{mode}_tripupdates"
        con.execute(
            "insert into staging.stg_capture_window values"
            " (?, ?, 'trip_updates', 1, ?, ?)",
            [feed, mode, first, last],
        )
        for day in days:
            o = _origin(day)
            for i in range(50):
                agency = "A" if i < 40 else "Q"
                start = o + timedelta(hours=6, minutes=10 * i)
                end = start + timedelta(minutes=40)
                ran = agency == "A"
                left = end + timedelta(minutes=1) if ran else None
                trip = f"{mode}-{day:%d}-{i}"
                con.execute(
                    "insert into gold.fct_trip_outcome values"
                    " (?, ?, ?, ?, ?, ?, ?, ?, true, ?, ?)",
                    [
                        day,
                        trip,
                        mode,
                        agency,
                        start,
                        end,
                        "ran" if ran else "not_judged",
                        ran,
                        ran,
                        left,
                    ],
                )
                if ran:
                    con.execute(
                        "insert into staging.stg_rt_trip_state values"
                        " (?, ?, ?, ?, 'REMOVED', 0, ?)",
                        [mode, feed, trip, day, left],
                    )
                    con.execute(
                        "insert into bronze.snapshot values"
                        " (?, ?, 'trip_updates', ?, 30)",
                        [feed, mode, left - timedelta(seconds=30)],
                    )
                    con.execute(
                        "insert into bronze.snapshot values"
                        " (?, ?, 'trip_updates', ?, 29)",
                        [feed, mode, left],
                    )
    remart(con)
    con.execute(
        """
        create table gold.dq_trip_match as
        select service_date, mode, count(*) filter (seen_tu)
            as realtime_trips, 1.0 as match_share
        from gold.fct_trip_outcome group by all
        """
    )
    con.execute(
        """
        create table gold.dq_stop_arrival as
        select service_date, mode, 1.0 as delay_sane_share,
            0.99 as schedule_agree_share, 1.0 as before_exit_share
        from gold.dq_trip_match
        """
    )
    con.execute(
        """
        create table gold.dq_arrival_position as
        select service_date, mode, 0.95 as within_150m_share
        from gold.dq_trip_match
        """
    )
    con.execute(
        """
        create table gold.dq_position_plausibility as
        select service_date as obs_date, mode, 0.999 as plausible_share
        from gold.dq_trip_match
        """
    )
    con.execute(
        """
        create table gold.dq_results (
            run_id varchar, run_at timestamptz, service_date_from date,
            service_date_to date, node varchar, resource_type varchar,
            status varchar, failures bigint, message varchar,
            seconds double)
        """
    )
    run_at = last + timedelta(hours=1)
    for node, kind, status in [
        ("model.transit_gold.fct_trip_outcome", "model", "success"),
        ("not_null_fct_stop_arrival_sched_arrival_utc.ab12", "test", "pass"),
        (
            "share_at_least_dq_trip_match_match_share__0_95.cd34",
            "test",
            "pass",
        ),
    ]:
        con.execute(
            "insert into gold.dq_results values"
            " ('run-1', ?, null, null, ?, ?, ?, 0, null, 1.0)",
            [run_at, node, kind, status],
        )
    con.close()
    return path


def remart(con: duckdb.DuckDBPyConnection) -> None:
    """The route mart's day totals, as the dbt model counts them."""
    con.execute(
        """
        create or replace table gold.mart_route_reliability as
        select service_date, mode,
            count(*) filter (tu_covered and operator_covered)
                as trips_covered,
            count(*) filter (outcome = 'never_seen') as trips_never_seen
        from gold.fct_trip_outcome group by all
        """
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return make_db(tmp_path / "transit.duckdb")


def run(db: Path, **kw) -> dict:
    with duckdb.connect(str(db), read_only=True) as con:
        return gate.run_gate(con, **kw)


def failed(result: dict) -> set[tuple[str, str, str | None]]:
    return {
        (d["service_date"], c["check"], c["mode"])
        for d in result["days"]
        for c in d["checks"]
        if c["status"] == "fail"
    }


def sql(db: Path, *statements: str) -> None:
    with duckdb.connect(str(db)) as con:
        for s in statements:
            con.execute(s)


def test_bars_come_from_the_quality_schema() -> None:
    bars = {(b.model, b.column): b.threshold for b in gate.quality_bars()}
    assert bars[("dq_trip_match", "match_share")] == 0.95
    assert bars[("dq_arrival_position", "within_150m_share")] == 0.9
    assert len(bars) == 6


def test_two_green_days_are_a_run_of_two(db: Path) -> None:
    res = run(db)
    assert [d["status"] for d in res["days"]] == ["green", "green"]
    assert failed(res) == set()
    assert res["modes"] == ["bus", "ferry"]
    assert res["streak_days"] == 2 and res["streak_start"] == "2026-11-25"
    assert not res["passed"]
    assert res["earliest_pass_day"] == "2026-12-01"
    assert run(db, days_needed=2)["passed"]


def test_a_day_whose_grace_has_not_passed_is_open(db: Path) -> None:
    sql(
        db,
        "update staging.stg_capture_window"
        " set covered_to = timestamptz '2026-11-26 00:00:00+00'",
    )
    res = run(db)
    assert [d["status"] for d in res["days"]] == ["green", "open"]
    assert res["streak_days"] == 1


def test_a_red_day_restarts_the_run(tmp_path: Path) -> None:
    db = make_db(tmp_path / "transit.duckdb", (D1, D2, D2 + timedelta(days=1)))
    sql(
        db,
        "update gold.dq_trip_match set match_share = 0.9"
        " where service_date = '2026-11-26' and mode = 'ferry'",
    )
    res = run(db)
    assert [d["status"] for d in res["days"]] == ["green", "red", "green"]
    assert failed(res) == {
        ("2026-11-26", "bar:dq_trip_match.match_share", "ferry")
    }
    assert res["streak_days"] == 1 and res["streak_start"] == "2026-11-27"


def test_a_failing_share_test_does_not_fail_the_build(db: Path) -> None:
    # The whole-build test fails when any day breaks the bar; the gate
    # judges that bar per day instead, so only a structural test counts.
    sql(
        db,
        "update gold.dq_results set status = 'fail'"
        " where node like 'share_at_least%'",
    )
    assert failed(run(db)) == set()
    sql(
        db,
        "update gold.dq_results set status = 'fail'"
        " where node like 'not_null%'",
    )
    assert ("2026-11-25", "build", None) in failed(run(db))


def test_a_build_before_the_day_closed_does_not_count(db: Path) -> None:
    sql(
        db,
        "update gold.dq_results"
        " set run_at = timestamptz '2026-11-26 05:00:00+00'",
    )
    res = run(db)
    assert ("2026-11-26", "build", None) in failed(res)
    assert ("2026-11-25", "build", None) not in failed(res)


def test_a_windowed_build_covers_only_its_days(db: Path) -> None:
    sql(
        db,
        "update gold.dq_results set service_date_from = '2026-11-26',"
        " service_date_to = '2026-11-26'",
    )
    assert ("2026-11-25", "build", None) in failed(run(db))


def test_a_missing_mode_fails_the_day(db: Path) -> None:
    sql(
        db,
        "delete from gold.dq_trip_match"
        " where service_date = '2026-11-26' and mode = 'ferry'",
    )
    assert ("2026-11-26", "modes", "ferry") in failed(run(db))
    # Asked for a mode the lakehouse never had: every day fails.
    res = run(db, modes=["bus", "ferry", "metro"])
    assert ("2026-11-25", "modes", "metro") in failed(res)


def test_a_capture_gap_fails_the_capture_check(db: Path) -> None:
    sql(
        db,
        "update gold.fct_trip_outcome set tu_covered = false,"
        " outcome = 'not_judged'"
        " where service_date = '2026-11-25' and mode = 'bus'"
        " and trip_id in ('bus-25-0', 'bus-25-1', 'bus-25-2')",
    )
    with duckdb.connect(str(db)) as con:
        remart(con)
    res = run(db)
    assert failed(res) == {("2026-11-25", "capture", "bus")}
    check = next(
        c
        for c in res["days"][0]["checks"]
        if c["check"] == "capture" and c["mode"] == "bus"
    )
    assert check["value"] == pytest.approx(47 / 50)


def test_an_uncovered_operator_judged_a_ghost_fails(db: Path) -> None:
    # D-038: an operator with no realtime that day is never a ghost.
    sql(
        db,
        "update gold.fct_trip_outcome set outcome = 'never_seen'"
        " where trip_id = 'bus-25-45'",
        "update gold.mart_route_reliability set trips_never_seen = 1"
        " where service_date = '2026-11-25' and mode = 'bus'",
    )
    assert failed(run(db)) == {("2026-11-25", "operators:rule", "bus")}


def test_the_mart_must_agree_with_the_outcomes(db: Path) -> None:
    sql(
        db,
        "update gold.mart_route_reliability set trips_covered = 50"
        " where service_date = '2026-11-26' and mode = 'ferry'",
    )
    assert failed(run(db)) == {("2026-11-26", "operators:mart", "ferry")}


def test_a_mode_with_no_covered_operator_fails(db: Path) -> None:
    sql(
        db,
        "update gold.fct_trip_outcome set operator_covered = false,"
        " outcome = 'not_judged'"
        " where service_date = '2026-11-26' and mode = 'ferry'",
        "update gold.mart_route_reliability set trips_covered = 0"
        " where service_date = '2026-11-26' and mode = 'ferry'",
    )
    res = run(db)
    assert ("2026-11-26", "operators:covered", "ferry") in failed(res)


def test_an_operator_that_drops_out_is_a_warning(db: Path) -> None:
    # Operator B runs 25 bus trips a day; on the 26th it is not in the
    # feed, so D-038 leaves its trips not_judged. The day stays green
    # but says so.
    first_25 = "cast(split_part(trip_id, '-', 3) as int) < 25"
    sql(
        db,
        f"update gold.fct_trip_outcome set agency_id = 'B'"
        f" where mode = 'bus' and {first_25}",
        f"update gold.fct_trip_outcome set operator_covered = false,"
        f" outcome = 'not_judged', seen_tu = false"
        f" where mode = 'bus' and service_date = '2026-11-26'"
        f" and {first_25}",
        "update gold.mart_route_reliability set trips_covered = 15"
        " where mode = 'bus' and service_date = '2026-11-26'",
    )
    res = run(db)
    day2 = res["days"][1]
    warns = [c for c in day2["checks"] if c["status"] == "warn"]
    assert [(c["check"], c["mode"], c["value"]) for c in warns] == [
        ("operators:dropped", "bus", 25)
    ]
    assert "operator B" in warns[0]["detail"]
    assert day2["status"] == "green"  # a warning does not fail the day


def test_trip_left_rows_must_exist_and_be_empty(db: Path) -> None:
    sql(
        db,
        "update staging.stg_rt_trip_state set stop_count = 2"
        " where trip_id = 'bus-25-3'",
    )
    assert failed(run(db)) == {("2026-11-25", "trip_left:rows", "bus")}
    sql(
        db,
        "delete from staging.stg_rt_trip_state"
        " where service_date = '2026-11-26' and mode = 'ferry'",
    )
    assert ("2026-11-26", "trip_left:rows", "ferry") in failed(run(db))


def test_trips_that_never_left_fail_the_share(db: Path) -> None:
    sql(
        db,
        "update gold.fct_trip_outcome set left_feed_at = null"
        " where service_date = '2026-11-25' and mode = 'ferry'"
        " and trip_id < 'ferry-25-2'",
    )
    res = run(db)
    # 12 of 40 trips seen to run never left: 70% < 90%.
    assert failed(res) == {("2026-11-25", "trip_left:share", "ferry")}


def test_one_snapshot_ending_most_trips_fails(db: Path) -> None:
    # A bad file that ends every trip (what D-036 and D-049 prevent).
    t = datetime(2026, 11, 25, 3, 0, tzinfo=UTC)
    with duckdb.connect(str(db)) as con:
        con.execute(
            "insert into bronze.snapshot values"
            " ('bus_tripupdates', 'bus', 'trip_updates', ?, 200)",
            [t],
        )
        con.execute(
            "insert into bronze.snapshot values"
            " ('bus_tripupdates', 'bus', 'trip_updates', ?, 0)",
            [t + timedelta(seconds=30)],
        )
        for i in range(150):
            con.execute(
                "insert into staging.stg_rt_trip_state values"
                " ('bus', 'bus_tripupdates', ?, '2026-11-25',"
                " 'REMOVED', 0, ?)",
                [f"x{i}", t + timedelta(seconds=30)],
            )
    assert failed(run(db)) == {("2026-11-25", "trip_left:burst", "bus")}


def test_since_and_until_limit_the_days(db: Path) -> None:
    res = run(db, since=D2)
    assert [d["service_date"] for d in res["days"]] == ["2026-11-26"]
    res = run(db, until=D1)
    assert [d["service_date"] for d in res["days"]] == ["2026-11-25"]


def test_cli_exit_code_and_json(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "gold").mkdir(parents=True)
    make_db(data / "gold" / "transit.duckdb")
    out = tmp_path / "gate.json"
    args = ["--data-dir", str(data), "gate", "--json", str(out)]
    assert main(args) == 1  # 2 of 7 days
    assert json.loads(out.read_text())["streak_days"] == 2
    assert main([*args, "--days", "2"]) == 0


def test_gate_report_follows_the_report_standard(db: Path) -> None:
    sql(
        db,
        "update gold.dq_trip_match set match_share = 0.9"
        " where service_date = '2026-11-25' and mode = 'ferry'",
    )
    res = json.loads(json.dumps(run(db), default=str))
    text = gate_report.build(res, "abc1234", "2026-12-09", "CI")
    rep = parse(text)  # enforces the report standard
    assert rep.meta["id"] == "R2-02"
    assert rep.meta["verdict"] == "Fail"
    assert rep.meta["status"] == "Draft"
    assert "| Wed 25 Nov | " in text and "| Fail |" in text
    assert "| `dq_trip_match.match_share` | Ferries | 95% | 90.00%" in text
    assert "1 of 7 green service days" in text

    passing = json.loads(json.dumps(run(db, days_needed=1), default=str))
    rep = parse(gate_report.build(passing, "abc1234", "2026-12-09", "CI"))
    assert rep.meta["verdict"] == "Pass"


def test_gate_on_a_real_dbt_build(gold_db: Path) -> None:  # noqa: F811
    # dbt-duckdb keeps a connection open in this process, so no
    # read_only connection here (see tests/test_gold.py).
    with duckdb.connect(str(gold_db)) as con:
        res = gate.run_gate(con, grace_minutes=20)
    days = {d["service_date"]: d for d in res["days"]}
    # Tuesday 17 Nov closes inside the capture (its after-midnight trip
    # T4); Wednesday's T4 runs on Thursday, outside it, so it is open.
    assert days["2026-11-18"]["status"] == "open"
    checks = {(c["check"], c["mode"]): c for c in days["2026-11-17"]["checks"]}
    assert checks[("build", None)]["status"] == "pass"
    assert checks[("modes", "bus")]["status"] == "pass"
    assert checks[("operators:rule", "bus")]["status"] == "pass"
    assert checks[("operators:mart", "bus")]["status"] == "pass"
