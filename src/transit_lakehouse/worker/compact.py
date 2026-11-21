"""Compact the realtime tables' small files, one closed hour at a time.

The worker commits about once a minute (D-027), so each table gains a
small Parquet file per partition per run: about 1,440 a day. Readers
open every file, so this module merges them: for each table, partition
(``obs_date``, ``mode``) and UTC hour of ``fetched_at`` that is closed,
the files that lie wholly inside that hour are rewritten as one file,
sorted by the table's sort key, and swapped in with a single Iceberg
overwrite commit. Rows, row order within a sort key and the watermark
properties are unchanged, so a replay and ``verify`` still match.

Memory stays flat: DuckDB reads the files and sorts with the worker's
memory limit, spilling to disk, and the result is streamed to Parquet
in batches. Old files leave the table at once and leave the disk when
``expire`` removes the snapshots that still point at them.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog import Catalog
from pyiceberg.conversions import from_bytes
from pyiceberg.io.pyarrow import parquet_file_to_data_file
from pyiceberg.manifest import DataFile
from pyiceberg.table import Table
from pyiceberg.types import TimestamptzType

from transit_lakehouse.worker.tables import SORT_KEYS, TABLES, ensure_tables

log = logging.getLogger(__name__)

MIN_FILES = 4  # fewer files in an hour are left alone
BATCH_ROWS = 50_000


@dataclass
class CompactStats:
    groups: int = 0
    files_in: int = 0
    files_out: int = 0
    rows: int = 0
    by_table: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"{self.files_in:,} files into {self.files_out:,} "
            f"({self.groups} table-hours, {self.rows:,} rows)"
        )


def _hour_of(micros: int) -> datetime:
    t = datetime.fromtimestamp(micros / 1e6, tz=UTC)
    return t.replace(minute=0, second=0, microsecond=0)


def plan(
    table: Table, before: datetime, min_files: int = MIN_FILES
) -> list[list[DataFile]]:
    """Groups of files to merge: same partition, same closed UTC hour."""
    field_id = table.schema().find_field("fetched_at").field_id
    groups: dict[tuple, list[DataFile]] = defaultdict(list)
    for task in table.scan().plan_files():
        f = task.file
        lo, hi = f.lower_bounds or {}, f.upper_bounds or {}
        if field_id not in lo or field_id not in hi:
            continue
        start = _hour_of(from_bytes(TimestamptzType(), lo[field_id]))
        if _hour_of(from_bytes(TimestamptzType(), hi[field_id])) != start:
            continue  # spans hours: already a merged or odd file
        if start + timedelta(hours=1) > before:
            continue  # hour still open: more files may arrive
        groups[(tuple(f.partition), start)].append(f)
    return [
        sorted(files, key=lambda f: f.file_path)
        for _, files in sorted(groups.items(), key=lambda kv: str(kv[0]))
        if len(files) >= min_files
    ]


def _connect(spill: Path):
    import duckdb

    con = duckdb.connect()
    limit = os.environ.get("WORKER_DUCKDB_MEMORY", "200MB")
    con.execute(f"SET memory_limit = '{limit}'")
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute("SET threads = 2")
    con.execute("SET enable_progress_bar = false")
    return con


def _rewrite(con, name: str, table: Table, files: list[DataFile]) -> DataFile:
    """Write one sorted file from ``files``; return its DataFile."""
    schema = pq.read_schema(files[0].file_path)  # keeps Iceberg field ids
    keys = ", ".join(f'"{k}"' for k in SORT_KEYS[name])
    folder = files[0].file_path.rsplit("/", 1)[0]
    out_path = f"{folder}/compact-{uuid.uuid4().hex}.parquet"
    cur = con.cursor()
    rel = cur.read_parquet([f.file_path for f in files]).order(keys)
    # DuckDB 2.x renamed the relation's fetch_arrow_reader.
    reader = (
        rel.to_arrow_reader(BATCH_ROWS)
        if hasattr(rel, "to_arrow_reader")
        else rel.fetch_arrow_reader(BATCH_ROWS)
    )
    output = table.io.new_output(out_path)
    with (
        output.create() as stream,
        pq.ParquetWriter(stream, schema, compression="zstd") as writer,
    ):
        for batch in reader:
            writer.write_table(
                pa.Table.from_batches([batch])
                .select(schema.names)
                .cast(schema)
            )
    cur.close()
    return parquet_file_to_data_file(table.io, table.metadata, out_path)


def compact(
    catalog: Catalog,
    now: datetime | None = None,
    min_files: int = MIN_FILES,
    max_groups: int | None = None,
) -> CompactStats:
    """Merge small files in every closed hour of the realtime tables."""
    now = now or datetime.now(UTC)
    before = now.replace(minute=0, second=0, microsecond=0)
    stats = CompactStats()
    tables = ensure_tables(catalog)
    with tempfile.TemporaryDirectory(prefix="compact-") as tmp:
        con = _connect(Path(tmp))
        try:
            for name in TABLES:
                table = tables[name]
                for files in plan(table, before, min_files):
                    if max_groups is not None and stats.groups >= max_groups:
                        return stats
                    new = _rewrite(con, name, table, files)
                    if new.record_count != sum(f.record_count for f in files):
                        raise RuntimeError(f"{name}: row count changed")
                    with (
                        table.transaction() as tx,
                        tx.update_snapshot().overwrite() as ow,
                    ):
                        for old in files:
                            ow.delete_data_file(old)
                        ow.append_data_file(new)
                    stats.groups += 1
                    stats.files_in += len(files)
                    stats.files_out += 1
                    stats.rows += new.record_count
                    stats.by_table[name] = stats.by_table.get(name, 0) + len(
                        files
                    )
                    log.info(
                        "%s: %d files -> 1 (%d rows)",
                        name,
                        len(files),
                        new.record_count,
                    )
        finally:
            con.close()
    return stats
