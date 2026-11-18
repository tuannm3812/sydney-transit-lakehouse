"""Measure how much of each realtime snapshot actually changes.

Every poll lands a full snapshot of the network, but most of it repeats
the snapshot before. This tool decodes consecutive landed snapshots of a
feed and counts, row by row, what changed. The answer sizes the Phase 1
silver layer (store only changes) before we build it::

    python -m transit_lakehouse.checks profile --feed buses_tripupdates \
        --hour 2026-11-18T02

Rows are trip-update stop predictions keyed by (trip, start date, stop),
or vehicle positions keyed by vehicle. Read-only: it never writes to the
data directory.
"""

from __future__ import annotations

import argparse
import gzip
import os
import statistics
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

Row = tuple
Key = tuple


def rows_of(payload: bytes) -> dict[Key, Row]:
    """Flatten one FeedMessage into {key: value} rows.

    Entities that fail to decode on their own (TfNSW's string in
    TripDescriptor field 7, D-020) are skipped and counted by the caller
    through the entity total.
    """
    msg = gtfs_realtime_pb2.FeedMessage()
    try:
        msg.ParseFromString(payload)
    except DecodeError:
        return {}
    out: dict[Key, Row] = {}
    for ent in msg.entity:
        if ent.HasField("trip_update"):
            tu = ent.trip_update
            trip = (tu.trip.trip_id, tu.trip.start_date)
            for stu in tu.stop_time_update:
                stop = stu.stop_id or str(stu.stop_sequence)
                out[("tu", *trip, stop)] = (
                    stu.arrival.time,
                    stu.arrival.delay,
                    stu.departure.time,
                    stu.departure.delay,
                    stu.schedule_relationship,
                )
            out[("trip", *trip)] = (tu.trip.schedule_relationship,)
        if ent.HasField("vehicle"):
            v = ent.vehicle
            vid = v.vehicle.id or ent.id
            out[("vp", vid)] = (
                v.trip.trip_id,
                round(v.position.latitude, 6),
                round(v.position.longitude, 6),
                v.timestamp,
                v.occupancy_status,
            )
    return out


@dataclass
class Profile:
    snapshots: int = 0
    rows: list[int] | None = None
    changed: list[int] | None = None
    added: list[int] | None = None
    removed: list[int] | None = None
    stored_bytes: int = 0

    def __post_init__(self) -> None:
        self.rows, self.changed, self.added, self.removed = [], [], [], []

    @property
    def change_share(self) -> float:
        total = sum(self.rows[1:])
        moved = sum(self.changed) + sum(self.added) + sum(self.removed)
        return moved / total if total else 0.0


def profile(payloads: Iterable[bytes]) -> Profile:
    """Diff consecutive snapshots."""
    prof = Profile()
    prev: dict[Key, Row] | None = None
    for payload in payloads:
        cur = rows_of(payload)
        prof.snapshots += 1
        prof.rows.append(len(cur))
        if prev is not None:
            prof.changed.append(
                sum(1 for k, v in cur.items() if k in prev and prev[k] != v)
            )
            prof.added.append(sum(1 for k in cur if k not in prev))
            prof.removed.append(sum(1 for k in prev if k not in cur))
        prev = cur
    return prof


def landed_files(data_dir: Path, feed: str, hour: datetime) -> list[Path]:
    folder = (
        data_dir
        / "raw"
        / f"feed={feed}"
        / f"date={hour:%Y-%m-%d}"
        / f"hour={hour:%H}"
    )
    return sorted(folder.glob("*.pb*"))


def _read(path: Path) -> bytes:
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def report(feed: str, hour: datetime, prof: Profile) -> str:
    if prof.snapshots < 2:
        return f"{feed} {hour:%Y-%m-%d %H}:00 UTC: fewer than 2 snapshots."
    mean = statistics.fmean
    share = prof.change_share
    lines = [
        f"{feed}, {hour:%Y-%m-%d %H}:00 UTC, {prof.snapshots} snapshots, "
        f"{prof.stored_bytes / 1e6:,.1f} MB on disk",
        f"  rows per snapshot      {mean(prof.rows):>14,.0f}",
        f"  changed per snapshot   {mean(prof.changed):>14,.0f}",
        f"  added per snapshot     {mean(prof.added):>14,.0f}",
        f"  removed per snapshot   {mean(prof.removed):>14,.0f}",
        f"  share that moved       {share:>14.1%}",
        "  change-only reduction  "
        f"{(1 / share if share else float('inf')):>13,.1f}x",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "data")),
    )
    parser.add_argument("--feed", action="append", required=True)
    parser.add_argument(
        "--hour",
        required=True,
        help="UTC hour to profile, e.g. 2026-11-18T02",
    )
    args = parser.parse_args(argv)
    hour = datetime.strptime(args.hour, "%Y-%m-%dT%H")
    for feed in args.feed:
        files = landed_files(args.data_dir, feed, hour)
        prof = profile(_read(p) for p in files)
        prof.stored_bytes = sum(p.stat().st_size for p in files)
        print(report(feed, hour, prof))
    return 0


if __name__ == "__main__":
    sys.exit(main())
