"""Light worker CLI (D-023, D-027).

    python -m transit_lakehouse.worker run --once      # one pass
    python -m transit_lakehouse.worker run --loop 60   # always-on
    python -m transit_lakehouse.worker expire --older-than 24h
    python -m transit_lakehouse.worker status          # rows, watermarks
    python -m transit_lakehouse.worker compact         # merge small files

    python -m transit_lakehouse.worker --data-dir /tmp/r replay \\
        --bucket B --feed buses_tripupdates --start 2026-11-17T00 \\
        --end 2026-11-18T00
    python -m transit_lakehouse.worker verify --other /tmp/r \\
        --feed buses_tripupdates --start 2026-11-17T00 --end 2026-11-18T00

    # gate-day backfill into the live lakehouse (D-040, docs/9_phase1.md)
    python -m transit_lakehouse.worker --data-dir /data replay \\
        --bucket B --scratch /data/backfill --start 2026-11-17T06 \\
        --end 2026-11-27T21

Reads ``DATA_DIR`` (raw snapshots in, ``lakehouse/`` out) and
``FEEDS_CONFIG``. Needs no TfNSW key: it never calls the API.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

import pyarrow as pa

from transit_lakehouse.checks.manifest import parse_window
from transit_lakehouse.collector.config import load_feeds
from transit_lakehouse.worker.replay import MAINTAIN_EVERY
from transit_lakehouse.worker.runner import (
    DEFAULT_MAX_ROWS,
    expire,
    run_loop,
    run_once,
    single_run,
)
from transit_lakehouse.worker.tables import (
    ensure_tables,
    open_catalog,
    watermarks,
)


def _status(catalog) -> str:
    lines = []
    for name, table in ensure_tables(catalog).items():
        snap = table.current_snapshot()
        total = int(snap.summary.get("total-records", 0)) if snap else 0
        lines.append(f"{name}: {total:,} rows, {len(table.snapshots())} snaps")
        for feed, mark in sorted(watermarks(table).items()):
            lines.append(f"  {feed:<34} {mark.rsplit('/', 1)[-1]}")
    return "\n".join(lines)


def _replay_or_verify(args, catalog, feeds) -> int:
    from transit_lakehouse.worker import replay as rp

    start, end = rp.parse_hour(args.start), rp.parse_hour(args.end)
    if args.cmd == "verify":
        other = open_catalog(args.other)
        compared, diffs = rp.verify(catalog, other, feeds, start, end)
        for d in diffs:
            print(
                f"DIFF {d.table} {d.feed} {d.hour}: "
                f"{d.left[0]:,} rows vs {d.right[0]:,} rows"
            )
        print(
            f"{compared} table-hours compared, {len(diffs)} differ"
            + ("" if diffs else ": identical")
        )
        return 1 if diffs else 0
    scratch = args.scratch or args.data_dir
    if args.bucket:
        import boto3  # the offload extra; credentials from the role

        source = rp.S3Tars(boto3.client("s3"), args.bucket)
    else:
        source = rp.LocalTars(args.tars)
    scratch.mkdir(parents=True, exist_ok=True)
    # The lock sits with the lakehouse, so a backfill into the live one
    # and the live worker can never run at once.
    with single_run(args.data_dir):
        stats = rp.replay(
            source,
            feeds,
            start,
            end,
            scratch,
            catalog,
            maintain_every=args.maintain_every or None,
        )
    print(
        f"replayed {stats.hours} hours, {stats.files} files, "
        f"{stats.timetables} timetables, {stats.maintenance} compactions: "
        + ", ".join(f"{k} +{v:,}" for k, v in stats.rows.items() if v)
    )
    for name in stats.skipped:
        print(f"skipped {name}: already built to the end")
    for key in stats.missing_hours:
        print(f"missing {key}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "data")),
    )
    parser.add_argument(
        "--feeds",
        type=Path,
        default=Path(os.environ.get("FEEDS_CONFIG", "config/feeds.toml")),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="build tables from new raw files")
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", type=float, metavar="SECONDS")
    run.add_argument("--feed", action="append", help="limit to these")
    run.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    run.add_argument(
        "--max-files", type=int, help="per feed per run (backfill pacing)"
    )
    exp = sub.add_parser("expire", help="expire old Iceberg snapshots")
    exp.add_argument("--older-than", default="24h")
    sub.add_parser("status", help="rows and watermarks per table")
    cmp = sub.add_parser(
        "compact",
        help="merge small files in closed hours (run --loop does this hourly)",
    )
    cmp.add_argument("--max-groups", type=int, help="stop after this many")
    rep = sub.add_parser(
        "replay",
        help="rebuild realtime tables from offloaded hour tars into "
        "--data-dir (use an empty folder)",
    )
    src = rep.add_mutually_exclusive_group(required=True)
    src.add_argument("--bucket", help="S3 offload bucket (D-026)")
    src.add_argument(
        "--tars", type=Path, help="folder laid out like the bucket"
    )
    rep.add_argument(
        "--scratch",
        type=Path,
        help="folder for the hour files (default --data-dir); never the "
        "collector's data folder",
    )
    rep.add_argument(
        "--maintain-every",
        type=int,
        default=MAINTAIN_EVERY,
        help="compact and expire after this many hours (0: only at the "
        "end), I-20",
    )
    rep.add_argument(
        "--feed", action="append", help="default: every enabled feed"
    )
    rep.add_argument(
        "--exclude-feed",
        action="append",
        default=[],
        help="leave these out (e.g. static_gtfs, loaded on its own)",
    )
    ver = sub.add_parser(
        "verify", help="compare --data-dir's tables with --other's"
    )
    ver.add_argument("--other", type=Path, required=True)
    ver.add_argument("--feed", action="append", required=True)
    for p in (rep, ver):
        p.add_argument(
            "--start", required=True, help="UTC hour, 2026-11-17T21"
        )
        p.add_argument("--end", required=True, help="UTC hour, exclusive")
    args = parser.parse_args(argv)

    # Arrow's default mimalloc pool keeps freed memory, so peak use only
    # grows over a backlog; the system allocator measured ~25% lower.
    pa.set_memory_pool(pa.system_memory_pool())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # pyiceberg logs "Defaulting to PyArrow FileIO" once per table opened.
    logging.getLogger("pyiceberg").setLevel(logging.WARNING)
    catalog = open_catalog(args.data_dir)

    if args.cmd == "status":
        print(_status(catalog))
        return 0
    if args.cmd == "compact":
        from transit_lakehouse.worker.compact import compact

        with single_run(args.data_dir):
            print(compact(catalog, max_groups=args.max_groups).summary())
        return 0
    if args.cmd == "expire":
        removed = expire(catalog, parse_window(args.older_than))
        for name, n in removed.items():
            print(f"{name}: expired {n} snapshots")
        return 0

    feeds = list(load_feeds(args.feeds))
    if args.feed:
        feeds = [f for f in feeds if f.name in set(args.feed)]
    feeds = [
        f for f in feeds if f.name not in getattr(args, "exclude_feed", [])
    ]

    if args.cmd in ("replay", "verify"):
        return _replay_or_verify(args, catalog, feeds)

    kwargs = {"max_rows": args.max_rows, "max_files": args.max_files}
    with single_run(args.data_dir):
        if args.once:
            stats = run_once(args.data_dir, catalog, feeds, **kwargs)
            print(stats.summary())
            return 0
        stopping = False

        def _stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        run_loop(
            args.data_dir,
            catalog,
            feeds,
            args.loop,
            lambda: stopping,
            **kwargs,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
