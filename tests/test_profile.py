from __future__ import annotations

from google.transit import gtfs_realtime_pb2

from transit_lakehouse.checks.profile import profile, rows_of


def _tu(delays: list[int]) -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    ent = msg.entity.add()
    ent.id = "t1"
    ent.trip_update.trip.trip_id = "T1"
    ent.trip_update.trip.start_date = "20261118"
    for i, d in enumerate(delays):
        stu = ent.trip_update.stop_time_update.add()
        stu.stop_id = f"S{i}"
        stu.arrival.delay = d
    return msg.SerializeToString()


def test_rows_key_trip_updates_by_stop() -> None:
    rows = rows_of(_tu([0, 30, 60]))
    assert ("tu", "T1", "20261118", "S1") in rows
    assert len(rows) == 4  # 3 stops + the trip row


def test_profile_counts_only_changed_rows() -> None:
    prof = profile([_tu([0, 30, 60]), _tu([0, 30, 90]), _tu([0, 30, 90])])
    assert prof.snapshots == 3
    assert prof.changed == [1, 0]
    assert prof.added == [0, 0]
    assert round(prof.change_share, 3) == round(1 / 8, 3)


def test_undecodable_snapshot_is_empty() -> None:
    assert rows_of(b"\xff\xff\xff") == {}
