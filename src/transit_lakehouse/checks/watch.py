"""Live watch: alert when a feed goes silent or the disk runs low.

Usage (cron, every 5 minutes)::

    python -m transit_lakehouse.checks watch --ntfy-topic <topic>

The daily capture check only reports the day before, so TfNSW's
36-minute outage on 2026-11-20 was found the next morning (I-06, O-01
in docs/10_incidents.md). This check runs often and alerts while a
problem is happening.

A feed is **silent** when its last successful poll is older than
``SILENT_AFTER_INTERVALS`` poll intervals (at least ``SILENT_MIN_S``).
That covers an upstream outage, a bad key and a dead collector alike,
since all of them stop successful polls. The disk is **low** when free
space under the data directory drops below ``--min-free-gb``.

Alerts fire only when the set of problems changes (new problem or
recovery), plus a reminder every ``--remind`` while any stays open, so
a long outage sends a handful of messages, not one every 5 minutes.
Every run prints its result; exit code 0 when healthy, 1 otherwise.

Alerts go to ntfy (https://ntfy.sh), a free push service with a phone
app and no account. The topic name works like a password, so it is
read from ``--ntfy-topic`` or ``ALERT_NTFY_TOPIC`` and never printed.
Messages carry feed names, times and disk figures only: no key, no URL
query and no payload.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from transit_lakehouse.checks.manifest import (
    first_seen,
    parse_window,
    read_polls,
)
from transit_lakehouse.collector.config import Feed, load_feeds

SILENT_AFTER_INTERVALS = 4  # the gap rule's limit (D-007, D-022)
SILENT_MIN_S = 120.0  # rides out one slow poll and the cron's own jitter
DEFAULT_MIN_FREE_GB = 10.0
DEFAULT_REMIND = "3h"
NTFY_SERVER = "https://ntfy.sh"


@dataclass(frozen=True)
class Problem:
    """One open problem, keyed so runs can compare sets."""

    key: str
    text: str


def silent_after_s(feed: Feed) -> float:
    return max(SILENT_MIN_S, SILENT_AFTER_INTERVALS * feed.interval_s)


def find_problems(
    data_dir: Path,
    feeds: Iterable[Feed],
    now: datetime,
    min_free_gb: float,
    free_bytes: Callable[[Path], int] | None = None,
) -> list[Problem]:
    """Return the problems open at ``now``, silent feeds first."""
    feeds = list(feeds)
    problems: list[Problem] = []
    if feeds:
        lookback = max(silent_after_s(f) for f in feeds)
        polls = read_polls(data_dir, now - timedelta(seconds=lookback), now)
        for feed in feeds:
            limit = silent_after_s(feed)
            recent = polls.get(feed.name, [])
            ok = [p for p in recent if p.ok]
            if ok:
                age = (now - ok[-1].fetched_at).total_seconds()
                if age <= limit:
                    continue
                since = f"last success {ok[-1].fetched_at:%H:%M} UTC"
            else:
                activated = first_seen(data_dir, feed.name)
                if activated is None or activated > now - timedelta(
                    seconds=limit
                ):
                    continue  # not enabled yet, or too new to judge
                since = f"no success for over {limit / 60:.0f} min"
            last = recent[-1] if recent else None
            why = (
                f"; last attempt {last.status or 'network error'}"
                if last and not last.ok
                else "; no attempts (collector down?)"
                if last is None
                else ""
            )
            problems.append(
                Problem(
                    f"silent:{feed.name}", f"{feed.name} silent: {since}{why}"
                )
            )
    free = (free_bytes or _free_bytes)(data_dir)
    if free < min_free_gb * 1e9:
        problems.append(
            Problem(
                "disk",
                f"disk low: {free / 1e9:.1f} GB free "
                f"(alert below {min_free_gb:.0f} GB)",
            )
        )
    return problems


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"open": {}, "last_alert": None}


def decide(
    state: dict, problems: list[Problem], now: datetime, remind: timedelta
) -> tuple[str | None, dict]:
    """Return the message to send (or None) and the next state."""
    current = {p.key: p.text for p in problems}
    before = state.get("open", {})
    new = [k for k in current if k not in before]
    fixed = [k for k in before if k not in current]
    last = state.get("last_alert")
    last_at = datetime.fromisoformat(last) if last else None

    lines: list[str] = []
    if new:
        lines += ["NEW: " + current[k] for k in new]
    if fixed:
        lines += ["RECOVERED: " + before[k].split(":")[0] for k in fixed]
    if not lines and current and (last_at is None or now - last_at >= remind):
        lines = ["STILL OPEN: " + t for t in current.values()]
    if lines and not current:
        lines.append("All feeds healthy again.")

    message = "\n".join(lines) if lines else None
    return message, {
        "open": current,
        "last_alert": now.isoformat() if message else last,
        "checked_at": now.isoformat(),
    }


def send_ntfy(topic: str, title: str, message: str, urgent: bool) -> None:
    resp = httpx.post(
        f"{NTFY_SERVER}/{topic}",
        content=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "high" if urgent else "default",
            "Tags": "rotating_light" if urgent else "white_check_mark",
        },
        timeout=10,
    )
    resp.raise_for_status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "data")),
    )
    parser.add_argument(
        "--feeds",
        type=Path,
        default=Path(os.environ.get("FEEDS_CONFIG", "config/feeds.toml")),
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("docs/reports/watch-state.json"),
        help="Where open problems are remembered between runs.",
    )
    parser.add_argument(
        "--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB
    )
    parser.add_argument("--remind", default=DEFAULT_REMIND)
    parser.add_argument(
        "--ntfy-topic",
        default=os.environ.get("ALERT_NTFY_TOPIC", ""),
        help="ntfy topic; without one, problems are only printed.",
    )
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    problems = find_problems(
        args.data_dir, load_feeds(args.feeds), now, args.min_free_gb
    )
    state = load_state(args.state)
    message, new_state = decide(
        state, problems, now, parse_window(args.remind)
    )

    stamp = f"{now:%Y-%m-%d %H:%M} UTC"
    if problems:
        print(f"{stamp} watch: {len(problems)} problem(s)")
        for p in problems:
            print(f"  {p.text}")
    else:
        print(f"{stamp} watch: all feeds healthy")

    if message:
        if args.ntfy_topic:
            try:
                send_ntfy(
                    args.ntfy_topic,
                    "Transit collector: problem"
                    if problems
                    else "Transit collector: recovered",
                    message,
                    urgent=bool(problems),
                )
                print("  alert sent")
            except httpx.HTTPError as exc:
                # Keep the old state so the alert is retried next run.
                print(f"  alert failed: {type(exc).__name__}")
                new_state = {**state, "checked_at": now.isoformat()}
        else:
            print("  (no ntfy topic set; alert not sent)")

    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
