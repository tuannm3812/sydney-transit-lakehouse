"""Raw hours as an ordered stream of messages (no Kafka needed here).

The producer and the tests share this: it reads the offload tars
(``raw/feed=…/date=…/hour=HH.tar``, D-026) one hour at a time, exactly
as ``worker replay`` does, and yields every landed file of every feed in
fetch order. Each feed starts with one **seed** message: the last
readable file of the hour before the window, which the stream loads as
"previous" without writing rows, so the first rows are diffed as the
worker diffed them (D-027, D-049).
"""

from __future__ import annotations

import heapq
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from transit_lakehouse.collector.config import Feed
from transit_lakehouse.worker.decode import parse_name, read_snapshot
from transit_lakehouse.worker.replay import TarSource, _extract, _key, hours
from transit_lakehouse.worker.runner import TABLES_FOR_KIND

TOPIC_PREFIX = "raw."  # one topic per feed kind, keyed by feed (D-023)


def topic_for(kind: str) -> str:
    return TOPIC_PREFIX + kind


def kind_of_topic(topic: str) -> str:
    return topic.removeprefix(TOPIC_PREFIX)


def streamable(feeds: list[Feed]) -> list[Feed]:
    """Realtime feeds the worker builds; the static timetable stays with
    the worker (one ~300 MB zip a few times a day is not a stream)."""
    return [f for f in feeds if f.kind in TABLES_FOR_KIND]


@dataclass(frozen=True)
class Message:
    feed: str
    kind: str
    source_file: str  # raw/feed=…/date=…/hour=…/<stamp>_<sha12>.pb.gz
    fetched_at: datetime
    path: Path  # where the bytes are while the hour is on disk
    seed: bool = False

    @property
    def topic(self) -> str:
        return topic_for(self.kind)

    def value(self) -> bytes:
        return self.path.read_bytes()


def _seed(
    source: TarSource, feed: Feed, start: datetime, scratch: Path
) -> Message | None:
    before = start.replace(minute=0, second=0, microsecond=0)
    files = _extract(source, feed.name, before - timedelta(hours=1), scratch)
    for rel in reversed(files):
        if not read_snapshot(scratch, feed.name, rel).failed:
            return _message(feed, rel, scratch, seed=True)
    return None


def _message(feed: Feed, rel: str, root: Path, seed: bool = False):
    fetched, _ = parse_name(rel.rsplit("/", 1)[-1])
    return Message(feed.name, feed.kind, rel, fetched, root / rel, seed)


def missing_hours(
    source, feeds: list[Feed], start: datetime, end: datetime
) -> list[str]:
    """Hour tars the window needs that the source does not hold."""
    return [
        _key(feed.name, hour)
        for feed in streamable(feeds)
        for hour in hours(start - timedelta(hours=1), end)
        if not source.exists(_key(feed.name, hour))
    ]


@dataclass
class Missing:
    keys: list[str]


def stream_hours(
    source: TarSource,
    feeds: list[Feed],
    start: datetime,
    end: datetime,
    scratch: Path,
    missing: Missing | None = None,
) -> Iterator[Message]:
    """Seeds first, then each hour's files of all feeds in fetch order.

    Only one hour is on disk at a time: an hour's folder is deleted once
    the caller has consumed all of its messages."""
    feeds = streamable(feeds)
    seed_dir = scratch / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    for feed in feeds:
        msg = _seed(source, feed, start, seed_dir)
        if msg is not None:
            yield msg
    shutil.rmtree(seed_dir, ignore_errors=True)
    for hour in hours(start, end):
        hour_dir = Path(tempfile.mkdtemp(prefix=f"{hour:%H}-", dir=scratch))
        per_feed = []
        for feed in feeds:
            files = _extract(source, feed.name, hour, hour_dir)
            if not files and missing is not None:
                missing.keys.append(_key(feed.name, hour))
            per_feed.append([_message(feed, r, hour_dir) for r in files])
        yield from heapq.merge(*per_feed, key=lambda m: (m.fetched_at, m.feed))
        shutil.rmtree(hour_dir, ignore_errors=True)
