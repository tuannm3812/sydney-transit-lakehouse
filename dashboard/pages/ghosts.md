---
title: Ghost trips
---

<script>
  const modeColours = {
    bus: '#00B5EF', train: '#F6891F', metro: '#168388',
    lightrail: '#EE343F', ferry: '#5AB031', coach: '#742283'
  };
</script>

Trips that were timetabled but never showed up in the live feed (**never seen**), or showed up and then left it well before the end of the route (**vanished**). Only operators that published realtime data that day are judged.

```sql modes
select distinct mode from transit.route_honesty order by mode
```

```sql days
select distinct service_date from transit.service_days order by service_date desc
```

<Dropdown name=mode data={modes} value=mode title="Mode">
  <DropdownOption value="%" valueLabel="All modes" />
</Dropdown>

<Dropdown name=kind title="Kind">
  <DropdownOption value="%" valueLabel="Never seen or vanished" />
  <DropdownOption value="missing" valueLabel="Never seen" />
  <DropdownOption value="vanished" valueLabel="Vanished" />
</Dropdown>

```sql by_hour
select
    local_hour,
    mode,
    count(*) as ghost_trips
from transit.ghost_trips
where mode like '${inputs.mode.value}'
    and ghost_kind like '${inputs.kind.value}'
group by all
order by local_hour
```

<BarChart
  data={by_hour}
  x=local_hour
  y=ghost_trips
  series=mode
  seriesColors={modeColours}
  type=stacked
  title="Ghost trips by scheduled start hour (Sydney time)"
/>

## Routes with the most ghost trips

```sql routes
select
    mode,
    route_short_name as route,
    agency_name as operator,
    sum(trips_judged) as trips_checked,
    sum(trips_missing) as never_seen,
    sum(trips_missing_high) as never_seen_high_confidence,
    sum(trips_vanished) as vanished,
    sum(trips_missing + trips_vanished) / sum(trips_judged) as ghost_share
from transit.route_honesty
where mode like '${inputs.mode.value}'
group by all
having sum(trips_judged) >= 10
order by sum(trips_missing + trips_vanished) desc
limit 50
```

<DataTable data={routes} rows=15 search=true>
  <Column id=mode />
  <Column id=route />
  <Column id=operator />
  <Column id=trips_checked fmt=num0 />
  <Column id=never_seen fmt=num0 />
  <Column id=never_seen_high_confidence title="Never seen, high confidence" fmt=num0 />
  <Column id=vanished fmt=num0 />
  <Column id=ghost_share title="Ghost share" fmt=pct1 contentType=colorscale />
</DataTable>

## By operator

```sql operators
select
    mode,
    agency_name as operator,
    sum(trips_judged) as trips_checked,
    sum(trips_ran) / sum(trips_judged) as ran_share,
    sum(trips_missing) / sum(trips_judged) as never_seen_share,
    sum(trips_vanished) / sum(trips_judged) as vanished_share
from transit.route_honesty
where mode like '${inputs.mode.value}'
group by all
order by trips_checked desc
```

<DataTable data={operators} rows=20>
  <Column id=mode />
  <Column id=operator />
  <Column id=trips_checked fmt=num0 />
  <Column id=ran_share title="Ran" fmt=pct1 />
  <Column id=never_seen_share title="Never seen" fmt=pct1 />
  <Column id=vanished_share title="Vanished" fmt=pct1 />
</DataTable>

## Every ghost trip

Confidence is **high** when other trips of the same route were seen that day and vehicle positions were captured for the whole run, **medium** when other trips of the route were seen, and **low** otherwise.

```sql trips
select
    service_date,
    sched_start as due,
    mode,
    route_short_name as route,
    trip_headsign as towards,
    agency_name as operator,
    ghost_kind as kind,
    confidence,
    stops_done,
    stop_count,
    trip_id
from transit.ghost_trips
where mode like '${inputs.mode.value}'
    and ghost_kind like '${inputs.kind.value}'
order by service_date desc, due
```

<DataTable data={trips} rows=25 search=true />
