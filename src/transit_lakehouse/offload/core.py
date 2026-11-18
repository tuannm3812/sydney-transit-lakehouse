"""Offload closed UTC hours to S3 and prune verified local copies (D-026).

Each closed hour becomes one object per feed, so the request count stays
near 300 a day however many snapshots land::

    raw/feed=<feed>/date=YYYY-MM-DD/hour=HH.tar      (snapshots, as landed)
    manifest/feed=<feed>/date=YYYY-MM-DD/hour=HH.jsonl

The tar is uncompressed because every member is already gzip or zip.
Members are sorted and their mtimes fixed, so a re-run builds the same
bytes. S3 validates each upload's SHA-256 on receipt, the object is read
back (size and digest) before a local state file records it, and
``prune`` checks S3 again before deleting anything.
Manifests are uploaded but never pruned: capture checks read them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

STATE_DIR = "_offload"


class S3Like(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Partition:
    kind: str  # raw | manifest
    feed: str
    date: str
    hour: str
    path: Path  # hour directory (raw) or hour file (manifest)

    @property
    def end(self) -> datetime:
        start = datetime.strptime(
            f"{self.date} {self.hour}", "%Y-%m-%d %H"
        ).replace(tzinfo=UTC)
        return start + timedelta(hours=1)

    @property
    def key(self) -> str:
        ext = "tar" if self.kind == "raw" else "jsonl"
        return (
            f"{self.kind}/feed={self.feed}/date={self.date}/"
            f"hour={self.hour}.{ext}"
        )


def partitions(data_dir: Path) -> Iterator[Partition]:
    """Every raw hour directory and manifest hour file, oldest first."""
    for kind in ("raw", "manifest"):
        root = data_dir / kind
        if not root.exists():
            continue
        for feed_dir in sorted(root.glob("feed=*")):
            feed = feed_dir.name.removeprefix("feed=")
            for date_dir in sorted(feed_dir.glob("date=*")):
                date = date_dir.name.removeprefix("date=")
                pattern = "hour=*" if kind == "raw" else "hour=*.jsonl"
                for p in sorted(date_dir.glob(pattern)):
                    if kind == "raw" and not p.is_dir():
                        continue
                    hour = p.name.removeprefix("hour=").removesuffix(".jsonl")
                    yield Partition(kind, feed, date, hour, p)


def state_path(data_dir: Path, part: Partition) -> Path:
    return data_dir / STATE_DIR / (part.key + ".json")


def _members(part: Partition) -> list[Path]:
    return sorted(
        p
        for p in part.path.iterdir()
        if p.is_file() and not p.name.endswith(".tmp")
    )


def build_tar(part: Partition, out: Path) -> None:
    """Write a deterministic, uncompressed tar of one raw hour."""
    with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as tar:
        for p in _members(part):
            info = tar.gettarinfo(str(p), arcname=p.name)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with p.open("rb") as fh:
                tar.addfile(info, fh)


def sha256_b64(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def remote_matches(
    s3: S3Like, bucket: str, key: str, size: int, digest: str
) -> bool:
    try:
        head = s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    except Exception:  # noqa: BLE001 - missing object or no access
        return False
    # S3 rejects a put whose body does not match ChecksumSHA256, so the
    # digest we store in metadata at put time is a verified claim. Use
    # S3's own checksum when it returns one.
    remote = head.get("ChecksumSHA256") or head.get("Metadata", {}).get(
        "sha256"
    )
    return head.get("ContentLength") == size and remote == digest


@dataclass
class Result:
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0


def upload(
    data_dir: Path,
    s3: S3Like,
    bucket: str,
    now: datetime,
    grace: timedelta = timedelta(minutes=15),
    dry_run: bool = False,
    log=print,
) -> Result:
    """Upload every closed partition that has no state file yet."""
    res = Result()
    tmp_dir = data_dir / STATE_DIR / "tmp"
    for part in partitions(data_dir):
        if part.end + grace > now:
            continue  # the collector may still write this hour
        marker = state_path(data_dir, part)
        if marker.exists():
            res.skipped += 1
            continue
        if part.kind == "raw" and not _members(part):
            continue
        if dry_run:
            log(f"would upload {part.key}")
            res.uploaded += 1
            continue
        tmp_dir.mkdir(parents=True, exist_ok=True)
        if part.kind == "raw":
            body = tmp_dir / "upload.tar"
            build_tar(part, body)
        else:
            body = part.path
        size = body.stat().st_size
        digest = sha256_b64(body)
        try:
            if not remote_matches(s3, bucket, part.key, size, digest):
                with body.open("rb") as fh:
                    s3.put_object(
                        Bucket=bucket,
                        Key=part.key,
                        Body=fh,
                        ChecksumSHA256=digest,
                        Metadata={"sha256": digest},
                    )
            if not remote_matches(s3, bucket, part.key, size, digest):
                raise RuntimeError("uploaded object does not match")
        except Exception as exc:  # noqa: BLE001 - keep going, report
            log(f"FAILED {part.key}: {type(exc).__name__}: {exc}")
            res.failed += 1
            continue
        finally:
            if part.kind == "raw":
                body.unlink(missing_ok=True)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "key": part.key,
                    "size": size,
                    "sha256_b64": digest,
                    "files": len(_members(part))
                    if part.kind == "raw"
                    else None,
                    "uploaded_at": now.isoformat(),
                }
            )
        )
        res.uploaded += 1
        res.bytes += size
        log(f"uploaded {part.key} ({size:,} bytes)")
    return res


def prune(
    data_dir: Path,
    s3: S3Like,
    bucket: str,
    now: datetime,
    keep: timedelta,
    dry_run: bool = False,
    log=print,
) -> Result:
    """Delete local raw hours older than ``keep`` that S3 verifiably holds.

    The S3 object is checked again here, and the local hour must still
    match what was uploaded (same file count), so a late write or a
    missing object blocks the delete.
    """
    res = Result()
    for part in partitions(data_dir):
        if part.kind != "raw" or part.end + keep > now:
            continue
        marker = state_path(data_dir, part)
        if not marker.exists():
            continue
        meta = json.loads(marker.read_text())
        if meta.get("pruned_at"):
            continue
        ok = len(_members(part)) == meta["files"] and remote_matches(
            s3, bucket, part.key, meta["size"], meta["sha256_b64"]
        )
        if not ok:
            log(f"KEPT {part.key}: local or S3 copy no longer matches")
            res.failed += 1
            continue
        if dry_run:
            log(f"would delete {part.path}")
            res.uploaded += 1
            continue
        res.bytes += sum(p.stat().st_size for p in _members(part))
        shutil.rmtree(part.path)
        meta["pruned_at"] = now.isoformat()
        marker.write_text(json.dumps(meta))
        res.uploaded += 1
        log(f"deleted local {part.path}")
    return res
