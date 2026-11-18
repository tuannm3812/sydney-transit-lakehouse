"""Render the quota report as one self-contained, styled HTML page.

The look follows the project deck (UTS palette A, Tracker chapter in
ochre, transport modes in TfNSW-style colours, everything else in
Viridis or neutral). Charts are inline SVG, so the page has no
dependencies and renders the same on the server, in a browser or as an
artifact. Every chart has a table view below it.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

# TfNSW-style mode colours, as in the deck's scripts/colours.py.
MODE_COLOURS = {
    "Bus": "#00B5EF",
    "Trains": "#F6891F",
    "Metro": "#00959A",
    "Light rail": "#EE343F",
    "Ferries": "#5AB031",
    "Network-wide": "var(--neutral)",
}
MODE_PREFIX = (
    ("buses", "Bus"),
    ("sydneytrains", "Trains"),
    ("nswtrains", "Trains"),
    ("metro", "Metro"),
    ("lightrail", "Light rail"),
    ("ferries", "Ferries"),
)
KIND_SUFFIX = (
    ("vehiclepos", "positions"),
    ("tripupdates", "trip updates"),
)
SPECIAL = {
    "service_alerts": ("Network-wide", "alerts"),
    "static_gtfs": ("Network-wide", "timetable"),
}


def describe(feed: str) -> tuple[str, str]:
    """Return (mode, what) for a feed name, e.g. ("Bus", "positions")."""
    if feed in SPECIAL:
        return SPECIAL[feed]
    mode = next((m for p, m in MODE_PREFIX if feed.startswith(p)), "Other")
    what = next((w for s, w in KIND_SUFFIX if feed.endswith(s)), feed)
    return mode, what


def _mode_rank(feed: str) -> int:
    modes = list(MODE_COLOURS)
    mode = describe(feed)[0]
    return modes.index(mode) if mode in modes else len(modes)


def _colour(mode: str) -> str:
    return MODE_COLOURS.get(mode, "var(--neutral)")


def _size(n: float | None) -> str:
    if n is None:
        return "–"
    if n >= 1e9:
        return f"{n / 1e9:,.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:,.1f} MB"
    return f"{n / 1e3:,.0f} kB"


def _fonts(font_dir: Path | None) -> str:
    """Inline Google Sans as data URIs when the TTFs are available."""
    if not font_dir or not font_dir.is_dir():
        return ""
    faces = []
    for weight, name in ((400, "Regular"), (700, "Bold")):
        path = font_dir / f"GoogleSans-{name}.ttf"
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode()
            faces.append(
                "@font-face{font-family:'Google Sans';"
                f"font-weight:{weight};font-display:swap;"
                f"src:url(data:font/ttf;base64,{data}) format('truetype')}}"
            )
    return "".join(faces)


CSS = """
/* Layout: one reading column; summary tiles, then one chart per
   question (quota, volume, runway), each followed by its table. */
:root{
  --ground:#FFFFFF; --card:#F4F5F6; --ink:#1B2430; --body:#5A6370;
  --muted:#6B7480; --hair:#D8DEE3; --rule:#89949D;
  --accent:#896521; --accent-fill:#F6F1E5; --marker:#B8452E;
  --neutral:#9AA3AD; --good:#2E6B66;
  --sans:'Google Sans','Segoe UI',system-ui,-apple-system,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#12171E; --card:#1B222B; --ink:#E8ECF0; --body:#B3BBC4;
  --muted:#8C96A1; --hair:#2C3540; --rule:#5D6873;
  --accent:#D9B062; --accent-fill:#2A2416; --marker:#E07A63;
  --neutral:#7D8792; --good:#6FB8B0; color-scheme:dark}}
:root[data-theme="dark"]{
  --ground:#12171E; --card:#1B222B; --ink:#E8ECF0; --body:#B3BBC4;
  --muted:#8C96A1; --hair:#2C3540; --rule:#5D6873;
  --accent:#D9B062; --accent-fill:#2A2416; --marker:#E07A63;
  --neutral:#7D8792; --good:#6FB8B0; color-scheme:dark}
body{background:var(--ground);color:var(--body);font-family:var(--sans);
  font-size:15px;line-height:1.55;
  padding-left:max(16px,env(safe-area-inset-left,0px));
  padding-right:max(16px,env(safe-area-inset-right,0px));
  padding-block:32px 48px}
main{max-width:920px;margin:0 auto;display:grid;gap:40px}
h1,h2,h3{color:var(--ink);text-wrap:balance;margin:0;line-height:1.2}
h1{font-size:30px;font-weight:700}
h2{font-size:20px;font-weight:700}
p{margin:0;max-width:68ch}
.eyebrow{font-size:12px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--accent);font-weight:700}
header{display:grid;gap:10px;border-bottom:2px solid var(--accent);
  padding-bottom:20px}
.meta{font-size:13px;color:var(--muted)}
.preview{display:inline-block;background:var(--accent-fill);
  color:var(--accent);font-size:12px;font-weight:700;padding:2px 8px;
  border-radius:4px;margin-left:6px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:12px}
.tile{background:var(--card);border-radius:6px;padding:16px;display:grid;
  gap:4px;align-content:start}
.tile .label{font-size:12px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted)}
.tile .value{font-size:30px;font-weight:700;color:var(--ink);
  font-variant-numeric:tabular-nums;line-height:1.1}
.tile .note{font-size:13px}
.tile.good .value{color:var(--good)}
section{display:grid;gap:14px;min-width:0}
.takeaway{background:var(--accent-fill);border-left:3px solid var(--accent);
  padding:10px 14px;color:var(--ink);max-width:none}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:12px;height:12px;border-radius:3px;display:inline-block}
.chart{overflow-x:auto}
.chart svg{display:block;width:100%;height:auto;min-width:640px}
svg text{fill:var(--body);font-family:var(--sans)}
svg .ink{fill:var(--ink);font-weight:700}
svg .grid{stroke:var(--hair);stroke-width:1}
svg .axis{stroke:var(--rule);stroke-width:1}
svg .mark:hover{opacity:.8}
details{background:var(--card);border-radius:6px;padding:10px 14px}
summary{cursor:pointer;color:var(--ink);font-weight:700;font-size:14px}
summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.table{overflow-x:auto;margin-top:10px}
table{border-collapse:collapse;font-size:13px;width:100%;
  font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--hair);
  text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--ink);font-weight:700}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;
  margin-right:6px}
footer{font-size:12px;color:var(--muted);border-top:1px solid var(--hair);
  padding-top:12px}
code{font-size:12px}
"""


def _legend(modes: list[str]) -> str:
    items = "".join(
        f'<span><i style="background:{_colour(m)}"></i>{escape(m)}</span>'
        for m in modes
    )
    return f'<div class="legend" aria-hidden="true">{items}</div>'


def _quota_bar(st: dict) -> str:
    """One stacked bar of requests/day, against the daily quota."""
    quota = st["daily_quota"]
    w, x0, bar_y, bar_h = 880, 10, 34, 34
    scale = (w - 2 * x0) / quota
    parts, x = [], float(x0)
    # Group segments by mode so each mode reads as one block.
    order = sorted(
        st["feeds"], key=lambda f: (_mode_rank(f["feed"]), -f["per_day"])
    )
    for f in order:
        mode, what = describe(f["feed"])
        seg = f["per_day"] * scale
        tip = f"{mode} {what}: {f['per_day']:,.0f} requests/day"
        parts.append(
            f'<rect class="mark" x="{x:.1f}" y="{bar_y}" '
            f'width="{max(seg - 2, 1):.1f}" height="{bar_h}" rx="3" '
            f'fill="{_colour(mode)}"><title>{escape(tip)}</title></rect>'
        )
        x += seg
    used = st["requests_per_day"]
    budget_x = x0 + 0.8 * quota * scale
    end_x = x0 + quota * scale
    ticks = "".join(
        f'<line class="grid" x1="{x0 + v * scale:.1f}" y1="{bar_y - 6}" '
        f'x2="{x0 + v * scale:.1f}" y2="{bar_y + bar_h + 6}"/>'
        f'<text x="{x0 + v * scale:.1f}" y="{bar_y + bar_h + 22}" '
        f'font-size="12" text-anchor="middle">{v // 1000}k</text>'
        for v in range(0, quota + 1, 10_000)
    )
    return (
        f'<svg viewBox="0 0 {w} 100" role="img" aria-label="Requests per '
        f'day: {used:,.0f} of the {quota:,} quota">'
        f"{ticks}"
        f'<rect x="{x0}" y="{bar_y}" width="{end_x - x0:.1f}" '
        f'height="{bar_h}" rx="3" fill="var(--card)"/>'
        f"{''.join(parts)}"
        f'<line x1="{budget_x:.1f}" y1="{bar_y - 10}" x2="{budget_x:.1f}" '
        f'y2="{bar_y + bar_h + 4}" stroke="var(--marker)" stroke-width="2"/>'
        f'<text x="{budget_x - 6:.1f}" y="{bar_y - 14}" font-size="12" '
        f'text-anchor="end">Collector budget, 80%</text>'
        f'<text class="ink" x="{x:.1f}" y="{bar_y - 14}" font-size="13" '
        f'text-anchor="middle">{used:,.0f}/day</text>'
        f'<text x="{end_x:.1f}" y="{bar_y - 14}" font-size="12" '
        f'text-anchor="end">Quota {quota:,}</text>'
        "</svg>"
    )


def _volume_bars(st: dict) -> str:
    """On-disk bytes per day, one horizontal bar per feed."""
    rows = sorted(st["feeds"], key=lambda f: -f["stored_per_day"])
    top = max((f["stored_per_day"] for f in rows), default=0) or 1
    label_w, w, row_h = 210, 880, 30
    bar_max = w - label_w - 90
    h = row_h * len(rows) + 10
    out = []
    for i, f in enumerate(rows):
        mode, what = describe(f["feed"])
        y = 5 + i * row_h
        bw = max(f["stored_per_day"] / top * bar_max, 2)
        tip = (
            f"{mode} {what}: {_size(f['stored_per_day'])}/day on disk, "
            f"{_size(f['received_per_day'])}/day received"
        )
        out.append(
            f'<text x="{label_w - 12}" y="{y + 19}" font-size="13" '
            f'text-anchor="end">{escape(mode)} {escape(what)}</text>'
            f'<rect class="mark" x="{label_w}" y="{y + 5}" width="{bw:.1f}" '
            f'height="{row_h - 10}" rx="3" fill="{_colour(mode)}">'
            f"<title>{escape(tip)}</title></rect>"
            f'<text class="ink" x="{label_w + bw + 8:.1f}" y="{y + 19}" '
            f'font-size="13">{_size(f["stored_per_day"])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="On-disk '
        f'volume per day by feed">'
        f'<line class="axis" x1="{label_w}" y1="0" x2="{label_w}" '
        f'y2="{h}"/>{"".join(out)}</svg>'
    )


def _runway(st: dict) -> str | None:
    """Projected disk use from now until the volume is full."""
    disk, per_day = st.get("disk"), st["stored_per_day"]
    if not disk or per_day <= 0:
        return None
    total, used = disk["total"], disk["used"]
    days_left = disk["free"] / per_day
    horizon = max(7, int(days_left * 1.15) + 1)
    w, h, left, r, t, b = 880, 240, 56, 20, 20, 36
    sx = (w - left - r) / horizon
    sy = (h - t - b) / total

    def px(day: float, gb: float) -> tuple[float, float]:
        return left + day * sx, h - b - gb * sy

    x_now, y_now = px(0, used)
    x_full, y_full = px(days_left, total)
    grid = []
    step = 20e9 if total > 60e9 else 10e9
    v = 0.0
    while v <= total:
        _, y = px(0, v)
        grid.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{w - r}" '
            f'y2="{y:.1f}"/><text x="{left - 8}" y="{y + 4:.1f}" '
            f'font-size="12" text-anchor="end">{v / 1e9:.0f} GB</text>'
        )
        v += step
    gen = datetime.fromisoformat(st["generated"])
    xt = []
    for d in range(0, horizon + 1, 7 if horizon > 21 else 2):
        x, _ = px(d, 0)
        day = gen + timedelta(days=d)
        xt.append(
            f'<text x="{x:.1f}" y="{h - b + 20}" font-size="12" '
            f'text-anchor="middle">{day:%-d %b}</text>'
        )
    full_day = gen + timedelta(days=days_left)
    _, y_cap = px(0, total)
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Disk fills '
        f'in about {days_left:.0f} days, around {full_day:%-d %b}">'
        f"{''.join(grid)}{''.join(xt)}"
        f'<line x1="{left}" y1="{y_cap:.1f}" x2="{w - r}" y2="{y_cap:.1f}" '
        f'stroke="var(--marker)" stroke-width="2" stroke-dasharray="5 4"/>'
        f'<text x="{left + 6}" y="{y_cap - 8:.1f}" font-size="12">'
        f"Volume size {_size(total)}</text>"
        f'<polygon points="{x_now:.1f},{h - b} {x_now:.1f},{y_now:.1f} '
        f'{x_full:.1f},{y_full:.1f} {x_full:.1f},{h - b}" '
        f'fill="var(--accent)" fill-opacity=".14"/>'
        f'<line x1="{x_now:.1f}" y1="{y_now:.1f}" x2="{x_full:.1f}" '
        f'y2="{y_full:.1f}" stroke="var(--accent)" stroke-width="2"/>'
        f'<circle cx="{x_now:.1f}" cy="{y_now:.1f}" r="5" '
        f'fill="var(--accent)" stroke="var(--ground)" stroke-width="2">'
        f"<title>Now: {_size(used)} used</title></circle>"
        f'<circle cx="{x_full:.1f}" cy="{y_full:.1f}" r="5" '
        f'fill="var(--marker)" stroke="var(--ground)" stroke-width="2">'
        f"<title>Full around {full_day:%-d %b}</title></circle>"
        f'<text class="ink" x="{x_now + 12:.1f}" y="{y_now + 22:.1f}" '
        f'font-size="13">Now, {_size(used)} used</text>'
        f'<text class="ink" x="{x_full - 12:.1f}" y="{y_cap - 8:.1f}" '
        f'font-size="13" text-anchor="end">Full around '
        f"{full_day:%-d %b}</text>"
        "</svg>"
    )


def _requests_table(st: dict) -> str:
    rows = "".join(
        "<tr>"
        f'<td><span class="dot" style="background:'
        f'{_colour(describe(f["feed"])[0])}"></span>'
        f"{escape(' '.join(describe(f['feed'])))}</td>"
        f"<td>{f['requests']:,}</td><td>{f['per_day']:,.0f}</td>"
        f"<td>{f['new']:,}</td><td>{f['unchanged']:,}</td>"
        f"<td>{f['limit_hits']}</td><td>{f['other_errors']}</td></tr>"
        for f in sorted(st["feeds"], key=lambda f: -f["per_day"])
    )
    return (
        '<div class="table"><table><thead><tr><th>Feed</th><th>Requests'
        "</th><th>Per day</th><th>New</th><th>Unchanged</th><th>Limit hits"
        "</th><th>Other errors</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _size_table(st: dict) -> str:
    rows = "".join(
        "<tr>"
        f'<td><span class="dot" style="background:'
        f'{_colour(describe(f["feed"])[0])}"></span>'
        f"{escape(' '.join(describe(f['feed'])))}</td>"
        f"<td>{_size(f['size_p50'])}</td><td>{_size(f['size_p95'])}</td>"
        f"<td>{_size(f['size_max'])}</td>"
        f"<td>{_size(f['received_per_day'])}</td>"
        f"<td>{_size(f['stored_per_day'])}</td>"
        f"<td>{_size(f['stored_per_day'] * 30)}</td></tr>"
        for f in sorted(st["feeds"], key=lambda f: -f["stored_per_day"])
    )
    return (
        '<div class="table"><table><thead><tr><th>Feed</th><th>p50</th>'
        "<th>p95</th><th>Max</th><th>Received/day</th><th>On disk/day</th>"
        "<th>On disk/30 days</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def render_html(
    st: dict,
    font_dir: Path | None = None,
    preview_note: str | None = None,
) -> str:
    """Return the full report page. ``st`` is quota_report.compute_stats."""
    font_dir = font_dir or (
        Path(os.environ["REPORT_FONT_DIR"])
        if os.environ.get("REPORT_FONT_DIR")
        else None
    )
    start = datetime.fromisoformat(st["window_start"])
    end = datetime.fromisoformat(st["window_end"])
    hours = st["observed_hours"]
    quota, per_day = st["daily_quota"], st["requests_per_day"]
    share = per_day / quota if quota else 0
    stored = st["stored_per_day"]
    present = {describe(f["feed"])[0] for f in st["feeds"]}
    modes = [m for m in MODE_COLOURS if m in present]
    top = max(st["feeds"], key=lambda f: f["stored_per_day"], default=None)
    top_share = top["stored_per_day"] / stored if top and stored else 0
    disk = st.get("disk")
    days_left = disk["free"] / stored if disk and stored else None

    hits = st["limit_hits"]
    tiles = [
        (
            "Quota used",
            f"{share:.0%}",
            f"{per_day:,.0f} of {quota:,} requests a day",
            "",
        ),
        (
            "Limit rejections",
            f"{hits:,}",
            f"across {st['total_requests']:,} requests",
            "good" if hits == 0 else "",
        ),
        (
            "On disk per day",
            _size(stored),
            f"{stored * 30 / 1e9:,.0f} GB per 30 days, gzipped",
            "",
        ),
    ]
    if days_left is not None:
        full = datetime.fromisoformat(st["generated"]) + timedelta(
            days=days_left
        )
        tiles.append(
            (
                "Disk runway",
                f"{days_left:.0f} days",
                f"{_size(disk['free'])} free, full around {full:%-d %b}",
                "",
            )
        )
    tiles_html = "".join(
        f'<div class="tile {cls}"><span class="label">{escape(lbl)}</span>'
        f'<span class="value">{escape(val)}</span>'
        f'<span class="note">{escape(note)}</span></div>'
        for lbl, val, note, cls in tiles
    )
    if hits == 0 and st["total_requests"]:
        quota_take = (
            f"No rate or quota rejections in {hours:.1f} hours. The real "
            f"quota is at least {per_day:,.0f} requests a day, leaving "
            f"{max(0.8 * quota - per_day, 0):,.0f} a day of headroom under "
            "the collector's 80% budget."
        )
    else:
        quota_take = (
            f"{hits:,} limit rejections. The quota or burst limit is "
            "binding at this cadence."
        )
    runway = _runway(st)
    runway_section = (
        "<section><h2>When does the disk fill?</h2>"
        f'<p class="takeaway">At {_size(stored)} a day the volume fills in '
        f"about {days_left:.0f} days. Raw snapshots must move to object "
        "storage or be compacted into Iceberg before then.</p>"
        f'<div class="chart">{runway}</div></section>'
        if runway and days_left is not None
        else ""
    )
    headers = "".join(
        f"<li><code>{escape(k)}</code>: {escape(', '.join(v))}</li>"
        for k, v in st["headers"].items()
    )
    note = (
        f'<span class="preview">{escape(preview_note)}</span>'
        if preview_note
        else ""
    )
    return f"""<title>Quota and Volume Report</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>{_fonts(font_dir)}{CSS}</style>
<main>
<header>
<span class="eyebrow">Tracker · Phase 0, collect first</span>
<h1>API quota and data volume</h1>
<p class="meta">Window {start:%d %b %H:%M} to {end:%d %b %H:%M} UTC,
{hours:.1f} hours of data from {len(st["feeds"])} feeds.{note}</p>
</header>
<div class="tiles">{tiles_html}</div>
<section>
<h2>How much of the daily quota do we use?</h2>
<p class="takeaway">{escape(quota_take)}</p>
{_legend(modes)}
<div class="chart">{_quota_bar(st)}</div>
<details><summary>Requests by feed</summary>{_requests_table(st)}
</details>
</section>
<section>
<h2>What fills the disk?</h2>
<p class="takeaway">{escape(" ".join(describe(top["feed"])) if top else "")}
is {top_share:.0%} of the volume on disk. It is the first lever if
storage has to shrink.</p>
{_legend(modes)}
<div class="chart">{_volume_bars(st)}</div>
<details><summary>Snapshot sizes by feed</summary>{_size_table(st)}
</details>
</section>
{runway_section}
<details><summary>Response headers seen</summary>
<ul>{headers or "<li>No limit, quota or retry headers.</li>"}</ul>
</details>
<footer><strong>Sydney Transport Live Lakehouse</strong> · generated
{datetime.fromisoformat(st["generated"]):%Y-%m-%d %H:%M} UTC by
<code>checks quota --html</code> from the collector's poll manifest.
</footer>
</main>
"""
