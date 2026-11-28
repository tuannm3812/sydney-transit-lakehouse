<script>
	import '@evidence-dev/tailwind/fonts.css';
	import '../app.css';
	import { EvidenceDefaultLayout } from '@evidence-dev/core-components';
	export let data;
</script>

<!--
  Site design (dashboard/DESIGN.md). Layout concept: a transit
  departure board read in daylight: a slate ink on a faint blue-grey
  ground, one blue for what you can press, TfNSW mode colours only on
  mode data, Viridis for every other data colour.
-->
<EvidenceDefaultLayout
	{data}
	title="Sydney timetable honesty"
	githubRepo="https://github.com/tuannm3812/sydney-transit-lakehouse"
	homePageName="Overview"
	maxWidth={1180}
>
	<slot slot="content" />
</EvidenceDefaultLayout>

<style>
	@font-face {
		font-family: 'Google Sans';
		src: url('/fonts/GoogleSans-Regular.woff2') format('woff2');
		font-weight: 400;
		font-display: swap;
	}
	@font-face {
		font-family: 'Google Sans';
		src: url('/fonts/GoogleSans-Bold.woff2') format('woff2');
		font-weight: 700;
		font-display: swap;
	}

	:global(:root) {
		--ink: #14222d;
		--ink-2: #2a3843;
		--muted: #5b6a76;
		--line: #dce3e8;
		--ground: #f3f6f8;
		--card: #ffffff;
		--press: #32658a;
		--done: #2e6b66;
		--done-ground: #e8f2f0;
		--watch: #896521;
		--watch-ground: #f6efe0;
		--font-ui: 'Google Sans', 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
	}
	:global(html[data-theme='dark']) {
		--ink: #eef3f6;
		--ink-2: #d3dde4;
		--muted: #94a4b0;
		--line: #283642;
		--ground: #16212a;
		--card: #0f171e;
		--press: #86b4d8;
		--done: #7cc3bb;
		--done-ground: #17302d;
		--watch: #d9b464;
		--watch-ground: #2c2617;
		color-scheme: dark;
	}

	:global(body),
	:global(article),
	:global(.markdown) {
		font-family: var(--font-ui);
	}
	:global(article h1.title),
	:global(.markdown h1) {
		font-size: 2rem;
		line-height: 1.15;
		letter-spacing: -0.01em;
		text-wrap: balance;
		color: var(--ink);
	}
	:global(.markdown h2) {
		margin-top: 2.75rem;
		padding-top: 1.25rem;
		border-top: 1px solid var(--line);
		font-size: 1.35rem;
		text-wrap: balance;
		color: var(--ink);
	}
	:global(.markdown p),
	:global(.markdown li) {
		max-width: 68ch;
		color: var(--ink-2);
	}
	:global(table) {
		font-variant-numeric: tabular-nums;
	}

	/* Page opener: an eyebrow naming the product, the question, and one
	   line of context. Used at the top of every page. */
	:global(.lede) {
		display: grid;
		gap: 0.5rem;
		margin: 0.25rem 0 1.5rem;
	}
	:global(.lede .eyebrow) {
		font-size: 0.75rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--press);
	}
	:global(.lede .question) {
		margin: 0;
		font-size: clamp(1.6rem, 1.2rem + 1.6vw, 2.4rem);
		line-height: 1.1;
		font-weight: 700;
		letter-spacing: -0.015em;
		text-wrap: balance;
		color: var(--ink);
	}
	:global(.lede .context) {
		margin: 0;
		max-width: 62ch;
		font-size: 1.05rem;
		line-height: 1.55;
		color: var(--muted);
	}

	/* The headline figures: one row of equal cards. */
	:global(.figures) {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr));
		gap: 0.75rem;
		margin: 1.25rem 0 0.5rem;
	}
	:global(.figure) {
		display: grid;
		align-content: start;
		gap: 0.3rem;
		min-width: 0;
		padding: 0.9rem 1rem 1rem;
		background: var(--ground);
		border-radius: 10px;
	}
	:global(.figure .label) {
		font-size: 0.8rem;
		color: var(--muted);
	}
	:global(.figure .value) {
		font-size: 1.85rem;
		font-weight: 700;
		line-height: 1.1;
		font-variant-numeric: tabular-nums;
		color: var(--ink);
	}
	:global(.figure .note) {
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
		color: var(--muted);
	}
	:global(.figure.lead) {
		background: var(--done-ground);
	}
	:global(.figure.lead .value) {
		color: var(--done);
	}

	/* Status chips: state in form as well as colour. */
	:global(.chips) {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin: 0.75rem 0 0;
		padding: 0;
		list-style: none;
	}
	:global(.chip) {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		padding: 0.25rem 0.7rem 0.25rem 0.55rem;
		border: 1px solid var(--line);
		border-radius: 999px;
		font-size: 0.85rem;
		color: var(--ink-2);
	}
	:global(.chip .dot) {
		width: 0.65rem;
		height: 0.65rem;
		border-radius: 50%;
		background: var(--c, var(--muted));
	}
	:global(.chip.later) {
		border-style: dashed;
		color: var(--muted);
	}
	:global(.chip.later .dot) {
		background: transparent;
		border: 1.5px solid var(--c, var(--muted));
	}

	/* One quiet notice per page, for scope and caveats. */
	:global(.notice) {
		margin: 1.25rem 0;
		padding: 0.8rem 1rem;
		max-width: 72ch;
		border-left: 3px solid var(--watch);
		background: var(--watch-ground);
		border-radius: 0 8px 8px 0;
		color: var(--ink-2);
		line-height: 1.5;
	}
	:global(.notice b) {
		color: var(--ink);
	}

	/* Glossary as a definition grid instead of a bullet list. */
	:global(.terms) {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
		gap: 0.75rem 1.5rem;
		margin: 1rem 0;
	}
	:global(.terms div) {
		min-width: 0;
	}
	:global(.terms dt) {
		font-weight: 700;
		color: var(--ink);
	}
	:global(.terms dd) {
		margin: 0.2rem 0 0;
		color: var(--ink-2);
		line-height: 1.5;
	}

	/* Page links on the overview. */
	:global(.next) {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
		gap: 0.75rem;
		margin: 1rem 0;
	}
	:global(.next a) {
		display: grid;
		gap: 0.25rem;
		padding: 0.9rem 1rem;
		border: 1px solid var(--line);
		border-radius: 10px;
		text-decoration: none;
		color: var(--ink-2);
		transition: border-color 0.15s ease;
	}
	:global(.next a:hover),
	:global(.next a:focus-visible) {
		border-color: var(--press);
	}
	:global(.next a b) {
		color: var(--press);
	}

	/* Every table that could outgrow a phone scrolls in its own box. */
	:global(.markdown .table-container),
	:global(.markdown div:has(> table)) {
		overflow-x: auto;
		max-width: 100%;
	}

	@media (prefers-reduced-motion: reduce) {
		:global(.next a) {
			transition: none;
		}
	}
</style>
