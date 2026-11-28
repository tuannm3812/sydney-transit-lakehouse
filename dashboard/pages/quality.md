---
title: Data quality
hide_title: true
sidebar_position: 5
---

<div class="lede">
  <span class="eyebrow">Data quality</span>
  <h1 class="question">Can you trust these numbers?</h1>
  <p class="context">Every number on this site comes from a build that runs these checks first. A failed check is shown here, not hidden.</p>
</div>

```sql summary
select
    max(run_at) as built_at,
    count(*) filter (where resource_type = 'test') as tests,
    count(*) filter (where resource_type = 'test' and status = 'pass') as tests_passed,
    count(*) filter (where resource_type = 'model') as models
from transit.quality
```

<div class="figures">
  <div class="figure lead">
    <span class="label">Checks passed</span>
    <span class="value"><Value data={summary} column=tests_passed fmt=num0 /></span>
    <span class="note">of <Value data={summary} column=tests fmt=num0 /> run</span>
  </div>
  <div class="figure">
    <span class="label">Tables built</span>
    <span class="value"><Value data={summary} column=models fmt=num0 /></span>
    <span class="note">by dbt on DuckDB</span>
  </div>
  <div class="figure">
    <span class="label">Last build</span>
    <span class="value"><Value data={summary} column=built_at fmt="d mmm" /></span>
    <span class="note"><Value data={summary} column=built_at fmt="H:MM AM/PM" /> UTC</span>
  </div>
</div>

## Realtime trips that match the timetable

```sql match
select service_date, mode_name, realtime_trips, matched_trips, match_share
from transit.trip_match
order by service_date desc, mode_name
```

<DataTable data={match} emptySet=pass emptyMessage="No rows for this selection yet" rows=20>
  <Column id=service_date />
  <Column id=mode_name title="Mode" />
  <Column id=realtime_trips fmt=num0 />
  <Column id=matched_trips fmt=num0 />
  <Column id=match_share title="Matched" fmt=pct1 />
</DataTable>

## Checks that failed

```sql failed
select node as check_name, failures as failing_rows
from transit.quality
where status not in ('pass', 'success')
order by node
```

{#if failed.length > 0}

<DataTable data={failed}>
  <Column id=check_name title="Check" wrap=true />
  <Column id=failing_rows title="Failing rows" fmt=num0 />
</DataTable>

A check can fail on a day the site does not show: each build starts a day early and the first day is only partly captured, so its few trips can miss a threshold.

{:else}

Every check in the last build passed.

{/if}

## Every check in the last build

```sql checks
select
    case when status in ('pass', 'success') then 'passed' else status end
        as status,
    resource_type as kind, node, failures, seconds
from transit.quality
order by status <> 'passed' desc, kind desc, node
```

<DataTable data={checks} emptySet=pass emptyMessage="No checks recorded" rows=50 search=true>
  <Column id=status />
  <Column id=kind />
  <Column id=node title="Check or table" wrap=true />
  <Column id=failures title="Failing rows" fmt=num0 />
  <Column id=seconds fmt=num1 />
</DataTable>
