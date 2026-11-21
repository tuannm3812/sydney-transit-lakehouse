"""Compaction merges an hour's small files without changing rows."""

from datetime import timedelta
from pathlib import Path

from tests.test_worker import (
    T0,
    TU_FEED,
    VP_FEED,
    land,
    rows,
    tu_bytes,
    vp_bytes,
)
from transit_lakehouse.worker.compact import compact
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import open_catalog, watermarks


def _files(catalog, name: str) -> int:
    return len(list(catalog.load_table(name).scan().plan_files()))


def _build(data_dir: Path):
    ts = int(T0.timestamp())
    land(
        data_dir,
        TU_FEED.name,
        [
            tu_bytes(ts + 30 * i, {"A": [0, 10 * i, 20 * i], "B": [i, i]})
            for i in range(6)
        ],
    )
    land(
        data_dir,
        VP_FEED.name,
        [vp_bytes(ts + 15 * i, {"v1": (ts + 15 * i, 5.0)}) for i in range(6)],
    )
    catalog = open_catalog(data_dir, env={})
    for _ in range(6):  # one commit, so one small file, per snapshot
        run_once(data_dir, catalog, [TU_FEED, VP_FEED], max_files=1)
    return catalog


def test_compaction_merges_closed_hours_only(tmp_path: Path) -> None:
    catalog = _build(tmp_path / "data")
    names = ("silver.stop_prediction", "silver.vehicle_position")
    before = {n: rows(catalog, n) for n in names}
    marks = {n: watermarks(catalog.load_table(n)) for n in names}
    assert _files(catalog, "silver.stop_prediction") >= 4

    # The hour is still open: nothing moves.
    open_hour = compact(catalog, now=T0 + timedelta(minutes=30))
    assert open_hour.groups == 0

    stats = compact(catalog, now=T0 + timedelta(hours=2))
    assert stats.groups >= 2 and stats.files_out == stats.groups
    for n in names:
        assert _files(catalog, n) == 1
        assert rows(catalog, n) == before[n]
        assert watermarks(catalog.load_table(n)) == marks[n]

    # A merged hour is not merged again.
    assert compact(catalog, now=T0 + timedelta(hours=2)).groups == 0


def test_compacted_file_is_sorted_by_the_table_key(tmp_path: Path) -> None:
    catalog = _build(tmp_path / "data")
    compact(catalog, now=T0 + timedelta(hours=2))
    data = catalog.load_table("silver.stop_prediction").scan().to_arrow()
    keys = list(
        zip(
            data["trip_id"].to_pylist(),
            data["stop_sequence"].to_pylist(),
            data["fetched_at"].to_pylist(),
            strict=True,
        )
    )
    assert keys == sorted(keys)


def test_expire_deletes_files_only_old_snapshots_used(tmp_path: Path) -> None:
    from transit_lakehouse.worker.runner import expire

    data_dir = tmp_path / "data"
    catalog = _build(data_dir)
    folder = data_dir / "lakehouse" / "warehouse" / "silver"
    pattern = "stop_prediction/data/**/*.parquet"
    small = len(list(folder.glob(pattern)))
    expected = rows(catalog, "silver.stop_prediction")
    compact(catalog, now=T0 + timedelta(hours=2))
    assert len(list(folder.glob(pattern))) == small + 1  # old kept for now
    expire(catalog, timedelta(seconds=0))
    assert len(list(folder.glob(pattern))) == 1
    assert rows(catalog, "silver.stop_prediction") == expected


def test_expire_deletes_old_metadata(tmp_path: Path) -> None:
    from transit_lakehouse.worker.runner import expire

    data_dir = tmp_path / "data"
    catalog = _build(data_dir)
    compact(catalog, now=T0 + timedelta(hours=2))
    expire(catalog, timedelta(seconds=0))
    meta = data_dir / "lakehouse" / "warehouse" / "silver"
    meta = meta / "stop_prediction" / "metadata"
    table = catalog.load_table("silver.stop_prediction")
    assert len(table.snapshots()) == 1
    snap = table.current_snapshot()
    live = {Path(snap.manifest_list).name} | {
        Path(m.manifest_path).name for m in snap.manifests(table.io)
    }
    avro = {p.name for p in meta.glob("*.avro")}
    assert avro == live  # only the current snapshot's manifests remain
    jsons = {p.name for p in meta.glob("*.metadata.json")}
    logged = {Path(e.metadata_file).name for e in table.metadata.metadata_log}
    assert jsons == logged | {Path(table.metadata_location).name}
    assert len(jsons) <= 21
    # A rerun, a fresh start, has nothing to expire: no new files at all.
    catalog = open_catalog(data_dir, env={})
    assert sum(expire(catalog, timedelta(seconds=0)).values()) == 0
    assert {p.name for p in meta.glob("*.metadata.json")} == jsons


def test_old_tables_get_metadata_cleanup(tmp_path: Path) -> None:
    from transit_lakehouse.worker.tables import (
        METADATA_PROPERTIES,
        ensure_tables,
    )

    catalog = open_catalog(tmp_path / "data", env={})
    table = ensure_tables(catalog)["silver.trip_status"]
    with table.transaction() as tx:  # as a table made before D-032
        tx.remove_properties(*METADATA_PROPERTIES)
    table = ensure_tables(catalog)["silver.trip_status"]
    for key, value in METADATA_PROPERTIES.items():
        assert table.properties[key] == value
