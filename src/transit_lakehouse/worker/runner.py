"""Incremental run: new raw files → bronze and silver commits (D-027).

One run reads every raw file landed since each table's watermark, diffs
each file against the one before it (silver.py), and commits each table
once with its new rows and watermarks. A crash at any point is safe:

- rows and watermark move together in one Iceberg commit (tables.py);
- a table whose commit landed before the crash is ahead of the others,
  so the next run decodes from the lowest watermark and emits rows only
  to the tables that do not hold them yet.

Realtime vehicle positions, trip updates and Service Alerts (a type-2
history per alert) are built here, then at most one new static timetable
zip per run (timetable.py).
"""

from __future__ import annotations

import fcntl
import logging
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pyiceberg.catalog import Catalog

from transit_lakehouse.collector.config import Feed
from transit_lakehouse.worker.decode import landed_after, read_snapshot
from transit_lakehouse.worker.silver import FeedDiffer, Rows
from transit_lakehouse.worker.tables import commit, ensure_tables, watermarks
from transit_lakehouse.worker.timetable import (
    VersionStats,
    ensure_timetable_tables,
    run_timetable,
)

log = logging.getLogger(__name__)

TABLES_FOR_KIND: dict[str, tuple[str, ...]] = {
    "trip_updates": (
        "bronze.snapshot",
        "silver.stop_prediction",
        "silver.trip_status",
    ),
    "vehicle_positions": ("bronze.snapshot", "silver.vehicle_position"),
    "service_alerts": (
        "bronze.snapshot",
        "silver.alert",
        "silver.alert_scope",
    ),
}
# Flush to Iceberg when this many rows are buffered, so a backlog of
# bus trip updates never outgrows the 2 GB host. A real 8 am peak
# snapshot holds about 84,000 changed stop predictions, so at peak this
# flushes after every snapshot. At 100,000 rows, peak memory reached
# 941 MB on a real peak hour (docs/reports/worker-dryrun-20261119.md).
DEFAULT_MAX_ROWS = 50_000


@dataclass
class RunStats:
    files: int = 0
    commits: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    unseeded: list[str] = field(default_factory=list)
    timetables: list[VersionStats] = field(default_factory=list)
    seconds: float = 0.0

    def summary(self) -> str:
        parts = ", ".join(f"{k} +{v:,}" for k, v in self.rows.items() if v)
        text = (
            f"{self.files} files, {self.commits} commits in "
            f"{self.seconds:.1f} s" + (f": {parts}" if parts else "")
        )
        for tt in self.timetables:
            text += (
                f"; timetable {tt.version}: {tt.trips:,} trips, "
                f"{tt.new_patterns:,} new of {tt.patterns:,} patterns"
            )
        return text


def buildable(feeds: Iterable[Feed]) -> list[Feed]:
    return [f for f in feeds if f.kind in TABLES_FOR_KIND]


@contextmanager
def single_run(data_dir: Path):
    """Refuse to run twice at once against the same lakehouse."""
    path = data_dir / "lakehouse" / ".worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another worker holds {path}") from exc
        yield


def run_once(
    data_dir: Path,
    catalog: Catalog,
    feeds: Iterable[Feed],
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_files: int | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunStats:
    """Process every new raw file once. Returns what was written."""
    started = time.monotonic()
    tables = ensure_tables(catalog)
    marks = {name: watermarks(t) for name, t in tables.items()}
    stats = RunStats(rows={name: 0 for name in tables})
    rows = Rows()
    pending: dict[str, dict[str, str]] = {name: {} for name in tables}

    def flush() -> None:
        stamp = now()
        cols = rows.snapshots.data
        cols["ingested_at"] = [stamp] * len(rows.snapshots)
        for name, buffer in rows.by_table().items():
            if not len(buffer) and not pending[name]:
                continue
            stats.rows[name] += commit(
                name, tables[name], buffer.data, pending[name]
            )
            stats.commits += 1
            marks[name].update(pending[name])
            buffer.clear()
            pending[name] = {}

    for feed in buildable(feeds):
        names = TABLES_FOR_KIND[feed.kind]
        table_marks = {n: marks[n].get(feed.name, "") for n in names}
        low = min(table_marks.values())
        files = landed_after(data_dir, feed.name, low)
        if max_files is not None:
            files = files[:max_files]
        if not files:
            continue
        differ = FeedDiffer(feed.name, feed.kind)
        if low:
            if (data_dir / low).exists():
                differ.seed(read_snapshot(data_dir, feed.name, low))
            else:
                # The file before the batch was pruned: its rows are all
                # emitted as new. Logged, because replay from S3 would
                # emit them as changes instead.
                stats.unseeded.append(feed.name)
                log.warning("%s: seed file %s is gone", feed.name, low)
        for rel in files:
            snap = read_snapshot(data_dir, feed.name, rel)
            differ.apply(
                snap,
                rows,
                ingested_at=None,
                emit=lambda t, rel=rel, tm=table_marks: rel > tm.get(t, ""),
            )
            for name in names:
                pending[name][feed.name] = rel
            stats.files += 1
            if len(rows) >= max_rows:
                flush()
                table_marks = {n: marks[n].get(feed.name, "") for n in names}
    flush()
    if any(f.kind == "static_gtfs" for f in feeds):
        # At most one zip per run: a ~300 MB timetable takes a while,
        # and the realtime tables should not wait behind several.
        stats.timetables = run_timetable(data_dir, catalog, max_versions=1)
    stats.seconds = time.monotonic() - started
    return stats


def run_loop(
    data_dir: Path,
    catalog: Catalog,
    feeds: list[Feed],
    interval_s: float,
    should_stop: Callable[[], bool],
    **kwargs,
) -> None:
    """Run every ``interval_s`` seconds until ``should_stop()``.

    Once an hour, after a run, the closed hours' small files are
    compacted (compact.py)."""
    from transit_lakehouse.worker.compact import compact

    compacted_hour = None
    while not should_stop():
        tick = time.monotonic()
        try:
            stats = run_once(data_dir, catalog, feeds, **kwargs)
            log.info("run: %s", stats.summary())
        except Exception:
            # One bad run must not end the service; the next run starts
            # from the committed watermarks again.
            log.exception("run failed")
        hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        if hour != compacted_hour:
            try:
                log.info("compact: %s", compact(catalog).summary())
                gone = expire(catalog, EXPIRE_AFTER)
                log.info("expire: %d snapshots", sum(gone.values()))
                compacted_hour = hour
            except Exception:
                log.exception("compact or expire failed")
        deadline = tick + interval_s
        while not should_stop() and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))


EXPIRE_AFTER = timedelta(hours=24)  # run --loop expires hourly (D-032)


def _files(table) -> tuple[set[str], set[str]]:
    """Data files and metadata files (manifest lists and manifests) that
    any live snapshot of ``table`` points at."""
    data: set[str] = set()
    meta: set[str] = set()
    for snap in table.snapshots():
        meta.add(snap.manifest_list)
        for manifest in snap.manifests(table.io):
            if manifest.manifest_path in meta:
                continue  # shared with an earlier snapshot: read once
            meta.add(manifest.manifest_path)
            for entry in manifest.fetch_manifest_entry(table.io):
                data.add(entry.data_file.file_path)
    return data, meta


def expire(catalog: Catalog, older_than: timedelta) -> dict[str, int]:
    """Expire Iceberg snapshots older than ``older_than``, and delete the
    data files, manifests and manifest lists that only those snapshots
    used.

    The worker commits about once a minute, so without expiry the table
    metadata grows by ~1,440 snapshots a day; and compaction's merged
    files stay on disk until the snapshots that point at them go.
    PyIceberg drops expired snapshots from the metadata but leaves their
    files, so this deletes them, then sweeps metadata files nothing
    points at (D-032). The table properties trim old metadata JSON on
    each commit. Covers the realtime and timetable tables. The current
    snapshot is always kept, and a table with nothing to expire gets no
    commit.
    """
    started = time.time()
    cutoff = datetime.now(UTC) - older_than
    cutoff_ms = int(cutoff.timestamp() * 1000)
    out: dict[str, int] = {}
    tables = {**ensure_tables(catalog), **ensure_timetable_tables(catalog)}
    for name, table in tables.items():
        current = table.current_snapshot()
        old = [
            s
            for s in table.snapshots()
            if s.timestamp_ms < cutoff_ms
            and (current is None or s.snapshot_id != current.snapshot_id)
        ]
        out[name] = len(old)
        if old:  # no commit, so no new metadata file, when nothing expires
            data_before, _ = _files(table)
            table.maintenance.expire_snapshots().older_than(cutoff).commit()
            table.refresh()
            data_after, _ = _files(table)
            for path in sorted(data_before - data_after):
                table.io.delete(path)
        _sweep_metadata(table, started)
    return out


def _sweep_metadata(table, before: float) -> int:
    """Delete metadata files in a local table's ``metadata/`` folder that
    the current metadata no longer points at: manifests of snapshots
    expired before D-032's fix and JSON files that left the metadata log.
    Only files older than ``before`` go, so a commit made while this
    runs is never touched (the worker lock keeps writers out anyway)."""
    folder = Path(table.metadata_location).parent
    if not folder.is_dir():
        return 0  # object storage: no listing here, nothing to sweep
    _, live = _files(table)
    live.add(table.metadata_location)
    live.update(e.metadata_file for e in table.metadata.metadata_log)
    live = {str(Path(p.removeprefix("file://")).resolve()) for p in live}
    gone = 0
    for path in folder.iterdir():
        if not path.name.endswith((".avro", ".metadata.json")):
            continue
        if str(path.resolve()) in live or path.stat().st_mtime >= before:
            continue
        path.unlink()
        gone += 1
    return gone
