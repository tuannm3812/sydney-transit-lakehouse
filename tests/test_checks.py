from __future__ import annotations

import json
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


def test_poll_at_window_start_does_not_exceed_expected(
    tmp_path: Path,
) -> None:
    # 6 polls at 0,15,..,75 s inside an 80 s span = 6 slots, not 5.
    lander = Lander(tmp_path)
    for i in range(6):
        ts = START + timedelta(seconds=15 * i)
        lander.record(
            PollRecord(
                feed="f",
                fetched_at=ts.isoformat(),
                status=200,
                outcome="new",
                latency_ms=1,
                header_timestamp=int(ts.timestamp()),
            )
        )
    end = START + timedelta(seconds=80)
    polls = read_polls(tmp_path, START, end)["f"]
    r = evaluate_feed(_feed(), polls, START, end)
    assert (r.ok, r.expected) == (6, 6)


def test_cli_defaults_follow_data_dir_env(monkeypatch, tmp_path: Path) -> None:
    # Regression: in the container DATA_DIR=/data, and the checks must
    # read the same place the collector writes (found on the EC2 host).
    from transit_lakehouse.checks import capture

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "elsewhere"))
    seen = {}

    def fake_read(data_dir, start, end):
        seen["dir"] = data_dir
        return {}

    monkeypatch.setattr(capture, "read_polls", fake_read)
    repo = Path(__file__).resolve().parents[1]
    feeds = str(repo / "config/feeds.toml")
    capture.main(["--feeds", feeds, "--window", "1m"])
    assert seen["dir"] == tmp_path / "elsewhere"


def test_quota_rate_uses_observed_span_not_window(tmp_path: Path) -> None:
    # 1 hour of polls inside a 24 h window must report 5,760/day, not 240.
    _write(tmp_path, "buses_vehiclepos")
    report = build_report(
        read_polls(tmp_path, END - timedelta(hours=24), END),
        END - timedelta(hours=24),
        END,
        60_000,
    )
    assert "5,760/day" in report


def _write_frozen(tmp: Path, frozen_polls: int) -> None:
    """One new snapshot, then ``frozen_polls`` unchanged ones at 15 s."""
    lander = Lander(tmp)
    for i in range(240):
        ts = START + timedelta(seconds=15 * i)
        frozen = 0 < i % 120 <= frozen_polls
        lander.record(
            PollRecord(
                feed="buses_vehiclepos",
                fetched_at=ts.isoformat(),
                status=200,
                outcome="unchanged" if frozen else "new",
                latency_ms=50,
                header_timestamp=int(ts.timestamp()) - 8,
            )
        )


def test_stuck_feed_warns_but_passes(tmp_path: Path) -> None:
    _write_frozen(tmp_path, frozen_polls=24)  # 6 minutes frozen
    polls = read_polls(tmp_path, START, END)["buses_vehiclepos"]
    r = evaluate_feed(_feed(), polls, START, END)
    assert r.passed, r.failures
    assert r.longest_unchanged_s == 360
    assert r.warnings and r.warnings[0].startswith("stuck")


def test_short_unchanged_run_does_not_warn(tmp_path: Path) -> None:
    _write_frozen(tmp_path, frozen_polls=4)  # 1 minute
    polls = read_polls(tmp_path, START, END)["buses_vehiclepos"]
    r = evaluate_feed(_feed(), polls, START, END)
    assert r.longest_unchanged_s == 60
    assert not r.warnings


def test_html_report_renders_with_disk_runway(tmp_path: Path) -> None:
    from transit_lakehouse.checks.quota_report import compute_stats
    from transit_lakehouse.checks.report_html import describe, render_html

    _write(tmp_path, "buses_vehiclepos")
    st = compute_stats(read_polls(tmp_path, START, END), START, END, 60_000)
    st["disk"] = {"total": 80e9, "used": 10e9, "free": 70e9}
    page = render_html(st)
    assert "<title>Quota and Volume Report</title>" in page
    assert "Bus positions" in page
    assert "Disk runway" in page
    assert "5,760" in page
    assert describe("service_alerts") == ("Network-wide", "alerts")
    assert describe("sydneytrains_tripupdates") == ("Trains", "trip updates")


def test_html_report_shows_change_against_previous(tmp_path: Path) -> None:
    from transit_lakehouse.checks.quota_report import compute_stats
    from transit_lakehouse.checks.report_html import render_html

    _write(tmp_path, "buses_vehiclepos")
    st = compute_stats(read_polls(tmp_path, START, END), START, END, 60_000)
    prev = json.loads(json.dumps(st))
    prev["feeds"][0]["per_day"] = st["feeds"][0]["per_day"] / 2
    st["previous"] = prev
    page = render_html(st)
    assert "vs previous" in page
    assert "▲ 100%" in page


def test_html_report_survives_no_data_and_full_disk(tmp_path: Path) -> None:
    from transit_lakehouse.checks.quota_report import compute_stats
    from transit_lakehouse.checks.report_html import render_html

    empty = compute_stats({}, START, END, 60_000)
    empty["disk"] = {"total": 80e9, "used": 80e9, "free": 0}
    assert "<title>" in render_html(empty)

    _write(tmp_path, "buses_vehiclepos")
    st = compute_stats(read_polls(tmp_path, START, END), START, END, 60_000)
    st["disk"] = {"total": 80e9, "used": 80e9, "free": 0}
    page = render_html(st)
    assert "0 days" in page
    assert st["downloaded_per_day"] >= st["received_per_day"]
