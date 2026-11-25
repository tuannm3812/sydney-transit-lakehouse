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
    # A and B new, nothing changes until C appears (A's stop count same)
    # and B leaves the feed, which writes a REMOVED row (D-036).
    got = sorted((r["trip_id"], r["trip_status"]) for r in trips)
    assert got == [
        ("A", "SCHEDULED"),
        ("B", "REMOVED"),
        ("B", "SCHEDULED"),
        ("C", "SCHEDULED"),
    ]
    [gone] = [r for r in trips if r["trip_status"] == "REMOVED"]
    assert (gone["stop_count"], gone["current_delay_s"]) == (0, 60)
    assert gone["route_id"] == "R1"

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
    assert len(rows(catalog, "silver.trip_status")) == 4


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


def test_unreadable_file_does_not_end_every_trip(data_dir: Path) -> None:
    ts = int(T0.timestamp())
    good, _ = land(
        data_dir,
        TU_FEED.name,
        [tu_bytes(ts, {"A": [0, 30]}), tu_bytes(ts + 30, {"A": [0, 30]})],
    )
    bad = data_dir / good.replace(".pb.gz", "").replace("T020000", "T020015")
    bad = bad.with_name(bad.name + ".pb.gz")
    bad.write_bytes(b"not gzip")  # lands between the two good files
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED])
    trips = rows(catalog, "silver.trip_status")
    assert "REMOVED" not in {r["trip_status"] for r in trips}


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
        assert len(table.snapshots()) <= 1  # alert tables were never used
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


def test_warehouse_lands_inside_a_path_with_spaces(tmp_path: Path) -> None:
    # Mike's clone lives under "3. Personal"; a file:// URI turned the
    # space into a literal "%20" folder beside the repo.
    data_dir = tmp_path / "3. Personal" / "data"
    _land_example(data_dir)
    run_once(data_dir, open_catalog(data_dir, env={}), [TU_FEED])
    warehouse = data_dir / "lakehouse" / "warehouse"
    assert list(warehouse.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*%20*"))


def test_uniform_delay_shift_writes_only_the_trip_delay(
    data_dir: Path,
) -> None:
    ts = int(T0.timestamp())
    land(
        data_dir,
        TU_FEED.name,
        [
            tu_bytes(ts, {"A": [0, 0, 0]}),
            tu_bytes(ts + 30, {"A": [60, 60, 60]}),  # whole trip late
            tu_bytes(ts + 60, {"A": [60, 60, 120]}),  # one stop moves
        ],
    )
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED])
    stops = rows(catalog, "silver.stop_prediction")
    assert len(stops) == 3 + 0 + 1
    trips = rows(catalog, "silver.trip_status")
    assert sorted(r["current_delay_s"] for r in trips) == [0, 60]


def test_offset_encoding_is_lossless(data_dir: Path) -> None:
    """Every stop's prediction in every snapshot can be rebuilt."""
    import random

    rng = random.Random(7)
    ts = int(T0.timestamp())
    delays = {f"T{i}": [0] * 6 for i in range(5)}
    truth = []
    payloads = []
    for k in range(25):
        for trip, ds in delays.items():
            roll = rng.random()
            if roll < 0.4:  # whole trip shifts
                shift = rng.choice([-30, 30, 90])
                delays[trip] = [d + shift for d in ds]
            elif roll < 0.6:  # one stop and those after it move
                j = rng.randrange(6)
                delays[trip] = ds[:j] + [d + 45 for d in ds[j:]]
        payloads.append(tu_bytes(ts + 30 * k, delays))
        truth.append({t: list(ds) for t, ds in delays.items()})
    land(data_dir, TU_FEED.name, payloads)
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED])
    stops = rows(catalog, "silver.stop_prediction")
    trips = rows(catalog, "silver.trip_status")
    base_time = int(T0.timestamp())

    for k, snapshot in enumerate(truth):
        at = T0 + timedelta(seconds=30 * k)
        for trip, ds in snapshot.items():
            current = max(
                (
                    r
                    for r in trips
                    if r["trip_id"] == trip and r["fetched_at"] <= at
                ),
                key=lambda r: r["fetched_at"],
            )
            for seq, delay in enumerate(ds, start=1):
                row = max(
                    (
                        r
                        for r in stops
                        if r["trip_id"] == trip
                        and r["stop_sequence"] == seq
                        and r["fetched_at"] <= at
                    ),
                    key=lambda r: r["fetched_at"],
                )
                shift = current["current_delay_s"] - row["trip_delay_s"]
                assert row["arrival_delay_s"] + shift == delay
                rebuilt = row["arrival_time"].timestamp() + shift
                assert rebuilt == base_time + 600 * seq + delay
    # and it is smaller than one row per stop per change
    assert len(stops) < 25 * 5 * 6 / 2


AL_FEED = Feed("service_alerts", "service_alerts", "https://x/alerts", 60)


def _ld(field_no: int, body: bytes) -> bytes:
    from transit_lakehouse.worker.decode import _varint

    return _varint(field_no << 3 | 2) + _varint(len(body)) + body


def alert_entity(alert_id: str, header: str, route: str) -> bytes:
    ent = gtfs_realtime_pb2.FeedEntity()
    ent.id = alert_id
    a = ent.alert
    a.cause = gtfs_realtime_pb2.Alert.CONSTRUCTION
    a.effect = gtfs_realtime_pb2.Alert.DETOUR
    a.header_text.translation.add(text=header, language="en")
    period = a.active_period.add()
    period.start, period.end = 1_763_400_000, 1_763_500_000
    sel = a.informed_entity.add()
    sel.route_id = route
    sel.stop_id = "2000338"
    return ent.SerializeToString()


def tfnsw_bad_entity(alert_id: str) -> bytes:
    """An alert whose trip selector carries a string in field 7 (D-020)."""
    trip = gtfs_realtime_pb2.TripDescriptor(trip_id="T9").SerializeToString()
    trip += _ld(7, b"135N.817.160.64.A.8.9120237")
    selector = _ld(4, trip)
    alert = _ld(5, selector) + b"\x30\x01"  # cause = UNKNOWN_CAUSE
    return _ld(1, alert_id.encode()) + _ld(5, alert)


def alerts_bytes(ts: int, entities: list[bytes]) -> bytes:
    header = gtfs_realtime_pb2.FeedHeader(
        gtfs_realtime_version="2.0", timestamp=ts
    ).SerializeToString()
    return _ld(1, header) + b"".join(_ld(2, e) for e in entities)


def test_tfnsw_alert_is_repaired_not_dropped() -> None:
    from transit_lakehouse.worker.decode import Snapshot

    bad = tfnsw_bad_entity("A9")
    with pytest.raises(Exception):  # noqa: B017 - the bindings reject it
        gtfs_realtime_pb2.FeedEntity().ParseFromString(bad)
    payload = alerts_bytes(1_763_431_200, [alert_entity("A1", "x", "R"), bad])
    snap = decode(payload, Snapshot("f", "raw/x", T0, "abc", 1))
    assert [e.id for e in snap.entities] == ["A1", "A9"]
    assert (snap.entities_repaired, snap.entity_errors) == (1, 0)
    assert snap.entities[1].alert.informed_entity[0].trip.trip_id == "T9"


def test_alerts_history_new_changed_removed(data_dir: Path) -> None:
    ts = int(T0.timestamp())
    land(
        data_dir,
        AL_FEED.name,
        [
            alerts_bytes(ts, [alert_entity("A1", "Lift out", "R1")]),
            alerts_bytes(ts + 60, [alert_entity("A1", "Lift out", "R1")]),
            alerts_bytes(
                ts + 120,
                [
                    alert_entity("A1", "Lift out until 5 pm", "R1"),
                    tfnsw_bad_entity("A9"),
                ],
            ),
            alerts_bytes(ts + 180, [tfnsw_bad_entity("A9")]),
        ],
    )
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [AL_FEED])
    alerts = sorted(
        rows(catalog, "silver.alert"), key=lambda r: r["fetched_at"]
    )
    assert sorted((r["alert_id"], r["event"]) for r in alerts) == [
        ("A1", "changed"),
        ("A1", "new"),
        ("A1", "removed"),
        ("A9", "new"),
    ]
    first = next(r for r in alerts if r["event"] == "new")
    assert first["cause"] == "CONSTRUCTION" and first["effect"] == "DETOUR"
    assert first["header_text"] == "Lift out"
    assert first["mode"] == "all"
    scopes = rows(catalog, "silver.alert_scope")
    assert {(r["alert_id"], r["route_id"], r["trip_id"]) for r in scopes} == {
        ("A1", "R1", None),
        ("A9", None, "T9"),
    }
    assert len(scopes) == 3  # A1 twice (new, changed), A9 once
    snaps = rows(catalog, "bronze.snapshot")
    assert sum(r["entities_repaired"] for r in snaps) == 2


def _bad_after(data_dir: Path, rel: str) -> str:
    """Land an unreadable file 15 s after ``rel`` (the first file, at
    T0), between it and the next one, and return its path."""
    assert "T020000" in rel
    bad = rel.replace("T020000", "T020015")
    (data_dir / bad).write_bytes(b"not gzip")
    return bad


def _good_bad_good(data_dir: Path, feed: str, first: bytes, last: bytes):
    """A good file, an unreadable one, then another good file."""
    good, _ = land(data_dir, feed, [first, last])
    return _bad_after(data_dir, good)


def test_failed_file_keeps_trip_state(data_dir: Path) -> None:
    """D-049: the next good file diffs against the last good one."""
    ts = int(T0.timestamp())
    bad = _good_bad_good(
        data_dir,
        TU_FEED.name,
        tu_bytes(ts, {"A": [0, 30], "B": [60]}),
        tu_bytes(ts + 30, {"A": [0, 30]}),
    )
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [TU_FEED])
    snaps = rows(catalog, "bronze.snapshot")
    assert [s["decode_error"] is not None for s in snaps].count(True) == 1
    assert bad in {s["source_file"] for s in snaps}
    # Stops are written once, by the first file; nothing is re-emitted.
    assert len(rows(catalog, "silver.stop_prediction")) == 3
    trips = rows(catalog, "silver.trip_status")
    status = sorted((r["trip_id"], r["trip_status"]) for r in trips)
    # B ended: its REMOVED row comes from the next good file.
    assert status == [("A", "SCHEDULED"), ("B", "REMOVED"), ("B", "SCHEDULED")]


def test_run_starting_after_failed_file_seeds_from_last_good(
    tmp_path: Path,
) -> None:
    """A run whose watermark is a failed file looks back to the last
    readable one, so two runs give the same rows as one (D-049)."""
    ts = int(T0.timestamp())
    payloads = (
        tu_bytes(ts, {"A": [0, 30], "B": [60]}),
        tu_bytes(ts + 30, {"A": [0, 30]}),
    )
    one, two = tmp_path / "one", tmp_path / "two"
    for d in (one, two):
        _good_bad_good(d, TU_FEED.name, *payloads)
    cat_one = open_catalog(one, env={})
    run_once(one, cat_one, [TU_FEED])
    cat_two = open_catalog(two, env={})
    run_once(two, cat_two, [TU_FEED], max_files=2)  # stops at the bad file
    run_once(two, cat_two, [TU_FEED])
    for name in ("silver.stop_prediction", "silver.trip_status"):
        assert rows(cat_two, name) == rows(cat_one, name)


def test_failed_file_keeps_alert_and_vehicle_state(data_dir: Path) -> None:
    ts = int(T0.timestamp())
    al = alerts_bytes(ts, [alert_entity("A1", "Detour", "R1")])
    _good_bad_good(data_dir, AL_FEED.name, al, al)
    _good_bad_good(
        data_dir,
        VP_FEED.name,
        vp_bytes(ts, {"v1": (ts, 5.0)}),
        vp_bytes(ts + 30, {"v1": (ts, 5.0)}),
    )
    catalog = open_catalog(data_dir, env={})
    run_once(data_dir, catalog, [AL_FEED, VP_FEED])
    events = [r["event"] for r in rows(catalog, "silver.alert")]
    assert events == ["new"]  # no "removed" then "new" around the bad file
    assert len(rows(catalog, "silver.vehicle_position")) == 1
