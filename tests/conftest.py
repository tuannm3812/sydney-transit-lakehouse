from __future__ import annotations

from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.collector.config import Feed, Settings


def make_feed_bytes(timestamp: int, n_entities: int = 2) -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = timestamp
    for i in range(n_entities):
        ent = msg.entity.add()
        ent.id = f"v{i}"
        ent.vehicle.position.latitude = -33.87
        ent.vehicle.position.longitude = 151.21
    return msg.SerializeToString()


@pytest.fixture
def feed() -> Feed:
    return Feed(
        name="buses_vehiclepos",
        kind="vehicle_positions",
        url="https://api.example.test/v1/gtfs/vehiclepos/buses",
        interval_s=15,
    )


@pytest.fixture
def settings(tmp_path: Path, feed: Feed) -> Settings:
    return Settings(
        api_key="test-key",
        data_dir=tmp_path / "data",
        feeds=(feed,),
        max_rps=1000,
    )
