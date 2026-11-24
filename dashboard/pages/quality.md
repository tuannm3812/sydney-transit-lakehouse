---
title: Data quality
---

Every number on this site comes from a build that runs these checks first. A failed check is shown here, not hidden.

```sql summary
select
    max(run_at) as built_at,
    count(*) filter (where resource_type = 'test') as tests,
    count(*) filter (where resource_type = 'test' and status = 'pass') as tests_passed,
    count(*) filter (where resource_type = 'model') as models
from transit.quality
```

<BigValue data={summary} value=tests_passed title="Checks passed" fmt=num0 />
<BigValue data={summary} value=tests title="Checks run" fmt=num0 />
<BigValue data={summary} value=models title="Tables built" fmt=num0 />

Last build: <Value data={summary} column=built_at fmt="d mmm yyyy H:MM AM/PM" /> (UTC).

## Realtime trips that match the timetable

```sql match
select service_date, mode, realtime_trips, matched_trips, match_share
from transit.trip_match
order by service_date desc, mode
```

<DataTable data={match} emptySet=pass emptyMessage="No rows for this selection yet" rows=20>
  <Column id=service_date />
  <Column id=mode />
  <Column id=realtime_trips fmt=num0 />
  <Column id=matched_trips fmt=num0 />
  <Column id=match_share title="Matched" fmt=pct1 />
</DataTable>

## Every check in the last build

```sql checks
select resource_type as kind, node, status, failures, seconds
from transit.quality
order by status <> 'pass' desc, kind, node
```

<DataTable data={checks} emptySet=pass emptyMessage="No rows for this selection yet" rows=50 search=true />
