"""Summarise a benchmark run from the stream's metrics files.

Latency per snapshot is ``committed_at − produced_at``: from the broker
taking the file (standing in for the collector landing it) to the last
table's Iceberg commit of its micro-batch. The light worker's figure is
fetch → ``ingested_at`` (Phase 1 item 4), so ``ingested_at −
produced_at`` is reported too. Optional ``docker stats`` samples
(scripts/bench/sample_stats.sh) add peak memory and mean CPU per
container.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    i = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return values[i]


def _secs(a: str, b: str) -> float:
    return (
        datetime.fromisoformat(a) - datetime.fromisoformat(b)
    ).total_seconds()


def _mem_mb(text: str) -> float:
    """``docker stats`` MemUsage, e.g. '1.2GiB / 15GiB' → MB used."""
    used = text.split("/")[0].strip()
    units = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "B": 1 / 2**20}
    for unit, scale in units.items():
        if used.endswith(unit):
            return float(used[: -len(unit)]) * scale * 1.048576
    raise ValueError(f"no memory figure in {text!r}")


def summarise(metrics: Path, stats: Path | None = None) -> str:
    snaps = _load(metrics / "snapshots.jsonl")
    batches = _load(metrics / "batches.jsonl")
    upkeep = _load(metrics / "maintenance.jsonl")
    if not snaps:
        return f"no snapshots recorded in {metrics}"
    lines = ["# Spark benchmark run", ""]
    first = min(s["produced_at"] for s in snaps)
    last = max(s["committed_at"] for s in snaps)
    wall = _secs(last, first)
    rows = defaultdict(int)
    for b in batches:
        for table, n in b["rows"].items():
            rows[table] += n
    total = sum(rows.values())
    lines += [
        f"- Window: first message {first}, last commit {last} "
        f"({wall / 3600:.2f} h)",
        f"- {len(snaps):,} snapshots in {len(batches):,} micro-batches; "
        f"{total:,} rows ({total / max(wall, 1):,.0f} rows/s overall)",
        "- Batch time p50 / p95 / max: "
        + " / ".join(
            f"{_pct([b['seconds'] for b in batches], q):.1f} s"
            for q in (0.5, 0.95, 1.0)
        ),
    ]
    if upkeep:
        secs = [m["seconds"] for m in upkeep]
        lines.append(
            f"- Hourly compaction and expiry: {len(upkeep)} runs, "
            f"mean {statistics.mean(secs):.1f} s, max {max(secs):.1f} s"
        )
    lines += [
        "",
        "| table | rows |",
        "|---|---:|",
        *(f"| {t} | {n:,} |" for t, n in sorted(rows.items())),
        "",
        "| feed | snapshots | p50 commit | p95 commit | max commit "
        "| p95 ingested_at |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    by_feed = defaultdict(list)
    for s in snaps:
        by_feed[s["feed"]].append(s)
    for feed, items in sorted(by_feed.items()):
        commit = [_secs(s["committed_at"], s["produced_at"]) for s in items]
        ingest = [_secs(s["ingested_at"], s["produced_at"]) for s in items]
        lines.append(
            f"| {feed} | {len(items):,} | {_pct(commit, 0.5):.1f} s "
            f"| {_pct(commit, 0.95):.1f} s | {max(commit):.1f} s "
            f"| {_pct(ingest, 0.95):.1f} s |"
        )
    if stats is not None and stats.exists():
        lines += _stats_table(stats)
    return "\n".join(lines) + "\n"


def _stats_table(path: Path) -> list[str]:
    mem = defaultdict(list)
    cpu = defaultdict(list)
    for s in _load(path):
        try:  # docker prints "--" while a container starts or stops
            used = _mem_mb(s["MemUsage"])
            share = float(s["CPUPerc"].rstrip("%"))
        except (KeyError, ValueError):
            continue
        mem[s["Name"]].append(used)
        cpu[s["Name"]].append(share)
    out = [
        "",
        "| container | samples | peak memory | mean CPU | peak CPU |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in sorted(mem):
        out.append(
            f"| {name} | {len(mem[name])} | {max(mem[name]):,.0f} MB "
            f"| {statistics.mean(cpu[name]):.0f}% "
            f"| {max(cpu[name]):.0f}% |"
        )
    return out
