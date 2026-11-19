# Report standard

Every phase report in this folder follows the same structure and is rendered to PDF with the same template, so Phase 0 and Phase 1 reports read and look alike.

The Markdown file is the source and the evidence. The PDF is generated from it and is not committed:

```
pip install -e ".[report]"
python -m transit_lakehouse.reports docs/reports/<file>.md --check   # rules only
python -m transit_lakehouse.reports docs/reports/<file>.md --out <dir>
```

The renderer needs Chrome or Chromium (`CHROME_BIN`, or the usual install paths). Set `REPORT_ASSET_DIR` to a folder laid out like the deck kit's `assets` (`fonts/`, `brand/uts-logo.svg`, `project/tfnsw/tfnsw-logo.png`) to embed Google Sans and the logos; use the full Google Sans files, because the deck's subset fonts miss some characters. `tests/test_reports.py` checks every phase report in this folder against these rules.

## 1. Kinds of report

| Kind | Id | Example | Written |
|---|---|---|---|
| Phase report | `R<phase>-<nn>` | `R1-01` Light worker dry run | by hand, at a milestone or a gate |
| Generated evidence | none | `quota-24h.md`, `capture-24h-20261118.txt` | by a `checks` command |

Generated evidence keeps its generated layout; phase reports cite it. Only phase reports carry the front block and become PDFs.

- File name: `<topic>-<YYYYMMDD>.md`, dated the day the report is finalised (UTC).
- Ids are never reused. A report marked Final is not rewritten: a correction or rerun is a new report that names the one it supersedes, and the old one's status becomes Superseded.

## 2. Title and subtitle

| | Rule | Good | Not |
|---|---|---|---|
| Title | What was tested or measured, at most 8 words, sentence case. No id, phase, date or result. | Light worker dry run on real data | Phase 1 report: worker works! |
| Subtitle | One sentence, at most 30 words, ending in a full stop: what was done, on which data, where. No numbers; numbers go in the Summary. | The live collector's capture rate, API quota, data volume and first offload, two days into the run. | 100% capture and 46% quota. |

The renderer refuses a longer title or subtitle. Below the front block, repeat the title as a `#` line so the file reads well on GitHub; the PDF prints it once.

## 3. Front block and metadata

Every phase report starts with this block. Each field is one line; the renderer refuses a missing or unknown field. Each field is printed once, in one place:

| Field | Shown in | What to write |
|---|---|---|
| `id` | header line and footer | `R<phase>-<nn>` |
| `phase` | phase track (not repeated in the metadata) | the number only; the name is added (section 7) |
| `status` | facts strip, footer | `Draft`, `Interim`, `Final` or `Superseded` |
| `verdict` | facts strip | `Pass`, `Pass with actions`, `Fail` or `Info` (no gate decided) |
| `date` | facts strip, footer | day finalised, `YYYY-MM-DD`, UTC |
| `commit` | facts strip | short hash of the code the numbers came from; several with what each covers |
| `window` | metadata lines: Data window | the UTC period of the data measured, not when the report was written |
| `environment` | metadata lines: Environment | where it ran: host, OS, Python and key library versions |
| `decisions` | metadata lines: Decisions | the `D-NNN` rows the report rests on or changes |
| `evidence` | metadata lines: Evidence | files and folders behind the numbers; gitignored ones say where they live |

The facts strip is one row of four short values (status, verdict, date, commit). The metadata lines below it are plain label and value rows with hairlines, no fills.

```
---
id: R1-01
title: Light worker dry run on real data
subtitle: One sentence on what was tested, on which data and where.
phase: 1
status: Final
verdict: Pass with actions
date: 2026-11-19
window: 17 Nov 2026, 16:00 to 17:00 and 21:00 to 22:00 UTC
commit: 09e726f
environment: Mike's Mac (Apple silicon, macOS), Python 3.14, pyiceberg 0.12
decisions: D-027, D-028
evidence: hour tars from the offload bucket; data/lakehouse on the Mac
---
```

## 4. Sections

Use exactly these `##` headings, in this order. Write "None." when a section has nothing.

1. **Summary.** One blockquote that answers the report's question in one or two sentences (the answer band), then three to five bullets. Each bullet opens with a bold sentence, carries one number and points to the section that holds it.
2. **Scope and method.** What ran, on which data, at which commit, with which command. One `flow` figure of the path the data takes. Say what was out of scope.
3. **Results.** One `###` per experiment or run. Open with a line naming the commit (and decision), then the table, then at most one paragraph and one chart.
4. **Findings.** What the results mean. One bold lead sentence per bullet, each citing a section, a `D-NNN` row or an evidence file.
5. **Limits.** What the numbers do not show.
6. **Next steps.** What happens next, what waits for the gate, and what needs Mike.

Optional, last: `## Appendix` with commands and raw output.

Numbering is added by the template: a section number sits in a filled circle (**1**, **2**, ...) and subsections are numbered `3.1`, `3.2` with no trailing dot. Do not type numbers into headings. Refer to them in the text as "section 3.4".

## 5. Tables

Every table follows one colour format, applied by the template. Authors only follow these Markdown rules:

| Element | Markdown | Printed as |
|---|---|---|
| Header row | the first row | light ochre fill, bold ink, ochre rule below |
| Body rows | plain rows | white and light grey stripes |
| Total row | first cell starts with `All ` or `Total` | bold, ink rule above, no stripe |
| Headline cell | the whole cell in `**bold**`, at most one per table, never in a total row or a scaled column | light ochre fill, bold, no border |
| Colour scale | the column header in `*italics*` | cells shaded light to dark blue by value, one hue (Viridis blue); a thin gradient under the header marks the column |
| Status cell | exactly `Pass`, `Identical`, `Yes`, `Fail`, `Differs`, `No` or `Warn` | teal, red or ochre chip |
| Feed name | `` `buses_tripupdates` `` | TfNSW mode-colour dot before the name |
| Table name | `` `silver.trip_status` `` | tinted bronze, silver or gold by layer |
| Key and value table | leave both header cells empty | grey labels on the left, no header row |

Colour scales:

- Use a scale only where comparing magnitudes down a column is the point (volume by feed, peak memory by run, share changed). At most two scaled columns per table.
- The scale runs from the column's smallest to largest value in five steps; when the largest is 20 times the smallest or more it uses a log scale, so one large row does not wash out the rest.
- Total rows and cells without a number are not shaded. Status colours (teal, red, ochre) are never used as a scale.

Content rules:

- The first column names the row (feed, run, metric); later columns are measurements.
- Right-align numeric columns (`---:`) and use one unit per column, written in the cell (`757 MB`, `9.8 s`).
- Thousands separators (`5,632,675`); `n/a` for a value that cannot exist, `–` for one not measured.
- Times in UTC, with Sydney time in a separate column or in brackets where it helps (D-006).
- Order rows by the story: largest first for volumes, time order for runs.

## 6. Figures

A figure comes after the table that holds its numbers, never instead of it.

**Bar chart.** A fenced `chart` block; GitHub shows the source, the PDF draws it:

````
```chart
title: Peak memory by run at 09e726f
unit: MB
limit: 900 | container cap
Rebuild: 757
Timetable: 638
```
````

One series. Bars whose label starts with a mode (Bus, Trains, Metro, Light rail, Ferries) take the TfNSW mode colour, and the other bars beside them are neutral grey; a chart with no modes is one Viridis blue. `limit` draws a dashed red line.

**Flow strip.** A fenced `flow` block, one step per line as `kind | name | note`. The kind sets the colour: `source` grey, `raw` ink, `store` blue (object storage), `bronze`, `silver`, `gold` (lakehouse layers), `check` teal.

````
```flow
raw | Hour tars | from object storage
bronze | bronze.snapshot | one row per raw file
check | Verify | replay compared with the live build
```
````

## 7. Template (fixed, do not restyle per report)

| Element | Rule |
|---|---|
| Page | A4 portrait, white, Google Sans 9.6 pt body, Google Sans Code for code |
| Header | `94693 Big Data Engineering · Report R1-01` left, TfNSW then UTS logo right, ochre rule below |
| Title block | title 23 pt, subtitle in grey, then the phase track: all phases, done in teal, this phase in blue, later in grey |
| Metadata | facts strip (status and verdict as chips, date, commit), then metadata lines with grey labels |
| Answer band | the Summary's blockquote, plain dark band |
| Accent | ochre `#896521` for numbers, rules and table headers in every phase |
| Chips | Final and Pass teal; Interim, Pass with actions and Warn ochre; Fail red; Draft, Superseded and Info grey |
| Footer | every page: `<id> · <title> · <status> · <date> · Data © State of NSW (Transport for NSW)` left, on one line, `Page n of N` right, small grey. No running header: the footer already names the report. |
| Draft | a faint DRAFT watermark |

Phase names, as on the deck's phase slides: 0 Collect first, 1 Build the lakehouse, 2 Reliability marts, 3 Ghost trips and bunching, 4 AI analyst.

Footer rules: the footer carries only the id, title, status, date, data credit and page number, built from the front block, never typed by hand. It never holds a URL, a person's name or a commit. Status and date in the footer always match the front block, so a printed page can be dated on its own.

## 8. Writing rules

- **Every number traces to evidence.** A measured number names its run or file; an estimate says "estimated" and how it was scaled.
- Short sentences and plain words; no em-dashes. Say "object storage", not a vendor name, unless the vendor is the point.
- Code, paths, tables and commands in backticks.
- Bold only the lead sentence of a bullet, the headline number in the answer band, and one headline cell per table.
- Never include the API key, `.env` contents, IP addresses or email addresses. The renderer refuses a report that looks like it does.
