"""Read landed raw snapshots back into GTFS-realtime entities.

The raw files are the source of truth (D-005, D-023). This module turns
one file into a :class:`Snapshot`: its fetch time (from the file name),
the feed header timestamp and the decoded entities.

Decoding is tolerant. TfNSW sometimes sends fields the official bindings
cannot read (a string in ``TripDescriptor`` field 7, D-020), which makes
a whole-message parse fail. When that happens the envelope is walked by
hand and each entity is parsed on its own, so one bad entity costs one
row, not the snapshot. Failed entities are counted, never dropped
silently.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.collector.poller import _read_varint

STAMP_FORMAT = "%Y%m%dT%H%M%S.%fZ"


@dataclass
class Snapshot:
    """One landed snapshot, decoded."""

    feed: str
    source_file: str  # path relative to data_dir, e.g. raw/feed=…/….pb.gz
    fetched_at: datetime
    sha12: str
    stored_bytes: int
    header_ts: int | None = None
    entities: list[gtfs_realtime_pb2.FeedEntity] = field(default_factory=list)
    entity_errors: int = 0
    decode_error: str = ""  # whole-file failure, or partial_decode note

    @property
    def partial(self) -> bool:
        return bool(self.decode_error)


def parse_name(name: str) -> tuple[datetime, str]:
    """Return (fetched_at, sha12) from a landed file name."""
    stem = name.split(".pb")[0]
    stamp, _, sha12 = stem.partition("_")
    fetched = datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)
    return fetched, sha12


def read_payload(path: Path) -> bytes:
    data = path.read_bytes()
    return gzip.decompress(data) if path.name.endswith(".gz") else data


def _envelope(payload: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (field number, bytes) for length-delimited top-level fields."""
    pos = 0
    while pos < len(payload):
        key, pos = _read_varint(payload, pos)
        field_no, wire = key >> 3, key & 7
        if wire == 2:
            size, pos = _read_varint(payload, pos)
            if pos + size > len(payload):
                raise DecodeError("truncated field")
            yield field_no, payload[pos : pos + size]
            pos += size
        elif wire == 0:
            _, pos = _read_varint(payload, pos)
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
        else:
            raise DecodeError(f"unexpected wire type {wire}")


def decode(payload: bytes, snap: Snapshot) -> Snapshot:
    """Fill ``snap`` with the header timestamp and entities."""
    msg = gtfs_realtime_pb2.FeedMessage()
    try:
        msg.ParseFromString(payload)
    except DecodeError as exc:
        return _decode_per_entity(payload, snap, str(exc))
    if msg.header.HasField("timestamp"):
        snap.header_ts = msg.header.timestamp
    snap.entities = list(msg.entity)
    return snap


def _decode_per_entity(
    payload: bytes, snap: Snapshot, full_error: str
) -> Snapshot:
    try:
        for field_no, body in _envelope(payload):
            if field_no == 1:
                header = gtfs_realtime_pb2.FeedHeader()
                header.ParseFromString(body)
                if header.HasField("timestamp"):
                    snap.header_ts = header.timestamp
            elif field_no == 2:
                ent = gtfs_realtime_pb2.FeedEntity()
                try:
                    ent.ParseFromString(body)
                except DecodeError:
                    snap.entity_errors += 1
                    continue
                snap.entities.append(ent)
    except DecodeError as exc:
        snap.decode_error = f"decode_error: {full_error}; envelope: {exc}"
        return snap
    snap.decode_error = f"partial_decode: {full_error}"
    return snap


def read_snapshot(data_dir: Path, feed: str, rel: str) -> Snapshot:
    """Read and decode one landed file."""
    path = data_dir / rel
    fetched, sha12 = parse_name(path.name)
    snap = Snapshot(
        feed=feed,
        source_file=rel,
        fetched_at=fetched,
        sha12=sha12,
        stored_bytes=path.stat().st_size,
    )
    try:
        payload = read_payload(path)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        snap.decode_error = f"read_error: {exc}"
        return snap
    return decode(payload, snap)


def landed_after(data_dir: Path, feed: str, after: str) -> list[str]:
    """Landed realtime files of a feed whose path sorts after ``after``.

    Paths sort by UTC date, hour and fetch stamp (D-006), so a plain
    string compare against the last processed path is the watermark.
    Whole date and hour folders before the watermark are skipped
    without listing them.
    """
    root = data_dir / "raw" / f"feed={feed}"
    if not root.exists():
        return []
    after_parts = after.split("/")
    after_date = after_parts[2] if len(after_parts) > 3 else ""
    after_hour = after_parts[3] if len(after_parts) > 3 else ""
    out: list[str] = []
    for date_dir in sorted(root.glob("date=*")):
        if date_dir.name < after_date:
            continue
        for hour_dir in sorted(date_dir.glob("hour=*")):
            if date_dir.name == after_date and hour_dir.name < after_hour:
                continue
            for path in sorted(hour_dir.glob("*.pb*")):
                if path.name.endswith(".tmp"):
                    continue  # mid-write (the lander renames atomically)
                rel = path.relative_to(data_dir).as_posix()
                if rel > after:
                    out.append(rel)
    return out
