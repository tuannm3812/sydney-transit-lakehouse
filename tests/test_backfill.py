"""The gate-day backfill (D-040, D-043): replay S3 hours straight into
the live lakehouse, then let the live worker carry on from local raw.

The tables this gives must match a lakehouse the live worker built on
its own from the same snapshots, hour for hour.
"""

from __future__ import annotations

import random
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_replay import _bucket
from tests.test_timetable import BASE, gtfs_zip
from tests.test_worker import (
    AL_FEED,
    T0,
    TU_FEED,
    VP_FEED,
    alert_entity,
    alerts_bytes,
    tu_bytes,
    vp_bytes,
)
from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.worker import replay as rp
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import ensure_tables, open_catalog

STATIC = Feed("static_gtfs", "static_gtfs", "https://x/gtfs", 21600)
REALTIME = [TU_FEED, VP_FEED, AL_FEED]
FEEDS = [*REALTIME, STATIC]
HOURS = 4


def _land(root: Path) -> Path:
    """Four hours of three realtime feeds and two timetable publishes,
    landed as the collector lands them (with a manifest folder)."""
    rng = random.Random(7)
    data = root / "live"
    lander = Lander(data)
    (data / "manifest").mkdir(parents=True)
    delays = {f"T{i}": [0] * 5 for i in range(6)}
    base = int(T0.timestamp())
    for step in range(HOURS * 12):  # every 5 minutes
        at = T0 + timedelta(minutes=5 * step)
        ts = base + 300 * step
        for trip, ds in delays.items():
            if rng.random() < 0.5:
                delays[trip] = [d + rng.choice([30, -15, 60]) for d in ds]
        lander.land(TU_FEED.name, at, tu_bytes(ts, delays))
        lander.land(
            VP_FEED.name,
            at,
            vp_bytes(ts, {f"v{i}": (ts - i, 10.0 + step) for i in range(4)}),
        )
        ents = [alert_entity("A1", f"v{step // 10}", "R1")]
        lander.land(AL_FEED.name, at, alerts_bytes(ts, ents))
    for hours, extra in ((1.2, {}), (2.5, {"T4": ["10:00:00"] * 3})):
        lander.land(
            STATIC.name,
            T0 + timedelta(hours=hours),
            gtfs_zip({**BASE, **extra}),
            compress=False,
        )
    return data


def _reference(live: Path):
    """The live worker alone, run after every snapshot had landed."""
    catalog = open_catalog(live, env={})
    for _ in range(3):  # one timetable per run
        run_once(live, catalog, FEEDS)
    return catalog


def _versions(catalog) -> list[str]:
    rows = catalog.load_table("silver.timetable_version").scan()
    return sorted(r["version"] for r in rows.to_arrow().to_pylist())


@pytest.fixture
def setup(tmp_path: Path):
    live = _land(tmp_path)
    bucket = _bucket(live, tmp_path)
    server = tmp_path / "server"
    shutil.copytree(live, server)  # raw/ and manifest/, as on the host
    return live, bucket, server


def test_backfill_then_live_matches_live_only(setup) -> None:
    live, bucket, server = setup
    reference = _reference(live)
    catalog = open_catalog(server, env={})
    start, cut = T0 + timedelta(hours=1), T0 + timedelta(hours=3)

    stats = rp.replay(
        rp.LocalTars(bucket),
        FEEDS,
        start,
        cut,
        server / "backfill",
        catalog,
        maintain_every=1,
    )
    assert stats.timetables == 2 and stats.maintenance >= 2
    # Expired as it went (I-20): only a handful of snapshots are left.
    for name, table in ensure_tables(catalog).items():
        assert len(table.snapshots()) <= 2, name
    # The scratch folder keeps one seed file per realtime feed.
    left = list((server / "backfill" / "raw").rglob("*.pb.gz"))
    assert len(left) == len(REALTIME)
    # The collector's raw tree is untouched.
    assert sorted((server / "raw").rglob("*")) == sorted(
        server / p.relative_to(live) for p in (live / "raw").rglob("*")
    )

    # The live worker carries on from the backfill's watermarks.
    after = run_once(server, catalog, FEEDS)
    assert not after.unseeded and not after.timetables
    compared, diffs = rp.verify(
        reference, catalog, REALTIME, start, T0 + timedelta(hours=HOURS)
    )
    assert compared > 0 and diffs == []
    assert _versions(catalog) == _versions(reference)


def test_backfill_resumes_after_a_cut_run(setup) -> None:
    live, bucket, server = setup
    reference = _reference(live)
    catalog = open_catalog(server, env={})
    start = T0 + timedelta(hours=1)
    src, scratch = rp.LocalTars(bucket), server / "backfill"

    rp.replay(src, FEEDS, start, start + timedelta(hours=1), scratch, catalog)
    # The same command with a later end picks up where it stopped.
    end = T0 + timedelta(hours=HOURS)
    again = rp.replay(src, FEEDS, start, end, scratch, catalog)
    assert again.hours == 2 * len(REALTIME) + 1  # + the 2.5 h timetable
    # Run again: nothing new. The last hour is fetched again, since a
    # watermark cannot tell whether its hour was complete.
    done = rp.replay(src, FEEDS, start, end, scratch, catalog)
    assert done.files == 0 and done.hours == 0 and not done.timetables
    # A window that ends before the watermark's hour is skipped outright.
    early = rp.replay(
        src, REALTIME, start, end - timedelta(hours=1), scratch, catalog
    )
    assert sorted(early.skipped) == sorted(f.name for f in REALTIME)

    _, diffs = rp.verify(reference, catalog, REALTIME, start, end)
    assert diffs == []
    assert _versions(catalog) == _versions(reference)


def test_backfill_never_leaves_a_hole(setup) -> None:
    """A window that starts after the tables end is moved back to them,
    so a missed day is filled rather than skipped."""
    live, bucket, server = setup
    reference = _reference(live)
    catalog = open_catalog(server, env={})
    src, scratch = rp.LocalTars(bucket), server / "backfill"
    start, end = T0 + timedelta(hours=1), T0 + timedelta(hours=HOURS)
    rp.replay(
        src, REALTIME, start, start + timedelta(hours=1), scratch, catalog
    )
    rp.replay(src, REALTIME, end - timedelta(hours=1), end, scratch, catalog)
    _, diffs = rp.verify(reference, catalog, REALTIME, start, end)
    assert diffs == []


def test_replay_refuses_the_collectors_data_folder(setup) -> None:
    _, bucket, server = setup
    before = sorted((server / "raw").rglob("*"))
    with pytest.raises(ValueError, match="collector"):
        rp.replay(
            rp.LocalTars(bucket),
            REALTIME,
            T0 + timedelta(hours=1),
            T0 + timedelta(hours=2),
            server,
            open_catalog(server, env={}),
        )
    assert sorted((server / "raw").rglob("*")) == before


def test_cli_backfill_into_the_live_lakehouse(setup, capsys) -> None:
    from transit_lakehouse.worker.__main__ import main
    from transit_lakehouse.worker.runner import single_run

    _, bucket, server = setup
    feeds = server.parent / "feeds.toml"
    feeds.write_text(
        "".join(
            f'[[feeds]]\nname = "{f.name}"\nkind = "{f.kind}"\n'
            f'path = "/x"\ninterval_s = {f.interval_s}\n'
            for f in FEEDS
        )
    )
    args = [
        "--data-dir", str(server), "--feeds", str(feeds), "replay",
        "--tars", str(bucket), "--scratch", str(server / "backfill"),
        "--start", f"{T0 + timedelta(hours=1):%Y-%m-%dT%H}",
        "--end", f"{T0 + timedelta(hours=3):%Y-%m-%dT%H}",
    ]  # fmt: skip
    # The backfill takes the live worker's lock: never both at once.
    with single_run(server), pytest.raises(RuntimeError, match="another"):
        main(args)
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "2 timetables" in out and "missing" not in out
