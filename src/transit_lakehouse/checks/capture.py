"""Capture-rate and freshness SLA check over the poll manifest.

Usage::

    python -m transit_lakehouse.checks.capture --window 24h
    python -m transit_lakehouse.checks.capture --window 7d --gate

Exit code 0 when every feed meets its SLA, 1 otherwise, so the check
can drive cron alerts or a CI-style gate.

Definitions (see docs/3_decisions.md D-007):

* capture rate = successful polls / scheduled polls in the window. An
  unchanged snapshot is still a successful capture: we asked on time
  and learned nothing moved.
* freshness = fetch time minus the feed header's own timestamp.
* staleness = now minus the last successful poll.
* stuck = a realtime feed returning the same sha256 for more than
  5 minutes. It is a warning, not a failure (D-019): the gateway is up
  but upstream may have frozen, which only TfNSW can fix.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from transit_lakehouse.checks.manifest import (
    Poll,
    parse_window,
    percentile,
    read_polls,
)
from transit_lakehouse.collector.config import Feed, load_feeds

CAPTURE_SLA = 0.99
FRESHNESS_P95_SLA_S = {
    "vehicle_positions": 60.0,
    "trip_updates": 90.0,
    "service_alerts": 300.0,
    "static_gtfs": float("inf"),  # a daily zip has no header timestamp
}
# Plan: the same sha256 for more than 5 minutes means the feed is stuck.
STUCK_AFTER_S = 300.0
STALE_AFTER_INTERVALS = 4


@dataclass
class FeedResult:
    feed: str
    expected: int
    ok: int
    new: int
    capture_rate: float
    freshness_p50_s: float | None
    freshness_p95_s: float | None
    max_gap_s: float | None
    staleness_s: float | None
    coverage_start: str | None
    failures: list[str]
    longest_unchanged_s: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def evaluate_feed(
    feed: Feed,
    polls: list[Poll],
    start: datetime,
    end: datetime,
    gate: bool = False,
) -> FeedResult:
    """Score one feed against its SLAs for the window."""
    failures: list[str] = []
    first = polls[0].fetched_at if polls else None
    # Outside gate mode, don't penalise the minutes before the collector
    # first started; the gate demands the full window.
    effective_start = start if gate or first is None else max(start, first)
    span = (end - effective_start).total_seconds()
    # Slots in [start, end): a poll at the very start counts, so ceil.
    expected = max(1, math.ceil(span / feed.interval_s))
    ok_polls = [p for p in polls if p.ok]
    capture = min(1.0, len(ok_polls) / expected)

    ages = [a for p in ok_polls if (a := p.age_s) is not None]
    p50, p95 = percentile(ages, 0.5), percentile(ages, 0.95)
    times = [p.fetched_at for p in ok_polls]
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    staleness = (end - times[-1]).total_seconds() if times else None

    if capture < CAPTURE_SLA:
        failures.append(f"capture {capture:.2%} < {CAPTURE_SLA:.0%}")
    limit = FRESHNESS_P95_SLA_S[feed.kind]
    if p95 is not None and p95 > limit:
        failures.append(f"freshness p95 {p95:.0f}s > {limit:.0f}s")
    if staleness is None or staleness > STALE_AFTER_INTERVALS * (
        feed.interval_s
    ):
        failures.append("stale: no recent successful poll")
    if gate and (first is None or first > start):
        failures.append("insufficient history for the full window")

    longest = longest_unchanged_s(ok_polls) if feed.is_realtime else None
    warnings: list[str] = []
    if longest is not None and longest > STUCK_AFTER_S:
        warnings.append(f"stuck: same snapshot for {longest:.0f}s")

    return FeedResult(
        feed=feed.name,
        expected=expected,
        ok=len(ok_polls),
        new=sum(p.outcome == "new" for p in ok_polls),
        capture_rate=round(capture, 5),
        freshness_p50_s=p50,
        freshness_p95_s=p95,
        max_gap_s=max(gaps) if gaps else None,
        staleness_s=staleness,
        coverage_start=first.isoformat() if first else None,
        failures=failures,
        longest_unchanged_s=longest,
        warnings=warnings,
    )


def longest_unchanged_s(ok_polls: list[Poll]) -> float | None:
    """Longest time the payload hash stayed the same, in seconds.

    Measured from the poll that landed a snapshot to the last poll that
    still saw it. Unchanged polls before the first "new" in the window
    are measured from the window's first poll.
    """
    if not ok_polls:
        return None
    longest = 0.0
    since = ok_polls[0].fetched_at
    for p in ok_polls:
        if p.outcome == "new":
            since = p.fetched_at
        else:
            longest = max(longest, (p.fetched_at - since).total_seconds())
    return longest


def feed_window_start(feed: Feed, start: datetime, end: datetime) -> datetime:
    """Widen the window for feeds polled less often than it spans.

    A 1 h window over a 6-hourly feed usually holds no poll at all, which
    would read as a stale feed. Look back at least two intervals instead.
    """
    return min(start, end - timedelta(seconds=2 * feed.interval_s))


def _fmt(value: float | None, suffix: str = "s") -> str:
    return "-" if value is None else f"{value:.0f}{suffix}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        # Same env var the collector writes to, so containers agree.
        default=Path(os.environ.get("DATA_DIR", "data")),
    )
    parser.add_argument(
        "--feeds",
        type=Path,
        default=Path(os.environ.get("FEEDS_CONFIG", "config/feeds.toml")),
    )
    parser.add_argument("--window", default="24h")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Require full history for the window (Phase 0 gate).",
    )
    parser.add_argument("--json", type=Path, help="Also write JSON here.")
    args = parser.parse_args(argv)

    end = datetime.now(UTC)
    start = end - parse_window(args.window)
    feeds = load_feeds(args.feeds)
    starts = {f.name: feed_window_start(f, start, end) for f in feeds}
    polls = read_polls(args.data_dir, min(starts.values()), end)
    results = [
        evaluate_feed(
            f,
            [
                p
                for p in polls.get(f.name, [])
                if p.fetched_at >= starts[f.name]
            ],
            starts[f.name],
            end,
            args.gate,
        )
        for f in feeds
    ]

    print(f"Capture check {start:%Y-%m-%d %H:%M} to {end:%H:%M} UTC")
    print(
        f"{'feed':<32}{'capture':>9}{'ok/exp':>14}{'p50':>6}"
        f"{'p95':>6}{'gap':>7}  status"
    )
    for r in results:
        status = "PASS" if r.passed else "FAIL " + "; ".join(r.failures)
        if r.warnings:
            status += " (WARN " + "; ".join(r.warnings) + ")"
        print(
            f"{r.feed:<32}{r.capture_rate:>9.2%}"
            f"{f'{r.ok}/{r.expected}':>14}"
            f"{_fmt(r.freshness_p50_s):>6}{_fmt(r.freshness_p95_s):>6}"
            f"{_fmt(r.max_gap_s):>7}  {status}"
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                    "gate": args.gate,
                    "feeds": [asdict(r) for r in results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
