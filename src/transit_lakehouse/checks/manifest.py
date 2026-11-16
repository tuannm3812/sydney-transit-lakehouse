"""Read the collector's poll manifest for a time window."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

OK_OUTCOMES = frozenset({"new", "unchanged"})
_WINDOW_RE = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_window(text: str) -> timedelta:
    """Parse ``15m``, ``24h`` or ``7d`` into a timedelta."""
    match = _WINDOW_RE.match(text.strip())
    if not match:
        raise ValueError(f"Bad window {text!r}; use e.g. 15m, 24h, 7d")
    value, unit = match.groups()
    return timedelta(seconds=float(value) * _UNITS[unit])


@dataclass(frozen=True)
class Poll:
    feed: str
    fetched_at: datetime
    status: int | None
    outcome: str
    size_bytes: int
    header_timestamp: int | None
    error: str | None
    headers: dict[str, str]

    @property
    def ok(self) -> bool:
        return self.outcome in OK_OUTCOMES

    @property
    def age_s(self) -> float | None:
        """Seconds between the feed's own timestamp and our fetch."""
        if self.header_timestamp is None:
            return None
        return self.fetched_at.timestamp() - self.header_timestamp


def _hours(start: datetime, end: datetime) -> Iterator[datetime]:
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur < end:
        yield cur
        cur += timedelta(hours=1)


def read_polls(
    data_dir: Path, start: datetime, end: datetime
) -> dict[str, list[Poll]]:
    """Return polls per feed with ``start <= fetched_at < end``."""
    root = data_dir / "manifest"
    out: dict[str, list[Poll]] = {}
    if not root.exists():
        return out
    for feed_dir in sorted(root.glob("feed=*")):
        feed = feed_dir.name.removeprefix("feed=")
        polls: list[Poll] = []
        for hour in _hours(start.astimezone(UTC), end.astimezone(UTC)):
            path = feed_dir / f"date={hour:%Y-%m-%d}" / f"hour={hour:%H}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn line from a crash mid-write
                ts = datetime.fromisoformat(raw["fetched_at"])
                if not start <= ts < end:
                    continue
                polls.append(
                    Poll(
                        feed=feed,
                        fetched_at=ts,
                        status=raw.get("status"),
                        outcome=raw.get("outcome", ""),
                        size_bytes=int(raw.get("size_bytes") or 0),
                        header_timestamp=raw.get("header_timestamp"),
                        error=raw.get("error"),
                        headers=raw.get("headers") or {},
                    )
                )
        polls.sort(key=lambda p: p.fetched_at)
        out[feed] = polls
    return out


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[rank]
