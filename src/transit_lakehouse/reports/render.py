"""Render a phase report (docs/reports/README.md) to HTML and PDF.

``parse`` splits the front block from the body and checks both against
the standard. ``to_html`` builds one self-contained A4 page: header with
logos, the phase track, the metadata table, numbered sections, the
Summary's blockquote as the answer band, ``chart`` fences as bar charts
and ``flow`` fences as pipeline strips. Tables get the standard colour
format (README section 5). ``to_pdf`` prints the page with headless
Chrome.
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
    "window",
    "commit",
    "environment",
    "decisions",
    "evidence",
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
# As in the deck's phase slides.
PHASES = {
    "0": "Collect first",
    "1": "Build the lakehouse",
    "2": "Reliability marts",
    "3": "Ghost trips and bunching",
    "4": "AI analyst",
}
TITLE_WORDS, SUBTITLE_WORDS = 8, 30
# TfNSW mode colours (deck scripts/colours.py), keyed by feed prefix.
MODE_COLOURS = (
    ("buses", "#00B5EF"),
    ("sydneytrains", "#F6891F"),
    ("nswtrains", "#F6891F"),
    ("metro", "#00959A"),
    ("lightrail", "#EE343F"),
    ("ferries", "#5AB031"),
)
CHART_MODES = (
    ("Bus", "#00B5EF"),
    ("Trains", "#F6891F"),
    ("Metro", "#00959A"),
    ("Light rail", "#EE343F"),
    ("Ferries", "#5AB031"),
)
STATUS_WORDS = {
    "pass": "good",
    "identical": "good",
    "yes": "good",
    "fail": "bad",
    "differs": "bad",
    "no": "bad",
    "warn": "warn",
}
FLOW_KINDS = ("source", "raw", "store", "bronze", "silver", "gold", "check")
SUBJECT = "94693 Big Data Engineering"
BAR = "#3B528B"  # Viridis blue: one series, not a transport mode
NEUTRAL = "#9AA3AD"  # network-wide bars beside mode bars


class ReportError(ValueError):
    """The report does not follow docs/reports/README.md."""


@dataclass
class Report:
    meta: dict[str, str]
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
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if raw.startswith((" ", "\t")):
            raise ReportError(f"front block values fit on one line: {raw}")
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
    unknown = sorted(set(meta) - set(REQUIRED))
    if unknown:
        raise ReportError(f"unknown front block field {', '.join(unknown)}")
    if len(meta["title"].split()) > TITLE_WORDS:
        raise ReportError(f"title is over {TITLE_WORDS} words")
    sub = meta["subtitle"]
    if len(sub.split()) > SUBTITLE_WORDS or not sub.endswith("."):
        raise ReportError(
            f"subtitle is one sentence of at most {SUBTITLE_WORDS} words"
        )
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
    return Report(meta, body, sections)


# --- figures ----------------------------------------------------------


def _number(text: str) -> float:
    return float(text.replace(",", "").split()[0])


def _mode_of(label: str) -> str | None:
    return next((c for p, c in CHART_MODES if label.startswith(p)), None)


def _bar_colour(label: str, by_mode: bool) -> str:
    """Transport modes keep their TfNSW colour and other bars beside them
    are neutral; a chart without modes is one Viridis blue (README 6)."""
    if not by_mode:
        return BAR
    return _mode_of(label) or NEUTRAL


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
    by_mode = any(_mode_of(name) for name, _ in bars)
    longest = max(len(f"{v:,.0f} {unit}") for _, v in bars)
    label_w, row_h, bar_h = 190, 26, 14
    value_w = 14 + 7 * longest  # room for the value label after a bar
    plot_w = 660 - label_w - value_w
    head = 24 if limit else 6
    height = head + row_h * len(bars) + 4
    x = lambda v: label_w + v / top * plot_w  # noqa: E731
    parts = [
        f'<svg class="chart" viewBox="0 0 660 '
        f'{height}" role="img" aria-label="{escape(title)}">'
    ]
    for tick in (0.25, 0.5, 0.75, 1.0):
        tx = label_w + tick * plot_w
        parts.append(
            f'<line class="grid" x1="{tx:.1f}" x2="{tx:.1f}" '
            f'y1="{head - 4}" y2="{height - 4}"/>'
        )
    for i, (name, value) in enumerate(bars):
        y = head + i * row_h
        w = max(x(value) - label_w, 2)
        r = min(4, w / 2)
        parts.append(
            f'<text class="cl" x="{label_w - 10}" y="{y + bar_h - 3}" '
            f'text-anchor="end">{escape(name)}</text>'
            f'<path d="M{label_w},{y} h{w - r:.1f} q{r},0 {r},{r} '
            f'v{bar_h - 2 * r} q0,{r} -{r},{r} h-{w - r:.1f} z" '
            f'fill="{_bar_colour(name, by_mode)}"/>'
            f'<text class="cv" x="{label_w + w + 6:.1f}" '
            f'y="{y + bar_h - 3}">{value:,.0f} {escape(unit)}</text>'
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


def flow_html(source: str) -> str:
    """Draw a ``flow`` fence: one step per line, ``kind | name | note``.

    Kinds colour the step by lakehouse layer (README section 6)."""
    steps = []
    for line in source.strip().splitlines():
        kind, _, rest = line.partition("|")
        name, _, note = rest.partition("|")
        kind = kind.strip()
        if kind not in FLOW_KINDS:
            raise ReportError(
                f"flow step kind must be one of {', '.join(FLOW_KINDS)}"
            )
        steps.append(
            f'<div class="step {kind}"><b>{escape(name.strip())}</b>'
            f"<span>{escape(note.strip())}</span></div>"
        )
    if len(steps) < 2:
        raise ReportError("a flow needs at least two steps")
    return (
        '<div class="flow">' + '<i class="arrow"></i>'.join(steps) + "</div>"
    )


# --- HTML -------------------------------------------------------------


def _mode_colour(code: str) -> str | None:
    for prefix, colour in MODE_COLOURS:
        if code.startswith(prefix):
            return colour
    return None


def _code_inline(_self, tokens, idx, options, env):
    """Feed names get their mode dot; table names their layer tint."""
    text = tokens[idx].content
    colour = _mode_colour(text)
    layer = text.split(".", 1)[0]
    if colour:
        return (
            f'<code class="feed"><i style="background:{colour}"></i>'
            f"{escape(text)}</code>"
        )
    if layer in ("bronze", "silver", "gold") and "." in text:
        return f'<code class="{layer}">{escape(text)}</code>'
    return f"<code>{escape(text)}</code>"


def _style_tables(tokens) -> None:
    """Apply the table colour format (README section 5)."""
    from markdown_it.token import Token

    i = 0
    while i < len(tokens):
        if tokens[i].type != "table_open":
            i += 1
            continue
        end = next(
            j for j in range(i, len(tokens)) if tokens[j].type == "table_close"
        )
        heads = [
            tokens[j + 1].content
            for j in range(i, end)
            if tokens[j].type == "th_open"
        ]
        cls = "t kv" if not any(h.strip() for h in heads) else "t"
        tokens[i].attrSet("class", cls)
        row_open = None
        first_cell = True
        for j in range(i, end):
            tok = tokens[j]
            if tok.type == "tr_open":
                row_open, first_cell = tok, True
            elif tok.type == "td_open":
                inline = tokens[j + 1]
                text = inline.content.strip()
                if first_cell and text.startswith(("All ", "Total")):
                    row_open.attrSet("class", "total")
                first_cell = False
                kind = STATUS_WORDS.get(text.lower())
                if kind:
                    chip = Token("html_inline", "", 0)
                    chip.content = (
                        f'<span class="chip st-{kind}">{escape(text)}</span>'
                    )
                    inline.children = [chip]
                elif (
                    kids := [
                        c
                        for c in inline.children or []
                        if not (c.type == "text" and not c.content)
                    ]
                ) and (kids[0].type, kids[-1].type) == (
                    "strong_open",
                    "strong_close",
                ):
                    tok.attrSet("class", "hl")
        i = end + 1


def _markdown(body: str) -> str:
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    default_fence = md.renderer.rules.get("fence")

    def fence(_self, tokens, idx, options, env):
        tok = tokens[idx]
        info = tok.info.strip()
        if info == "chart":
            return chart_svg(tok.content)
        if info == "flow":
            return flow_html(tok.content)
        return default_fence(tokens, idx, options, env)

    md.add_render_rule("fence", fence)
    md.add_render_rule("code_inline", _code_inline)
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
    _style_tables(tokens)
    html = md.renderer.render(tokens, md.options, {})
    # Keep each subsection's heading with its text and table.
    parts = re.split(r"(?=<h[23][ >])", html)
    return "".join(
        f'<section class="subsec">{p}</section>' if p.startswith("<h3") else p
        for p in parts
    )


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
@page{size:A4;margin:16mm 17mm 18mm;
  @bottom-left{content:var(--foot);font:7.5pt var(--sans);color:#6B7480}
  @bottom-right{content:"Page " counter(page) " of " counter(pages);
    font:7.5pt var(--sans);color:#6B7480}}
:root{--ink:#1B2430;--body:#3A4350;--muted:#6B7480;--hair:#E1E5E9;
  --rule:#C9D0D6;--card:#F4F5F6;--zebra:#F8F9FA;
  --accent:#896521;--accent-fill:#F6F1E5;--accent-line:#E3D3AE;
  --good:#2E6B66;--good-fill:#E3F0EE;--bad:#B3261E;--bad-fill:#FBEAE8;
  --blue:#2D5C88;--blue-fill:#E7EEF5;--band:#1B2430;
  --bronze:#9A5B2A;--bronze-f:#F5E8DC;--silver:#6E7B88;--silver-f:#E9EDF1;
  --gold:#A67C00;--gold-f:#F8EFD2;
  --sans:'Google Sans','Segoe UI',system-ui,-apple-system,sans-serif;
  --mono:'Google Sans Code',ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:#fff;color:var(--body);font:9.6pt/1.5 var(--sans);
  counter-reset:h2}
/* header: course and report name left, logos right */
.top{display:flex;justify-content:space-between;align-items:center;
  border-bottom:2px solid var(--accent);padding-bottom:8px}
.eyebrow{font-size:8.5pt;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted)}
.eyebrow b{color:var(--accent)}
.logos{display:flex;gap:14px;align-items:center}
.logos img{height:30px}
.logos img.uts{height:38px}
h1{color:var(--ink);font-size:23pt;line-height:1.12;margin:16px 0 4px;
  letter-spacing:-.01em}
.sub{font-size:11pt;color:var(--muted);margin:0 0 14px;max-width:150mm}
/* phase track */
.track{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;
  margin:0 0 12px}
.track div{font-size:7.5pt;line-height:1.25;color:var(--muted);
  border-top:4px solid var(--hair);padding-top:4px}
.track b{display:block;font-size:7.5pt;text-transform:uppercase;
  letter-spacing:.05em}
.track .done{border-color:var(--good);color:var(--good)}
.track .now{border-color:var(--blue);color:var(--blue);font-weight:700}
/* metadata table */
table.meta{width:100%;border-collapse:collapse;margin:0 0 16px;
  font-size:8.8pt;border-top:2px solid var(--ink);
  border-bottom:1px solid var(--rule)}
table.meta th{width:17%;text-align:left;font-weight:700;color:var(--accent);
  background:var(--accent-fill);padding:5px 8px;font-size:7.6pt;
  text-transform:uppercase;letter-spacing:.05em;vertical-align:top;
  border-bottom:1px solid #fff}
table.meta td{padding:5px 10px;color:var(--ink);vertical-align:top;
  border-bottom:1px solid var(--hair)}
.chip{display:inline-block;padding:0 8px;border-radius:9px;
  font-size:8pt;font-weight:700;line-height:1.6}
.chip.Final,.chip.v-pass,.chip.st-good{background:var(--good-fill);
  color:var(--good)}
.chip.Interim,.chip.v-pass-with-actions,.chip.st-warn{
  background:var(--accent-fill);color:var(--accent)}
.chip.Draft,.chip.Superseded,.chip.v-info{background:var(--card);
  color:var(--ink)}
.chip.v-fail,.chip.st-bad{background:var(--bad-fill);color:var(--bad)}
body.draft main::before{content:"DRAFT";position:fixed;top:40%;left:12%;
  font:700 120pt var(--sans);color:rgba(27,36,48,.05);
  transform:rotate(-30deg)}
/* headings: section number in a circle, subsections 3.1 */
h2{color:var(--ink);font-size:14pt;margin:22px 0 10px;display:flex;
  align-items:center;gap:10px;counter-increment:h2;counter-reset:h3;
  break-after:avoid;padding-bottom:5px;border-bottom:1px solid var(--hair)}
h2::before{content:counter(h2);flex:0 0 24px;height:24px;
  border-radius:50%;background:var(--accent);color:#fff;font-size:11pt;
  font-weight:700;display:flex;align-items:center;justify-content:center}
h3{color:var(--ink);font-size:10.8pt;margin:16px 0 6px;
  counter-increment:h3;break-after:avoid}
h3::before{content:counter(h2) "." counter(h3);color:var(--accent);
  margin-right:8px}
p{margin:0 0 8px}
ul,ol{margin:0 0 8px;padding-left:18px}
li{margin:0 0 4px}
li::marker{color:var(--accent)}
strong{color:var(--ink)}
a{color:inherit}
p code,li code,td code{white-space:nowrap}
code{font:8.4pt var(--mono);background:var(--card);padding:0 3px;
  border-radius:3px;color:var(--ink)}
code.feed i{display:inline-block;width:7px;height:7px;border-radius:50%;
  margin:0 4px 0 1px;vertical-align:1px}
code.bronze{background:var(--bronze-f);color:var(--bronze)}
code.silver{background:var(--silver-f);color:#46525E}
code.gold{background:var(--gold-f);color:var(--gold)}
pre{background:var(--card);padding:8px 10px;border-radius:6px;
  white-space:pre-wrap;break-inside:avoid;font-size:8.2pt}
pre code{background:none;padding:0}
blockquote.answer{margin:4px 0 12px;background:var(--band);color:#fff;
  border-radius:6px;padding:12px 16px 12px 18px;font-size:11pt;
  line-height:1.45;border-left:5px solid var(--accent)}
blockquote.answer p{margin:0}
blockquote.answer strong{color:#F3D9A4}
blockquote:not(.answer){margin:0 0 8px;padding:6px 12px;
  border-left:3px solid var(--accent);background:var(--accent-fill)}
/* tables: tinted header, zebra rows, total rows, highlighted cell */
table.t{border-collapse:collapse;width:100%;margin:4px 0 10px;
  font-size:8.6pt;font-variant-numeric:tabular-nums;break-inside:avoid}
table.t th{text-align:left;color:var(--ink);font-weight:700;
  background:var(--accent-fill);border-bottom:1.5px solid var(--accent);
  padding:5px 8px;font-size:8pt}
table.t td{border-bottom:1px solid var(--hair);padding:4px 8px;
  vertical-align:top}
table.t tbody tr:nth-child(even) td{background:var(--zebra)}
table.t tr{break-inside:avoid}
table.t tr.total td{font-weight:700;color:var(--ink);background:#fff;
  border-top:1.5px solid var(--ink);border-bottom:none}
table.t td.hl{background:var(--accent-fill)!important;
  box-shadow:inset 3px 0 0 var(--accent)}
table.kv thead{display:none}
table.kv{border-top:2px solid var(--ink)}
table.kv td:first-child{width:30%;color:var(--muted);font-weight:700;
  font-size:8pt}
section.subsec{break-inside:avoid}
/* figures */
.fig{margin:8px 0 12px;break-inside:avoid}
.fig figcaption{font-size:8.6pt;font-weight:700;color:var(--ink);
  margin-bottom:4px}
.chart{width:100%;height:auto}
.chart .cl{font:10.5px var(--sans);fill:#3A4350}
.chart .cv{font:700 10.5px var(--sans);fill:#1B2430}
.chart .base{stroke:#89949D;stroke-width:1}
.chart .grid{stroke:#E1E5E9;stroke-width:1}
.chart .lim{stroke:#B3261E;stroke-width:1.5;stroke-dasharray:4 3}
.chart .lim-t{fill:#B3261E;font-weight:700}
.flow{display:flex;align-items:stretch;margin:8px 0 12px;
  break-inside:avoid}
.flow .step{flex:1;border-radius:6px;padding:7px 9px;font-size:7.8pt;
  line-height:1.3;border-top:4px solid}
.flow .step b{display:block;font-size:8.6pt;color:var(--ink);
  margin-bottom:2px}
.flow .step span{color:var(--body)}
.flow .arrow{flex:0 0 16px;align-self:center;height:0;
  border-top:1.5px solid var(--rule);position:relative}
.flow .arrow::after{content:"";position:absolute;right:0;top:-5px;
  border:4px solid transparent;border-left:6px solid var(--rule)}
.flow .source{background:var(--card);border-color:#89949D}
.flow .raw{background:var(--card);border-color:var(--ink)}
.flow .store{background:var(--blue-fill);border-color:var(--blue)}
.flow .bronze{background:var(--bronze-f);border-color:var(--bronze)}
.flow .silver{background:var(--silver-f);border-color:var(--silver)}
.flow .gold{background:var(--gold-f);border-color:var(--gold)}
.flow .check{background:var(--good-fill);border-color:var(--good)}
"""


def _track(phase: str) -> str:
    cells = []
    for num, name in PHASES.items():
        state = "now" if num == phase else "done" if num < phase else "later"
        cells.append(
            f'<div class="{state}"><b>Phase {num}</b>{escape(name)}</div>'
        )
    return '<div class="track">' + "".join(cells) + "</div>"


def to_html(report: Report, assets: Assets | None = None) -> str:
    assets = assets or ReportAssets(None)
    m = report.meta
    logos = []
    if (t := assets.tfnsw()) is not None:
        logos.append(f'<img src="{t}" alt="Transport for NSW">')
    if (u := assets.uts()) is not None:
        logos.append(
            f'<img class="uts" src="{u}" '
            'alt="University of Technology Sydney">'
        )
    foot = f"{m['id']} · {m['title']}".replace('"', "'")
    status = f'<span class="chip {m["status"]}">{m["status"]}</span>'
    verdict = (
        f'<span class="chip v-{_slug(m["verdict"])}">'
        f"{escape(m['verdict'])}</span>"
    )
    phase = f"Phase {m['phase']} · {PHASES[m['phase']]}"
    rows = [
        ("Report", escape(m["id"]), "Phase", escape(phase)),
        ("Status", status, "Verdict", verdict),
        (
            "Date",
            escape(m["date"]),
            "Commit",
            f"<code>{escape(m['commit'])}</code>",
        ),
        ("Data window", escape(m["window"]), None, None),
        ("Environment", escape(m["environment"]), None, None),
        ("Decisions", escape(m["decisions"]), None, None),
        ("Evidence", escape(m["evidence"]), None, None),
    ]
    meta_html = "".join(
        f"<tr><th>{k1}</th><td>{v1}</td><th>{k2}</th><td>{v2}</td></tr>"
        if k2
        else f'<tr><th>{k1}</th><td colspan="3">{v1}</td></tr>'
        for k1, v1, k2, v2 in rows
    )
    return f"""<!doctype html>
<html lang="en-AU"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(m["id"])} {escape(m["title"])}</title>
<style>{assets.fonts()}{CSS}:root{{--foot:"{escape(foot)}"}}</style>
</head><body class="{m["status"].lower()}">
<header class="top"><div class="eyebrow">{SUBJECT} ·
<b>Report {escape(m["id"])}</b></div>
<div class="logos">{"".join(logos)}</div></header>
<h1>{escape(m["title"])}</h1>
<p class="sub">{escape(m["subtitle"])}</p>
{_track(m["phase"])}
<table class="meta">{meta_html}</table>
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
