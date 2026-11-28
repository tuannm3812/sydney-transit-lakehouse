---
title: About
hide_title: true
sidebar_position: 6
---

```sql totals
select
    min(service_date) as first_day,
    max(service_date) as last_day,
    count(*) as service_days,
    sum(trips_judged) as trips_judged,
    strftime(min(service_date), '%-d %b %Y') as first_label,
    strftime(max(service_date), '%-d %b %Y') as last_label,
    format('{:,}', sum(trips_judged)::bigint) as judged_label
from transit.service_days
```

```sql build
select
    max(run_at) as built_at,
    strftime(max(run_at)::timestamp, '%-d %b %Y') as built_label
from transit.quality
```

<div class="lede">
  <span class="eyebrow">About</span>
  <h1 class="question">How this site works</h1>
  <p class="context">When the timetable says a trip runs, does the live feed show it running, and do the vehicles keep their spacing? This is an independent data engineering project, not an official Transport for NSW service. For journey planning, use Transport for NSW's own apps.</p>
</div>

{#if totals.length > 0 && totals[0].service_days > 0}
<p>This edition covers {totals[0].service_days} service days, {totals[0].first_label} to {totals[0].last_label}, and {totals[0].judged_label} checked trips. It was built on {build[0].built_label} (UTC).</p>
{/if}

## The data

- **Live feeds**: Transport for NSW's GTFS-realtime vehicle positions and trip updates for buses, Sydney Trains, Metro, Inner West light rail and ferries, and the statewide service alerts. A collector polls vehicle positions every 15 seconds (ferries 30), trip updates every 30 and alerts every 60, and keeps every distinct snapshot, untouched. Buses, trains and metro have been collected since 17 November 2026, light rail and ferries since 18 November.
- **Timetable**: Transport for NSW's complete static GTFS timetable, fetched as it is republished, so each day is judged against the timetable in effect that day.
- **Source and licence**: Transport for NSW Open Data Hub, under Creative Commons Attribution 4.0. Map tiles © OpenStreetMap contributors.

## How it is built

Raw snapshots land in object storage by feed and hour. A small worker decodes them into Apache Iceberg tables (vehicle positions, stop predictions, trip states, alerts and the timetable), and dbt on DuckDB builds the marts this site reads. The site itself is static: it reads a public extract of those marts and nothing else. The code, the decisions log and every report are in the [GitHub repository](https://github.com/tuannm3812/sydney-transit-lakehouse).

## How a trip is judged

1. Each realtime trip is matched to the timetable on its trip id and service day.
2. A trip is judged only if its operator published live data that day, and the feed was captured from ten minutes before its start until three hours after its scheduled end. A gap in our own collection makes a trip unjudged, never a ghost.
3. A judged trip that no trip update or vehicle position ever named is **never seen**. One that left the feed with more than two stops to go, more than ten minutes before its scheduled end, **vanished**. Everything else **ran**, or was cancelled in the feed.
4. Bunching compares each pair of consecutive vehicles at each stop with the gap the timetable planned between the same two trips. A gap under a third of the plan is bunched, and a pair bunched at three or more stops is a **bunch**. A bus that overtakes the one ahead counts.

## Limits

- **A few days are not a pattern.** Rankings of routes and stops need weeks of data before they are fair. Every table shows how many trips or gaps a figure rests on.
- **Never seen is not proof.** A bus can run with a broken tracker. Every report hand-checks a seeded sample of flagged trips against the raw snapshots; in the first two days, none of the 40 trips checked was lost by the pipeline.
- **Only what the feeds carry.** Operators without live data are not judged. Sydney Trains trips are not judged yet, and light rail is not in the bunching radar, because those feeds lack a field the checks need.
- **Times are Sydney time** on the charts. Service days follow the timetable, so a trip after midnight belongs to the day before.
