"""Report the observed API quota behaviour and snapshot sizes.

TfNSW does not let a developer read their own quota usage (staff reply
on the Open Data forum, Oct 2025), so the "real quota" is established
empirically: how many requests we made per day, and whether any of them
were rejected for rate or quota. Run it after 24 hours::

    python -m transit_lakehouse.checks.quota_report --window 24h \
        --out docs/reports/quota-24h.md

``--probe`` makes one live request per enabled feed and prints every
response header, in case the gateway does expose limit headers.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from transit_lakehouse.checks.manifest import (
    Poll,
    parse_window,
    percentile,
    read_polls,
)
from transit_lakehouse.collector.config import (
    DEFAULT_DAILY_QUOTA,
    load_feeds,
)

PROBE_SPACING_S = 1.5
LIMIT_REASONS = ("rate_limited", "quota_exhausted", "http_503", "http_429")


def _mb(n: float) -> str:
    """Human-readable size: kB below 1 MB, else MB."""
    if n < 1e6:
        return f"{n / 1e3:,.1f} kB"
    return f"{n / 1e6:,.2f} MB"


def _reason(p: Poll) -> str:
    if p.ok:
        return p.outcome
    return (p.error or p.outcome).split(":", 1)[0]


def build_report(
    polls: dict[str, list[Poll]],
    start: datetime,
    end: datetime,
    daily_quota: int,
) -> str:
    """Render the markdown report."""
    # Rate over the time we actually have data for, not the requested
    # window: a collector started 4 minutes ago is not running at 4/60
    # of its real rate.
    firsts = [p[0].fetched_at for p in polls.values() if p]
    observed_start = max(start, min(firsts)) if firsts else start
    days = max((end - observed_start).total_seconds() / 86400, 1e-9)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# API quota and snapshot size report",
        "",
        f"Window: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC "
        f"({days * 24:.1f} h of data). Generated {generated} UTC.",
        "",
        "## Requests and limit signals",
        "",
        "| feed | requests | req/day | ok | unchanged | limit hits "
        "| other errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    total = 0
    limit_hits_total = 0
    all_reasons: Counter[str] = Counter()
    header_seen: dict[str, set[str]] = {}
    for feed, items in sorted(polls.items()):
        reasons = Counter(_reason(p) for p in items)
        all_reasons.update(reasons)
        limit_hits = sum(reasons[r] for r in LIMIT_REASONS)
        other = sum(1 for p in items if not p.ok) - limit_hits
        total += len(items)
        limit_hits_total += limit_hits
        lines.append(
            f"| {feed} | {len(items):,} | {len(items) / days:,.0f} "
            f"| {reasons['new']:,} | {reasons['unchanged']:,} "
            f"| {limit_hits} | {other} |"
        )
        for p in items:
            for k, v in p.headers.items():
                header_seen.setdefault(k, set()).add(v)
    per_day = total / days
    lines += [
        "",
        f"Total: **{total:,} requests ({per_day:,.0f}/day)**, "
        f"{per_day / daily_quota:.0%} of the assumed "
        f"{daily_quota:,}/day quota.",
        "",
    ]
    if limit_hits_total == 0 and total:
        lines.append(
            f"No rate or quota rejections across {total:,} requests. "
            "Over a full 24 h window this confirms the real quota is at "
            f"least {per_day:,.0f} requests/day."
        )
    elif total:
        lines.append(
            f"**{limit_hits_total} limit rejections.** The quota or rate "
            "limit is binding at this cadence; see the reasons below."
        )
    breakdown = ", ".join(f"{k} {v:,}" for k, v in all_reasons.most_common())
    lines += ["", f"Outcome breakdown: {breakdown or 'none'}", ""]

    lines += [
        "## Snapshot sizes (successful polls)",
        "",
        "| feed | p50 | p95 | max | received/day | on disk/day "
        "| on disk/30 days |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    landed_total = 0.0
    stored_total = 0.0
    for feed, items in sorted(polls.items()):
        sizes = [float(p.size_bytes) for p in items if p.ok]
        landed = sum(p.size_bytes for p in items if p.outcome == "new")
        # Polls landed before gzip (D-017) have no stored_bytes, so scale
        # received bytes by the ratio seen on polls that do.
        gz = [p for p in items if p.outcome == "new" and p.stored_bytes]
        ratio = (
            sum(p.stored_bytes for p in gz) / sum(p.size_bytes for p in gz)
            if gz and sum(p.size_bytes for p in gz)
            else 1.0
        )
        stored = landed * ratio
        landed_total += landed
        stored_total += stored
        if not sizes:
            lines.append(f"| {feed} | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| {feed} | {_mb(percentile(sizes, 0.5) or 0)} "
            f"| {_mb(percentile(sizes, 0.95) or 0)} | {_mb(max(sizes))} "
            f"| {_mb(landed / days)} | {_mb(stored / days)} "
            f"| {_mb(stored / days * 30)} |"
        )
    lines += [
        "",
        f"Received {_mb(landed_total / days)}/day. On disk after gzip: "
        f"**{_mb(stored_total / days)}/day, "
        f"{stored_total / days * 30 / 1e9:,.1f} GB per 30 days** "
        "before compaction into Iceberg.",
        "",
        "## Response headers observed",
        "",
    ]
    if header_seen:
        for k, values in sorted(header_seen.items()):
            sample = ", ".join(sorted(values)[:5])
            lines.append(f"- `{k}`: {sample}")
    else:
        lines.append(
            "None of the kept headers (limit, quota, retry, x-*) were "
            "returned. Run with `--probe` to see every header once."
        )
    return "\n".join(lines) + "\n"


def probe(feeds_path: Path, only: list[str] | None = None) -> int:
    """One live request per feed, printing every header."""
    import httpx  # imported here so the report runs without a key

    key = os.environ.get("TFNSW_API_KEY", "").strip()
    if not key:
        print("TFNSW_API_KEY is not set", file=sys.stderr)
        return 2
    with httpx.Client(
        timeout=10, headers={"Authorization": f"apikey {key}"}
    ) as client:
        feeds = [
            f for f in load_feeds(feeds_path) if not only or f.name in only
        ]
        for i, feed in enumerate(feeds):
            if i:
                time.sleep(PROBE_SPACING_S)  # back-to-back gets 429 (D-013)
            resp = client.get(feed.url, timeout=feed.timeout_s)
            print(
                f"## {feed.name} -> {resp.status_code}, "
                f"{len(resp.content):,} bytes"
            )
            for k, v in resp.headers.items():
                print(f"  {k}: {v}")
            if resp.status_code != 200:
                print(f"  body: {resp.text[:300]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        # Same env var the collector writes to, so containers agree.
        default=Path(os.environ.get("DATA_DIR", "data")),
    )
    parser.add_argument(
        "--feeds",
        type=Path,
        default=Path(os.environ.get("FEEDS_CONFIG", "config/feeds.toml")),
    )
    parser.add_argument("--window", default="24h")
    parser.add_argument(
        "--daily-quota",
        type=int,
        default=int(os.environ.get("DAILY_QUOTA", DEFAULT_DAILY_QUOTA)),
    )
    parser.add_argument("--out", type=Path, help="Write markdown here.")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument(
        "--only",
        action="append",
        metavar="FEED",
        help="With --probe: probe just this feed (repeatable).",
    )
    args = parser.parse_args(argv)
    if args.probe:
        return probe(args.feeds, args.only)

    end = datetime.now(UTC)
    start = end - parse_window(args.window)
    report = build_report(
        read_polls(args.data_dir, start, end), start, end, args.daily_quota
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
