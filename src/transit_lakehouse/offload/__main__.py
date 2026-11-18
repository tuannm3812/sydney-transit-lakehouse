"""``python -m transit_lakehouse.offload upload|prune`` (D-026).

Bucket and region come from ``OFFLOAD_BUCKET`` and ``AWS_REGION``.
Credentials come from the EC2 instance role; no keys are stored.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from transit_lakehouse.checks.manifest import parse_window
from transit_lakehouse.offload import core


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["upload", "prune"])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "data")),
    )
    parser.add_argument("--bucket", default=os.environ.get("OFFLOAD_BUCKET"))
    parser.add_argument("--grace", default="15m")
    parser.add_argument(
        "--keep", default="3d", help="prune: local raw hours to keep"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.bucket:
        print("OFFLOAD_BUCKET is not set", file=sys.stderr)
        return 2

    import boto3  # only the offload job needs it

    s3 = boto3.client(
        "s3", region_name=os.environ.get("AWS_REGION", "ap-southeast-2")
    )
    try:  # fail early, and let --dry-run prove the role reaches S3
        s3.head_bucket(Bucket=args.bucket)
    except Exception as exc:  # noqa: BLE001 - report and stop
        print(
            f"cannot reach bucket {args.bucket}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(f"bucket {args.bucket} reachable")
    now = datetime.now(UTC)
    if args.command == "upload":
        res = core.upload(
            args.data_dir,
            s3,
            args.bucket,
            now,
            grace=parse_window(args.grace),
            dry_run=args.dry_run,
        )
    else:
        res = core.prune(
            args.data_dir,
            s3,
            args.bucket,
            now,
            keep=parse_window(args.keep),
            dry_run=args.dry_run,
        )
    print(
        f"{args.command}: {res.uploaded} done, {res.skipped} already done, "
        f"{res.failed} failed, {res.bytes:,} bytes"
    )
    return 1 if res.failed else 0


if __name__ == "__main__":
    sys.exit(main())
