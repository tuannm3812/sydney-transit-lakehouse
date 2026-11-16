from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from transit_lakehouse.checks.capture import evaluate_feed
from transit_lakehouse.checks.manifest import parse_window, read_polls
from transit_lakehouse.checks.quota_report import build_report
from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander, PollRecord

END = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
START = END - timedelta(hours=1)


def _write(tmp: Path, feed: str, skip_every: int = 0) -> None:
    lander = Lander(tmp)
    for i in range(240):  # 1 hour at 15 s
        if skip_every and i % skip_every == 0:
            continue
        ts = START + timedelta(seconds=15 * i)
        lander.record(
            PollRecord(
                feed=feed,
                fetched_at=ts.isoformat(),
                status=200,
                outcome="new" if i % 2 else "unchanged",
                latency_ms=50,
                size_bytes=400_000,
                header_timestamp=int(ts.timestamp()) - 8,
            )
        )


def _feed() -> Feed:
    return Feed("buses_vehiclepos", "vehicle_positions", "https://x", 15)


def test_parse_window() -> None:
    assert parse_window("7d") == timedelta(days=7)
    assert parse_window("15m") == timedelta(minutes=15)


def test_full_capture_passes(tmp_path: Path) -> None:
    _write(tmp_path, "buses_vehiclepos")
    polls = read_polls(tmp_path, START, END)["buses_vehiclepos"]
    r = evaluate_feed(_feed(), polls, START, END, gate=True)
    assert r.passed, r.failures
    assert r.capture_rate == 1.0
    assert r.freshness_p95_s == 8


def test_two_percent_loss_fails_sla(tmp_path: Path) -> None:
    _write(tmp_path, "buses_vehiclepos", skip_every=40)  # 6 of 240 lost
    polls = read_polls(tmp_path, START, END)["buses_vehiclepos"]
    r = evaluate_feed(_feed(), polls, START, END)
    assert not r.passed
    assert r.max_gap_s == 30


def test_gate_requires_full_history(tmp_path: Path) -> None:
    _write(tmp_path, "buses_vehiclepos")
    polls = read_polls(tmp_path, START, END)["buses_vehiclepos"]
    r = evaluate_feed(
        _feed(), polls, START - timedelta(days=7), END, gate=True
    )
    assert "insufficient history" in "; ".join(r.failures)


def test_quota_report_mentions_confirmed_floor(tmp_path: Path) -> None:
    _write(tmp_path, "buses_vehiclepos")
    report = build_report(read_polls(tmp_path, START, END), START, END, 60_000)
    assert "5,760/day" in report
    assert "No rate or quota rejections" in report
    assert "| buses_vehiclepos |" in report
