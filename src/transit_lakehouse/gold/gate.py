"""Phase 2 gate check: is the quality page green, day by day (D-057).

    python -m transit_lakehouse.gold gate --since 2026-11-29 \\
        --json docs/reports/gold-gate.json

The Phase 2 gate (docs/11_phase2.md) is the data-quality page green for
7 straight service days, counted from the first full day of every mode
after the worker went live (D-039). This module decides "green" for one
Sydney service day from the gold database alone, read-only, so the same
check runs on the host, on a replayed lakehouse in the cloud and in CI.

A service day is green when every check below passes for it:

``build``
    A dbt build that covers the day, run after the day's data was
    complete, has every model successful and every structural test
    (not null, unique, accepted values) passing. The quality-bar tests
    are judged per day instead, by the next check, because a dbt test
    fails a whole build when any one day breaks its bar.
``bar``
    Each ``share_at_least`` bar in ``models/quality/schema.yml``,
    re-applied to the day's row for each mode. The thresholds are read
    from that file, so the page and the gate cannot disagree (D-035).
``modes``
    Every mode with a trip-update feed in the window has realtime trips
    that day (D-039's "full day of every mode").
``capture``
    At least 95% of each mode's scheduled trips are judged on capture:
    the trip-update feed was captured from before the trip's start to
    the grace after its end (D-035, D-048).
``operators``
    The D-038 rule held: no trip of an operator without realtime that
    day is ``never_seen`` or ``partial``; each mode has at least one
    covered operator; and the route mart counts the same covered and
    never-seen trips as ``fct_trip_outcome``. An operator covered the
    day before but not this day is a warning: it is the case D-038
    turns into ``not_judged``, so a feed that drops an operator would
    otherwise go unnoticed.
``trip_left``
    The D-036 rows are there and sane: each mode has ``REMOVED`` rows,
    every one has stop count 0, at least 90% of the trips seen to run
    have left the feed by the end of their grace, and no single
    snapshot removed more than half of the trips the feed carried (the
    sign of one bad file ending every trip, which D-036 and D-049 rule
    out).

The gate passes when the green days since ``--since`` end in a run of
at least ``--days`` (7) consecutive service days. A red day restarts the
run. Days whose data is not yet complete (the last trip's grace has not
passed in the captured data) are listed as open and do not count either
way. The check writes JSON for ``gold gate-report`` (R2-02).
"""

# SQL is built from fixed model and column names read from the
# package's own schema.yml and from parsed dates, never from data:
# ruff: noqa: S608

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import yaml

from transit_lakehouse.gold.dq import DQ_TABLE

QUALITY_SCHEMA = (
    Path(__file__).with_name("dbt") / "models" / "quality" / "schema.yml"
)
# The day column of each quality model; plausibility is by poll date.
DAY_COLUMN = {"dq_position_plausibility": "obs_date"}
SHARE_TEST = "share_at_least"
GATE_DAYS = 7
CAPTURE_BAR = 0.95
LEFT_FEED_BAR = 0.90
OPERATOR_DROP_MIN_TRIPS = 20
GOOD_STATUSES = {"pass", "success"}
# Bars measured from vehicle positions: expected for modes with that feed.
VP_MODELS = {"dq_position_plausibility"}
# Known gaps that warn instead of failing when a mode has no row.
# A bar measured on a subset of rows, and that subset's size column: a
# share from fewer than MIN_SAMPLE rows warns instead of judging. The
# train feed gives delays, not times, so few train arrivals carry the
# feed's own scheduled time (14 on 26 Nov 2026, D-059).
SAMPLE_COLUMN = {
    ("dq_stop_arrival", "schedule_agree_share"): "schedule_checked",
}
MIN_SAMPLE = 100
BAR_EXEMPT = {
    ("dq_stop_arrival", "lightrail"): "no light rail arrivals yet (O-04)",
    ("dq_arrival_position", "lightrail"): "no light rail arrivals yet (O-04)",
}

PASS, FAIL, WARN = "pass", "fail", "warn"


@dataclass
class Check:
    """One check on one service day, for one mode or for the day."""

    check: str
    mode: str | None
    status: str
    value: float | int | None = None
    bar: float | int | None = None
    detail: str = ""


@dataclass
class Day:
    service_date: date
    status: str  # green, red or open
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]


@dataclass
class Bar:
    model: str
    column: str
    threshold: float


def quality_bars(path: Path = QUALITY_SCHEMA) -> list[Bar]:
    """The ``share_at_least`` bars declared on the quality models."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    bars = []
    for model in doc.get("models", []):
        for col in model.get("columns", []):
            for test in col.get("tests", []) or []:
                if isinstance(test, dict) and SHARE_TEST in test:
                    spec = test[SHARE_TEST]
                    args = spec.get("arguments", spec)
                    bars.append(
                        Bar(model["name"], col["name"], args["threshold"])
                    )
    return bars


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params=()) -> list:
    return con.execute(sql, list(params)).fetchall()


def _relation_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    schema, table = name.split(".")
    return bool(
        _rows(
            con,
            "select 1 from information_schema.tables"
            " where table_schema = ? and table_name = ?",
            (schema, table),
        )
    )


# --- which days ----------------------------------------------------------


def service_days(
    con: duckdb.DuckDBPyConnection, grace_minutes: int
) -> list[tuple[date, bool, datetime]]:
    """Each service day in ``fct_trip_outcome``: (day, complete, end).

    A day is complete when trip updates were captured past its last
    scheduled trip end plus the grace, so every trip could be judged.
    """
    last = _rows(
        con,
        "select max(covered_to) from staging.stg_capture_window"
        " where kind = 'trip_updates'",
    )[0][0]
    days = _rows(
        con,
        "select service_date, max(sched_end_utc) from gold.fct_trip_outcome"
        " group by 1 order by 1",
    )
    grace = timedelta(minutes=grace_minutes)
    return [
        (d, last is not None and last >= end + grace, end + grace)
        for d, end in days
    ]


def feed_modes(con: duckdb.DuckDBPyConnection, kind: str) -> list[str]:
    """Modes with a feed of this kind anywhere in the lakehouse."""
    return [
        m
        for (m,) in _rows(
            con,
            "select distinct mode from bronze.snapshot"
            " where kind = ? and mode is not null order by 1",
            (kind,),
        )
    ]


def trip_update_modes(con: duckdb.DuckDBPyConnection) -> list[str]:
    return feed_modes(con, "trip_updates")


# --- checks --------------------------------------------------------------


def check_build(
    con: duckdb.DuckDBPyConnection,
    day: date,
    complete_at: datetime,
    share_nodes: set[str],
) -> list[Check]:
    """The latest build covering the day, after its data was complete."""
    if not _relation_exists(con, DQ_TABLE):
        return [Check("build", None, FAIL, detail="no dq_results table")]
    # Only a full build counts: a later `gold build --select` run holds
    # a few nodes and must not hide a failed full build.
    models = sorted(model_names())
    marks = ", ".join("?" for _ in models)
    run = _rows(
        con,
        f"select run_id, min(run_at) as run_at from {DQ_TABLE}"
        " where (service_date_from is null or service_date_from <= ?)"
        " and (service_date_to is null or service_date_to >= ?)"
        " and run_at >= ?"
        " group by run_id"
        " having count(distinct node) filter ("
        f"resource_type = 'model' and node in ({marks})) = ?"
        " order by run_at desc limit 1",
        (day, day, complete_at, *models, len(models)),
    )
    if not run:
        return [
            Check(
                "build",
                None,
                FAIL,
                detail="no full build covering this day after its data closed",
            )
        ]
    run_id, run_at = run[0]
    nodes = _rows(
        con,
        f"select node, resource_type, status from {DQ_TABLE}"
        " where run_id = ? order by node",
        (run_id,),
    )
    bad = [
        node
        for node, kind, status in nodes
        if status not in GOOD_STATUSES
        and not (kind == "test" and _is_share_node(node, share_nodes))
    ]
    return [
        Check(
            "build",
            None,
            FAIL if bad else PASS,
            value=len(nodes) - len(bad),
            bar=len(nodes),
            detail=(
                f"run {run_id[:8]} at {run_at:%Y-%m-%d %H:%M} UTC"
                + (f"; failed: {', '.join(bad)}" if bad else "")
            ),
        )
    ]


def model_names(root: Path = QUALITY_SCHEMA.parents[1]) -> set[str]:
    """Every dbt model in the package, as dq_results names them."""
    return {p.stem for p in root.rglob("*.sql")}


def _is_share_node(node: str, share_nodes: set[str]) -> bool:
    # dbt names a generic test <test>_<model>_<column>[_<args>...].
    return any(node.startswith(prefix) for prefix in share_nodes)


def share_node_prefixes(bars: list[Bar]) -> set[str]:
    # dq.record keeps the unique id after "test.transit_gold.".
    return {f"{SHARE_TEST}_{b.model}_{b.column}" for b in bars}


def check_bars(
    con: duckdb.DuckDBPyConnection,
    day: date,
    bars: list[Bar],
    modes: list[str],
) -> list[Check]:
    """Each bar for each mode; a missing row or a null share fails."""
    vp_modes = set(feed_modes(con, "vehicle_positions"))
    out = []
    for bar in bars:
        col = DAY_COLUMN.get(bar.model, "service_date")
        sample = SAMPLE_COLUMN.get((bar.model, bar.column))
        found = _rows(
            con,
            f"select mode, {bar.column}, {sample or 'null'}"
            f" from gold.{bar.model} where {col} = ? order by mode",
            (day,),
        )
        rows = {m: v for m, v, _ in found}
        small = {
            m: n
            for m, v, n in found
            if sample and v is not None and (n or 0) < MIN_SAMPLE
        }
        expected = (
            [m for m in modes if m in vp_modes]
            if bar.model in VP_MODELS
            else modes
        )
        name = f"bar:{bar.model}.{bar.column}"
        for mode in expected:
            value = rows.get(mode)
            if value is not None:
                continue
            exempt = (bar.model, mode) in BAR_EXEMPT
            out.append(
                Check(
                    name,
                    mode,
                    WARN if exempt else FAIL,
                    bar=bar.threshold,
                    detail=BAR_EXEMPT.get((bar.model, mode), "no value"),
                )
            )
        for mode, value in rows.items():
            if value is None:
                continue
            if mode in small:
                # Too few rows to judge a share: shown, not failed.
                out.append(
                    Check(
                        name,
                        mode,
                        WARN,
                        value=round(float(value), 6),
                        bar=bar.threshold,
                        detail=f"only {small[mode]} rows to measure"
                        f" (under {MIN_SAMPLE})",
                    )
                )
                continue
            out.append(
                Check(
                    f"bar:{bar.model}.{bar.column}",
                    mode,
                    PASS if value >= bar.threshold else FAIL,
                    value=round(float(value), 6),
                    bar=bar.threshold,
                )
            )
    return out


def check_modes(
    con: duckdb.DuckDBPyConnection, day: date, modes: list[str]
) -> list[Check]:
    seen = dict(
        _rows(
            con,
            "select mode, realtime_trips from gold.dq_trip_match"
            " where service_date = ?",
            (day,),
        )
    )
    return [
        Check(
            "modes",
            m,
            PASS if seen.get(m, 0) > 0 else FAIL,
            value=int(seen.get(m, 0)),
            detail="" if seen.get(m, 0) else "no realtime trips this day",
        )
        for m in modes
    ]


def check_capture(con: duckdb.DuckDBPyConnection, day: date) -> list[Check]:
    rows = _rows(
        con,
        "select mode, count(*), count(*) filter (tu_covered)"
        " from gold.fct_trip_outcome where service_date = ?"
        " group by 1 order by 1",
        (day,),
    )
    return [
        Check(
            "capture",
            mode,
            PASS if covered / n >= CAPTURE_BAR else FAIL,
            value=round(covered / n, 6),
            bar=CAPTURE_BAR,
            detail=f"{covered:,} of {n:,} scheduled trips covered",
        )
        for mode, n, covered in rows
    ]


def check_operators(con: duckdb.DuckDBPyConnection, day: date) -> list[Check]:
    """D-038: only operators with realtime that day are judged."""
    out = []
    rows = _rows(
        con,
        """
        select
            mode,
            count(*) filter (
                outcome in ('never_seen', 'partial') and not operator_covered
            ) as broken,
            count(distinct agency_id) filter (operator_covered) as covered,
            count(distinct agency_id) as operators,
            count(*) filter (tu_covered and operator_covered) as trips_covered,
            count(*) filter (outcome = 'never_seen') as never_seen
        from gold.fct_trip_outcome
        where service_date = ?
        group by 1 order by 1
        """,
        (day,),
    )
    mart = {
        m: (c, n)
        for m, c, n in _rows(
            con,
            "select mode, sum(trips_covered), sum(trips_never_seen)"
            " from gold.mart_route_reliability where service_date = ?"
            " group by 1",
            (day,),
        )
    }
    for mode, broken, covered, operators, trips_cov, never in rows:
        out.append(
            Check(
                "operators:rule",
                mode,
                FAIL if broken else PASS,
                value=int(broken),
                bar=0,
                detail=f"{covered} of {operators} operators covered"
                + (
                    f"; {broken} uncovered trips judged never_seen or partial"
                    if broken
                    else ""
                ),
            )
        )
        if not covered:
            out.append(
                Check(
                    "operators:covered",
                    mode,
                    FAIL,
                    value=0,
                    detail="no operator of this mode had realtime",
                )
            )
        m_cov, m_never = mart.get(mode, (0, 0))
        same = (int(m_cov or 0), int(m_never or 0)) == (trips_cov, never)
        out.append(
            Check(
                "operators:mart",
                mode,
                PASS if same else FAIL,
                value=int(m_cov or 0),
                bar=int(trips_cov),
                detail=(
                    ""
                    if same
                    else f"mart covered/never_seen {m_cov}/{m_never},"
                    f" outcomes {trips_cov}/{never}"
                ),
            )
        )
    dropped = _rows(
        con,
        """
        with cov as (
            select service_date, mode, agency_id,
                bool_or(operator_covered) as covered,
                count(*) as trips
            from gold.fct_trip_outcome
            where service_date in (?, ?::date - 1)
            group by all
        )
        select t.mode, t.agency_id, t.trips
        from cov t
        join cov y
            on y.mode = t.mode
            and y.agency_id is not distinct from t.agency_id
            and y.service_date = t.service_date - 1
        where t.service_date = ? and y.covered and not t.covered
            and t.trips >= ?
        order by t.trips desc
        """,
        (day, day, day, OPERATOR_DROP_MIN_TRIPS),
    )
    for mode, agency, trips in dropped:
        out.append(
            Check(
                "operators:dropped",
                mode,
                WARN,
                value=int(trips),
                detail=f"operator {agency} had realtime the day before,"
                f" not this day ({trips:,} trips not judged)",
            )
        )
    return out


def check_trip_left(con: duckdb.DuckDBPyConnection, day: date) -> list[Check]:
    """D-036: REMOVED rows present, well formed, and no mass endings.

    Read from gold.dq_trip_left, which the build keeps per day (D-060).
    """
    out = []
    stats = {
        m: rest
        for m, *rest in _rows(
            con,
            "select mode, removed_rows, removed_with_stops,"
            " worst_removed_share, mass_endings, empty_endings"
            " from gold.dq_trip_left where service_date = ?",
            (day,),
        )
    }
    seen = _rows(
        con,
        "select mode, count(*),"
        " count(*) filter (left_feed_at is not null)"
        " from gold.fct_trip_outcome"
        " where service_date = ? and seen_tu and tu_covered"
        " and outcome in ('ran', 'partial')"
        " group by 1 order by 1",
        (day,),
    )
    for mode, ran, left in seen:
        n, bad, worst, bursts, empties = stats.get(mode, (0, 0, None, 0, 0))
        out.append(
            Check(
                "trip_left:rows",
                mode,
                PASS if n and not bad else FAIL,
                value=int(n),
                detail=(
                    "no REMOVED rows"
                    if not n
                    else f"{bad} REMOVED rows with stops"
                    if bad
                    else ""
                ),
            )
        )
        share = left / ran if ran else 1.0
        out.append(
            Check(
                "trip_left:share",
                mode,
                PASS if share >= LEFT_FEED_BAR else FAIL,
                value=round(share, 6),
                bar=LEFT_FEED_BAR,
                detail=f"{left:,} of {ran:,} trips seen to run left the feed",
            )
        )
        if worst is None:
            continue
        out.append(
            Check(
                "trip_left:burst",
                mode,
                FAIL if bursts else PASS,
                value=int(bursts),
                bar=0,
                detail=f"largest share removed by one snapshot {worst:.1%}",
            )
        )
        # A readable empty feed ends every trip on it: Sydney Trains
        # empties for about 90 s at 1 am Sydney (D-059). It is the feed,
        # not a bad file, so it is shown, not failed.
        if empties:
            out.append(
                Check(
                    "trip_left:empty",
                    mode,
                    WARN,
                    value=int(empties),
                    detail="an empty snapshot ended over half the trips",
                )
            )
    return out


# --- the gate ------------------------------------------------------------


def judge_day(
    con: duckdb.DuckDBPyConnection,
    day: date,
    complete: bool,
    complete_at: datetime,
    modes: list[str],
    bars: list[Bar],
) -> Day:
    if not complete:
        return Day(day, "open")
    checks = [
        *check_build(con, day, complete_at, share_node_prefixes(bars)),
        *check_modes(con, day, modes),
        *check_capture(con, day),
        *check_bars(con, day, bars, modes),
        *check_operators(con, day),
        *check_trip_left(con, day),
    ]
    # Only the modes asked for: --mode leaves the others out entirely.
    checks = [c for c in checks if c.mode is None or c.mode in modes]
    status = "red" if any(c.status == FAIL for c in checks) else "green"
    return Day(day, status, checks)


def streak(days: list[Day]) -> tuple[date | None, int]:
    """The current run of green days: (first day, length)."""
    # The newest days may still be open; they neither count nor break.
    while days and days[-1].status == "open":
        days = days[:-1]
    start, n, prev = None, 0, None
    for d in days:
        gap = prev is not None and d.service_date != prev + timedelta(days=1)
        prev = d.service_date
        if d.status != "green" or gap:
            # A red day, an open day or a day missing from gold breaks
            # the run; a green day after a gap starts a new one.
            start, n = None, 0
        if d.status == "green":
            if n == 0:
                start = d.service_date
            n += 1
    return start, n


def run_gate(
    con: duckdb.DuckDBPyConnection,
    since: date | None = None,
    until: date | None = None,
    days_needed: int = GATE_DAYS,
    grace_minutes: int = 180,
    modes: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    bars = quality_bars()
    modes = modes or trip_update_modes(con)
    judged = [
        judge_day(con, d, complete, end, modes, bars)
        for d, complete, end in service_days(con, grace_minutes)
        if (since is None or d >= since) and (until is None or d <= until)
    ]
    start, n = streak(judged)
    passed = n >= days_needed
    remaining = max(days_needed - n, 0)
    closed = [d for d in judged if d.status != "open"]
    earliest = (
        closed[-1].service_date + timedelta(days=remaining)
        if closed and not passed
        else None
    )
    return {
        "checked_at": (now or datetime.now(UTC)).isoformat(),
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "days_needed": days_needed,
        "grace_minutes": grace_minutes,
        "modes": modes,
        "bars": [asdict(b) for b in bars],
        "streak_start": start.isoformat() if start else None,
        "streak_days": n,
        "passed": passed,
        "earliest_pass_day": earliest.isoformat() if earliest else None,
        "days": [
            {
                "service_date": d.service_date.isoformat(),
                "status": d.status,
                "failures": len(d.failures),
                "warnings": len(d.warnings),
                "checks": [asdict(c) for c in d.checks],
            }
            for d in judged
        ],
    }


def summary(result: dict[str, Any]) -> str:
    lines = []
    for d in result["days"]:
        head = f"{d['service_date']}  {d['status']:<5}"
        if d["status"] == "open":
            lines.append(f"{head}  data not complete yet")
            continue
        lines.append(
            f"{head}  {len(d['checks'])} checks,"
            f" {d['failures']} failed, {d['warnings']} warnings"
        )
        for c in d["checks"]:
            if c["status"] != PASS:
                mode = f" [{c['mode']}]" if c["mode"] else ""
                value = "" if c["value"] is None else f" {c['value']}"
                bar = "" if c["bar"] is None else f" (bar {c['bar']})"
                lines.append(
                    f"    {c['status'].upper()} {c['check']}{mode}"
                    f"{value}{bar} {c['detail']}".rstrip()
                )
    verdict = "PASS" if result["passed"] else "not yet"
    lines.append(
        f"gate {verdict}: {result['streak_days']} of"
        f" {result['days_needed']} green days in a row"
        + (f" from {result['streak_start']}" if result["streak_start"] else "")
        + (
            f"; earliest pass after service day {result['earliest_pass_day']}"
            if result["earliest_pass_day"]
            else ""
        )
    )
    return "\n".join(lines)


def write_json(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str) + "\n")
