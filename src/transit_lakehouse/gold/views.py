"""Expose the bronze and silver Iceberg tables to DuckDB as views.

dbt-duckdb reads plain DuckDB relations, and the DuckDB ``iceberg``
extension has to be downloaded at run time, which the worker image and
CI cannot rely on. PyIceberg already knows the current snapshot of
each table, so this module asks it for the table's live Parquet files
and creates ``<namespace>.<table>`` views that read exactly those
files with ``read_parquet``. DuckDB then streams them under its own
memory limit instead of PyIceberg loading whole tables into Arrow.

This is correct only while the tables have no delete files. The worker
writes appends and copy-on-write overwrites (D-027, D-032), so none
exist today; :func:`refresh_views` refuses a table that has any rather
than silently reading deleted rows.

The views are rebuilt on every gold run, because compaction (D-032)
replaces files and the old ones are deleted by expiry.
"""

# Identifiers come from the Iceberg catalog and paths from PyIceberg's
# own file plan, both quoted; nothing comes from row data:
# ruff: noqa: S608

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NoSuchNamespaceError
from pyiceberg.expressions import AlwaysTrue, GreaterThanOrEqual
from pyiceberg.types import (
    BooleanType,
    DateType,
    DoubleType,
    FloatType,
    IcebergType,
    IntegerType,
    LongType,
    StringType,
    TimestampType,
    TimestamptzType,
)

NAMESPACES = ("bronze", "silver")
# Tables partitioned by poll date; ``since`` prunes their files.
DATED = frozenset({"obs_date"})

_DUCK_TYPES: dict[type[IcebergType], str] = {
    StringType: "VARCHAR",
    IntegerType: "INTEGER",
    LongType: "BIGINT",
    FloatType: "FLOAT",
    DoubleType: "DOUBLE",
    BooleanType: "BOOLEAN",
    DateType: "DATE",
    TimestamptzType: "TIMESTAMPTZ",
    TimestampType: "TIMESTAMP",
}


@dataclass(frozen=True)
class ViewInfo:
    name: str
    files: int
    columns: int


class DeleteFilesError(RuntimeError):
    """A table has delete files, which a plain Parquet read would miss."""


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def view_sql(name: str, schema, files: list[str]) -> str:
    """The ``CREATE VIEW`` for one table over its live data files.

    Columns are selected by name and cast to the Iceberg type, so a file
    written before a column was added reads it as null
    (``union_by_name``), and an empty table still has its full schema.
    """
    ns, table = name.split(".", 1)
    target = f"{_quote(ns)}.{_quote(table)}"
    cols = []
    for field in schema.fields:
        duck = _DUCK_TYPES[type(field.field_type)]
        cols.append((field.name, duck))
    if not files:
        select = ", ".join(
            f"CAST(NULL AS {duck}) AS {_quote(col)}" for col, duck in cols
        )
        return f"CREATE OR REPLACE VIEW {target} AS SELECT {select} LIMIT 0"
    paths = ", ".join(_literal(f) for f in files)
    select = ", ".join(
        f"CAST({_quote(col)} AS {duck}) AS {_quote(col)}" for col, duck in cols
    )
    return (
        f"CREATE OR REPLACE VIEW {target} AS SELECT {select} "
        f"FROM read_parquet([{paths}], union_by_name = true)"
    )


def live_files(table, since: date | None = None) -> list[str]:
    """Paths of the table's current data files (pruned by ``since``)."""
    names = {f.name for f in table.schema().fields}
    row_filter = AlwaysTrue()
    if since is not None and DATED & names:
        row_filter = GreaterThanOrEqual("obs_date", since.isoformat())
    tasks = list(table.scan(row_filter=row_filter).plan_files())
    if any(t.delete_files for t in tasks):
        raise DeleteFilesError(
            f"{table.name()} has delete files; gold reads Parquet directly"
        )
    return sorted(t.file.file_path for t in tasks)


def refresh_views(
    con: duckdb.DuckDBPyConnection,
    catalog: Catalog,
    since: date | None = None,
) -> list[ViewInfo]:
    """(Re)create a view for every bronze and silver table."""
    out: list[ViewInfo] = []
    for ns in NAMESPACES:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(ns)}")
        try:
            idents = catalog.list_tables(ns)
        except NoSuchNamespaceError:  # the worker has not run yet
            idents = []
        for ident in sorted(idents):
            name = ".".join(ident)
            table = catalog.load_table(name)
            files = live_files(table, since)
            con.execute(view_sql(name, table.schema(), files))
            out.append(ViewInfo(name, len(files), len(table.schema().fields)))
    return out
