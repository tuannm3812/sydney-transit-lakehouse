from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pytest
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.worker.decode import (
    decode,
    landed_after,
    parse_name,
    read_snapshot,
)
from transit_lakehouse.worker.runner import expire, run_once
from transit_lakehouse.worker.silver import mode_of, quarantine_reason
from transit_lakehouse.worker.tables import (
    commit,
    ensure_tables,
    open_catalog,
    watermarks,
)

T0 = datetime(2026, 11, 18, 2, 0, tzinfo=UTC)
TU_FEED = Feed("buses_tripupdates", "trip_updates", "https://x/tu", 30)
VP_FEED = Feed("buses_vehiclepos", "vehicle_positions", "https://x/vp", 15)


def tu_bytes(ts: int, trips: dict[str, list[int]]) -> bytes:
    """One trip update per trip, with an arrival delay per stop."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = ts
    for trip_id, delays in trips.items():
        ent = msg.entity.add()
        ent.id = trip_id
        tu = ent.trip_update
        tu.trip.trip_id = trip_id
        tu.trip.start_date = "20261118"
        tu.trip.route_id = "R1"
        for i, delay in enumerate(delays, start=1):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = f"S{i}"
            stu.arrival.delay = delay
            stu.arrival.time = int(T0.timestamp()) + 600 * i + delay
    return msg.SerializeToString()


def vp_bytes(ts: int, vehicles: dict[str, tuple[int, float]]) -> bytes:
    """Vehicles as {id: (vehicle timestamp, speed m/s)}."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = ts
    for vid, (vts, speed) in vehicles.items():
        ent = msg.entity.add()
        ent.id = vid
        v = ent.vehicle
        v.vehicle.id = vid
        v.trip.trip_id = f"trip-{vid}"
        v.position.latitude = -33.87
        v.position.longitude = 151.21
        v.position.speed = speed
        v.timestamp = vts
    return msg.SerializeToString()


def land(data_dir: Path, feed: str, payloads: list[bytes]) -> list[str]:
    lander = Lander(data_dir)
    return [
        lander.land(feed, T0 + timedelta(seconds=30 * i), p)
        for i, p in enumerate(payloads)
    ]


def rows(catalog, name: str, drop=("ingested_at",)) -> list[dict]:
    table = catalog.load_table(name)
    data = table.scan().to_arrow()
    data = data.drop_columns([c for c in drop if c in data.column_names])
    return sorted(
        data.to_pylist(), key=lambda r: tuple(str(v) for v in r.values())
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _land_example(data_dir: Path) -> None:
    ts = int(T0.timestamp())
    land(
        data_dir,
        TU_FEED.name,
        [
            tu_bytes(ts, {"A": [0, 0, 0], "B": [60, 60]}),
            tu_bytes(ts + 30, {"A": [0, 30, 30], "B": [60, 60]}),
            tu_bytes(ts + 60, {"A": [0, 30, 30], "B": [60, 60]}),
            tu_bytes(ts + 90, {"A": [0, 30, 90], "C": [0]}),
        ],
    )
    land(
        data_dir,
        VP_FEED.name,
        [
            vp_bytes(ts, {"v1": (ts - 5, 10.0), "v2": (ts - 3, 5.0)}),
            vp_bytes(ts + 15, {"v1": (ts - 5, 10.0), "v2": (ts + 12, 50.0)}),
            vp_bytes(ts + 30, {"v1": (ts + 25, 11.0)}),
        ],
    )


def test_file_name_gives_fetch_time_and_hash() -> None:
    fetched, sha12 = parse_name("20261118T020000.123456Z_abcdef123456.pb.gz")
    assert fetched == T0.replace(microsecond=123456)
    assert sha12 == "abcdef123456"


def test_mode_from_feed_name() -> None:
    assert mode_of("buses_tripupdates") == "bus"
    assert mode_of("lightrail_innerwest_vehiclepos") == "lightrail"
    assert mode_of("ferries_vehiclepos") == "ferry"


def test_change_only_rows(data_dir: Path) -> None:
    _land_example(data_dir)
    catalog = open_catalog(data_dir, env={})
    stats = run_once(data_dir, catalog, [TU_FEED, VP_FEED])
    assert stats.files == 7

    stops = rows(catalog, "silver.stop_prediction")
    # first snapshot: 5 stops new; second: A/S2 and A/S3 changed; third:
    # nothing; fourth: A/S3 changed, C/S1 new.
    assert len(stops) == 5 + 2 + 0 + 2
    per_stop = {}
    for r in stops:
        key = (r["trip_id"], r["stop_sequence"])
        per_stop[key] = per_stop.get(key, 0) + 1
    assert per_stop == {
        ("A", 1): 1,
        ("A", 2): 2,
        ("A", 3): 3,
        ("B", 1): 1,
        ("B", 2): 1,
        ("C", 1): 1,
    }
    trips = rows(catalog, "silver.trip_status")
    # A and B new, nothing changes until C appears (A's stop count same).
    assert sorted(r["trip_id"] for r in trips) == ["A", "B", "C"]

    vehicles = rows(catalog, "silver.vehicle_position")
    # v1 is new, repeats, then moves; v2 is new, then a 180 km/h report.
    assert len(vehicles) == 4
    fast = [r for r in vehicles if r["quarantined"]]
    assert [r["quarantine_reason"] for r in fast] == ["speed_over_150kmh"]
    assert all(r["mode"] == "bus" for r in vehicles)

    snaps = rows(catalog, "bronze.snapshot")
    assert len(snaps) == 7
    assert [r["rows_emitted"] for r in snaps if r["kind"] == "trip_updates"]
    assert all(r["decode_error"] is None for r in snaps)


def test_rerun_is_a_no_op(data_dir: Path) -> None:
    _land_example(data_dir)
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED, VP_FEED])
    before = rows(catalog, "silver.stop_prediction")
    stats = run_once(data_dir, catalog, [TU_FEED, VP_FEED])
    assert stats.files == 0
    assert rows(catalog, "silver.stop_prediction") == before


def test_replay_in_any_batches_rebuilds_identical_tables(
    tmp_path: Path, data_dir: Path
) -> None:
    """The Phase 1 gate in miniature: batch boundaries don't matter."""
    _land_example(data_dir)
    whole = open_catalog(data_dir, env={})
    run_once(data_dir, whole, [TU_FEED, VP_FEED])

    replay_root = tmp_path / "replay"
    env = {
        "LAKEHOUSE_CATALOG_URI": f"sqlite:///{replay_root / 'cat.db'}",
        "LAKEHOUSE_WAREHOUSE": (replay_root / "wh").as_uri(),
    }
    replay_root.mkdir()
    piecewise = open_catalog(data_dir, env=env)
    for _ in range(10):  # one file per feed per run, plus tiny flushes
        run_once(data_dir, piecewise, [TU_FEED, VP_FEED], max_files=1)
    run_once(data_dir, piecewise, [TU_FEED, VP_FEED], max_rows=1)

    for name in ensure_tables(whole):
        assert rows(whole, name) == rows(piecewise, name), name


def test_crash_between_table_commits_does_not_duplicate(
    data_dir: Path,
) -> None:
    _land_example(data_dir)
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED])
    expected = rows(catalog, "silver.stop_prediction")

    # Simulate a crash where only stop_prediction committed the next
    # file: wind the other tables' watermarks back by re-creating them.
    files = landed_after(data_dir, TU_FEED.name, "")
    for name in ("bronze.snapshot", "silver.trip_status"):
        catalog.purge_table(name)
    tables = ensure_tables(catalog)
    for name in ("bronze.snapshot", "silver.trip_status"):
        assert watermarks(tables[name]) == {}
    assert watermarks(tables["silver.stop_prediction"]) == {
        TU_FEED.name: files[-1]
    }

    run_once(data_dir, catalog, [TU_FEED])
    assert rows(catalog, "silver.stop_prediction") == expected
    assert len(rows(catalog, "bronze.snapshot")) == len(files)
    assert len(rows(catalog, "silver.trip_status")) == 3


def test_new_files_continue_from_the_watermark(data_dir: Path) -> None:
    ts = int(T0.timestamp())
    lander = Lander(data_dir)
    catalog = open_catalog(data_dir, env={})
    lander.land(TU_FEED.name, T0, tu_bytes(ts, {"A": [0, 0]}))
    run_once(data_dir, catalog, [TU_FEED])
    lander.land(
        TU_FEED.name,
        T0 + timedelta(hours=1),  # next hour folder
        tu_bytes(ts + 3600, {"A": [0, 45]}),
    )
    stats = run_once(data_dir, catalog, [TU_FEED])
    assert stats.files == 1
    stops = rows(catalog, "silver.stop_prediction")
    # The second run is seeded from the first file: only S2 changed.
    assert len(stops) == 3


def test_entity_level_fallback_keeps_good_entities() -> None:
    good = gtfs_realtime_pb2.FeedEntity()
    good.id = "ok"
    good.vehicle.vehicle.id = "v1"
    header = gtfs_realtime_pb2.FeedHeader()
    header.gtfs_realtime_version = "2.0"
    header.timestamp = 1_763_431_200
    # field 2 (entity), length 3, bytes that are not a valid FeedEntity
    bad_entity = b"\x12\x03\x0a\x05x"
    payload = (
        b"\x0a"
        + bytes([len(header.SerializeToString())])
        + header.SerializeToString()
        + b"\x12"
        + bytes([len(good.SerializeToString())])
        + good.SerializeToString()
        + bad_entity
    )
    from transit_lakehouse.worker.decode import Snapshot

    snap = decode(
        payload,
        Snapshot("f", "raw/x", T0, "abc", len(payload)),
    )
    assert snap.header_ts == 1_763_431_200
    assert [e.id for e in snap.entities] == ["ok"]
    assert snap.entity_errors == 1
    assert snap.decode_error.startswith("partial_decode")


def test_corrupt_file_is_recorded_not_skipped(data_dir: Path) -> None:
    rel = "raw/feed=buses_tripupdates/date=2026-11-18/hour=02/"
    path = data_dir / rel / "20261118T020000.000000Z_abcdef123456.pb.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not gzip")
    snap = read_snapshot(
        data_dir, TU_FEED.name, path.relative_to(data_dir).as_posix()
    )
    assert snap.decode_error.startswith("read_error")
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED])
    [row] = rows(catalog, "bronze.snapshot")
    assert row["decode_error"].startswith("read_error")
    assert row["entities"] == 0


def test_quarantine_rules() -> None:
    now = T0
    ts = int(now.timestamp())
    assert quarantine_reason(-33.8, 151.2, 10.0, ts, now) is None
    assert quarantine_reason(0.0, 0.0, None, ts, now) == "no_position"
    assert quarantine_reason(51.5, -0.1, None, ts, now) == "outside_nsw"
    assert (
        quarantine_reason(-33.8, 151.2, None, ts + 3600, now)
        == "timestamp_in_future"
    )


def test_commit_never_moves_a_watermark_back(data_dir: Path) -> None:
    catalog = open_catalog(data_dir, env={})
    table = ensure_tables(catalog)["bronze.snapshot"]
    empty = {f.name: [] for f in table.schema().as_arrow()}
    commit("bronze.snapshot", table, empty, {"f": "raw/b"})
    commit("bronze.snapshot", table, empty, {"f": "raw/a"})
    assert watermarks(table) == {"f": "raw/b"}


def test_expire_keeps_current_snapshot(data_dir: Path) -> None:
    _land_example(data_dir)
    catalog = open_catalog(data_dir, env={})
    for _ in range(3):
        run_once(data_dir, catalog, [TU_FEED, VP_FEED], max_files=1)
    expected = rows(catalog, "silver.vehicle_position")
    expire(catalog, timedelta(seconds=0))
    for table in ensure_tables(catalog).values():
        assert len(table.snapshots()) == 1
    assert rows(catalog, "silver.vehicle_position") == expected


def test_arrow_types_match_iceberg(data_dir: Path) -> None:
    _land_example(data_dir)
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED, VP_FEED])
    table = catalog.load_table("silver.stop_prediction")
    data = table.scan().to_arrow()
    assert data.schema.field("arrival_time").type == pa.timestamp(
        "us", tz="UTC"
    )
