"""Land raw protobuf snapshots and an append-only poll manifest.

Layout under ``data_dir`` (all partitions in UTC, see D-006)::

    raw/feed=<feed>/date=YYYY-MM-DD/hour=HH/<fetched_at>_<sha12>.pb.gz
    manifest/feed=<feed>/date=YYYY-MM-DD/hour=HH.jsonl

Every poll attempt gets a manifest line, including failures and
unchanged snapshots, because capture rate is computed from it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class PollRecord:
    """One poll attempt as written to the manifest."""

    feed: str
    fetched_at: str  # ISO-8601 UTC, stamped when the response arrived
    status: int | None  # HTTP status, None on network error
    outcome: str  # new | unchanged | http_error | network_error
    latency_ms: float
    size_bytes: int = 0  # payload as received, before compression
    stored_bytes: int = 0  # on disk after gzip (D-017)
    sha256: str | None = None
    header_timestamp: int | None = None  # FeedHeader.timestamp, epoch s
    entity_count: int | None = None
    path: str | None = None  # relative to data_dir when landed
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def partition(feed: str, ts: datetime) -> tuple[str, str]:
    """Return (date, hour) partition values for a UTC timestamp."""
    ts = ts.astimezone(UTC)
    return ts.strftime("%Y-%m-%d"), ts.strftime("%H")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # readers never see a half-written snapshot


class Lander:
    """Writes snapshots and manifest lines; remembers last hash per feed."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._last_sha: dict[str, str] = {}
        self.last_stored_bytes = 0

    def is_unchanged(self, feed: str, digest: str) -> bool:
        if feed not in self._last_sha:
            self._last_sha[feed] = self._recover_last_sha(feed) or ""
        return self._last_sha[feed] == digest

    def land(self, feed: str, ts: datetime, payload: bytes) -> str:
        """Write a snapshot and return its path relative to data_dir."""
        digest = sha256_hex(payload)
        date, hour = partition(feed, ts)
        stamp = ts.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        rel = (
            Path("raw")
            / f"feed={feed}"
            / f"date={date}"
            / f"hour={hour}"
            / f"{stamp}_{digest[:12]}.pb.gz"
        )
        # Lossless and deterministic (mtime=0), so the bytes TfNSW sent
        # are recoverable exactly; sha256 is of the uncompressed payload.
        packed = gzip.compress(payload, compresslevel=6, mtime=0)
        _atomic_write(self.data_dir / rel, packed)
        self._last_sha[feed] = digest
        self.last_stored_bytes = len(packed)
        return rel.as_posix()

    def record(self, rec: PollRecord) -> None:
        ts = datetime.fromisoformat(rec.fetched_at)
        date, hour = partition(rec.feed, ts)
        path = (
            self.data_dir
            / "manifest"
            / f"feed={rec.feed}"
            / f"date={date}"
            / f"hour={hour}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(rec), separators=(",", ":"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _recover_last_sha(self, feed: str) -> str | None:
        """Find the newest landed hash so restarts don't re-land."""
        root = self.data_dir / "manifest" / f"feed={feed}"
        if not root.exists():
            return None
        for path in sorted(root.glob("date=*/hour=*.jsonl"), reverse=True):
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn last line after a crash
                if rec.get("outcome") == "new" and rec.get("sha256"):
                    return str(rec["sha256"])
        return None
