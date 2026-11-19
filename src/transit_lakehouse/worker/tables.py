"""Iceberg catalog and table definitions for bronze and silver (D-027).

The catalog is PyIceberg's SQL catalog on a SQLite file, so there is no
catalog service to run on the 2 GB host. The warehouse (data and
metadata files) is a local folder by default and can point at object
storage later through ``LAKEHOUSE_WAREHOUSE`` without code changes.

Every table is partitioned by ``obs_date`` (the UTC date of the poll,
D-006) and ``mode``, written as zstd Parquet. All columns are optional
so a schema can grow without rewriting history.

Each table records how far it has read each feed in a table property,
``transit.watermark.<feed>`` = the last raw file whose rows it holds.
The property is set in the same Iceberg commit as the rows, so a crash
can never leave rows without their watermark or the reverse.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    BooleanType,
    DateType,
    DoubleType,
    FloatType,
    IcebergType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from transit_lakehouse.worker.silver import (
    SNAPSHOT_COLUMNS,
    STOP_COLUMNS,
    TRIP_COLUMNS,
    VEHICLE_COLUMNS,
)

WATERMARK_PREFIX = "transit.watermark."
TABLE_PROPERTIES = {
    "write.parquet.compression-codec": "zstd",
    "format-version": "2",
}

STR, TSZ, DATE = StringType(), TimestamptzType(), DateType()
INT, LONG, FLT, DBL = IntegerType(), LongType(), FloatType(), DoubleType()
BOOL = BooleanType()

TYPES: dict[str, IcebergType] = {
    # shared
    "mode": STR,
    "feed": STR,
    "kind": STR,
    "source_file": STR,
    "fetched_at": TSZ,
    "observed_at": TSZ,
    "obs_date": DATE,
    # bronze.snapshot
    "sha12": STR,
    "header_ts": TSZ,
    "stored_bytes": LONG,
    "entities": INT,
    "entity_errors": INT,
    "decode_error": STR,
    "rows_emitted": INT,
    "ingested_at": TSZ,
    # trips and stops
    "trip_id": STR,
    "start_date": STR,
    "start_time": STR,
    "route_id": STR,
    "direction_id": INT,
    "stop_sequence": INT,
    "stop_id": STR,
    "arrival_time": TSZ,
    "arrival_delay_s": INT,
    "departure_time": TSZ,
    "departure_delay_s": INT,
    "stop_status": STR,
    "trip_status": STR,
    "vehicle_id": STR,
    "delay_s": INT,
    "current_delay_s": INT,
    "trip_delay_s": INT,
    "stop_count": INT,
    # vehicles
    "vehicle_label": STR,
    "latitude": DBL,
    "longitude": DBL,
    "bearing": FLT,
    "speed_mps": FLT,
    "occupancy_status": STR,
    "current_stop_sequence": INT,
    "current_status": STR,
    "event_ts": TSZ,
    "quarantined": BOOL,
    "quarantine_reason": STR,
}

TABLES: dict[str, tuple[str, ...]] = {
    "bronze.snapshot": SNAPSHOT_COLUMNS,
    "silver.stop_prediction": STOP_COLUMNS,
    "silver.trip_status": TRIP_COLUMNS,
    "silver.vehicle_position": VEHICLE_COLUMNS,
}

# Sort within each write so Parquet row groups cluster by trip and stop
# (or vehicle), which is how the gold models read them.
SORT_KEYS: dict[str, tuple[str, ...]] = {
    "bronze.snapshot": ("feed", "source_file"),
    "silver.stop_prediction": (
        "mode",
        "trip_id",
        "start_date",
        "stop_sequence",
        "fetched_at",
    ),
    "silver.trip_status": ("mode", "trip_id", "start_date", "fetched_at"),
    "silver.vehicle_position": ("mode", "vehicle_id", "fetched_at"),
}


def schema_for(
    columns: tuple[str, ...],
    types: Mapping[str, IcebergType] = TYPES,
) -> Schema:
    return Schema(
        *(
            NestedField(i, name, types[name], required=False)
            for i, name in enumerate(columns, start=1)
        )
    )


def spec_for(
    schema: Schema, parts: tuple[str, ...] = ("obs_date", "mode")
) -> PartitionSpec:
    fields = []
    for i, name in enumerate(parts):
        source = schema.find_field(name).field_id
        fields.append(
            PartitionField(source, 1000 + i, IdentityTransform(), name)
        )
    return PartitionSpec(*fields)


def open_catalog(
    data_dir: Path, env: Mapping[str, str] | None = None
) -> Catalog:
    """Open (and create if needed) the lakehouse catalog."""
    env = os.environ if env is None else env
    root = data_dir / "lakehouse"
    root.mkdir(parents=True, exist_ok=True)
    uri = env.get("LAKEHOUSE_CATALOG_URI") or (
        f"sqlite:///{(root / 'catalog.db').resolve()}"
    )
    warehouse = env.get("LAKEHOUSE_WAREHOUSE") or (
        # A plain path, not a file:// URI: as_uri() percent-encodes
        # spaces and pyarrow then writes to a literal "%20" folder.
        str((root / "warehouse").resolve())
    )
    return SqlCatalog("transit", uri=uri, warehouse=warehouse)


def create_table(
    catalog: Catalog,
    name: str,
    schema: Schema,
    parts: tuple[str, ...] = ("obs_date", "mode"),
) -> Table:
    with contextlib.suppress(NamespaceAlreadyExistsError):
        catalog.create_namespace(name.split(".")[0])
    return catalog.create_table_if_not_exists(
        name,
        schema=schema,
        partition_spec=spec_for(schema, parts),
        properties=TABLE_PROPERTIES,
    )


def ensure_tables(catalog: Catalog) -> dict[str, Table]:
    return {
        name: create_table(catalog, name, schema_for(columns))
        for name, columns in TABLES.items()
    }


def watermarks(table: Table) -> dict[str, str]:
    return {
        k.removeprefix(WATERMARK_PREFIX): v
        for k, v in table.properties.items()
        if k.startswith(WATERMARK_PREFIX)
    }


def to_arrow(name: str, table: Table, data: Mapping[str, list]) -> pa.Table:
    """Build an Arrow table that matches the Iceberg schema exactly."""
    schema = table.schema().as_arrow()
    arrays = [pa.array(data[f.name], type=f.type) for f in schema]
    out = pa.Table.from_arrays(arrays, schema=schema)
    keys = SORT_KEYS[name]
    return out.sort_by([(k, "ascending") for k in keys])


def commit(
    name: str,
    table: Table,
    data: Mapping[str, list],
    marks: Mapping[str, str],
) -> int:
    """Append rows and advance watermarks in one Iceberg commit."""
    rows = to_arrow(name, table, data)
    current = watermarks(table)
    props = {
        WATERMARK_PREFIX + feed: mark
        for feed, mark in marks.items()
        if mark > current.get(feed, "")
    }
    if not rows.num_rows and not props:
        return 0
    with table.transaction() as tx:
        if rows.num_rows:
            tx.append(rows)
        if props:
            tx.set_properties(props)
    return rows.num_rows
