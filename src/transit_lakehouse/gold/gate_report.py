"""Draft the Phase 2 gate report (R2-02) from ``gold gate`` JSON.

Usage, on the host once the gate check exits 0 (D-039, D-057)::

    docker compose --profile gold run --rm gold gate --since 2026-11-29 \\
        --json docs/reports/gold-gate.json
    docker compose --profile gold run --rm gold gate-report \\
        --gate-json docs/reports/gold-gate.json --commit abc1234 \\
        --out docs/reports/phase2-gate-20261209.md

It writes a phase report in the standard of docs/reports/README.md,
with the tables filled from the JSON: one row per service day, the
lowest value of each quality bar per mode, the operator-coverage rule
(D-038) and the trip-left rows (D-036). The verdict follows the gate.
Status is Draft, so a person reads it, writes the Findings and sets it
to Final before committing, as with R0-02.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPORT_ID = "R2-02"
TITLE = "Phase 2 seven-day quality gate"
SUBTITLE = (
    "Whether the gold quality checks stayed green for seven consecutive "
    "Sydney service days after the worker went live, checked from the "
    "gold database alone."
)
DECISIONS = "D-035, D-036, D-038, D-039, D-048, D-057"
MODE_NAMES = {
    "bus": "Bus",
    "buses": "Bus",
    "train": "Trains",
    "trains": "Trains",
    "sydneytrains": "Trains",
    "metro": "Metro",
    "lightrail": "Light rail",
    "light_rail": "Light rail",
    "ferry": "Ferries",
    "ferries": "Ferries",
}
CHECK_NAMES = {
    "build": "dbt build",
    "modes": "Mode present",
    "capture": "Trips judged on capture",
    "operators:rule": "Uncovered operator judged (D-038)",
    "operators:covered": "Covered operators (D-038)",
    "operators:mart": "Route mart agrees (D-038)",
    "operators:dropped": "Operator dropped from the feed",
    "trip_left:rows": "Trip-left rows (D-036)",
    "trip_left:share": "Trips seen to run that left the feed",
    "trip_left:burst": "Snapshots ending over half the trips",
    "trip_left:empty": "Empty snapshots ending over half the trips",
}


def mode_name(mode: str | None) -> str:
    if mode is None:
        return "All modes"
    return MODE_NAMES.get(mode.lower(), mode)


def check_name(check: str) -> str:
    if check.startswith("bar:"):
        return f"`{check[4:]}`"
    return CHECK_NAMES.get(check, check)


def _d(iso: str) -> str:
    return f"{date.fromisoformat(iso):%a %-d %b}"


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.2%}"


def _status(day: dict) -> str:
    return {"green": "Pass", "red": "Fail"}.get(day["status"], "Open")


def _day_rows(days: list[dict]) -> list[str]:
    rows = []
    for d in days:
        if d["status"] == "open":
            rows.append(f"| {_d(d['service_date'])} | – | – | – | Open |")
            continue
        rows.append(
            f"| {_d(d['service_date'])} | {len(d['checks']):,} "
            f"| {d['failures']} | {d['warnings']} | {_status(d)} |"
        )
    return rows


def _closed(days: list[dict]) -> list[dict]:
    return [d for d in days if d["status"] != "open"]


def _bar_rows(days: list[dict]) -> list[str]:
    low: dict[tuple[str, str], tuple[float, str, float]] = {}
    failed: set[tuple[str, str]] = set()
    for d in _closed(days):
        for c in d["checks"]:
            if not c["check"].startswith("bar:"):
                continue
            key = (c["check"], c["mode"])
            if c["status"] == "fail":
                failed.add(key)
            if c["value"] is None:
                continue
            if key not in low or c["value"] < low[key][0]:
                low[key] = (c["value"], d["service_date"], c["bar"])
    rows = []
    for check, mode in sorted(set(low) | failed):
        value, day, bar = low.get((check, mode), (None, None, None))
        needs = "–" if bar is None else f"{bar:.0%}"
        rows.append(
            f"| {check_name(check)} | {mode_name(mode)} | {needs} "
            f"| {_pct(value)} | {_d(day) if day else '–'} "
            f"| {'Fail' if (check, mode) in failed else 'Pass'} |"
        )
    return rows


def _by_mode(days: list[dict], check: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for d in _closed(days):
        for c in d["checks"]:
            if c["check"] == check:
                out[c["mode"]].append({**c, "day": d["service_date"]})
    return out


def _capture_rows(days: list[dict]) -> list[str]:
    rows = []
    for mode, checks in sorted(_by_mode(days, "capture").items()):
        worst = min(checks, key=lambda c: c["value"])
        fails = sum(c["status"] == "fail" for c in checks)
        rows.append(
            f"| {mode_name(mode)} | {_pct(worst['value'])} "
            f"| {_d(worst['day'])} | {fails} "
            f"| {'Pass' if not fails else 'Fail'} |"
        )
    return rows


def _operator_rows(days: list[dict]) -> list[str]:
    rule = _by_mode(days, "operators:rule")
    covered_fails = _by_mode(days, "operators:covered")
    mart = _by_mode(days, "operators:mart")
    dropped = _by_mode(days, "operators:dropped")
    rows = []
    for mode in sorted(rule):
        broken = sum(c["value"] or 0 for c in rule[mode])
        covered = [c["detail"].split(" operators")[0] for c in rule[mode]]
        mart_fails = sum(c["status"] == "fail" for c in mart.get(mode, []))
        fail = (
            broken
            or mart_fails
            or any(c["status"] == "fail" for c in rule[mode])
            or any(c["status"] == "fail" for c in covered_fails.get(mode, []))
        )
        rows.append(
            f"| {mode_name(mode)} | {covered[-1]} | {broken:,} "
            f"| {mart_fails} | {len(dropped.get(mode, []))} "
            f"| {'Fail' if fail else 'Pass'} |"
        )
    return rows


def _trip_left_rows(days: list[dict]) -> list[str]:
    rows_ = _by_mode(days, "trip_left:rows")
    share = _by_mode(days, "trip_left:share")
    burst = _by_mode(days, "trip_left:burst")
    out = []
    for mode in sorted(set(rows_) | set(share)):
        removed = sum(c["value"] or 0 for c in rows_.get(mode, []))
        worst = min(
            share.get(mode, []), key=lambda c: c["value"], default=None
        )
        bursts = sum(c["value"] or 0 for c in burst.get(mode, []))
        fail = any(
            c["status"] == "fail"
            for c in rows_.get(mode, [])
            + share.get(mode, [])
            + burst.get(mode, [])
        )
        out.append(
            f"| {mode_name(mode)} | {removed:,} "
            f"| {_pct(worst['value'] if worst else None)} | {bursts} "
            f"| {'Fail' if fail else 'Pass'} |"
        )
    return out


def _problems(days: list[dict], status: str) -> list[tuple[str, dict]]:
    return [
        (d["service_date"], c)
        for d in _closed(days)
        for c in d["checks"]
        if c["status"] == status
    ]


def _problem_rows(items: list[tuple[str, dict]]) -> list[str]:
    rows = []
    for day, c in items:
        value = c["value"]
        shown = (
            "–"
            if value is None
            else _pct(value)
            if isinstance(value, float) and value <= 1
            else f"{value:,}"
        )
        detail = c["detail"].replace("|", "/") or "–"
        rows.append(
            f"| {_d(day)} | {check_name(c['check'])} "
            f"| {mode_name(c['mode'])} | {shown} | {detail} |"
        )
    return rows


def build(
    gate: dict[str, Any],
    commit: str,
    report_date: str,
    environment: str,
) -> str:
    days = gate["days"]
    closed = _closed(days)
    passed = gate["passed"]
    verdict = "Pass" if passed else "Fail"
    first = days[0]["service_date"] if days else gate.get("since")
    last = days[-1]["service_date"] if days else gate.get("until")
    window = (
        f"service days {_d(first)} to {_d(last)} 2026 (Sydney)"
        if first and last
        else "no service days"
    )
    n, need = gate["streak_days"], gate["days_needed"]
    green = sum(d["status"] == "green" for d in closed)
    failures = _problems(days, "fail")
    warnings = _problems(days, "warn")
    answer = (
        f"The gate **passed**: the quality checks were green on {n} "
        f"consecutive service days from {_d(gate['streak_start'])}, "
        f"against the {need} the gate needs."
        if passed
        else f"The gate **has not passed**: the current run is {n} of "
        f"{need} green service days"
        + (
            f", so it can pass after service day "
            f"{_d(gate['earliest_pass_day'])} at the earliest."
            if gate.get("earliest_pass_day")
            else "."
        )
    )
    modes = ", ".join(mode_name(m) for m in gate["modes"])
    lines = [
        "---",
        f"id: {REPORT_ID}",
        f"title: {TITLE}",
        f"subtitle: {SUBTITLE}",
        "phase: 2",
        "status: Draft",
        f"verdict: {verdict}",
        f"date: {report_date}",
        f"window: {window}",
        f"commit: {commit}",
        f"environment: {environment}",
        f"decisions: {DECISIONS}",
        "evidence: docs/reports/gold-gate.json from `gold gate`; the gold "
        "database and gold.dq_results on the host (gitignored); "
        "docs/10_incidents.md",
        "---",
        "",
        f"# {TITLE}",
        "",
        "## Summary",
        "",
        f"> {answer}",
        "",
        f"- **{green} of {len(closed)} closed service days were green** "
        "(section 3.1).",
        f"- **{len(failures)} checks failed in the window** (section 3.6).",
        f"- **{len(warnings)} warnings were raised**, most of them "
        "operators that left the feed for a day (section 3.4)."
        if warnings
        else "- **No warnings were raised**, so no operator dropped out "
        "of the feed for a day (section 3.4).",
        f"- **{len(gate['modes'])} modes were checked every day**: "
        f"{modes} (section 3.2).",
        "",
        "## Scope and method",
        "",
        "The nightly gold build (D-035) reads the silver tables the live "
        "worker writes and records every model and test in "
        "`gold.dq_results`. `gold gate` reads the gold database "
        "read-only and judges each Sydney service day on its own (D-057).",
        "",
        "```flow",
        "silver | Silver tables | written by the live worker",
        "gold | Gold build | nightly dbt build, every result recorded",
        "check | Gate check | each service day judged, read-only",
        "```",
        "",
        "| Check | Passes when | Decision |",
        "|---|---|---|",
        "| dbt build | a build covering the day, run after its data "
        "closed, has every model and structural test passing | D-035 |",
        "| Quality bars | each `share_at_least` bar holds on the day's row "
        "for each mode | D-035 |",
        "| Mode present | every mode with a trip-update feed has realtime "
        "trips | D-039 |",
        f"| Trips judged on capture | at least "
        f"{_bar(gate, 'capture', 0.95)} of each mode's scheduled trips "
        "are covered from start to grace | D-048, D-057 |",
        "| Operator coverage | no uncovered operator's trip is "
        "`never_seen` or `partial`, and the route mart agrees | D-038 |",
        "| Trip left the feed | `REMOVED` rows present with stop count 0, "
        f"at least {_bar(gate, 'trip_left:share', 0.9)} of trips seen to "
        "run left the feed, and no snapshot ended over half the trips "
        "| D-036, D-049 |",
        "",
        f"A day is green when none of these fails, and the gate needs "
        f"{need} green days in a row (a red day restarts the run). A day "
        "is open until trip updates are captured past its last trip's "
        f"end plus the {gate['grace_minutes']}-minute grace. Out of "
        "scope: the volume test and the public quality page (work items "
        "11 and 12), which wait for four weeks of silver and the "
        "dashboard host.",
        "",
        "## Results",
        "",
        "### Service days",
        "",
        f"At {commit}.",
        "",
        "| Service day | Checks | Failed | Warnings | Check |",
        "|---|---:|---:|---:|---|",
        *_day_rows(days),
        "",
        "### Trips judged on capture",
        "",
        "| Mode | *Lowest share* | Day | Days failing | Check |",
        "|---|---:|---|---:|---|",
        *(_capture_rows(days) or ["| None | – | – | – | – |"]),
        "",
        "### Quality bars",
        "",
        "The lowest value of each bar in the window, per mode.",
        "",
        "| Bar | Mode | Needs | *Lowest* | Day | Check |",
        "|---|---|---:|---:|---|---|",
        *(_bar_rows(days) or ["| None | – | – | – | – | – |"]),
        "",
        "### Operator coverage",
        "",
        "Covered operators on the last closed day; the other columns add "
        "up over the window (D-038).",
        "",
        "| Mode | Covered operators | Uncovered trips judged "
        "| Days the mart differs | Operators dropped | Check |",
        "|---|---:|---:|---:|---:|---|",
        *(_operator_rows(days) or ["| None | – | – | – | – | – |"]),
        "",
        "### Trip left the feed",
        "",
        "| Mode | `REMOVED` rows | *Lowest share left* "
        "| Mass-removal snapshots | Check |",
        "|---|---:|---:|---:|---|",
        *(_trip_left_rows(days) or ["| None | – | – | – | – |"]),
        "",
        "### Failed checks and warnings",
        "",
        *(
            [
                "| Service day | Check | Mode | Value | Detail |",
                "|---|---|---|---:|---|",
                *_problem_rows(failures + warnings),
            ]
            if failures or warnings
            else ["None."]
        ),
        "",
        "## Findings",
        "",
        "- **Write the findings by hand before setting the report to "
        "Final.** Cite section 3, a D-NNN row or an incident (I-NN).",
        "",
        "## Limits",
        "",
        "- **Green means the checks held, not that every trip is right.** "
        "The bars are shares; a handful of wrong trips can sit under them.",
        "- **Operators without realtime are not judged at all** (D-038), "
        "so their ghost rates are unknown, not zero.",
        "- **The plausibility bar is by poll date in UTC**, not by "
        "service day, so it straddles two service days.",
        "",
        "## Next steps",
        "",
        "- **Record the gate as a decision row** and tick it in STATUS.md.",
        "- **Run the Phase 3 hand-check on backfilled history** (D-046).",
        "- **Build the volume test and the public quality page** once four "
        "weeks of silver and the dashboard host exist.",
        "",
    ]
    return "\n".join(lines)


def _bar(gate: dict, check: str, default: float) -> str:
    for d in _closed(gate["days"]):
        for c in d["checks"]:
            if c["check"] == check and c["bar"] is not None:
                return f"{c['bar']:.0%}"
    return f"{default:.0%}"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gate-json", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--date",
        dest="report_date",
        default=datetime.now(UTC).strftime("%Y-%m-%d"),
    )
    parser.add_argument(
        "--environment",
        default="AWS EC2 t3.small in ap-southeast-2 (Sydney), Docker "
        "Compose (D-015, D-016), gold capped at 900 MB (D-040)",
    )
    parser.add_argument("--out", type=Path, required=True)


def run(args: argparse.Namespace) -> int:
    gate = json.loads(args.gate_json.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        build(gate, args.commit, args.report_date, args.environment),
        encoding="utf-8",
    )
    print(f"wrote {args.out} (Draft; fill in Findings, then set Final)")
    return 0
