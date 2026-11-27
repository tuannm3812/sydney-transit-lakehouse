"""Redpanda + Spark benchmark CLI (D-041, docs/13_spark_benchmark.md).

    python -m transit_lakehouse.bench topics
    python -m transit_lakehouse.bench produce --bucket B \\
        --start 2026-11-23T00 --end 2026-11-24T00 --speed 1
    python -m transit_lakehouse.bench stream --idle-stop 300
    python -m transit_lakehouse.bench report

``--data-dir`` is the benchmark's own folder (its lakehouse, checkpoint
and metrics), never the worker's. Needs no TfNSW key: it reads the
offloaded hours only.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from transit_lakehouse.collector.config import load_feeds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("BENCH_DATA_DIR", "data-bench")),
    )
    parser.add_argument(
        "--feeds",
        type=Path,
        default=Path(os.environ.get("FEEDS_CONFIG", "config/feeds.toml")),
    )
    parser.add_argument(
        "--bootstrap",
        default=os.environ.get("BENCH_BOOTSTRAP", "localhost:9092"),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    top = sub.add_parser("topics", help="create the raw.<kind> topics")
    top.add_argument("--partitions", type=int, default=4)
    pro = sub.add_parser("produce", help="replay raw hours into Redpanda")
    src = pro.add_mutually_exclusive_group(required=True)
    src.add_argument("--bucket", help="S3 offload bucket (D-026)")
    src.add_argument("--tars", type=Path, help="folder laid out like it")
    pro.add_argument("--start", required=True, help="UTC hour, 2026-11-23T00")
    pro.add_argument("--end", required=True, help="UTC hour, exclusive")
    pro.add_argument("--feed", action="append", help="limit to these")
    pro.add_argument(
        "--check",
        action="store_true",
        help="only list hour tars the window needs that are missing",
    )
    pro.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="1 = real time, 10 = ten times faster, 0 = flat out",
    )
    stm = sub.add_parser("stream", help="run the Spark job")
    stm.add_argument("--trigger", type=int, default=15, help="seconds")
    stm.add_argument("--max-offsets", type=int, default=400)
    stm.add_argument("--driver-memory", default="3g")
    stm.add_argument("--no-maintain", action="store_true")
    stm.add_argument(
        "--idle-stop",
        type=float,
        help="stop after this many seconds with no new messages",
    )
    rep = sub.add_parser("report", help="summarise the metrics")
    rep.add_argument(
        "--stats", type=Path, help="docker stats JSON lines, if sampled"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("pyiceberg").setLevel(logging.WARNING)
    logging.getLogger("py4j").setLevel(logging.WARNING)

    if args.cmd == "topics":
        from transit_lakehouse.bench.producer import create_topics

        made = create_topics(args.bootstrap, args.partitions)
        print(f"created {len(made)} topics" + (f": {made}" if made else ""))
        return 0
    if args.cmd == "report":
        from transit_lakehouse.bench.report import summarise

        print(summarise(args.data_dir / "bench" / "metrics", args.stats))
        return 0
    if args.cmd == "stream":
        from transit_lakehouse.bench.stream import StreamConfig, run

        cfg = StreamConfig(
            data_dir=args.data_dir,
            bootstrap=args.bootstrap,
            trigger_s=args.trigger,
            max_offsets=args.max_offsets,
            driver_memory=args.driver_memory,
            maintain=not args.no_maintain,
        )
        run(cfg, idle_stop_s=args.idle_stop)
        return 0

    from confluent_kafka import Producer

    from transit_lakehouse.bench.producer import produce, producer_config
    from transit_lakehouse.bench.source import (
        Missing,
        missing_hours,
        stream_hours,
    )
    from transit_lakehouse.worker.replay import LocalTars, S3Tars, parse_hour

    feeds = list(load_feeds(args.feeds))
    if args.feed:
        feeds = [f for f in feeds if f.name in set(args.feed)]
    if args.bucket:
        import boto3  # read-only keys or the instance role

        source = S3Tars(boto3.client("s3"), args.bucket)
    else:
        source = LocalTars(args.tars)
    start, end = parse_hour(args.start), parse_hour(args.end)
    if args.check:
        gone = missing_hours(source, feeds, start, end)
        for key in gone:
            print(f"missing {key}")
        print(f"{len(gone)} hour tars missing (window plus the seed hour)")
        return 1 if gone else 0
    missing = Missing([])
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.data_dir) as scratch:
        messages = stream_hours(
            source,
            feeds,
            start,
            end,
            Path(scratch),
            missing,
        )
        stats = produce(
            messages, Producer(producer_config(args.bootstrap)), args.speed
        )
    print(
        f"produced {stats.messages:,} messages ({stats.seeds} seeds, "
        f"{stats.bytes / 1e6:,.0f} MB) in {stats.seconds:,.0f} s; "
        f"worst lag behind pace {stats.behind_s:.1f} s"
    )
    for key in missing.keys:
        print(f"missing {key}")
    for err in stats.failed:
        print(f"FAILED {err}")
    return 1 if stats.failed else 0


if __name__ == "__main__":
    sys.exit(main())
