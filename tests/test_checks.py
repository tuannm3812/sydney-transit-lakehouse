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


def test_slow_feed_window_looks_back_two_intervals() -> None:
    from transit_lakehouse.checks.capture import feed_window_start

    static = Feed("static_gtfs", "static_gtfs", "https://x", 21600)
    assert feed_window_start(static, START, END) == END - timedelta(hours=12)
    assert feed_window_start(_feed(), START, END) == START


def _write_at(tmp: Path, seconds: list[float], error: str | None = None):
    lander = Lander(tmp)
    for s in seconds:
        ts = START + timedelta(seconds=s)
        lander.record(
            PollRecord(
                feed="buses_vehiclepos",
                fetched_at=ts.isoformat(),
                status=200,
                outcome="new",
                latency_ms=1,
                header_timestamp=None if error else int(ts.timestamp()) - 5,
                error=error,
            )
        )


def _eval(tmp: Path, gate: bool = False):
    from transit_lakehouse.checks.manifest import first_seen

    polls = read_polls(tmp, START, END)["buses_vehiclepos"]
    return evaluate_feed(
        _feed(),
        polls,
        START,
        END,
        gate,
        active_since=first_seen(tmp, "buses_vehiclepos"),
    )


def test_leading_outage_after_activation_fails(tmp_path: Path) -> None:
    # Active since before the window; down for the first 30 minutes.
    lander = Lander(tmp_path)
    before = START - timedelta(hours=2)
    lander.record(
        PollRecord(
            feed="buses_vehiclepos",
            fetched_at=before.isoformat(),
            status=200,
            outcome="new",
            latency_ms=1,
        )
    )
    _write_at(tmp_path, [1800 + 15 * i for i in range(120)])
    r = _eval(tmp_path)
    assert r.capture_rate == 0.5
    assert any(f.startswith("gap 1800s") for f in r.failures)


def test_new_feed_is_scored_from_activation(tmp_path: Path) -> None:
    # First ever poll 30 minutes in: the earlier half is not an outage.
    _write_at(tmp_path, [1800 + 15 * i for i in range(120)])
    assert _eval(tmp_path).passed


def test_gate_accepts_first_poll_a_second_late(tmp_path: Path) -> None:
    lander = Lander(tmp_path)
    lander.record(
        PollRecord(
            feed="buses_vehiclepos",
            fetched_at=(START - timedelta(seconds=14)).isoformat(),
            status=200,
            outcome="new",
            latency_ms=1,
        )
    )
    _write_at(tmp_path, [1 + 15 * i for i in range(240)])
    r = _eval(tmp_path, gate=True)
    assert r.passed, r.failures


def test_midwindow_gap_fails_even_with_high_capture(tmp_path: Path) -> None:
    times = [15 * i for i in range(240) if not 100 <= i < 106]  # 90 s gap
    _write_at(tmp_path, times)
    r = _eval(tmp_path)
    assert r.capture_rate > 0.97
    assert any(f.startswith("gap 105s") for f in r.failures)


def test_undecodable_window_fails(tmp_path: Path) -> None:
    _write_at(tmp_path, [15 * i for i in range(240)], error="decode_error: x")
    r = _eval(tmp_path)
    assert "freshness unknown: no feed timestamps" in r.failures
    assert any(f.startswith("undecodable payloads") for f in r.failures)
