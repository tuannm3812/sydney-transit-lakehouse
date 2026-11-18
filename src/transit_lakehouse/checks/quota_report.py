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
import json
import os
import shutil
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
from transit_lakehouse.checks.report_html import render_html
from transit_lakehouse.collector.config import (
    DEFAULT_DAILY_QUOTA,
    load_feeds,
)

PROBE_SPACING_S = 1.5
# Per-request IDs: unique on every response, so listing them is noise.
NOISY_HEADERS = frozenset({"x-amz-cf-id", "x-varnish", "x-request-id"})
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


def compute_stats(
    polls: dict[str, list[Poll]],
    start: datetime,
    end: datetime,
    daily_quota: int,
) -> dict:
    """Aggregate the manifest into the numbers every report format uses."""
    # Rate over the time we actually have data for, not the requested
    # window: a collector started 4 minutes ago is not running at 4/60
    # of its real rate.
    firsts = [p[0].fetched_at for p in polls.values() if p]
    observed_start = max(start, min(firsts)) if firsts else start
    days = max((end - observed_start).total_seconds() / 86400, 1e-9)
    feeds = []
    all_reasons: Counter[str] = Counter()
    headers: dict[str, set[str]] = {}
    for feed, items in sorted(polls.items()):
        reasons = Counter(_reason(p) for p in items)
        all_reasons.update(reasons)
        limit_hits = sum(reasons[r] for r in LIMIT_REASONS)
        sizes = [float(p.size_bytes) for p in items if p.ok]
        landed = sum(p.size_bytes for p in items if p.outcome == "new")
        # Polls landed before gzip (D-017) have no stored_bytes, so scale
        # received bytes by the ratio seen on polls that do.
        gz = [p for p in items if p.outcome == "new" and p.stored_bytes]
        gz_in = sum(p.size_bytes for p in gz)
        ratio = sum(p.stored_bytes for p in gz) / gz_in if gz_in else 1.0
        for p in items:
            for k, v in p.headers.items():
                if k not in NOISY_HEADERS:
                    headers.setdefault(k, set()).add(v)
        feeds.append(
            {
                "feed": feed,
                "requests": len(items),
                "per_day": len(items) / days,
                "new": reasons["new"],
                "unchanged": reasons["unchanged"],
                "limit_hits": limit_hits,
                "other_errors": sum(1 for p in items if not p.ok) - limit_hits,
                "size_p50": percentile(sizes, 0.5) if sizes else None,
                "size_p95": percentile(sizes, 0.95) if sizes else None,
                "size_max": max(sizes) if sizes else None,
                "received_per_day": landed / days,
                "stored_per_day": landed * ratio / days,
            }
        )
    total = sum(f["requests"] for f in feeds)
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "observed_hours": days * 24,
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "daily_quota": daily_quota,
        "total_requests": total,
        "requests_per_day": total / days,
        "limit_hits": sum(f["limit_hits"] for f in feeds),
        "outcomes": dict(all_reasons.most_common()),
        "received_per_day": sum(f["received_per_day"] for f in feeds),
        "stored_per_day": sum(f["stored_per_day"] for f in feeds),
        "feeds": feeds,
        "headers": {k: sorted(v)[:5] for k, v in sorted(headers.items())},
        "disk": None,
    }


def disk_usage(path: Path) -> dict | None:
    """Total, used and free bytes of the volume holding ``path``."""
    try:
        du = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": du.total, "used": du.used, "free": du.free}


def build_report(
    polls: dict[str, list[Poll]],
    start: datetime,
    end: datetime,
    daily_quota: int,
) -> str:
    """Render the markdown report."""
    return render_markdown(compute_stats(polls, start, end, daily_quota))


def render_markdown(st: dict) -> str:
    start = datetime.fromisoformat(st["window_start"])
    end = datetime.fromisoformat(st["window_end"])
    generated = datetime.fromisoformat(st["generated"])
    lines = [
        "# API quota and snapshot size report",
        "",
        f"Window: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC "
        f"({st['observed_hours']:.1f} h of data). "
        f"Generated {generated:%Y-%m-%d %H:%M} UTC.",
        "",
        "## Requests and limit signals",
        "",
        "| feed | requests | req/day | ok | unchanged | limit hits "
        "| other errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for f in st["feeds"]:
        lines.append(
            f"| {f['feed']} | {f['requests']:,} | {f['per_day']:,.0f} "
            f"| {f['new']:,} | {f['unchanged']:,} "
            f"| {f['limit_hits']} | {f['other_errors']} |"
        )
    total, per_day = st["total_requests"], st["requests_per_day"]
    lines += [
        "",
        f"Total: **{total:,} requests ({per_day:,.0f}/day)**, "
        f"{per_day / st['daily_quota']:.0%} of the assumed "
        f"{st['daily_quota']:,}/day quota.",
        "",
    ]
    if st["limit_hits"] == 0 and total:
        lines.append(
            f"No rate or quota rejections across {total:,} requests. "
            "Over a full 24 h window this confirms the real quota is at "
            f"least {per_day:,.0f} requests/day."
        )
    elif total:
        lines.append(
            f"**{st['limit_hits']} limit rejections.** The quota or rate "
            "limit is binding at this cadence; see the reasons below."
        )
    breakdown = ", ".join(f"{k} {v:,}" for k, v in st["outcomes"].items())
    lines += ["", f"Outcome breakdown: {breakdown or 'none'}", ""]
    lines += [
        "## Snapshot sizes (successful polls)",
        "",
        "| feed | p50 | p95 | max | received/day | on disk/day "
        "| on disk/30 days |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for f in st["feeds"]:
        if f["size_p50"] is None:
            lines.append(f"| {f['feed']} | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| {f['feed']} | {_mb(f['size_p50'])} | {_mb(f['size_p95'])} "
            f"| {_mb(f['size_max'])} | {_mb(f['received_per_day'])} "
            f"| {_mb(f['stored_per_day'])} "
            f"| {_mb(f['stored_per_day'] * 30)} |"
        )
    stored = st["stored_per_day"]
    lines += [
        "",
        f"Received {_mb(st['received_per_day'])}/day. On disk after gzip: "
        f"**{_mb(stored)}/day, {stored * 30 / 1e9:,.1f} GB per 30 days** "
        "before compaction into Iceberg.",
        "",
        "## Response headers observed",
        "",
    ]
    if st["headers"]:
        for k, values in st["headers"].items():
            lines.append(f"- `{k}`: {', '.join(values)}")
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
    parser.add_argument("--json", type=Path, help="Write the stats here.")
    parser.add_argument(
        "--html", type=Path, help="Write the styled HTML report here."
    )
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
    stats = compute_stats(
        read_polls(args.data_dir, start, end), start, end, args.daily_quota
    )
    stats["disk"] = disk_usage(args.data_dir)
    report = render_markdown(stats)
    outputs = {
        args.out: report,
        args.json: json.dumps(stats, indent=2),
        args.html: render_html(stats) if args.html else None,
    }
    for path, text in outputs.items():
        if path and text is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
