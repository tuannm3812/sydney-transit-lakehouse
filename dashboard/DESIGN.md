# Dashboard design

The public site's look, so new pages match. It follows the project decks and poster (`lakehouse-deck/DESIGN.project.md` in the project files) and adapts them for a web page that people scan and operate.

## Concept

A transit departure board read in daylight: a slate ink on a white page with a faint blue-grey ground for figures, one blue for anything you can press, and colour on data only where it carries meaning.

## Tokens (`pages/+layout.svelte`, `evidence.config.yaml`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--ink` | `#14222D` | `#EEF3F6` | headings, figures |
| `--ink-2` | `#2A3843` | `#D3DDE4` | body text |
| `--muted` | `#5B6A76` | `#94A4B0` | labels, notes |
| `--line` | `#DCE3E8` | `#283642` | rules, card borders |
| `--ground` | `#F3F6F8` | `#16212A` | figure cards |
| `--press` | `#32658A` | `#86B4D8` | links, eyebrows (deck blue) |
| `--done` | `#2E6B66` on `#E8F2F0` | `#7CC3BB` | the "ran" figure, passing checks (deck teal) |
| `--watch` | `#896521` on `#F6EFE0` | `#D9B464` | the early-results notice (deck ochre) |

The Evidence theme (`evidence.config.yaml`) sets the same values for its own components.

## Type

Google Sans (OFL, `static/fonts/`, the decks' face) for everything, with Inter and the system UI as fallbacks. Page questions are 1.6 to 2.4 rem bold; section heads 1.35 rem with a rule above; running text at most 68 characters wide; digits tabular in figures and tables.

## Data colour

- Transport modes always use the TfNSW mode colours: Bus `#00B5EF`, Trains `#F6891F`, Metro `#00959A`, Light rail `#EE343F`, Ferries `#5AB031`. Modes not collected yet are grey `#B9C0C7`.
- Every other data encoding uses Viridis (the theme's default palette and colour scale).
- Status colours (teal done, ochre watch, red risk) are never used for data series.

## Page pattern

1. `hide_title: true` and a `.lede`: an eyebrow with the product name, the page's question as the heading, one paragraph of context.
2. Headline figures as one `.figures` row; the lead figure takes the teal ground.
3. One `.notice` at most, for scope or caveats.
4. Sections with a short sentence saying how to read the chart, then the chart or table.
5. Definitions as a `.terms` grid, page links as `.next` cards.
