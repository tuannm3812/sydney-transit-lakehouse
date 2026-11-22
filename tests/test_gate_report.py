from __future__ import annotations

import json
from pathlib import Path

from transit_lakehouse.checks.gate_report import build, main
from transit_lakehouse.reports.render import parse, to_html


def _feed(name: str, rate: float, gap: float, failures=()) -> dict:
    return {
        "feed": name,
        "expected": 40320,
        "ok": round(40320 * rate),
        "new": 0,
        "capture_rate": rate,
        "freshness_p50_s": 6.0,
        "freshness_p95_s": 12.0,
        "max_gap_s": gap,
        "staleness_s": 3.0,
        "coverage_start": None,
        "failures": list(failures),
    }


def _run(start: str, end: str, feeds: list[dict]) -> dict:
    return {"window_start": start, "window_end": end, "feeds": feeds}


def test_passing_gate_renders_as_a_valid_report(tmp_path: Path) -> None:
    gate = _run(
        "2026-11-20T16:00:00+00:00",
        "2026-11-27T16:00:00+00:00",
        [
            _feed("buses_vehiclepos", 0.9998, 31),
            _feed("metro_tripupdates", 0.9991, 62),
        ],
    )
    day = tmp_path / "capture-20261122.json"
    day.write_text(
        json.dumps(
            _run(
                "2026-11-21T00:05:00+00:00",
                "2026-11-22T00:05:00+00:00",
                [_feed("buses_vehiclepos", 0.9998, 28)],
            )
        )
    )
    text = build(gate, [day], "2ae606f", "2026-11-28", "CI")
    rep = parse(text)  # enforces the report standard
    assert rep.meta["verdict"] == "Pass"
    assert rep.meta["status"] == "Draft"
    assert "**passed**" in text
    assert "`metro_tripupdates` | 99.91%" in text
    assert "| 21 Nov |" in text
    assert "<table" in to_html(rep)


def test_failing_gate_names_the_feed(tmp_path: Path) -> None:
    gate_json = tmp_path / "gate.json"
    gate_json.write_text(
        json.dumps(
            _run(
                "2026-11-14T00:00:00+00:00",
                "2026-11-21T00:00:00+00:00",
                [_feed("buses_tripupdates", 0.9912, 2203, ["gap 2203s"])],
            )
        )
    )
    out = tmp_path / "r.md"
    assert (
        main(
            [
                "--gate-json",
                str(gate_json),
                "--commit",
                "9a8185f",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    text = out.read_text()
    assert parse(text).meta["verdict"] == "Fail"
    assert "`buses_tripupdates` failed:** gap 2203s" in text
