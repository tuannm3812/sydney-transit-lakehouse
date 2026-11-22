"""Record every dbt test and model result in ``gold.dq_results``.

The public quality page (Phase 2) reads this table, so it must hold
every run, pass or fail, the way the manifest holds every poll (D-007).
dbt writes ``run_results.json`` after each invocation; this module
appends one row per node from it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

DQ_TABLE = "gold.dq_results"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS gold;
CREATE TABLE IF NOT EXISTS {DQ_TABLE} (
    run_id VARCHAR,
    run_at TIMESTAMPTZ,
    service_date_from DATE,
    service_date_to DATE,
    node VARCHAR,
    resource_type VARCHAR,
    status VARCHAR,
    failures BIGINT,
    message VARCHAR,
    seconds DOUBLE
)
"""


def read_results(path: Path) -> tuple[str, list[dict]]:
    doc = json.loads(path.read_text())
    run_id = doc["metadata"]["invocation_id"]
    rows = []
    for r in doc["results"]:
        uid = r["unique_id"]
        rows.append(
            {
                "node": uid.split(".", 2)[-1],
                "resource_type": uid.split(".", 1)[0],
                "status": str(r["status"]),
                "failures": r.get("failures"),
                "message": r.get("message"),
                "seconds": r.get("execution_time"),
            }
        )
    return run_id, rows


def record(
    con: duckdb.DuckDBPyConnection,
    results: Path,
    window: tuple[str | None, str | None] = (None, None),
    now: datetime | None = None,
) -> int:
    """Append one row per dbt node from ``run_results.json``."""
    con.execute(DDL)
    run_id, rows = read_results(results)
    run_at = now or datetime.now(UTC)
    con.executemany(
        f"INSERT INTO {DQ_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
        [
            (
                run_id,
                run_at,
                window[0],
                window[1],
                r["node"],
                r["resource_type"],
                r["status"],
                r["failures"],
                r["message"],
                r["seconds"],
            )
            for r in rows
        ],
    )
    return len(rows)
