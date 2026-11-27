# Antigravity review, 2026-11-27

Antigravity CLI (agy), Gemini 4 Hydrogen, plan mode (read-only), on 06059d3..154e8e8 (PR #47, the public dashboard pages). Run at Mike's request; the full text is the [PR comment](https://github.com/tuannm3812/sydney-transit-lakehouse/pull/47#issuecomment-7220770847). The findings are summarised below as written.

## What happened to the findings

| Finding | Status |
|---|---|
| 1. Bunching map sums stop-hours the extract already cut at 3 gaps, and `spots` and `routes` group by route short name only (A) | Fixed in PR #47: the map text says only stop-hours with 3 or more gaps count, and both queries group by region and route id; the routes table shows the region |
| 2. Ghost-page confidence note describes the never-seen rule only; vanished trips are always medium (A) | Fixed in PR #47: the note says so |
| 3. Overview overstates scope and cadence; about page omits the 3-hour grace; early-results note shows with no days (A) | Fixed in PR #47: judged modes named, cadence per feed (15 s positions, ferries 30 s, 30 s trip updates, 60 s alerts) and start dates, 10 minutes before to 3 hours after, and the note needs at least one day |
| 4. Config comment names a `mode_colour` column that does not exist; `coach` and `nswtrains` not handled the same everywhere (A) | Fixed in PR #47: comment corrected; both map to a neutral grey in the source queries, the pages and the replay viewer |

Decisions to revisit, not part of this PR: I-20 and I-21 before the backfill (gate-day thread, D-056), the missing D-050 id in the decisions log, and O-04 (trains and light rail headways).

## Verdict

The change is limited to `dashboard/`; it introduces no pipeline or gold-layer correctness bugs and keeps the D-051 fix (`spots` reads `bunching_hotspots`). All six findings of the second 25 Nov review hold as closed.

## Findings

| # | Severity | File | Finding |
|---|---|---|---|
| 1 | A | `dashboard/pages/bunching.md` | `spots` sums `bunching_hotspots` across directions and hours with `having sum(headways) >= 10`, but `publish.py` already keeps only cells with `headways >= 3`, so the prose overstates what a circle counts; `spots` and `routes` group by `route_short_name` without region or route id, so routes sharing a short name merge. |
| 2 | A | `dashboard/pages/ghosts.md` | The three-tier confidence rule applies only to never-seen trips; `fct_trip_outcome` gives vanished trips `medium` always. |
| 3 | A | `dashboard/pages/index.md`, `about.md` | "Every timetabled trip" and "every 15 to 30 seconds since 17 November" overstate scope and cadence (D-019, D-025, O-04); the about page omits the 3-hour grace (`outcome_grace_minutes`); the early-results check passes with 0 service days. |
| 4 | A | `dashboard/evidence.config.yaml`, `sources/transit/*.sql`, `static/replay/index.html` | Stale config comment (`mode_colour`); `coach` and `nswtrains` handled differently across the source queries, the pages and the replay viewer. |
