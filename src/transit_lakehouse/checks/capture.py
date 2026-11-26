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
* An empty snapshot (0 entities) is left out of freshness (D-055): with
  no vehicles or trips there is nothing to be fresh about, and TfNSW
  leaves an empty ferry feed's timestamp frozen overnight. Capture and
  gaps still count every poll.
* stuck = a realtime feed returning the same sha256 for more than
  5 minutes. It is a warning, not a failure (D-019): the gateway is up
  but upstream may have frozen, which only TfNSW can fix.
* upstream gap = a gap over the gap limit in which the collector kept
  polling and every attempt failed on TfNSW's side (a 5xx or a network
  error such as a read timeout). Within the allowance of D-037 (at most
  30 minutes a UTC day and 2 outages a week) it is a warning, not a
  failure; capture must still reach 99%. Any other long gap fails.
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
    first_seen,
    parse_window,
    percentile,
    read_polls,
)
from transit_lakehouse.collector.config import Feed, load_feeds
from transit_lakehouse.collector.policy import MAX_BACKOFF_S

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
# Partial decodes (D-020) still give a timestamp; full failures do not.
INVALID_PAYLOAD_LIMIT = 0.01
# D-037: bounded allowance for upstream (TfNSW) outages.
UPSTREAM_DAILY_LIMIT_S = 1800.0
UPSTREAM_WEEKLY_OUTAGES = 2
# Upstream gaps closer together than this are one outage (I-06 had
# intermittent 502s for an hour after the main drop).
OUTAGE_MERGE_S = 1800.0


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
    upstream_outages: int = 0
    upstream_gap_s: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures


def evaluate_feed(
    feed: Feed,
    polls: list[Poll],
    start: datetime,
    end: datetime,
    gate: bool = False,
    active_since: datetime | None = None,
) -> FeedResult:
    """Score one feed against its SLAs for the window.

    ``active_since`` is when the feed was first ever polled. Outside gate
    mode the minutes before it are not counted; an outage after it is.
    Without it, the first poll in the window stands in (less strict).
    """
    failures: list[str] = []
    first = polls[0].fetched_at if polls else None
    activated = active_since or first
    effective_start = (
        start if gate or activated is None else max(start, activated)
    )
    span = (end - effective_start).total_seconds()
    # Slots in [start, end): a poll at the very start counts, so ceil.
    expected = max(1, math.ceil(span / feed.interval_s))
    ok_polls = [p for p in polls if p.ok]
    capture = min(1.0, len(ok_polls) / expected)

    # D-055: an empty snapshot carries no service to be fresh about.
    live = [p for p in ok_polls if p.entity_count != 0]
    ages = [a for p in live if (a := p.age_s) is not None]
    p50, p95 = percentile(ages, 0.5), percentile(ages, 0.95)
    times = [p.fetched_at for p in ok_polls]
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    if times:
        # Silence at the start of the window is a gap too.
        gaps.append((times[0] - effective_start).total_seconds())
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
    gap_limit = STALE_AFTER_INTERVALS * feed.interval_s
    spans = list(itertools.pairwise(times))
    if times:
        spans.append((effective_start, times[0]))
    long_spans = sorted(
        (a, b) for a, b in spans if (b - a).total_seconds() > gap_limit
    )
    own = [
        (a, b)
        for a, b in long_spans
        if not is_upstream_gap(polls, a, b, feed.interval_s)
    ]
    upstream = [g for g in long_spans if g not in own]
    if own:
        worst = max((b - a).total_seconds() for a, b in own)
        failures.append(f"gap {worst:.0f}s > {gap_limit:.0f}s")
    window_days = (end - effective_start).total_seconds() / 86400
    outages = count_outages(upstream)
    upstream_s = sum((b - a).total_seconds() for a, b in upstream)
    upstream_note: str | None = None
    if upstream:
        over = allowance_breaches(upstream, outages, window_days)
        longest_up = max((b - a).total_seconds() for a, b in upstream)
        note = (
            f"upstream outage: {outages} outage(s), "
            f"{upstream_s / 60:.1f} min, longest gap {longest_up:.0f}s"
        )
        if over:
            failures.append(f"{note}; over D-037 allowance ({over})")
        else:
            upstream_note = f"{note}; within D-037 allowance"
    if feed.is_realtime and ok_polls:
        if not ages and live:
            failures.append("freshness unknown: no feed timestamps")
        bad = sum(
            1 for p in ok_polls if (p.error or "").startswith("decode_error")
        )
        if bad / len(ok_polls) > INVALID_PAYLOAD_LIMIT:
            failures.append(
                f"undecodable payloads {bad / len(ok_polls):.1%} "
                f"> {INVALID_PAYLOAD_LIMIT:.0%}"
            )
    # The first slot may land up to one interval after the window opens.
    grace = timedelta(seconds=feed.interval_s)
    if gate and (activated is None or activated > start + grace):
        failures.append("insufficient history for the full window")

    longest = longest_unchanged_s(ok_polls) if feed.is_realtime else None
    warnings: list[str] = []
    if upstream_note:
        warnings.append(upstream_note)
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
        upstream_outages=outages,
        upstream_gap_s=round(upstream_s, 1),
    )


def is_upstream_gap(
    polls: list[Poll], start: datetime, end: datetime, interval_s: float
) -> bool:
    """True when TfNSW, not the collector, caused the gap.

    The collector must have kept trying (no silence longer than its
    longest backoff plus one interval) and every attempt in between
    must have failed with a 5xx or a network error.
    """
    inside = [p for p in polls if start < p.fetched_at < end]
    if not inside:
        return False
    if any(not (p.status is None or p.status >= 500) or p.ok for p in inside):
        return False
    marks = [start, *(p.fetched_at for p in inside), end]
    silence = max(
        (b - a).total_seconds() for a, b in itertools.pairwise(marks)
    )
    return silence <= MAX_BACKOFF_S + interval_s


def count_outages(gaps: list[tuple[datetime, datetime]]) -> int:
    """Upstream gaps within OUTAGE_MERGE_S of each other are one outage."""
    count, last_end = 0, None
    for a, b in sorted(gaps):
        if last_end is None or (a - last_end).total_seconds() > (
            OUTAGE_MERGE_S
        ):
            count += 1
        last_end = b if last_end is None else max(last_end, b)
    return count


def allowance_breaches(
    gaps: list[tuple[datetime, datetime]], outages: int, window_days: float
) -> str:
    """Describe how upstream gaps exceed D-037, or return ''."""
    per_day: dict[str, float] = {}
    for a, b in gaps:
        cur = a
        while cur < b:
            nxt = min(
                b,
                (cur + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
            key = f"{cur:%Y-%m-%d}"
            per_day[key] = per_day.get(key, 0.0) + (nxt - cur).total_seconds()
            cur = nxt
    reasons = [
        f"{day} {secs / 60:.0f} min > {UPSTREAM_DAILY_LIMIT_S / 60:.0f}"
        for day, secs in sorted(per_day.items())
        if secs > UPSTREAM_DAILY_LIMIT_S
    ]
    weeks = max(1, math.ceil(window_days / 7 - 1e-9))
    if outages > UPSTREAM_WEEKLY_OUTAGES * weeks:
        reasons.append(
            f"{outages} outages > {UPSTREAM_WEEKLY_OUTAGES * weeks}"
        )
    return "; ".join(reasons)


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
            active_since=first_seen(args.data_dir, f.name),
        )
        for f in feeds
    ]

    print(f"Capture check {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC")
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
