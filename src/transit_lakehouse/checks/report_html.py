"""Render the quota report as one self-contained, styled HTML page.

The look follows the project deck: UTS palette A, the Tracker chapter in
ochre, transport modes in TfNSW colours with the official mode symbols,
everything else neutral. Tables lead each numbered section and carry the
numbers; charts are a visual check below them. Status colour is reserved:
teal for "fine", red for anything that needs action.

Assets (fonts, the UTS mark, TfNSW logo and mode symbols) are embedded
when ``REPORT_ASSET_DIR`` points at a deck-kit ``assets`` folder, and
skipped otherwise, so the page always renders.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo("Australia/Sydney")
RUNWAY_WARN_DAYS = 30

# TfNSW mode colours, as in the deck's scripts/colours.py.
MODE_COLOURS = {
    "Bus": "#00B5EF",
    "Trains": "#F6891F",
    "Metro": "#00959A",
    "Light rail": "#EE343F",
    "Ferries": "#5AB031",
    "Network-wide": "var(--neutral)",
}
MODE_SYMBOL = {
    "Bus": "mode-bus.png",
    "Trains": "mode-train.png",
    "Metro": "mode-metro.png",
    "Light rail": "mode-lightrail.png",
    "Ferries": "mode-ferry.png",
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

# Small line icons (24 px grid), drawn in currentColor.
ICONS = {
    "ok": '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.6 2.6L16 9.7"/>',
    "warn": '<path d="M12 3.5 2.8 19.5h18.4z"/><path d="M12 10v4.2"/>'
    '<circle cx="12" cy="16.9" r=".4"/>',
    "alerts": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/>'
    '<path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "timetable": '<rect x="3.5" y="5" width="17" height="15" rx="2"/>'
    '<path d="M3.5 9.5h17M8 3v4M16 3v4"/>',
    "quota": '<path d="M4 18a8 8 0 1 1 16 0"/><path d="m12 18 4-5"/>',
    "disk": '<ellipse cx="12" cy="6" rx="7.5" ry="2.8"/>'
    '<path d="M4.5 6v12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V6"/>'
    '<path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "list": '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
}


def icon(name: str, cls: str = "ic") -> str:
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>'
    )


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


class Assets:
    """Optional deck-kit assets, embedded as data URIs."""

    def __init__(self, root: Path | None) -> None:
        self.root = root if root and root.is_dir() else None

    def _uri(self, rel: str, mime: str) -> str | None:
        if not self.root:
            return None
        path = self.root / rel
        if not path.exists():
            return None
        data = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{data}"

    def fonts(self) -> str:
        faces = []
        for weight, name in ((400, "Regular"), (700, "Bold")):
            uri = self._uri(f"fonts/GoogleSans-{name}.ttf", "font/ttf")
            if uri:
                faces.append(
                    "@font-face{font-family:'Google Sans';"
                    f"font-weight:{weight};font-display:swap;"
                    f"src:url({uri}) format('truetype')}}"
                )
        return "".join(faces)

    def symbol_css(self) -> str:
        rules = []
        for mode in MODE_SYMBOL:
            uri = self.mode(mode)
            if uri:
                rules.append(
                    f".sym-{_slug(mode)}{{background:url({uri}) center/"
                    "contain no-repeat}"
                )
        return "".join(rules)

    def uts(self) -> str | None:
        return self._uri("brand/uts-logo.svg", "image/svg+xml")

    def tfnsw(self) -> str | None:
        return self._uri("project/tfnsw/tfnsw-logo.png", "image/png")

    def mode(self, mode: str) -> str | None:
        name = MODE_SYMBOL.get(mode)
        return (
            self._uri(f"project/tfnsw/{name}", "image/png") if name else None
        )


CSS = """
/* Layout: report sheet. Header with logos and metadata, a summary
   strip, then numbered sections; each opens with its status callout,
   then the table, then a supporting chart. */
:root{
  --ground:#FFFFFF; --card:#F4F5F6; --ink:#1B2430; --body:#4A5360;
  --muted:#6B7480; --hair:#D8DEE3; --rule:#89949D;
  --accent:#896521; --accent-fill:#F6F1E5;
  --good:#2E6B66; --good-fill:#E8F2F0;
  --bad:#B3261E; --bad-fill:#FBEAE8;
  --neutral:#9AA3AD; --plate:#FFFFFF;
  --sans:'Google Sans','Segoe UI',system-ui,-apple-system,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#12171E; --card:#1B222B; --ink:#E8ECF0; --body:#C0C7CF;
  --muted:#8C96A1; --hair:#2C3540; --rule:#5D6873;
  --accent:#D9B062; --accent-fill:#2A2416;
  --good:#6FB8B0; --good-fill:#16292A;
  --bad:#F2867A; --bad-fill:#3A1A18;
  --neutral:#7D8792; --plate:#F4F5F6; color-scheme:dark}}
:root[data-theme="dark"]{
  --ground:#12171E; --card:#1B222B; --ink:#E8ECF0; --body:#C0C7CF;
  --muted:#8C96A1; --hair:#2C3540; --rule:#5D6873;
  --accent:#D9B062; --accent-fill:#2A2416;
  --good:#6FB8B0; --good-fill:#16292A;
  --bad:#F2867A; --bad-fill:#3A1A18;
  --neutral:#7D8792; --plate:#F4F5F6; color-scheme:dark}
body{background:var(--ground);color:var(--body);font-family:var(--sans);
  font-size:15px;line-height:1.55;
  padding-left:max(16px,env(safe-area-inset-left,0px));
  padding-right:max(16px,env(safe-area-inset-right,0px));
  padding-block:28px 48px}
main{max-width:980px;margin:0 auto;display:grid;gap:44px}
h1,h2,h3{color:var(--ink);text-wrap:balance;margin:0;line-height:1.2}
h1{font-size:30px}
h2{font-size:21px;display:flex;align-items:baseline;gap:12px}
h2 .num{color:var(--accent);font-variant-numeric:tabular-nums}
p{margin:0;max-width:72ch}
.ic{width:20px;height:20px;flex:none;vertical-align:-4px}
.brandbar{display:flex;justify-content:space-between;align-items:center;
  gap:16px;flex-wrap:wrap}
.logos{display:flex;align-items:center;gap:14px}
.logos img{height:44px;width:auto;display:block}
.plate{background:var(--plate);border-radius:4px;padding:6px 10px}
.plate img{height:30px}
.eyebrow{font-size:12px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--accent);font-weight:700}
header{display:grid;gap:14px;border-bottom:2px solid var(--accent);
  padding-bottom:20px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
  gap:8px 24px;margin:0;font-size:13px}
.meta div{display:grid;gap:1px;min-width:0}
.meta dt{color:var(--muted);text-transform:uppercase;letter-spacing:.06em;
  font-size:11px}
.meta dd{margin:0;color:var(--ink)}
.chip{display:inline-block;font-size:12px;font-weight:700;padding:2px 8px;
  border-radius:4px;background:var(--accent-fill);color:var(--accent)}
.lede{font-size:18px;color:var(--ink);max-width:none}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:12px}
.tile{background:var(--card);border-radius:6px;padding:14px 16px;
  display:grid;gap:4px;align-content:start;border-top:3px solid var(--hair)}
.tile.good{border-top-color:var(--good)}
.tile.bad{border-top-color:var(--bad)}
.tile .label{display:flex;gap:6px;align-items:center;font-size:12px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tile .value{font-size:28px;font-weight:700;color:var(--ink);
  font-variant-numeric:tabular-nums;line-height:1.15}
.tile.good .value{color:var(--good)}
.tile.bad .value{color:var(--bad)}
.tile .note{font-size:13px}
section{display:grid;gap:14px;min-width:0}
.callout{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;
  padding:12px 16px;border-radius:6px;border-left:4px solid}
.callout.good{background:var(--good-fill);border-color:var(--good)}
.callout.bad{background:var(--bad-fill);border-color:var(--bad)}
.callout .ic{width:24px;height:24px;grid-row:span 2}
.callout.good .ic{color:var(--good)}
.callout.bad .ic{color:var(--bad)}
.callout strong{color:var(--ink)}
.callout ul{margin:0;padding-left:18px;display:grid;gap:2px}
.table{overflow-x:auto;border:1px solid var(--hair);border-radius:6px}
table{border-collapse:collapse;font-size:13.5px;width:100%;
  font-variant-numeric:tabular-nums}
caption{text-align:left;padding:10px 12px;color:var(--muted);
  font-size:12.5px;caption-side:bottom}
th,td{padding:8px 12px;border-bottom:1px solid var(--hair);
  text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{color:var(--ink);font-weight:700;background:var(--card);
  font-size:12.5px}
tfoot td{font-weight:700;color:var(--ink);background:var(--card);
  border-bottom:0}
.feedcell{display:flex;align-items:center;gap:9px}
.sym{width:22px;height:22px;border-radius:4px;flex:none;display:grid;
  place-items:center;color:var(--ground)}
.sym .ic{width:16px;height:16px;vertical-align:0}
.bar{position:relative;isolation:isolate}
.bar::before{content:"";position:absolute;inset:6px auto 6px 0;
  width:var(--w);background:var(--c,var(--accent));opacity:.22;
  border-radius:3px;z-index:-1}
.heat{background:color-mix(in srgb,var(--accent) calc(var(--h)*45%),
  transparent)}
.pill{display:inline-flex;align-items:center;gap:4px;font-weight:700}
.pill.good{color:var(--good)}
.pill.bad{color:var(--bad)}
.pill .ic{width:16px;height:16px;vertical-align:-3px}
.muted{color:var(--muted)}
.delta{color:var(--ink);font-weight:700}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:12px;height:12px;border-radius:3px;display:inline-block}
figure{margin:0;display:grid;gap:8px}
figcaption{font-size:13px;color:var(--muted)}
.chart{overflow-x:auto}
.chart svg{display:block;width:100%;height:auto;min-width:640px}
svg text{fill:var(--body);font-family:var(--sans)}
svg .ink{fill:var(--ink);font-weight:700}
svg .grid{stroke:var(--hair);stroke-width:1}
svg .axis{stroke:var(--rule);stroke-width:1}
footer{font-size:12.5px;color:var(--muted);border-top:1px solid var(--hair);
  padding-top:14px;display:grid;gap:4px}
footer strong{color:var(--ink)}
code{font-size:12px}
a{color:var(--accent)}
"""


def _feed_cell(feed: str, assets: Assets) -> str:
    mode, what = describe(feed)
    if assets.mode(mode):
        # The symbol is embedded once, as a CSS class (see Assets.css).
        sym = f'<span class="sym sym-{_slug(mode)}"></span>'
    else:
        glyph = icon(what) if what in ICONS else ""
        sym = (
            f'<span class="sym" style="background:{_colour(mode)}">'
            f"{glyph}</span>"
        )
    return (
        f'<span class="feedcell">{sym}<span>{escape(mode)} '
        f"{escape(what)}</span></span>"
    )


def _slug(mode: str) -> str:
    return mode.lower().replace(" ", "-")


def _pill_count(n: int, zero_good: bool = True) -> str:
    if n == 0 and zero_good:
        return f'<span class="pill good">{icon("ok")}0</span>'
    if n == 0:
        return '<span class="muted">0</span>'
    return f'<span class="pill bad">{icon("warn")}{n:,}</span>'


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
    order = sorted(
        st["feeds"], key=lambda f: (_mode_rank(f["feed"]), -f["per_day"])
    )
    for f in order:
        mode, what = describe(f["feed"])
        seg = f["per_day"] * scale
        tip = f"{mode} {what}: {f['per_day']:,.0f} requests/day"
        parts.append(
            f'<rect x="{x:.1f}" y="{bar_y}" '
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
        f'y2="{bar_y + bar_h + 4}" stroke="var(--ink)" stroke-width="2" '
        'stroke-dasharray="4 3"/>'
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
        out.append(
            f'<text x="{label_w - 12}" y="{y + 19}" font-size="13" '
            f'text-anchor="end">{escape(mode)} {escape(what)}</text>'
            f'<rect x="{label_w}" y="{y + 5}" width="{bw:.1f}" '
            f'height="{row_h - 10}" rx="3" fill="{_colour(mode)}">'
            f"<title>{escape(mode)} {escape(what)}: "
            f"{_size(f['stored_per_day'])}/day</title></rect>"
            f'<text class="ink" x="{label_w + bw + 8:.1f}" y="{y + 19}" '
            f'font-size="13">{_size(f["stored_per_day"])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="On-disk '
        f'volume per day by feed">'
        f'<line class="axis" x1="{label_w}" y1="0" x2="{label_w}" '
        f'y2="{h}"/>{"".join(out)}</svg>'
    )


def _runway(st: dict, days_left: float) -> str:
    """Projected disk use from now until the volume is full."""
    disk, total = st["disk"], st["disk"]["total"]
    used = disk["used"]
    horizon = max(7, int(days_left * 1.15) + 1)
    w, h, left, r, t, b = 880, 240, 56, 20, 26, 36
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
    gen = datetime.fromisoformat(st["generated"]).astimezone(SYDNEY)
    xt = []
    for d in range(0, horizon + 1, 7 if horizon > 21 else 2):
        x, _ = px(d, 0)
        xt.append(
            f'<text x="{x:.1f}" y="{h - b + 20}" font-size="12" '
            f'text-anchor="middle">{gen + timedelta(days=d):%-d %b}</text>'
        )
    full_day = gen + timedelta(days=days_left)
    _, y_cap = px(0, total)
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Disk fills '
        f'in about {days_left:.0f} days, around {full_day:%-d %b}">'
        f"{''.join(grid)}{''.join(xt)}"
        f'<line x1="{left}" y1="{y_cap:.1f}" x2="{w - r}" y2="{y_cap:.1f}" '
        f'stroke="var(--bad)" stroke-width="2" stroke-dasharray="5 4"/>'
        f'<text x="{left + 6}" y="{y_cap - 8:.1f}" font-size="12">'
        f"Volume size {_size(total)}</text>"
        f'<polygon points="{x_now:.1f},{h - b} {x_now:.1f},{y_now:.1f} '
        f'{x_full:.1f},{y_full:.1f} {x_full:.1f},{h - b}" '
        f'fill="var(--accent)" fill-opacity=".14"/>'
        f'<line x1="{x_now:.1f}" y1="{y_now:.1f}" x2="{x_full:.1f}" '
        f'y2="{y_full:.1f}" stroke="var(--accent)" stroke-width="2"/>'
        f'<circle cx="{x_now:.1f}" cy="{y_now:.1f}" r="5" '
        f'fill="var(--accent)" stroke="var(--ground)" stroke-width="2"/>'
        f'<circle cx="{x_full:.1f}" cy="{y_full:.1f}" r="5" '
        f'fill="var(--bad)" stroke="var(--ground)" stroke-width="2"/>'
        f'<text class="ink" x="{x_now + 12:.1f}" y="{y_now + 22:.1f}" '
        f'font-size="13">Now, {_size(used)} used</text>'
        f'<text class="ink" x="{x_full - 12:.1f}" y="{y_cap - 8:.1f}" '
        f'font-size="13" text-anchor="end">Full around '
        f"{full_day:%-d %b}</text>"
        "</svg>"
    )


def _prev(st: dict, feed: str | None, key: str) -> float | None:
    """The same figure from the previous report, if one was supplied."""
    prev = st.get("previous")
    if not prev:
        return None
    if feed is None:
        return prev.get(key)
    match = next((f for f in prev["feeds"] if f["feed"] == feed), None)
    return match.get(key) if match else None


def _delta(cur: float, prev: float | None) -> str:
    """Change against the previous report, as an arrow and a percent."""
    if prev is None:
        return '<td class="muted">new</td>'
    if not prev:
        return '<td class="muted">–</td>'
    change = cur / prev - 1
    if abs(change) < 0.01:
        return '<td class="muted">≈ 0%</td>'
    arrow = "▲" if change > 0 else "▼"
    return f'<td class="delta">{arrow} {abs(change):.0%}</td>'


def _requests_table(st: dict, assets: Assets) -> str:
    quota = st["daily_quota"]
    feeds = sorted(
        st["feeds"], key=lambda f: (_mode_rank(f["feed"]), -f["per_day"])
    )
    top = max((f["per_day"] for f in feeds), default=1) or 1
    has_prev = bool(st.get("previous"))
    rows = []
    for f in feeds:
        mode = describe(f["feed"])[0]
        ok = f["new"] + f["unchanged"]
        unchanged = f["unchanged"] / ok if ok else 0
        rows.append(
            "<tr>"
            f"<td>{_feed_cell(f['feed'], assets)}</td>"
            f"<td>{f['requests']:,}</td>"
            f'<td class="bar" style="--w:{f["per_day"] / top * 100:.0f}%;'
            f'--c:{_colour(mode)}">{f["per_day"]:,.0f}</td>'
            + (
                _delta(f["per_day"], _prev(st, f["feed"], "per_day"))
                if has_prev
                else ""
            )
            + f"<td>{f['per_day'] / quota:.1%}</td>"
            f"<td>{f['new']:,}</td>"
            f"<td>{f['unchanged']:,} "
            f'<span class="muted">({unchanged:.0%})</span></td>'
            f"<td>{_pill_count(f['limit_hits'])}</td>"
            f"<td>{_pill_count(f['other_errors'])}</td></tr>"
        )
    tot = st["requests_per_day"]
    return (
        '<div class="table"><table><thead><tr><th>Feed</th>'
        "<th>Requests</th><th>Per day</th>"
        + ("<th>vs previous</th>" if has_prev else "")
        + "<th>Share of quota</th>"
        "<th>New</th><th>Unchanged</th><th>Limit hits</th>"
        "<th>Other errors</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody><tfoot><tr><td>All feeds</td>"
        f"<td>{st['total_requests']:,}</td><td>{tot:,.0f}</td>"
        + (
            _delta(tot, _prev(st, None, "requests_per_day"))
            if has_prev
            else ""
        )
        + f"<td>{tot / quota:.1%}</td>"
        f"<td>{sum(f['new'] for f in feeds):,}</td>"
        f"<td>{sum(f['unchanged'] for f in feeds):,}</td>"
        f"<td>{_pill_count(st['limit_hits'])}</td>"
        f"<td>{_pill_count(sum(f['other_errors'] for f in feeds))}</td>"
        "</tr></tfoot><caption>Per day is scaled from the hours observed. "
        "Unchanged means TfNSW had not refreshed the snapshot since the "
        "last poll, which still counts as a successful capture."
        "</caption></table></div>"
    )


def _size_table(st: dict, assets: Assets) -> str:
    feeds = sorted(st["feeds"], key=lambda f: -f["stored_per_day"])
    total = st["stored_per_day"] or 1
    has_prev = bool(st.get("previous"))
    rows = []
    for f in feeds:
        mode = describe(f["feed"])[0]
        share = f["stored_per_day"] / total
        ratio = (
            f["received_per_day"] / f["stored_per_day"]
            if f["stored_per_day"]
            else 0
        )
        rows.append(
            "<tr>"
            f"<td>{_feed_cell(f['feed'], assets)}</td>"
            f"<td>{_size(f['size_p50'])}</td><td>{_size(f['size_p95'])}</td>"
            f"<td>{_size(f['size_max'])}</td>"
            f"<td>{_size(f.get('downloaded_per_day'))}</td>"
            f"<td>{ratio:.1f}×</td>"
            f'<td class="bar" style="--w:{share * 100:.0f}%;'
            f'--c:{_colour(mode)}">{_size(f["stored_per_day"])}</td>'
            + (
                _delta(
                    f["stored_per_day"],
                    _prev(st, f["feed"], "stored_per_day"),
                )
                if has_prev
                else ""
            )
            + f'<td class="heat" style="--h:{share:.2f}">{share:.0%}</td>'
            f"<td>{_size(f['stored_per_day'] * 30)}</td></tr>"
        )
    recv, stored = st["received_per_day"], st["stored_per_day"]
    down = st.get("downloaded_per_day")
    return (
        '<div class="table"><table><thead><tr><th>Feed</th><th>p50 size'
        "</th><th>p95 size</th><th>Max size</th><th>Downloaded/day</th>"
        "<th>Gzip</th><th>On disk/day</th>"
        + ("<th>vs previous</th>" if has_prev else "")
        + "<th>Share of disk</th>"
        "<th>On disk/30 days</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody><tfoot><tr><td>All feeds</td>"
        "<td></td><td></td><td></td>"
        f"<td>{_size(down)}</td>"
        f"<td>{recv / stored if stored else 0:.1f}×</td>"
        f"<td>{_size(stored)}</td>"
        + (
            _delta(stored, _prev(st, None, "stored_per_day"))
            if has_prev
            else ""
        )
        + "<td>100%</td>"
        f"<td>{_size(stored * 30)}</td></tr></tfoot>"
        "<caption>Sizes are per snapshot as received. Downloaded counts "
        "every successful poll, repeats included. On disk counts only "
        "snapshots that changed, after gzip; the gzip ratio compares those "
        "two. The timetable is already a zip, so it is stored as received."
        "</caption></table></div>"
    )


def _callout(good: bool, title: str, points: list[str]) -> str:
    cls, ic = ("good", "ok") if good else ("bad", "warn")
    items = "".join(f"<li>{p}</li>" for p in points)
    return (
        f'<div class="callout {cls}">{icon(ic)}<strong>{title}</strong>'
        f"<ul>{items}</ul></div>"
    )


def render_html(
    st: dict,
    asset_dir: Path | None = None,
    preview_note: str | None = None,
    author: str | None = None,
) -> str:
    """Return the full report page. ``st`` is quota_report.compute_stats."""
    if asset_dir is None and os.environ.get("REPORT_ASSET_DIR"):
        asset_dir = Path(os.environ["REPORT_ASSET_DIR"])
    author = author or os.environ.get("REPORT_AUTHOR", "")
    assets = Assets(asset_dir)

    end = datetime.fromisoformat(st["window_end"]).astimezone(SYDNEY)
    hours = st["observed_hours"]
    obs_start = end - timedelta(hours=hours)
    generated = datetime.fromisoformat(st["generated"])
    gen_syd = generated.astimezone(SYDNEY)
    quota, per_day = st["daily_quota"], st["requests_per_day"]
    share = per_day / quota if quota else 0
    stored, received = st["stored_per_day"], st["received_per_day"]
    hits = st["limit_hits"]
    errors = sum(f["other_errors"] for f in st["feeds"])
    feeds = st["feeds"]
    present = {describe(f["feed"])[0] for f in feeds}
    modes = [m for m in MODE_COLOURS if m in present]
    by_calls = sorted(feeds, key=lambda f: -f["per_day"])
    by_disk = sorted(feeds, key=lambda f: -f["stored_per_day"])
    disk = st.get("disk")
    days_left = disk["free"] / stored if disk and stored else None
    full_day = (
        gen_syd + timedelta(days=days_left) if days_left is not None else None
    )

    def name(f: dict) -> str:
        return escape(" ".join(describe(f["feed"])))

    quota_ok = hits == 0 and errors == 0 and share <= 0.8
    runway_ok = days_left is None or days_left >= RUNWAY_WARN_DAYS

    # Headline and tiles.
    if quota_ok and runway_ok:
        lede = (
            f"{len(feeds)} feeds use {share:.0%} of the daily quota with "
            "no rejections, and storage has room to spare."
        )
    elif quota_ok:
        lede = (
            f"The quota is not the constraint ({share:.0%} used, no "
            f"rejections). Disk is: it fills in about {days_left:.0f} days "
            "at the current rate."
        )
    else:
        lede = (
            f"{hits:,} limit rejections and {errors:,} other errors in "
            f"{hours:.1f} hours. Check the feeds marked red below."
        )
    tiles = [
        (
            "quota",
            "Quota used",
            f"{share:.0%}",
            f"{per_day:,.0f} of {quota:,} requests a day",
            "good" if share <= 0.8 else "bad",
        ),
        (
            "ok" if hits == 0 else "warn",
            "Limit rejections",
            f"{hits:,}",
            f"across {st['total_requests']:,} requests",
            "good" if hits == 0 else "bad",
        ),
        (
            "disk",
            "On disk per day",
            _size(stored),
            f"{_size(stored * 30)} per 30 days, gzipped",
            "",
        ),
    ]
    if days_left is not None:
        tiles.append(
            (
                "clock",
                "Disk runway",
                f"{days_left:.0f} days",
                f"{_size(disk['free'])} free, full around {full_day:%-d %b}",
                "good" if runway_ok else "bad",
            )
        )
    tiles_html = "".join(
        f'<div class="tile {cls}"><span class="label">{icon(ic)}'
        f'{escape(lbl)}</span><span class="value">{escape(val)}</span>'
        f'<span class="note">{escape(note)}</span></div>'
        for ic, lbl, val, note, cls in tiles
    )

    # Section 1 insights.
    unchanged_top = max(
        feeds,
        key=lambda f: f["unchanged"] / max(f["new"] + f["unchanged"], 1),
        default=None,
    )
    q_points = [
        f"{per_day:,.0f} requests a day is {share:.0%} of the "
        f"{quota:,} quota, leaving {max(0.8 * quota - per_day, 0):,.0f} a "
        "day under the collector's 80% budget.",
        f"{hits:,} rate or quota rejections and {errors:,} other errors "
        f"across {st['total_requests']:,} requests in {hours:.1f} hours.",
    ]
    if by_calls:
        q_points.append(
            f"The busiest feed is {name(by_calls[0])} at "
            f"{by_calls[0]['per_day']:,.0f} a day "
            f"({by_calls[0]['per_day'] / quota:.0%} of quota)."
        )
    if unchanged_top and unchanged_top["unchanged"]:
        u = unchanged_top
        q_points.append(
            f"{name(u)} returned an unchanged snapshot most often: "
            f"{u['unchanged'] / (u['new'] + u['unchanged']):.0%} of "
            "its polls."
        )
    q_title = (
        "Within quota, no rejections"
        if quota_ok
        else "Needs attention: the API is rejecting or erroring"
    )

    # Section 2 insights.
    top = by_disk[0] if by_disk else None
    s_points = []
    if top and stored:
        s_points.append(
            f"{name(top)} is {top['stored_per_day'] / stored:.0%} of the "
            f"disk ({_size(top['stored_per_day'])} a day). It is the first "
            "lever if storage has to shrink."
        )
    if stored:
        s_points.append(
            f"Gzip cuts {_size(received)} received to {_size(stored)} on "
            f"disk ({received / stored:.1f}×). Over 30 days that is "
            f"{_size(stored * 30)} before compaction into Iceberg."
        )
    static = next((f for f in feeds if f["feed"] == "static_gtfs"), None)
    if static and static["new"] > 1:
        s_points.append(
            f"The static timetable changed {static['new']} times in "
            f"{hours:.1f} h, so it was stored {static['new']} times "
            f"({_size(static['size_max'])} each). If that continues it is "
            f"{static['stored_per_day'] / stored:.0%} of the disk."
        )
    s_good = runway_ok
    s_title = (
        "Storage is comfortable"
        if s_good
        else "Storage needs a plan before the disk fills"
    )

    # Section 3.
    runway_section = ""
    if days_left is not None:
        r_points = [
            f"{_size(disk['used'])} used of {_size(disk['total'])}; "
            f"{_size(disk['free'])} free.",
            f"At {_size(stored)} a day the volume is full in about "
            f"{days_left:.0f} days, around {full_day:%-d %B}.",
        ]
        if not runway_ok:
            r_points.append(
                "Raw snapshots must move to object storage or be compacted "
                "into Iceberg before then."
            )
        runway_section = (
            '<section><h2><span class="num">3</span>Disk runway</h2>'
            + _callout(
                runway_ok,
                "Runway is fine"
                if runway_ok
                else f"Warning: about {days_left:.0f} days of disk left",
                r_points,
            )
            + f'<figure><div class="chart">{_runway(st, days_left)}</div>'
            "<figcaption>Projected use at today's rate, from the volume "
            "size and free space when the report ran.</figcaption></figure>"
            "</section>"
        )
    header_rows = "".join(
        f"<tr><td><code>{escape(k)}</code></td>"
        f"<td style='text-align:left;white-space:normal'>"
        f"{escape(', '.join(v))}</td></tr>"
        for k, v in st["headers"].items()
    )
    headers_num = 4 if runway_section else 3

    uts, tfnsw = assets.uts(), assets.tfnsw()
    logos = ""
    if uts:
        logos += f'<img src="{uts}" alt="University of Technology Sydney">'
    if tfnsw:
        logos += (
            f'<span class="plate"><img src="{tfnsw}" '
            'alt="Transport for NSW"></span>'
        )
    status = (
        f'<span class="chip">{escape(preview_note)}</span>'
        if preview_note
        else '<span class="chip">Full 24 h window</span>'
        if hours >= 23.5
        else f'<span class="chip">Partial window, {hours:.1f} h</span>'
    )
    author_row = (
        f"<div><dt>Prepared by</dt><dd>{escape(author)}</dd></div>"
        if author
        else ""
    )
    return f"""<title>Quota and Volume Report</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>{assets.fonts()}{CSS}{assets.symbol_css()}</style>
<main>
<header>
<div class="brandbar"><div class="logos">{logos}</div>{status}</div>
<span class="eyebrow">Sydney Transport Live Lakehouse · Tracker ·
Phase 0, collect first</span>
<h1>API quota and data volume report</h1>
<dl class="meta">
{author_row}
<div><dt>Checked</dt><dd>{gen_syd:%-d %b %Y, %H:%M %Z}
<span class="muted">({generated:%H:%M} UTC)</span></dd></div>
<div><dt>Data window</dt><dd>{obs_start:%-d %b %H:%M} to
{end:%-d %b %H:%M} {end:%Z}, {hours:.1f} h</dd></div>
<div><dt>Source</dt><dd>Transport for NSW Open Data Hub,
GTFS-realtime and static GTFS, {len(feeds)} feeds</dd></div>
</dl>
</header>
<section>
<p class="lede">{escape(lede)}</p>
<div class="tiles">{tiles_html}</div>
</section>
<section>
<h2><span class="num">1</span>API quota and limits</h2>
{_callout(quota_ok, q_title, q_points)}
{_requests_table(st, assets)}
<figure>{_legend(modes)}<div class="chart">{_quota_bar(st)}</div>
<figcaption>Requests a day by mode, stacked against the {quota:,}
daily quota. The dashed line is the collector's 80% budget.
</figcaption></figure>
</section>
<section>
<h2><span class="num">2</span>Snapshot sizes and storage</h2>
{_callout(s_good, s_title, s_points)}
{_size_table(st, assets)}
<figure>{_legend(modes)}<div class="chart">{_volume_bars(st)}</div>
<figcaption>On-disk volume a day by feed, largest first.</figcaption>
</figure>
</section>
{runway_section}
<section>
<h2><span class="num">{headers_num}</span>Response headers</h2>
<p>TfNSW publishes no quota headers, so the collector keeps any that
mention limits, quotas or retries. Per-request IDs are left out.</p>
<div class="table"><table><thead><tr><th>Header</th>
<th style="text-align:left">Values seen</th></tr></thead>
<tbody>{header_rows or '<tr><td colspan="2">None.</td></tr>'}</tbody>
</table></div>
</section>
<footer>
<span><strong>Sydney Transport Live Lakehouse</strong>{
        f" · {escape(author)}" if author else ""
    } · University of Technology Sydney</span>
<span>Data: Transport for NSW Open Data Hub. Mode symbols © State of
New South Wales (Transport for NSW).</span>
<span>Generated {generated:%Y-%m-%d %H:%M} UTC by <code>checks quota
--html</code> from the collector's poll manifest.
<a href="https://github.com/tuannm3812/sydney-transit-lakehouse">Source
code</a>.</span>
</footer>
</main>
"""
