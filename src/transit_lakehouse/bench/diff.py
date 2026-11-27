"""The stream's per-feed step: the worker's own diff, fed from Kafka.

Spark groups each micro-batch by feed and calls :func:`diff_batch` with
that feed's new messages in offset order and the feed's saved state:
the last readable file's path and bytes. The step rebuilds the worker's
:class:`FeedDiffer` from that file (or reuses the one this Python
process kept, when it was left at the same file), applies each message
exactly as ``worker run`` applies a landed file, and returns the rows
as one Arrow table in a **union schema**: every column of every table,
plus ``_table`` naming the table each row belongs to. Using the same
decoder and differ is the point: the benchmark measures the engine, and
``worker verify`` can prove the rows identical (docs/13_spark_benchmark.md).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import pyarrow as pa

from transit_lakehouse.worker.decode import snapshot_from_bytes
from transit_lakehouse.worker.silver import FeedDiffer, Rows
from transit_lakehouse.worker.tables import TABLES, TYPES, schema_for

TABLE_COL = "_table"
PRODUCED_COL = "_produced_at"  # broker time, on bronze rows only


def union_columns() -> list[str]:
    out: list[str] = []
    for cols in TABLES.values():
        out.extend(c for c in cols if c not in out)
    return out


def union_arrow_schema() -> pa.Schema:
    fields = schema_for(tuple(union_columns()), TYPES).as_arrow()
    return pa.schema(
        [
            pa.field(TABLE_COL, pa.string()),
            *fields,
            pa.field(PRODUCED_COL, pa.timestamp("us", tz="UTC")),
        ]
    )


@dataclass(frozen=True)
class Incoming:
    source_file: str
    value: bytes
    seed: bool
    produced_at: datetime | None


@dataclass
class FeedState:
    last_file: str | None = None  # last readable file applied
    last_value: bytes | None = None


# One differ per feed per Python worker, reused only while the saved
# state says it is still at the same file (a retried batch rolls the
# state back, and then the differ is rebuilt from the state's bytes).
_CACHE: dict[str, tuple[str | None, FeedDiffer]] = {}


def _differ(feed: str, kind: str, state: FeedState) -> FeedDiffer:
    cached = _CACHE.get(feed)
    if cached is not None and cached[0] == state.last_file:
        return cached[1]
    differ = FeedDiffer(feed, kind)
    if state.last_file is not None and state.last_value is not None:
        differ.seed(
            snapshot_from_bytes(feed, state.last_file, state.last_value)
        )
    return differ


def diff_batch(
    feed: str,
    kind: str,
    messages: Iterable[Incoming],
    state: FeedState,
) -> tuple[pa.Table, FeedState]:
    """Apply ``messages`` (offset order) on top of ``state``."""
    differ = _differ(feed, kind, state)
    rows = Rows()
    produced: list[datetime | None] = []
    new = FeedState(state.last_file, state.last_value)
    for msg in messages:
        if new.last_file is not None and msg.source_file <= new.last_file:
            continue  # a resend of a file already applied
        snap = snapshot_from_bytes(feed, msg.source_file, msg.value)
        if msg.seed:
            differ.seed(snap)
        else:
            differ.apply(snap, rows, ingested_at=None)
            produced.append(msg.produced_at)
        if not snap.failed:
            new = FeedState(msg.source_file, msg.value)
    _CACHE[feed] = (new.last_file, differ)
    return to_union(rows, produced), new


def to_union(rows: Rows, produced: list[datetime | None]) -> pa.Table:
    schema = union_arrow_schema()
    parts = []
    for name, buffer in rows.by_table().items():
        if not len(buffer):
            continue
        n = len(buffer)
        own = schema_for(TABLES[name], TYPES).as_arrow()
        arrays = []
        for f in schema:
            if f.name == TABLE_COL:
                arrays.append(pa.array([name] * n, pa.string()))
            elif f.name == PRODUCED_COL:
                values = produced if name == "bronze.snapshot" else None
                arrays.append(
                    pa.array(values, f.type)
                    if values is not None
                    else pa.nulls(n, f.type)
                )
            elif f.name in own.names:
                arrays.append(pa.array(buffer.data[f.name], type=f.type))
            else:
                arrays.append(pa.nulls(n, f.type))
        parts.append(pa.Table.from_arrays(arrays, schema=schema))
    if not parts:
        return schema.empty_table()
    return pa.concat_tables(parts)
