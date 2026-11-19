"""Render a phase report (docs/reports/README.md) to HTML and PDF.

``parse`` splits the front block from the body and checks both against
the standard. ``to_html`` builds one self-contained A4 page: header with
logos and metadata, highlight tiles, numbered sections, the Summary's
blockquote as the answer band, and ``chart`` fences drawn as bar charts.
``to_pdf`` prints that page with headless Chrome.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from transit_lakehouse.checks.report_html import Assets

REQUIRED = (
    "id",
    "title",
    "subtitle",
    "phase",
    "status",
    "verdict",
    "date",
    "commit",
    "environment",
    "decisions",
    "evidence",
    "highlights",
)
SECTIONS = (
    "Summary",
    "Scope and method",
    "Results",
    "Findings",
    "Limits",
    "Next steps",
)
OPTIONAL_SECTIONS = ("Appendix",)
STATUSES = ("Draft", "Interim", "Final", "Superseded")
VERDICTS = ("Pass", "Pass with actions", "Fail", "Info")
# Things a report must never carry (docs/reports/README.md section 4).
LEAKS = (
    ("an IP address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("an email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    (
        "an API key",
        re.compile(r"(?i)apikey\s+\S{12,}|\b[A-Za-z0-9_-]{32,}\b"),
    ),
)
PHASES = {"0": "Collect first", "1": "Build the lakehouse"}
SUBJECT = "94693 Big Data Engineering"
BAR = "#3B528B"  # Viridis blue: one series, not a transport mode


class ReportError(ValueError):
    """The report does not follow docs/reports/README.md."""


@dataclass
class Report:
    meta: dict[str, str]
    highlights: list[tuple[str, str]]
    body: str
    sections: list[str] = field(default_factory=list)


def _strip_comment(value: str) -> str:
    cut = value.find("  #")
    return (value[:cut] if cut >= 0 else value).strip()


def parse(text: str) -> Report:
    """Split and check the front block; raise ReportError if it is off."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ReportError("missing front block (--- on the first line)")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ReportError("front block is not closed with ---") from exc
    meta: dict[str, str] = {}
    highlights: list[tuple[str, str]] = []
    key = ""
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if raw.startswith((" ", "\t")) and raw.strip().startswith("- "):
            if key != "highlights":
                raise ReportError(f"list item under {key!r}")
            value, sep, label = raw.strip()[2:].partition("|")
            if not sep:
                raise ReportError(f"highlight needs 'value | label': {raw}")
            highlights.append((value.strip(), label.strip()))
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise ReportError(f"bad front block line: {raw}")
        key = key.strip()
        meta[key] = _strip_comment(value)
    missing = [k for k in REQUIRED if k not in meta]
    if missing:
        raise ReportError(f"front block is missing {', '.join(missing)}")
    if meta["status"] not in STATUSES:
        raise ReportError(f"status must be one of {', '.join(STATUSES)}")
    if meta["verdict"] not in VERDICTS:
        raise ReportError(f"verdict must be one of {', '.join(VERDICTS)}")
    if meta["phase"] not in PHASES:
        raise ReportError(f"unknown phase {meta['phase']!r}")
    if not 2 <= len(highlights) <= 4:
        raise ReportError("give two to four highlights")
    rest = lines[end + 1 :]
    # A "# title" line for GitHub; the PDF prints the title itself.
    while rest and not rest[0].strip():
        rest = rest[1:]
    if rest and rest[0].startswith("# "):
        rest = rest[1:]
    body = "\n".join(rest)
    for what, pattern in LEAKS:
        hit = pattern.search(text)
        if hit:  # never echo the match: it may be the secret
            line = text.count("\n", 0, hit.start()) + 1
            raise ReportError(f"line {line} looks like {what}")
    sections = [
        ln[3:].strip() for ln in body.splitlines() if ln.startswith("## ")
    ]
    if tuple(sections[: len(SECTIONS)]) != SECTIONS or any(
        s not in OPTIONAL_SECTIONS for s in sections[len(SECTIONS) :]
    ):
        raise ReportError(
            "sections must be, in order: "
            + ", ".join(SECTIONS)
            + f" (optional: {', '.join(OPTIONAL_SECTIONS)}); "
            f"found {', '.join(sections) or 'none'}"
        )
    return Report(meta, highlights, body, sections)


# --- charts -----------------------------------------------------------


def _number(text: str) -> float:
    return float(text.replace(",", "").split()[0])


def chart_svg(source: str) -> str:
    """Draw a ``chart`` fence as a horizontal bar chart (inline SVG)."""
    title, unit, limit = "", "", None
    bars: list[tuple[str, float]] = []
    for line in source.strip().splitlines():
        key, _, value = line.partition(":")
        if key.strip() not in ("title", "unit", "limit"):
            key, _, value = line.rpartition(":")  # labels may hold times
        key, value = key.strip(), value.strip()
        if key == "title":
            title = value
        elif key == "unit":
            unit = value
        elif key == "limit":
            num, _, label = value.partition("|")
            limit = (_number(num), label.strip())
        elif key:
            bars.append((key, _number(value)))
    if not bars:
        raise ReportError("chart has no bars")
    top = max([v for _, v in bars] + ([limit[0]] if limit else []))
    label_w, plot_w, row_h, bar_h = 190, 400, 30, 14
    head = 26 if limit else 8
    height = head + row_h * len(bars) + 6
    x = lambda v: label_w + v / top * plot_w  # noqa: E731
    parts = [
        f'<svg class="chart" viewBox="0 0 {label_w + plot_w + 70} '
        f'{height}" role="img" aria-label="{escape(title)}">'
    ]
    for i, (name, value) in enumerate(bars):
        y = head + i * row_h
        w = max(x(value) - label_w, 2)
        parts.append(
            f'<text class="cl" x="{label_w - 10}" y="{y + bar_h - 2}" '
            f'text-anchor="end">{escape(name)}</text>'
            f'<path d="M{label_w},{y} h{w - 4} q4,0 4,4 v{bar_h - 8} '
            f'q0,4 -4,4 h-{w - 4} z" fill="{BAR}"/>'
            f'<text class="cv" x="{label_w + w + 6}" y="{y + bar_h - 2}">'
            f"{value:,.0f} {escape(unit)}</text>"
        )
    parts.append(
        f'<line class="base" x1="{label_w}" x2="{label_w}" y1="{head - 4}"'
        f' y2="{height - 4}"/>'
    )
    if limit:
        lx = x(limit[0])
        parts.append(
            f'<line class="lim" x1="{lx:.1f}" x2="{lx:.1f}" y1="14" '
            f'y2="{height - 4}"/>'
            f'<text class="cl lim-t" x="{lx:.1f}" y="10" '
            f'text-anchor="end">{escape(limit[1])} '
            f"{limit[0]:,.0f} {escape(unit)}</text>"
        )
    parts.append("</svg>")
    return (
        f'<figure class="fig"><figcaption>{escape(title)}</figcaption>'
        + "".join(parts)
        + "</figure>"
    )


# --- HTML -------------------------------------------------------------


def _markdown(body: str) -> str:
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    default_fence = md.renderer.rules.get("fence")

    def fence(_self, tokens, idx, options, env):
        tok = tokens[idx]
        if tok.info.strip() == "chart":
            return chart_svg(tok.content)
        return default_fence(tokens, idx, options, env)

    md.add_render_rule("fence", fence)
    tokens = md.parse(body)
    # The first blockquote under Summary is the answer band.
    in_summary = False
    for tok in tokens:
        if tok.type == "heading_open" and tok.tag == "h2":
            in_summary = False
        if tok.type == "inline" and tok.content.strip() == "Summary":
            in_summary = True
        if in_summary and tok.type == "blockquote_open":
            tok.attrSet("class", "answer")
            break
    for tok in tokens:  # tables scroll as one block, never split a row
        if tok.type == "table_open":
            tok.attrSet("class", "t")
    return md.renderer.render(tokens, md.options, {})


def _slug(text: str) -> str:
    return re.sub(r"[^a-z]+", "-", text.lower()).strip("-")


class ReportAssets(Assets):
    def fonts(self) -> str:
        faces = []
        for family, weight, style, name in (
            ("Google Sans", 400, "normal", "GoogleSans-Regular"),
            ("Google Sans", 700, "normal", "GoogleSans-Bold"),
            ("Google Sans", 400, "italic", "GoogleSans-Italic"),
            ("Google Sans Code", 400, "normal", "GoogleSansCode-Regular"),
            ("Google Sans Code", 700, "normal", "GoogleSansCode-Bold"),
        ):
            uri = self._uri(f"fonts/{name}.ttf", "font/ttf")
            if uri:
                faces.append(
                    f"@font-face{{font-family:'{family}';"
                    f"font-weight:{weight};font-style:{style};"
                    f"src:url({uri}) format('truetype')}}"
                )
        return "".join(faces)


CSS = """
@page{size:A4;margin:18mm 18mm 20mm;
  @bottom-left{content:var(--foot);font:8pt var(--sans);color:#6B7480}
  @bottom-right{content:"Page " counter(page) " of " counter(pages);
    font:8pt var(--sans);color:#6B7480}}
:root{--ink:#1B2430;--body:#3A4350;--muted:#6B7480;--hair:#D8DEE3;
  --card:#F4F5F6;--accent:#896521;--accent-fill:#F6F1E5;--good:#2E6B66;
  --good-fill:#E8F2F0;--band:#1B2430;--neutral:#9AA3AD;
  --sans:'Google Sans','Segoe UI',system-ui,-apple-system,sans-serif;
  --mono:'Google Sans Code',ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:#fff;color:var(--body);font:10pt/1.5 var(--sans);
  counter-reset:h2}
.top{display:flex;justify-content:space-between;align-items:center;
  border-bottom:2px solid var(--accent);padding-bottom:8px}
.eyebrow{font-size:8.5pt;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--accent)}
.logos{display:flex;gap:14px;align-items:center}
.logos img{height:30px}
.logos img.uts{height:38px}
h1{color:var(--ink);font-size:22pt;line-height:1.15;margin:16px 0 4px}
.sub{font-size:11.5pt;color:var(--muted);margin:0 0 14px}
.meta{display:grid;grid-template-columns:repeat(4,1fr);gap:0;
  border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);
  margin-bottom:14px}
.meta div{padding:6px 10px 7px 0}
.meta dt{font-size:7.5pt;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted)}
.meta dd{margin:0;color:var(--ink);font-size:9pt}
.meta .span3{grid-column:span 3}
.meta .full{grid-column:1/-1;border-top:1px solid var(--hair)}
.chip{display:inline-block;padding:0 8px;border-radius:9px;
  font-size:8pt;font-weight:700}
.chip.Final{background:var(--good-fill);color:var(--good)}
.chip.Interim{background:var(--accent-fill);color:var(--accent)}
.chip.Draft,.chip.Superseded{background:var(--card);color:var(--muted)}
.chip.v-pass{background:var(--good-fill);color:var(--good)}
.chip.v-pass-with-actions{background:var(--accent-fill);color:var(--accent)}
.chip.v-fail{background:#FBEAE8;color:#B3261E}
.chip.v-info{background:var(--card);color:var(--ink)}
body.draft main::before{content:"DRAFT";position:fixed;top:40%;left:12%;
  font:700 120pt var(--sans);color:rgba(27,36,48,.05);
  transform:rotate(-30deg)}
.tiles{display:grid;grid-template-columns:repeat(var(--n),1fr);gap:10px;
  margin:0 0 6px}
.tile{background:var(--card);border-radius:6px;padding:10px 12px;
  border-top:3px solid var(--accent)}
.tile b{display:block;color:var(--ink);font-size:17pt;line-height:1.1}
.tile span{font-size:8.5pt;color:var(--muted)}
h2{color:var(--ink);font-size:14pt;margin:22px 0 8px;
  counter-increment:h2;counter-reset:h3;break-after:avoid}
h2::before{content:counter(h2);color:var(--accent);margin-right:10px}
h3{color:var(--ink);font-size:11pt;margin:16px 0 6px;
  counter-increment:h3;break-after:avoid}
h3::before{content:counter(h2) "." counter(h3);color:var(--accent);
  margin-right:8px;font-weight:400}
p{margin:0 0 8px}
ul,ol{margin:0 0 8px;padding-left:18px}
li{margin:0 0 4px}
strong{color:var(--ink)}
a{color:inherit}
p code,li code,td code{white-space:nowrap}
code{font:8.6pt var(--mono);background:var(--card);padding:0 3px;
  border-radius:3px;color:var(--ink)}
pre{background:var(--card);padding:8px 10px;border-radius:6px;
  white-space:pre-wrap;break-inside:avoid;font-size:8.4pt}
pre code{background:none;padding:0}
blockquote.answer{margin:4px 0 12px;background:var(--band);color:#fff;
  border-radius:6px;padding:12px 16px;font-size:11pt;line-height:1.45}
blockquote.answer p{margin:0}
blockquote.answer strong{color:#F3D9A4}
blockquote:not(.answer){margin:0 0 8px;padding:6px 12px;
  border-left:3px solid var(--accent);background:var(--accent-fill)}
table.t{border-collapse:collapse;width:100%;margin:4px 0 10px;
  font-size:8.8pt;font-variant-numeric:tabular-nums;break-inside:avoid}
table.t th{text-align:left;color:var(--ink);font-weight:700;
  border-bottom:1.5px solid var(--ink);padding:4px 8px 4px 0}
table.t td{border-bottom:1px solid var(--hair);padding:4px 8px 4px 0;
  vertical-align:top}
table.t tr{break-inside:avoid}
.fig{margin:6px 0 12px;break-inside:avoid}
.fig figcaption{font-size:9pt;font-weight:700;color:var(--ink);
  margin-bottom:4px}
.chart{width:100%;height:auto}
.chart .cl{font:11px var(--sans);fill:#3A4350}
.chart .cv{font:700 11px var(--sans);fill:#1B2430}
.chart .base{stroke:#89949D;stroke-width:1}
.chart .lim{stroke:#B3261E;stroke-width:1.5;stroke-dasharray:4 3}
.chart .lim-t{fill:#B3261E;font-weight:700}
"""


def to_html(report: Report, assets: Assets | None = None) -> str:
    assets = assets or ReportAssets(None)
    m = report.meta
    phase = f"Phase {m['phase']} report · {PHASES[m['phase']]}"
    logos = []
    if (t := assets.tfnsw()) is not None:
        logos.append(f'<img src="{t}" alt="Transport for NSW">')
    if (u := assets.uts()) is not None:
        logos.append(
            f'<img class="uts" src="{u}" '
            'alt="University of Technology Sydney">'
        )
    foot = f"{m['id']} · {m['title']}".replace('"', "'")
    meta_cells = [
        ("Report", f"{escape(m['id'])}", ""),
        (
            "Status",
            f'<span class="chip {m["status"]}">{m["status"]}</span>',
            "",
        ),
        (
            "Verdict",
            f'<span class="chip v-{_slug(m["verdict"])}">'
            f"{escape(m['verdict'])}</span>",
            "",
        ),
        ("Date", escape(m["date"]), ""),
        ("Commit", f"<code>{escape(m['commit'])}</code>", ""),
        ("Decisions", escape(m["decisions"]), "span3"),
        ("Environment", escape(m["environment"]), "full"),
        ("Evidence", escape(m["evidence"]), "full"),
    ]
    meta_html = "".join(
        f'<div class="{cls}"><dt>{k}</dt><dd>{v}</dd></div>'
        for k, v, cls in meta_cells
    )
    tiles = "".join(
        f'<div class="tile"><b>{escape(v)}</b><span>{escape(lab)}</span></div>'
        for v, lab in report.highlights
    )
    return f"""<!doctype html>
<html lang="en-AU"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(m["id"])} {escape(m["title"])}</title>
<style>{assets.fonts()}{CSS}:root{{--foot:"{escape(foot)}"}}</style>
</head><body class="{m["status"].lower()}">
<header class="top"><div class="eyebrow">{SUBJECT} · {escape(phase)}</div>
<div class="logos">{"".join(logos)}</div></header>
<h1>{escape(m["title"])}</h1>
<p class="sub">{escape(m["subtitle"])}</p>
<dl class="meta">{meta_html}</dl>
<div class="tiles" style="--n:{len(report.highlights)}">{tiles}</div>
<main>{_markdown(report.body)}</main>
</body></html>
"""


# --- PDF --------------------------------------------------------------

CHROME_CANDIDATES = (
    "google-chrome",
    "chromium",
    "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/opt/pw-browsers/chromium",
)


def find_chrome() -> str:
    env = os.environ.get("CHROME_BIN")
    for cand in ((env,) if env else ()) + CHROME_CANDIDATES:
        path = shutil.which(cand) or (cand if Path(cand).is_file() else "")
        if path:
            return path
    raise ReportError("no Chrome found: set CHROME_BIN")


def to_pdf(html: str, out: Path, chrome: str | None = None) -> Path:
    chrome = chrome or find_chrome()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "report.html"
        page.write_text(html, encoding="utf-8")
        cmd = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={tmp}/profile",
            f"--print-to-pdf={out.resolve()}",
            page.as_uri(),
        ]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")  # Chrome refuses root otherwise
        subprocess.run(  # noqa: S603 - fixed args, local file only
            cmd, check=True, capture_output=True, timeout=120
        )
    return out
