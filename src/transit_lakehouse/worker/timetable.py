"""Static timetable versions → silver ``timetable_*`` tables (D-029).

The collector lands every distinct static GTFS zip TfNSW publishes
(D-019, D-021), about three a day. Each landed file becomes a
**version**, named by its file stem (fetch time and sha12, so a zip
TfNSW republishes later is a new version with its own fetch time).
Every service day can then be joined to the timetable that was live
then (docs/7_data_model.md).

Most of a zip is ``stop_times.txt`` (tens of millions of rows for NSW),
and consecutive publishes mostly repeat it. So stop times are stored
once per distinct *pattern*: a trip's ordered stops with their times,
named by a hash of that content. ``timetable_trip`` maps every trip of
every version to its ``pattern_id``; ``timetable_pattern_stop`` holds a
pattern's rows only the first time any version uses it. A republish
then costs its trip list plus whatever really changed.

The CSVs are read with DuckDB under a memory limit, spilling to disk,
so a ~300 MB zip fits next to the collector on the 2 GB host.

Each version is written idempotently: a table's rows for the version
are deleted and rewritten in one Iceberg commit, and the version row
plus the watermark go last, so a crash part-way just redoes the version.
"""

# SQL here is built only from this module's own column whitelists and a
# version id checked against the landed-file name pattern, never
# from file contents, so string-built queries are safe:
# ruff: noqa: S608

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import EqualTo
from pyiceberg.table import Table
from pyiceberg.types import (
    DoubleType,
    IcebergType,
    IntegerType,
    LongType,
    StringType,
    TimestamptzType,
)

from transit_lakehouse.worker.decode import parse_name
from transit_lakehouse.worker.tables import (
    WATERMARK_PREFIX,
    create_table,
    schema_for,
    watermarks,
)

log = logging.getLogger(__name__)

FEED = "static_gtfs"
VERSION_TABLE = "silver.timetable_version"
PATTERN_TABLE = "silver.timetable_pattern_stop"
PATTERN_INDEX = "silver.timetable_pattern"

STR, INT, LONG = StringType(), IntegerType(), LongType()
DBL, TSZ = DoubleType(), TimestamptzType()

# GTFS file → (table, {column: Iceberg type}). Columns missing from a
# file are written as nulls; a missing optional file writes no rows.
FILES: dict[str, tuple[str, dict[str, IcebergType]]] = {
    "agency.txt": (
        "silver.timetable_agency",
        {
            "agency_id": STR,
            "agency_name": STR,
            "agency_url": STR,
            "agency_timezone": STR,
        },
    ),
    "routes.txt": (
        "silver.timetable_route",
        {
            "route_id": STR,
            "agency_id": STR,
            "route_short_name": STR,
            "route_long_name": STR,
            "route_desc": STR,
            "route_type": INT,
        },
    ),
    "stops.txt": (
        "silver.timetable_stop",
        {
            "stop_id": STR,
            "stop_code": STR,
            "stop_name": STR,
            "stop_lat": DBL,
            "stop_lon": DBL,
            "location_type": INT,
            "parent_station": STR,
            "platform_code": STR,
        },
    ),
    "calendar.txt": (
        "silver.timetable_calendar",
        {
            "service_id": STR,
            "monday": INT,
            "tuesday": INT,
            "wednesday": INT,
            "thursday": INT,
            "friday": INT,
            "saturday": INT,
            "sunday": INT,
            "start_date": STR,
            "end_date": STR,
        },
    ),
    "calendar_dates.txt": (
        "silver.timetable_calendar_date",
        {"service_id": STR, "date": STR, "exception_type": INT},
    ),
}

TRIP_TABLE = "silver.timetable_trip"
TRIP_COLUMNS: dict[str, IcebergType] = {
    "trip_id": STR,
    "route_id": STR,
    "service_id": STR,
    "trip_headsign": STR,
    "direction_id": INT,
    "shape_id": STR,
    "block_id": STR,
    "pattern_id": STR,
    "first_departure_s": INT,
    "stop_count": INT,
}

# One row per distinct pattern, so "which patterns exist" is a small
# read (a scan of pattern_stop's ids cost ~250 MB on a NSW-sized feed).
PATTERN_INDEX_COLUMNS: dict[str, IcebergType] = {
    "pattern_id": STR,
    "first_version": STR,
    "example_trip_id": STR,
    "stop_count": INT,
}

PATTERN_COLUMNS: dict[str, IcebergType] = {
    "pattern_id": STR,
    "first_version": STR,
    "stop_sequence": INT,
    "stop_id": STR,
    "arrival_s": INT,
    "departure_s": INT,
    "pickup_type": INT,
    "drop_off_type": INT,
    "shape_dist_traveled": DBL,
    "timepoint": INT,
}

VERSION_COLUMNS: dict[str, IcebergType] = {
    "version": STR,
    "source_file": STR,
    "fetched_at": TSZ,
    "feed_publisher_name": STR,
    "feed_version": STR,
    "feed_start_date": STR,
    "feed_end_date": STR,
    "agencies": INT,
    "routes": INT,
    "stops": INT,
    "trips": INT,
    "patterns": INT,
    "new_patterns": INT,
    "stop_times": LONG,
    "ingested_at": TSZ,
}

_SQL_TYPES = {
    STR: "VARCHAR",
    INT: "INTEGER",
    LONG: "BIGINT",
    DBL: "DOUBLE",
}


def _with_version(cols: dict[str, IcebergType]) -> dict[str, IcebergType]:
    return {"version": STR, **cols}


def ensure_timetable_tables(catalog: Catalog) -> dict[str, Table]:
    out: dict[str, Table] = {}
    for table, cols in FILES.values():
        full = _with_version(cols)
        out[table] = create_table(
            catalog, table, schema_for(tuple(full), full), ("version",)
        )
    full = _with_version(TRIP_COLUMNS)
    out[TRIP_TABLE] = create_table(
        catalog, TRIP_TABLE, schema_for(tuple(full), full), ("version",)
    )
    out[PATTERN_INDEX] = create_table(
        catalog,
        PATTERN_INDEX,
        schema_for(tuple(PATTERN_INDEX_COLUMNS), PATTERN_INDEX_COLUMNS),
        ("first_version",),
    )
    out[PATTERN_TABLE] = create_table(
        catalog,
        PATTERN_TABLE,
        schema_for(tuple(PATTERN_COLUMNS), PATTERN_COLUMNS),
        ("first_version",),
    )
    out[VERSION_TABLE] = create_table(
        catalog,
        VERSION_TABLE,
        schema_for(tuple(VERSION_COLUMNS), VERSION_COLUMNS),
        ("version",),
    )
    return out


def landed_zips(data_dir: Path, after: str = "") -> list[str]:
    root = data_dir / "raw" / f"feed={FEED}"
    if not root.exists():
        return []
    rels = (
        p.relative_to(data_dir).as_posix()
        for p in root.glob("date=*/hour=*/*.zip")
    )
    return sorted(r for r in rels if r > after)


@dataclass
class VersionStats:
    version: str
    trips: int
    patterns: int
    new_patterns: int
    stop_times: int


_VERSION_RE = re.compile(r"^\d{8}T\d{6}\.\d{6}Z_[0-9a-f]{12}$")


def _check_version(version: str) -> None:
    if not _VERSION_RE.match(version):
        raise ValueError(f"unexpected timetable version id {version!r}")


def _connect(work: Path):
    import duckdb  # deferred: only timetable builds need it

    con = duckdb.connect()
    limit = os.environ.get("WORKER_DUCKDB_MEMORY", "200MB")
    con.execute(f"SET memory_limit = '{limit}'")
    con.execute(f"SET temp_directory = '{work / 'spill'}'")
    con.execute("SET threads = 2")
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET enable_progress_bar = false")
    return con


def _load_csv(con, path: Path, name: str) -> set[str]:
    """Load a GTFS CSV as all-text table ``name``; return its columns."""
    con.execute(
        f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_csv("
        "?, header = true, all_varchar = true, quote = '\"', "
        "escape = '\"', strict_mode = false)",
        [str(path)],
    )
    cols = {r[0] for r in con.execute(f"DESCRIBE {name}").fetchall()}
    return {c.strip().lstrip("﻿") for c in cols}


def _select(name: str, present: set[str], cols: dict[str, IcebergType]) -> str:
    """SELECT list casting each wanted column, NULL where absent."""
    parts = []
    for col, typ in cols.items():
        sql = _SQL_TYPES[typ]
        if col in present:
            parts.append(f'TRY_CAST(trim("{col}") AS {sql}) AS "{col}"')
        else:
            parts.append(f'CAST(NULL AS {sql}) AS "{col}"')
    return ", ".join(parts)


def _secs(col: str) -> str:
    """GTFS HH:MM:SS (hours may pass 24) → seconds after midnight."""
    t = f'trim("{col}")'
    return (
        f"TRY_CAST(split_part({t}, ':', 1) AS INTEGER) * 3600 + "
        f"TRY_CAST(split_part({t}, ':', 2) AS INTEGER) * 60 + "
        f"TRY_CAST(split_part({t}, ':', 3) AS INTEGER)"
    )


def _replace(
    table: Table,
    column: str,
    value: str,
    batches,
    schema: pa.Schema,
    properties: dict[str, str] | None = None,
) -> int:
    """Delete a version's rows and write new ones in one commit."""
    rows = 0
    with table.transaction() as tx:
        with warnings.catch_warnings():
            # "did not match any records" on a version's first write
            warnings.simplefilter("ignore", UserWarning)
            tx.delete(EqualTo(column, value))
        for batch in batches:
            data = pa.Table.from_batches([batch]).cast(schema)
            if data.num_rows:
                tx.append(data)
                rows += data.num_rows
        if properties:
            tx.set_properties(properties)
    return rows


def _batches(con, sql: str, size: int = 50_000):
    # A cursor of its own, so the stream never blocks the next query on
    # the main connection; closed once read.
    cur = con.cursor()
    try:
        rel = cur.sql(sql)
        # DuckDB 2.x renamed the relation's fetch_arrow_reader.
        if hasattr(rel, "to_arrow_reader"):
            yield from rel.to_arrow_reader(size)
        else:
            yield from rel.fetch_arrow_reader(size)
    finally:
        cur.close()


def build_version(
    data_dir: Path,
    rel: str,
    tables: dict[str, Table],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> VersionStats:
    """Write one landed zip as a timetable version."""
    stem = Path(rel).name.removesuffix(".zip")
    fetched_at, _ = parse_name(stem)
    version = stem
    _check_version(version)
    work_root = data_dir / "lakehouse" / "tmp"
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="timetable-", dir=work_root))
    try:
        with zipfile.ZipFile(data_dir / rel) as zf:
            members = {Path(n).name: n for n in zf.namelist()}
            for want in (*FILES, "trips.txt", "stop_times.txt"):
                if want in members:
                    with (
                        zf.open(members[want]) as src,
                        (work / want).open("wb") as dst,
                    ):
                        shutil.copyfileobj(src, dst, 1 << 20)
            info = (
                zf.read(members["feed_info.txt"])
                if "feed_info.txt" in members
                else b""
            )
        con = _connect(work)
        counts: dict[str, int] = {}
        for filename, (table, cols) in FILES.items():
            path = work / filename
            if not path.exists():
                counts[table] = 0
                _replace(
                    tables[table],
                    "version",
                    version,
                    [],
                    tables[table].schema().as_arrow(),
                )
                continue
            present = _load_csv(con, path, "src")
            sql = (
                f"SELECT '{version}' AS version, "
                + _select("src", present, cols)
                + " FROM src"
            )
            counts[table] = _replace(
                tables[table],
                "version",
                version,
                _batches(con, sql),
                tables[table].schema().as_arrow(),
            )
            path.unlink()

        stats = _trips_and_patterns(con, work, version, tables)
        meta = _feed_info(con, work, info)
        con.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    version_row = pa.table(
        {
            "version": [version],
            "source_file": [rel],
            "fetched_at": [fetched_at],
            "feed_publisher_name": [meta.get("feed_publisher_name")],
            "feed_version": [meta.get("feed_version")],
            "feed_start_date": [meta.get("feed_start_date")],
            "feed_end_date": [meta.get("feed_end_date")],
            "agencies": [counts["silver.timetable_agency"]],
            "routes": [counts["silver.timetable_route"]],
            "stops": [counts["silver.timetable_stop"]],
            "trips": [stats.trips],
            "patterns": [stats.patterns],
            "new_patterns": [stats.new_patterns],
            "stop_times": [stats.stop_times],
            "ingested_at": [now()],
        }
    )
    table = tables[VERSION_TABLE]
    _replace(
        table,
        "version",
        version,
        version_row.to_batches(),
        table.schema().as_arrow(),
        {WATERMARK_PREFIX + FEED: rel},
    )
    return stats


def _feed_info(con, work: Path, raw: bytes) -> dict[str, str | None]:
    if not raw:
        return {}
    path = work / "feed_info.txt"
    path.write_bytes(raw)
    present = _load_csv(con, path, "info")
    cols = {
        c: STR
        for c in (
            "feed_publisher_name",
            "feed_version",
            "feed_start_date",
            "feed_end_date",
        )
    }
    row = con.execute(
        f"SELECT {_select('info', present, cols)} FROM info LIMIT 1"
    ).fetchone()
    return dict(zip(cols, row or (), strict=False))


def _trips_and_patterns(
    con, work: Path, version: str, tables: dict[str, Table]
) -> VersionStats:
    trips_path, times_path = work / "trips.txt", work / "stop_times.txt"
    if not times_path.exists() or not trips_path.exists():
        trips = tables[TRIP_TABLE]
        _replace(trips, "version", version, [], trips.schema().as_arrow())
        return VersionStats(version, 0, 0, 0, 0)

    present = _load_csv(con, times_path, "st_raw")
    times_path.unlink()
    opt = {
        c: f'"{c}"' if c in present else "NULL"
        for c in (
            "pickup_type",
            "drop_off_type",
            "shape_dist_traveled",
            "timepoint",
        )
    }
    con.execute(
        f"""
        CREATE OR REPLACE TABLE st AS
        SELECT trim(trip_id) AS trip_id,
               TRY_CAST(trim(stop_sequence) AS INTEGER) AS stop_sequence,
               trim(stop_id) AS stop_id,
               {_secs("arrival_time")} AS arrival_s,
               {_secs("departure_time")} AS departure_s,
               TRY_CAST(trim({opt["pickup_type"]}) AS INTEGER)
                   AS pickup_type,
               TRY_CAST(trim({opt["drop_off_type"]}) AS INTEGER)
                   AS drop_off_type,
               TRY_CAST(trim({opt["shape_dist_traveled"]}) AS DOUBLE)
                   AS shape_dist_traveled,
               TRY_CAST(trim({opt["timepoint"]}) AS INTEGER) AS timepoint
        FROM st_raw
        """
    )
    con.execute("DROP TABLE st_raw")
    stop_times = con.execute("SELECT count(*) FROM st").fetchone()[0]
    # A pattern is the content of a trip's stop times, so any trip in
    # any version with identical stops and times shares it. The id
    # XORs a 128-bit md5 of every row (each row carries its own stop
    # sequence, so order is still part of the content) and adds the row
    # count. Fixed-size aggregates stream and spill; an ordered
    # string_agg ran out of memory on a NSW-sized timetable.
    con.execute(
        """
        CREATE OR REPLACE TABLE trip_pattern AS
        WITH h AS (
            SELECT trip_id, departure_s,
                   concat_ws('|', stop_sequence, stop_id, arrival_s,
                             departure_s, pickup_type, drop_off_type,
                             shape_dist_traveled, timepoint) AS r
            FROM st
        )
        SELECT trip_id,
               printf('%016x%016x%04x',
                      bit_xor(md5_number_upper(r)),
                      bit_xor(md5_number_lower(r)),
                      count(*) % 65536) AS pattern_id,
               min(departure_s) AS first_departure_s,
               count(*) AS stop_count
        FROM h GROUP BY trip_id
        """
    )

    # Patterns already stored by earlier versions (not this one, which
    # is rewritten in full if it is being redone after a crash).
    pattern_table = tables[PATTERN_TABLE]
    index_table = tables[PATTERN_INDEX]
    seen = index_table.scan(
        row_filter=f"first_version != '{version}'",
        selected_fields=("pattern_id",),
    ).to_arrow()
    con.register("seen_arrow", seen)
    con.execute(
        "CREATE OR REPLACE TABLE seen AS SELECT pattern_id FROM seen_arrow"
    )
    con.unregister("seen_arrow")
    del seen
    con.execute(
        """
        CREATE OR REPLACE TABLE new_pattern AS
        SELECT pattern_id, min(trip_id) AS trip_id,
               any_value(stop_count) AS stop_count
        FROM trip_pattern
        WHERE pattern_id NOT IN (SELECT pattern_id FROM seen)
        GROUP BY pattern_id
        """
    )
    patterns = con.execute(
        "SELECT count(DISTINCT pattern_id) FROM trip_pattern"
    ).fetchone()[0]
    new_patterns = con.execute("SELECT count(*) FROM new_pattern").fetchone()[
        0
    ]
    # Stops first, index last: a crash in between leaves stops that the
    # redo deletes and rewrites, never an index row without its stops.
    _replace(
        pattern_table,
        "first_version",
        version,
        _batches(
            con,
            f"""
            SELECT n.pattern_id, '{version}' AS first_version,
                   st.stop_sequence, st.stop_id, st.arrival_s,
                   st.departure_s, st.pickup_type, st.drop_off_type,
                   st.shape_dist_traveled, st.timepoint
            FROM new_pattern n JOIN st USING (trip_id)
            """,
        ),
        pattern_table.schema().as_arrow(),
    )
    _replace(
        index_table,
        "first_version",
        version,
        _batches(
            con,
            f"""
            SELECT pattern_id, '{version}' AS first_version,
                   trip_id AS example_trip_id, stop_count
            FROM new_pattern
            """,
        ),
        index_table.schema().as_arrow(),
    )
    con.execute("DROP TABLE st")

    present = _load_csv(con, trips_path, "trips")
    base = {k: v for k, v in TRIP_COLUMNS.items() if k not in _DERIVED}
    sql = f"""
        SELECT '{version}' AS version, t.*, tp.pattern_id,
               tp.first_departure_s, tp.stop_count
        FROM (SELECT {_select("trips", present, base)} FROM trips) t
        LEFT JOIN trip_pattern tp USING (trip_id)
    """
    trips = _replace(
        tables[TRIP_TABLE],
        "version",
        version,
        _batches(con, sql),
        tables[TRIP_TABLE].schema().as_arrow(),
    )
    return VersionStats(version, trips, patterns, new_patterns, stop_times)


_DERIVED = ("pattern_id", "first_departure_s", "stop_count")


def run_timetable(
    data_dir: Path, catalog: Catalog, max_versions: int | None = 1
) -> list[VersionStats]:
    """Build every landed timetable zip not yet in the tables."""
    tables = ensure_timetable_tables(catalog)
    after = watermarks(tables[VERSION_TABLE]).get(FEED, "")
    todo = landed_zips(data_dir, after)
    if max_versions is not None:
        todo = todo[:max_versions]
    out = []
    for rel in todo:
        log.info("timetable: building %s", rel)
        out.append(build_version(data_dir, rel, tables))
    return out
