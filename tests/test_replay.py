from __future__ import annotations

import logging
import random
from datetime import timedelta
from pathlib import Path

from tests.test_worker import (
    AL_FEED,
    T0,
    TU_FEED,
    VP_FEED,
    alert_entity,
    alerts_bytes,
    tfnsw_bad_entity,
    tu_bytes,
    vp_bytes,
)
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.offload import core
from transit_lakehouse.worker import replay as rp
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import open_catalog

FEEDS = [TU_FEED, VP_FEED, AL_FEED]


def _live(root: Path) -> Path:
    """Three hours of three feeds, landed and built like the server."""
    rng = random.Random(11)
    data = root / "live"
    lander = Lander(data)
    delays = {f"T{i}": [0] * 5 for i in range(6)}
    base = int(T0.timestamp())
    for step in range(3 * 12):  # every 5 minutes for 3 hours
        at = T0 + timedelta(minutes=5 * step)
        ts = base + 300 * step
        for trip, ds in delays.items():
            if rng.random() < 0.5:
                delays[trip] = [d + rng.choice([30, -15, 60]) for d in ds]
        lander.land(TU_FEED.name, at, tu_bytes(ts, delays))
        lander.land(
            VP_FEED.name,
            at,
            vp_bytes(ts, {f"v{i}": (ts - i, 10.0) for i in range(4)}),
        )
        ents = [alert_entity("A1", f"v{step // 10}", "R1")]
        if step % 7:
            ents.append(tfnsw_bad_entity("A9"))
        lander.land(AL_FEED.name, at, alerts_bytes(ts, ents))
    run_once(data, open_catalog(data, env={}), FEEDS)
    return data


def _bucket(live: Path, root: Path) -> Path:
    """Tar every hour the way the offload job does."""
    bucket = root / "bucket"
    for part in core.partitions(live):
        if part.kind != "raw":
            continue
        out = bucket / part.key
        out.parent.mkdir(parents=True, exist_ok=True)
        core.build_tar(part, out)
    return bucket


def test_replay_from_tars_matches_live(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    from transit_lakehouse.worker import tables

    # Few kept versions, so a commit through a stale handle would try to
    # delete metadata files that are already gone (server rehearsal,
    # 2026-11-23).
    for props in (tables.METADATA_PROPERTIES, tables.TABLE_PROPERTIES):
        monkeypatch.setitem(props, "write.metadata.previous-versions-max", "2")
    live = _live(tmp_path)
    bucket = _bucket(live, tmp_path)
    start, end = T0 + timedelta(hours=1), T0 + timedelta(hours=3)

    replayed = tmp_path / "replay"
    replayed.mkdir()
    catalog = open_catalog(replayed, env={})
    with caplog.at_level(logging.WARNING, logger="pyiceberg"):
        stats = rp.replay(
            rp.LocalTars(bucket), FEEDS, start, end, replayed, catalog
        )
    assert "Failed to delete" not in caplog.text
    assert stats.hours == 2 * len(FEEDS) and not stats.missing_hours
    # Only the seed snapshot of each feed is left on disk.
    left = list((replayed / "raw").rglob("*.pb.gz"))
    assert len(left) == len(FEEDS)

    compared, diffs = rp.verify(
        open_catalog(live, env={}), catalog, FEEDS, start, end
    )
    assert compared > 0 and diffs == []


def test_verify_catches_a_difference(tmp_path: Path) -> None:
    live = _live(tmp_path)
    bucket = _bucket(live, tmp_path)
    start, end = T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    replayed = tmp_path / "replay"
    replayed.mkdir()
    catalog = open_catalog(replayed, env={})
    # No seed hour: the first file's rows are all written as new, which
    # the live tables (seeded from the hour before) never did.
    later = start + timedelta(hours=-1)
    for feed in FEEDS:
        (
            bucket / f"raw/feed={feed.name}/date={later:%Y-%m-%d}/"
            f"hour={later:%H}.tar"
        ).unlink()
    rp.replay(rp.LocalTars(bucket), FEEDS, start, end, replayed, catalog)
    _, diffs = rp.verify(
        open_catalog(live, env={}), catalog, FEEDS, start, end
    )
    assert {d.table for d in diffs} >= {"silver.stop_prediction"}
