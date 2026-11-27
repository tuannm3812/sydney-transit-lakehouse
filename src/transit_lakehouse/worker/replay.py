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

It can also build straight into an existing lakehouse, which is how the
gate-day backfill runs (D-040, D-043): ``--scratch`` keeps the hour
files apart from the collector's ``raw/`` tree, and the tables'
watermarks decide where each feed carries on. An empty table starts at
``start``, and a feed that has a watermark carries on right after it
(a cut run resumes; a window that starts later than the watermark is
moved back to it), so the history never gets a hole. Windows therefore
run oldest first, each starting where the last ended, and the live
worker carries on from the last one's watermark (D-056). Every
``maintain_every`` hours the run compacts the hours it built and
expires every snapshot but the current one (incident I-20). Static
timetable hours (``static_gtfs``) are loaded the same way, one zip at a
time (D-029).

``verify`` compares two lakehouses hour by hour per feed and table with
a row count and an order-free digest computed in DuckDB, streaming from
Iceberg, so a whole day fits in the worker's memory.
"""

# Column names come from the Iceberg schema and filters from parsed
# datetimes and configured feed names, never from data:
# ruff: noqa: S608

from __future__ import annotations

import json
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
from transit_lakehouse.worker.runner import (
    TABLES_FOR_KIND,
    expire,
    run_once,
)
from transit_lakehouse.worker.tables import (
    commit,
    ensure_tables,
    watermarks,
)
from transit_lakehouse.worker.timetable import (
    FEED as TIMETABLE_FEED,
)
from transit_lakehouse.worker.timetable import (
    VERSION_TABLE,
    ensure_timetable_tables,
    run_timetable,
)

log = logging.getLogger(__name__)
IGNORED_COLUMNS = frozenset({"ingested_at"})


class TarSource(Protocol):
    def fetch(self, key: str, dest: Path) -> bool:
        """Copy one object to ``dest``; False when it does not exist."""


class LocalTars:
    """Tars in a folder laid out like the bucket (``raw/feed=…``)."""

    def __init__(self, root: Path) -> None:
        self.root = root

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

    def fetch(self, key: str, dest: Path) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # botocore ClientError 404
            if "404" in str(exc) or "Not Found" in str(exc):
                return False
            raise
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
    timetables: int = 0
    maintenance: int = 0
    skipped: list[str] = field(default_factory=list)


# Compact and expire after this many replayed hours. Without it every
# commit rewrites an ever larger metadata file: a cloud replay slowed
# from about 40 s to 6 min per bus hour after ~1,850 snapshots (I-20).
MAINTAIN_EVERY = 6


def _hour_of(rel: str) -> datetime:
    """The UTC hour of a raw file's path relative to the data dir."""
    parts = dict(p.split("=", 1) for p in rel.split("/") if "=" in p)
    return datetime.strptime(
        f"{parts['date']}T{parts['hour']}", "%Y-%m-%dT%H"
    ).replace(tzinfo=UTC)


def _state_path(scratch: Path, feed: str) -> Path:
    return scratch / "replay-state" / f"{feed}.json"


def _load_state(scratch: Path, feed: str) -> dict:
    try:
        return json.loads(_state_path(scratch, feed).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(scratch: Path, feed: str, state: dict) -> None:
    path = _state_path(scratch, feed)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


def _feed_marks(tables, names, feed: str) -> dict[str, str]:
    return {n: watermarks(tables[n]).get(feed, "") for n in names}


def _plan_feed(
    feed: str,
    marks: dict[str, str],
    state: dict,
    scratch: Path,
    start: datetime,
) -> tuple[datetime, bool]:
    """Where a feed's replay starts, from its tables' watermarks and the
    scratch folder's record of the last hour it finished. Returns the
    first hour and whether the hour before it must be fetched as the
    seed.

    - No table has a watermark: a fresh start at ``start``.
    - The lowest watermark is the one recorded when an hour finished,
      and its file is still in the scratch folder: carry on with the
      next hour, seeded from that file (no download).
    - Anything else (a run cut mid-hour, a table behind the others, a
      lost scratch folder): fetch the lowest watermark's hour again, so
      every file after it is built. A table with no watermark at all
      while the others have one restarts from the recorded first hour.

    The watermark wins over ``start`` whenever it has one, so the
    history never gets a hole (D-056)."""
    low, high = min(marks.values()), max(marks.values())
    if not high:
        return start, True
    if low and state.get("mark") == low and (scratch / low).exists():
        first = _parse(state["done"]) + timedelta(hours=1)
        refetch = False
    elif low:
        first, refetch = _hour_of(low), True
    elif "first" in state:
        first, refetch = _parse(state["first"]), True
    else:
        raise ValueError(
            f"{feed}: some tables have no watermark and the scratch "
            "folder has no record of where this feed's replay began; "
            "rebuild the lakehouse"
        )
    if first < start:
        log.warning(
            "%s: the tables end at %s, so replay starts at %s, not %s",
            feed,
            low or "(empty)",
            f"{first:%Y-%m-%dT%H}",
            f"{start:%Y-%m-%dT%H}",
        )
    return first, refetch


def _parse(hour: str) -> datetime:
    return datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=UTC)


def _guard_scratch(scratch: Path) -> None:
    if (scratch / "manifest").exists():
        # The collector writes manifest/ next to raw/. Replay deletes
        # each hour's files once built, so it must never work there.
        raise ValueError(
            f"{scratch} holds a poll manifest: it is the collector's data "
            "folder. Give replay its own --scratch folder"
        )


def replay(
    source: TarSource,
    feeds: list[Feed],
    start: datetime,
    end: datetime,
    data_dir: Path,
    catalog: Catalog,
    *,
    maintain_every: int | None = MAINTAIN_EVERY,
    now=None,
) -> ReplayStats:
    """Rebuild the tables for ``[start, end)`` into ``catalog``, using
    ``data_dir`` as the scratch folder for the hour files."""
    _guard_scratch(data_dir)
    stats = ReplayStats()
    since = 0

    def maintain(force: bool = False) -> None:
        nonlocal since
        # None: never; 0: only at the end; n: every n built hours.
        if not since or maintain_every is None:
            return
        if not force and (maintain_every <= 0 or since < maintain_every):
            return
        from transit_lakehouse.worker.compact import compact

        done = compact(catalog, now=now)
        gone = expire(catalog, timedelta(0))
        log.info(
            "replay maintenance: %s; expired %d snapshots",
            done.summary(),
            sum(gone.values()),
        )
        stats.maintenance += 1
        since = 0

    for feed in feeds:
        if feed.kind == "static_gtfs":
            _replay_timetables(
                source, feed, start, end, data_dir, catalog, stats
            )
            continue
        if feed.kind not in TABLES_FOR_KIND:
            continue
        tables = ensure_tables(catalog)
        names = TABLES_FOR_KIND[feed.kind]
        marks = _feed_marks(tables, names, feed.name)
        state = _load_state(data_dir, feed.name)
        first, refetch = _plan_feed(feed.name, marks, state, data_dir, start)
        if first >= end:
            stats.skipped.append(feed.name)
            log.info("%s: already built to %s", feed.name, min(marks.values()))
            continue
        seed: list[str] = []
        if refetch:
            # Seed: the last snapshot of the hour before, so the first
            # rows are diffed as the live worker diffed them.
            seed = _extract(
                source, feed.name, first - timedelta(hours=1), data_dir
            )
            if seed:
                seed = _trim_to_readable(data_dir, feed.name, seed)
        elif min(marks.values()):
            seed = [min(marks.values())]
        if not max(marks.values()):
            state = {"first": f"{first:%Y-%m-%dT%H}"}
            _save_state(data_dir, feed.name, state)
        if seed and any(not m for m in marks.values()):
            # Mark the seed as processed in every table that has no
            # watermark yet, so its rows are not written.
            for name in names:
                if marks[name]:
                    continue
                # An earlier feed's run committed to this table through
                # its own handle; a stale one makes PyIceberg try to
                # delete metadata files that are already gone.
                tables[name].refresh()
                empty = {f.name: [] for f in tables[name].schema().as_arrow()}
                commit(name, tables[name], empty, {feed.name: seed[-1]})
        for hour in hours(first, end):
            files = _extract(source, feed.name, hour, data_dir)
            if not files:
                stats.missing_hours.append(_key(feed.name, hour))
                continue
            run = run_once(data_dir, catalog, [feed])
            if run.files:
                # A refetched hour may hold nothing past the watermark.
                stats.hours += 1
                since += 1
            stats.files += run.files
            for name, n in run.rows.items():
                stats.rows[name] = stats.rows.get(name, 0) + n
            # Keep the next hour's seed: the files from the newest
            # readable one on (D-049), usually just the newest file.
            seed = _trim_to_readable(data_dir, feed.name, seed + files)
            done = _feed_marks(ensure_tables(catalog), names, feed.name)
            state = {
                **state,
                "done": f"{hour:%Y-%m-%dT%H}",
                "mark": min(done.values()),
            }
            _save_state(data_dir, feed.name, state)
            maintain()
    maintain(force=True)
    return stats


def _replay_timetables(
    source: TarSource,
    feed: Feed,
    start: datetime,
    end: datetime,
    data_dir: Path,
    catalog: Catalog,
    stats: ReplayStats,
) -> None:
    """Load every timetable zip landed in ``[start, end)``, oldest
    first, one at a time, deleting each once built. Most hours hold
    none: a zip lands only when TfNSW publishes a new one."""
    table = ensure_timetable_tables(catalog)[VERSION_TABLE]
    mark = watermarks(table).get(TIMETABLE_FEED, "")
    # As for the realtime feeds, the watermark wins over ``start``, so
    # no publish between the last run and this one is skipped (D-056).
    first = _hour_of(mark) if mark else start
    for hour in hours(first, end):
        files = _extract(source, feed.name, hour, data_dir)
        if not files:
            continue
        built = run_timetable(data_dir, catalog, max_versions=None)
        stats.timetables += len(built)
        stats.hours += 1 if built else 0
        for rel in files:
            (data_dir / rel).unlink(missing_ok=True)


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
