# Report standard

Every report in this folder follows the same structure and is rendered to PDF with the same template. Phase 0 and Phase 1 reports look and read alike, so they can sit side by side in a submission.

The Markdown file is the source and the evidence. The PDF is generated from it and is not committed:

```
pip install -e ".[report]"
python -m transit_lakehouse.reports docs/reports/<file>.md --out <dir>
```

The renderer needs a Chrome or Chromium binary (`CHROME_BIN`, or the usual install paths). Set `REPORT_ASSET_DIR` to the deck kit's `assets` folder to embed Google Sans and the UTS and TfNSW logos; without it the PDF uses system fonts and no logos.

## 1. Kinds of report

| Kind | Id | Example | Written |
|---|---|---|---|
| Phase report | `R<phase>-<nn>` | `R1-01` Light worker dry run | by hand, at a milestone or gate |
| Generated evidence | none | `quota-24h.md`, `capture-24h-20261118.txt` | by a `checks` command |

Generated evidence files keep their generated layout; a phase report cites them. Only phase reports carry the front block below and are rendered to PDF.

File name: `<topic>-<YYYYMMDD>.md`, dated by the day the report is finalised (UTC). Ids are never reused; a replacement report gets the next number and says which one it replaces.

## 2. Front block (required)

Every phase report starts with this block, in this order. The renderer refuses a report with a missing field.

```
---
id: R1-01
title: Light worker dry run on real data
subtitle: One sentence on what was tested and where
phase: 1
status: Final            # Draft | Interim | Final
date: 2026-11-19         # finalised, UTC
commit: 09e726f          # code the numbers came from
environment: Where it ran (host, OS, Python, key library versions)
decisions: D-027, D-028  # rows this report rests on or changes
evidence: docs/reports/…, data/… (gitignored, on host)
highlights:
  - 30 / 30 | table-hours identical after replay
  - 757 MB | peak memory, under the 900 MB cap
---
```

- `highlights` holds two to four headline numbers as `value | label`. They appear as tiles on the first page and each must also appear in the body.
- `phase` is a number. The renderer prints the phase name from section 6.

## 3. Sections (fixed order, numbered by the renderer)

Use exactly these `##` headings. Leave none out; write "None." when a section has nothing.

1. **Summary.** Opens with one blockquote that answers the report's question in one or two sentences (the answer band). Then three to five bullets, each with a number.
2. **Scope and method.** What ran, on which data window, at which commit, with which command. State what was out of scope.
3. **Results.** Tables first, one `###` per experiment or run. Each table is followed by at most one paragraph.
4. **Findings.** What the results mean, one bold lead sentence per bullet. Each finding cites a table above, a decision row (`D-NNN`) or an evidence file.
5. **Limits.** What the numbers do not show (machine, sample size, missing feeds).
6. **Next steps.** What happens next and what waits for the gate or for Mike.

Optional, after these: `## Appendix` with commands and raw output.

## 4. Writing rules

- **Every number traces to evidence.** A measured number names its run or file; an estimate says "estimated" and how it was scaled.
- **Times are UTC**, with Sydney local time in brackets where it helps: `21:00 UTC (8 am Sydney)` (D-006).
- Numbers use thousands separators (`5,632,675`), SI units with a space (`757 MB`, `9.8 s`), and the same unit down a column.
- Numeric table columns are right-aligned (`---:`). Bold at most one cell per table: the headline number.
- Code, paths, tables and commands in backticks: `silver.stop_prediction`, `run --once`.
- Short sentences, plain words, no em-dashes. Say "object storage", not a vendor name, unless the vendor is the point.
- Never include the API key, `.env` contents, IP addresses or personal email addresses.

## 5. Figures

A figure is optional and comes after the table that holds its numbers, never instead of it. Write it as a fenced `chart` block; GitHub shows the source, the PDF draws a horizontal bar chart:

````
```chart
title: Peak memory by run (MB)
unit: MB
limit: 900 | container cap
Rebuild: 757
Replay T16–T22: 743
```
````

One series per chart, one colour (Viridis blue), a dashed reference line for a limit. Transport modes keep their TfNSW colours wherever a mode is shown.

## 6. Template (fixed, do not restyle per report)

| Element | Rule |
|---|---|
| Page | A4 portrait, 18 mm margins, white |
| Type | Google Sans 10 pt body, 22 pt title; Google Sans Code for code |
| Header (page 1) | eyebrow `94693 Big Data Engineering · Phase N report`, logos right (TfNSW, then UTS) |
| Front block | metadata grid under the title, highlight tiles below it |
| Answer band | the Summary's blockquote, dark band |
| Accent | ochre `#896521` for section numbers and rules, in every phase |
| Status chip | Draft grey, Interim ochre, Final teal |
| Footer | report id and title left, `page n of N` right |

Phase names, as in the deck: 0 Collect first, 1 Build the lakehouse. Later phases are added here when they start.
