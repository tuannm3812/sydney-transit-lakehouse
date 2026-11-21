from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from transit_lakehouse.checks.watch import decide, find_problems
from transit_lakehouse.collector.config import Feed

NOW = datetime(2026, 11, 20, 14, 40, tzinfo=UTC)
GB = 1_000_000_000


def _feed(name: str = "buses_vehiclepos", interval: float = 15) -> Feed:
    return Feed(
        name=name,
        kind="vehicle_positions",
        url="https://api.example.test/v1/gtfs/vehiclepos/buses",
        interval_s=interval,
    )


def _polls(tmp: Path, feed: str, rows: list[tuple[float, int | None]]):
    """rows: (seconds before NOW, status); 200 is a success."""
    for ago, status in rows:
        ts = NOW - timedelta(seconds=ago)
        path = (
            tmp
            / "manifest"
            / f"feed={feed}"
            / f"date={ts:%Y-%m-%d}"
            / f"hour={ts:%H}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "fetched_at": ts.isoformat(),
            "status": status,
            "outcome": "new" if status == 200 else "http_error",
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


def _problems(tmp: Path, feeds: list[Feed], free_gb: float = 50):
    return find_problems(
        tmp, feeds, NOW, 10, free_bytes=lambda _: int(free_gb * GB)
    )


def test_healthy_feed_has_no_problem(tmp_path: Path) -> None:
    _polls(tmp_path, "buses_vehiclepos", [(t, 200) for t in (45, 30, 15)])
    assert _problems(tmp_path, [_feed()]) == []


def test_outage_makes_feed_silent(tmp_path: Path) -> None:
    # Like I-06: success 10 min ago, then only 502s.
    rows = [(3600, 200), (600, 200)] + [(t, 502) for t in (300, 150, 15)]
    _polls(tmp_path, "buses_vehiclepos", rows)
    (p,) = _problems(tmp_path, [_feed()])
    assert p.key == "silent:buses_vehiclepos"
    assert "last attempt 502" in p.text


def test_dead_collector_is_silent(tmp_path: Path) -> None:
    _polls(tmp_path, "buses_vehiclepos", [(7200, 200)])
    (p,) = _problems(tmp_path, [_feed()])
    assert "collector down" in p.text


def test_new_feed_is_not_judged_yet(tmp_path: Path) -> None:
    _polls(tmp_path, "buses_vehiclepos", [(30, 502)])
    assert _problems(tmp_path, [_feed()]) == []


def test_slow_feed_uses_its_own_interval(tmp_path: Path) -> None:
    # A 6-hourly timetable polled 7 h ago is fine (limit 24 h).
    _polls(tmp_path, "static_gtfs", [(7 * 3600, 200)])
    assert _problems(tmp_path, [_feed("static_gtfs", 6 * 3600)]) == []


def test_low_disk(tmp_path: Path) -> None:
    (p,) = _problems(tmp_path, [], free_gb=4.2)
    assert p.key == "disk"
    assert "4.2 GB free" in p.text


def test_alert_only_on_change_then_remind_then_recover() -> None:
    remind = timedelta(hours=3)
    from transit_lakehouse.checks.watch import Problem

    down = [Problem("silent:a", "a silent: last success 14:26 UTC")]
    msg, state = decide({}, down, NOW, remind)
    assert msg and msg.startswith("NEW: a silent")
    msg, state = decide(state, down, NOW + timedelta(minutes=5), remind)
    assert msg is None
    msg, state = decide(state, down, NOW + timedelta(hours=3), remind)
    assert msg and msg.startswith("STILL OPEN")
    msg, state = decide(state, [], NOW + timedelta(hours=4), remind)
    assert msg and "RECOVERED: a silent" in msg
    assert "healthy again" in msg
    assert state["open"] == {}
