"""Draft the Phase 0 gate report (R0-02) from capture check JSON.

Usage, on the collector host on gate day::

    docker compose run --rm checks capture --window 7d --gate \\
        --json docs/reports/capture-7d-gate.json
    docker compose run --rm checks gate-report \\
        --gate-json docs/reports/capture-7d-gate.json \\
        --daily 'docs/reports/capture-2026112*.json' \\
        --commit 2ae606f --out docs/reports/phase0-gate-20261128.md

It writes a phase report in the standard of docs/reports/README.md with
the tables filled from the JSON: the 7-day gate per feed and one row
per daily cron run. The verdict follows the gate. Status is Draft, so
a person reads it, adds context and sets it to Final before committing.
The report cites the incident log (docs/10_incidents.md) rather than
repeating it.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPORT_ID = "R0-02"
TITLE = "Phase 0 seven-day capture gate"
SUBTITLE = (
    "Whether the live collector captured at least 99% of scheduled polls "
    "on every feed for seven consecutive days, measured from its own "
    "manifest on the collector host."
)
DECISIONS = "D-007, D-022, D-031, D-033, D-034, D-037"


def _when(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _dt(iso: str) -> str:
    return f"{_when(iso):%-d %b %Y %H:%M}"


def _secs(value: float | None) -> str:
    if value is None:
        return "–"
    if value >= 3600:
        return f"{value / 3600:.1f} h"
    return f"{value:.0f} s"


def _feed_rows(feeds: list[dict]) -> list[str]:
    rows = []
    for f in feeds:
        status = "Pass" if not f["failures"] else "Fail"
        if f.get("upstream_outages") and not f["failures"]:
            status += " (upstream outage allowed, D-037)"
        p95 = f["freshness_p95_s"]
        rows.append(
            f"| `{f['feed']}` | {f['capture_rate']:.2%} "
            f"| {f['ok']:,} / {f['expected']:,} "
            f"| {'n/a' if p95 is None else _secs(p95)} "
            f"| {_secs(f['max_gap_s'])} | {status} |"
        )
    return rows


def _daily_row(path: Path) -> tuple[str, str]:
    run = json.loads(path.read_text(encoding="utf-8"))
    feeds = run["feeds"]
    low = min(feeds, key=lambda f: f["capture_rate"])
    failed = [f["feed"] for f in feeds if f["failures"]]
    start = _when(run["window_start"])
    status = "Pass" if not failed else "Fail"
    return (
        run["window_start"],
        f"| {start:%-d %b} | {_dt(run['window_start'])} to "
        f"{_dt(run['window_end'])} | `{low['feed']}` "
        f"| {low['capture_rate']:.2%} | {len(failed)} of {len(feeds)} "
        f"| {status} |",
    )


def build(
    gate: dict,
    daily: list[Path],
    commit: str,
    date: str,
    environment: str,
) -> str:
    feeds = gate["feeds"]
    passed = all(not f["failures"] for f in feeds)
    low = min(feeds, key=lambda f: f["capture_rate"])
    worst_gap = max(
        (
            f
            for f in feeds
            if f["max_gap_s"] is not None and f["feed"] != "static_gtfs"
        ),
        key=lambda f: f["max_gap_s"],
        default=None,
    )
    failed = [f for f in feeds if f["failures"]]
    window = f"{_dt(gate['window_start'])} to {_dt(gate['window_end'])} UTC"
    daily_rows = sorted(_daily_row(p) for p in daily)
    verdict = "Pass" if passed else "Fail"
    # D-037: TfNSW outages within the allowance pass but are long gaps.
    hit = [f for f in feeds if f.get("upstream_outages")]
    most = max((f["upstream_outages"] for f in hit), default=0)
    gaps_text = (
        "with no gap longer than four poll intervals except "
        f"{most} upstream outage{'s' if most > 1 else ''} on "
        f"{len(hit)} feed{'s' if len(hit) > 1 else ''}, allowed under "
        "D-037"
        if hit
        else "with no gap longer than four poll intervals"
    )
    answer = (
        f"The gate **passed**: every one of the {len(feeds)} feeds "
        f"captured at least 99% of its scheduled polls over the seven "
        f"days, {gaps_text}."
        if passed
        else f"The gate **failed** on {len(failed)} of {len(feeds)} feeds, "
        "so the seven-day clock has not completed."
    )
    fail_lines = (
        "".join(
            f"- **`{f['feed']}` failed:** {'; '.join(f['failures'])} "
            "(section 3.1).\n"
            for f in failed
        )
        if failed
        else ""
    )
    gap_line = (
        f"- **The longest gap was {_secs(worst_gap['max_gap_s'])}** on "
        f"`{worst_gap['feed']}` (section 3.1).\n"
        if worst_gap
        else ""
    )
    lines = [
        "---",
        f"id: {REPORT_ID}",
        f"title: {TITLE}",
        f"subtitle: {SUBTITLE}",
        "phase: 0",
        "status: Draft",
        f"verdict: {verdict}",
        f"date: {date}",
        f"window: {window}",
        f"commit: {commit}",
        f"environment: {environment}",
        f"decisions: {DECISIONS}",
        "evidence: docs/reports/capture-7d-gate.json and the daily "
        "capture-*.json on the collector host; docs/10_incidents.md",
        "---",
        "",
        f"# {TITLE}",
        "",
        "## Summary",
        "",
        f"> {answer}",
        "",
        f"- **The lowest feed was `{low['feed']}` at "
        f"{low['capture_rate']:.2%}** over the seven days (section 3.1).",
        gap_line.rstrip("\n") or None,
        fail_lines.rstrip("\n") or None,
        f"- **{len(daily_rows)} daily checks are in the window**, one per "
        "cron run at 00:05 UTC (section 3.2).",
        "- **Incidents before the window are in the incident log.** "
        "TfNSW's 502 outage on 20 Nov restarted the clock (D-033, I-06).",
        "",
        "## Scope and method",
        "",
        "The collector polls TfNSW's GTFS-realtime feeds from AWS EC2 in "
        "Sydney and records every attempt in its manifest (D-007).",
        "",
        "```flow",
        "source | TfNSW Open Data | realtime and timetable feeds",
        "raw | Collector | every attempt in the manifest",
        "check | Capture check | 7-day gate, read-only",
        "```",
        "",
        "| Check | Command | Window |",
        "|---|---|---|",
        f"| Seven-day gate | `checks capture --window 7d --gate` | {window} |",
        "| Daily checks | `checks capture --window 24h` (cron, 00:05 UTC) "
        "| one row per run |",
        "",
        "The gate needs, for every enabled feed, capture of at least 99% of "
        "scheduled polls, no gap over four intervals, known freshness and "
        "full history for the window (D-022). A gap TfNSW caused (every "
        "attempt a 5xx or network error while the collector kept polling) "
        "is allowed up to 30 minutes a day and 2 outages a week (D-037). "
        "Out of scope: the tables "
        "built from the data (Phase 1).",
        "",
        "## Results",
        "",
        "### Seven-day gate by feed",
        "",
        f"At {commit}.",
        "",
        "| Feed | Capture | Polls (got / expected) | p95 freshness "
        "| Longest gap | Check |",
        "|---|---:|---:|---:|---:|---|",
        *_feed_rows(feeds),
        "",
        "### Daily checks",
        "",
        "| Day | Window (UTC) | Lowest feed | Capture | Feeds failing "
        "| Check |",
        "|---|---|---|---:|---:|---|",
        *(row for _, row in daily_rows),
        "",
        "## Findings",
        "",
        "- **Write the findings by hand before setting the report to "
        "Final.** Cite section 3, a D-NNN row or an incident (I-NN).",
        "",
        "## Limits",
        "",
        "- **Capture is measured against our own schedule.** It shows we "
        "asked on time and got an answer, not that TfNSW's data was "
        "complete or correct.",
        "- **Upstream outages outside the window are not shown here.** "
        "See docs/10_incidents.md.",
        "",
        "## Next steps",
        "",
        "- **Record the gate as a decision row** and tick it in STATUS.md.",
        "- **Turn on the Phase 1 worker** (D-027), with Mike's approval.",
        "- **Probe the unverified feeds** (O-03) before adding any.",
        "",
    ]
    return "\n".join(line for line in lines if line is not None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gate-json", type=Path, required=True)
    parser.add_argument(
        "--daily",
        default="",
        help="Glob of daily capture JSON files from the cron.",
    )
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--date", default=datetime.now(UTC).strftime("%Y-%m-%d")
    )
    parser.add_argument(
        "--environment",
        default="AWS EC2 t3.small in ap-southeast-2 (Sydney), Docker "
        "Compose (D-015, D-016)",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    gate = json.loads(args.gate_json.read_text(encoding="utf-8"))
    daily = (
        [Path(p) for p in sorted(glob.glob(args.daily))] if args.daily else []
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        build(gate, daily, args.commit, args.date, args.environment),
        encoding="utf-8",
    )
    print(f"wrote {args.out} (Draft; fill in Findings, then set Final)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
