from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from transit_lakehouse.collector.landing import Lander, PollRecord

TS = datetime(2026, 11, 16, 23, 59, 58, 123456, tzinfo=UTC)


def test_land_partitions_by_feed_and_utc_hour(tmp_path: Path) -> None:
    lander = Lander(tmp_path)
    rel = lander.land("buses_vehiclepos", TS, b"abc")
    assert rel.startswith(
        "raw/feed=buses_vehiclepos/date=2026-11-16/hour=23/"
        "20261116T235958.123456Z_"
    )
    assert (tmp_path / rel).read_bytes() == b"abc"
    assert not list(tmp_path.rglob("*.tmp"))


def test_unchanged_detection_survives_restart(tmp_path: Path) -> None:
    lander = Lander(tmp_path)
    rel = lander.land("f", TS, b"abc")
    lander.record(
        PollRecord(
            feed="f",
            fetched_at=TS.isoformat(),
            status=200,
            outcome="new",
            latency_ms=1,
            sha256=__import__("hashlib").sha256(b"abc").hexdigest(),
            path=rel,
        )
    )
    fresh = Lander(tmp_path)  # simulates a container restart
    digest = __import__("hashlib").sha256(b"abc").hexdigest()
    assert fresh.is_unchanged("f", digest)
    assert not fresh.is_unchanged("f", "0" * 64)


def test_manifest_line_is_json(tmp_path: Path) -> None:
    lander = Lander(tmp_path)
    lander.record(
        PollRecord(
            feed="f",
            fetched_at=TS.isoformat(),
            status=503,
            outcome="http_error",
            latency_ms=5,
        )
    )
    path = tmp_path / "manifest/feed=f/date=2026-11-16/hour=23.jsonl"
    assert json.loads(path.read_text())["status"] == 503
