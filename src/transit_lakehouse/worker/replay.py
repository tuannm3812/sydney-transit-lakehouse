"""Rebuild the tables from offloaded raw hours, and prove they match.

This is the Phase 1 gate (docs/9_phase1.md): raw snapshots are the
source of truth (D-005, D-023), so replaying a window of them into an
empty lakehouse must give the same silver rows as the live worker wrote,
apart from ``ingested_at``.

``replay`` reads the hourly tars the offload job writes
(``raw/feed=…/date=…/hour=HH.tar``, D-026), either from S3 or from a
local folder with the same layout. It works one hour at a time into a
scratch ``raw/`` tree and deletes each hour once built, keeping only the
last snapshot as the seed for the next, so a day of bus trip updates
never needs more than one hour on disk. The hour before the window is
read only for its last snapshot, so the first rows are diffed exactly
as the live worker diffed them.

``verify`` compares two lakehouses hour by hour per feed and table with
a row count and an order-free digest computed in DuckDB, streaming from
Iceberg, so a whole day fits in the worker's memory.
"""

# Column names come from the Iceberg schema and filters from parsed
# datetimes and configured feed names, never from data:
# ruff: noqa: S608

from __future__ import annotations

import logging
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pyiceberg.catalog import Catalog
from pyiceberg.expressions import (
    And,
    EqualTo,
    GreaterThanOrEqual,
    LessThan,
)

from transit_lakehouse.collector.config import Feed
from transit_lakehouse.worker.decode import read_snapshot
from transit_lakehouse.worker.runner import TABLES_FOR_KIND, run_once
from transit_lakehouse.worker.tables import commit, ensure_tables

log = logging.getLogger(__name__)
IGNORED_COLUMNS = frozenset({"ingested_at"})


class TarSource(Protocol):
    def fetch(self, key: str, dest: Path) -> bool:
        """Copy one object to ``dest``; False when it does not exist."""


class LocalTars:
    """Tars in a folder laid out like the bucket (``raw/feed=…``)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def fetch(self, key: str, dest: Path) -> bool:
        src = self.root / key
        if not src.exists():
            return False
        shutil.copyfile(src, dest)
        return True


class S3Tars:
    """Tars in the offload bucket (read-only: get and head)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # botocore ClientError 404
            if "404" in str(exc) or "Not Found" in str(exc):
                return False
            raise
        return True

    def fetch(self, key: str, dest: Path) -> bool:
        if not self.exists(key):
            return False
        self.client.download_file(self.bucket, key, str(dest))
        return True


def hours(start: datetime, end: datetime) -> Iterator[datetime]:
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur < end:
        yield cur
        cur += timedelta(hours=1)


def _key(feed: str, hour: datetime) -> str:
    return f"raw/feed={feed}/date={hour:%Y-%m-%d}/hour={hour:%H}.tar"


def _hour_dir(data_dir: Path, feed: str, hour: datetime) -> Path:
    return (
        data_dir
        / "raw"
        / f"feed={feed}"
        / f"date={hour:%Y-%m-%d}"
        / f"hour={hour:%H}"
    )


def _extract(source: TarSource, feed: str, hour: datetime, data_dir: Path):
    """Unpack one hour; return its files' paths relative to data_dir."""
    target = _hour_dir(data_dir, feed, hour)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=data_dir, suffix=".tar") as tmp:
        if not source.fetch(_key(feed, hour), Path(tmp.name)):
            return []
        with tarfile.open(tmp.name) as tar:
            for member in tar.getmembers():
                # Offload tars hold bare snapshot file names only.
                name = Path(member.name).name
                if not member.isfile() or name != member.name:
                    raise ValueError(f"unexpected tar member {member.name}")
                with (
                    tar.extractfile(member) as src,
                    (target / name).open("wb") as dst,
                ):
                    shutil.copyfileobj(src, dst)
    return sorted(p.relative_to(data_dir).as_posix() for p in target.iterdir())


@dataclass
class ReplayStats:
    hours: int = 0
    files: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    missing_hours: list[str] = field(default_factory=list)


def replay(
    source: TarSource,
    feeds: list[Feed],
    start: datetime,
    end: datetime,
    data_dir: Path,
    catalog: Catalog,
) -> ReplayStats:
    """Rebuild realtime tables for ``[start, end)`` into ``catalog``."""
    stats = ReplayStats()
    tables = ensure_tables(catalog)
    for feed in feeds:
        if feed.kind not in TABLES_FOR_KIND:
            continue
        # Seed: the last snapshot of the hour before the window, marked
        # as already processed so its rows are not written.
        before = start.replace(minute=0, second=0, microsecond=0)
        seed = _extract(
            source, feed.name, before - timedelta(hours=1), data_dir
        )
        if seed:
            seed = _trim_to_readable(data_dir, feed.name, seed)
            marks = {feed.name: seed[-1]}
            for name in TABLES_FOR_KIND[feed.kind]:
                # An earlier feed's run committed to this table through
                # its own handle; a stale one makes PyIceberg try to
                # delete metadata files that are already gone.
                tables[name].refresh()
                empty = {f.name: [] for f in tables[name].schema().as_arrow()}
                commit(name, tables[name], empty, marks)
        for hour in hours(start, end):
            files = _extract(source, feed.name, hour, data_dir)
            if not files:
                stats.missing_hours.append(_key(feed.name, hour))
                continue
            run = run_once(data_dir, catalog, [feed])
            stats.hours += 1
            stats.files += run.files
            for name, n in run.rows.items():
                stats.rows[name] = stats.rows.get(name, 0) + n
            # Keep the next hour's seed: the files from the newest
            # readable one on (D-049), usually just the newest file.
            seed = _trim_to_readable(data_dir, feed.name, seed + files)
    return stats


def _trim_to_readable(
    data_dir: Path, feed: str, files: list[str]
) -> list[str]:
    """Delete ``files`` before the newest one that did not fail as a
    whole and return the rest. A run seeds from the last readable file
    at or before its watermark (D-049), so that file must stay."""
    keep = 0
    for i in range(len(files) - 1, -1, -1):
        if not read_snapshot(data_dir, feed, files[i]).failed:
            keep = i
            break
    for gone in files[:keep]:
        (data_dir / gone).unlink(missing_ok=True)
    return files[keep:]


@dataclass
class Difference:
    table: str
    feed: str
    hour: str
    left: tuple[int, int]
    right: tuple[int, int]


def _digest(con, catalog: Catalog, table: str, feed: str, hour: datetime):
    """Row count and an order-free content digest of one table, feed and
    hour. DuckDB reads the Parquet files itself, under its memory limit,
    so a peak bus hour fits the worker's cap: going through PyIceberg's
    Arrow reader decoded whole files at once and was OOM-killed at
    700 MB on the server rehearsal (2026-11-23)."""
    tbl = catalog.load_table(table)
    cols = [
        f.name for f in tbl.schema().fields if f.name not in IGNORED_COLUMNS
    ]
    lo, hi = hour, hour + timedelta(hours=1)
    row_filter = And(
        EqualTo("feed", feed),
        And(
            GreaterThanOrEqual("fetched_at", lo.isoformat()),
            LessThan("fetched_at", hi.isoformat()),
        ),
    )
    tasks = list(tbl.scan(row_filter=row_filter).plan_files())
    if any(t.delete_files for t in tasks):
        raise RuntimeError(f"{table}: delete files are not supported here")
    if not tasks:
        return 0, 0
    text = ", ".join(
        f"coalesce(CAST(\"{c}\" AS VARCHAR), '\\N')" for c in cols
    )
    cur = con.cursor()  # its own result, closed before the next
    count, digest = cur.execute(
        f"""
        SELECT count(*),
               coalesce(sum(md5_number_lower(concat_ws('|', {text}))
                            % 1000000007), 0)
               + coalesce(bit_xor(md5_number_upper(
                     concat_ws('|', {text}))) % 1000000007, 0)
        FROM read_parquet(?, union_by_name = true)
        WHERE feed = ? AND fetched_at >= ? AND fetched_at < ?
        """,
        [[t.file.file_path for t in tasks], feed, lo, hi],
    ).fetchone()
    cur.close()
    return int(count), int(digest)


def verify(
    left: Catalog,
    right: Catalog,
    feeds: list[Feed],
    start: datetime,
    end: datetime,
) -> tuple[int, list[Difference]]:
    """Compare two lakehouses per table, feed and hour. Returns
    (number of hour-slices compared, differences)."""
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'")
    con.execute("SET threads = 2")
    con.execute("SET TimeZone = 'UTC'")
    con.execute("SET enable_progress_bar = false")
    compared, diffs = 0, []
    for feed in feeds:
        for table in TABLES_FOR_KIND.get(feed.kind, ()):
            for hour in hours(start, end):
                a = _digest(con, left, table, feed.name, hour)
                b = _digest(con, right, table, feed.name, hour)
                compared += 1
                if a != b:
                    diffs.append(
                        Difference(
                            table, feed.name, f"{hour:%Y-%m-%dT%H}", a, b
                        )
                    )
    con.close()
    return compared, diffs


def parse_hour(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H").replace(tzinfo=UTC)
