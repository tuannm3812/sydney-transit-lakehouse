---
title: Ghost trips
hide_title: true
sidebar_position: 2
---

<script>
  // TfNSW mode colours (lakehouse-deck scripts/colours.py).
  const modeColours = {
    'Bus': '#00B5EF', 'Trains': '#F6891F', 'Metro': '#00959A',
    'Light rail': '#EE343F', 'Ferries': '#5AB031', 'NSW TrainLink': '#B9C0C7', 'Coaches': '#B9C0C7'
  };
</script>

<div class="lede">
  <span class="eyebrow">Ghost trips</span>
  <h1 class="question">Which trips never turned up?</h1>
  <p class="context">Trips that were in the timetable but never showed up in the live feed (<b>never seen</b>), or showed up and left it well before the end of the route (<b>vanished</b>). Only operators that published live data that day are judged, and only while the feed was being captured.</p>
</div>

```sql modes
select distinct mode_name from transit.route_honesty order by mode_name
```

```sql days
select distinct service_date::varchar as service_day from transit.service_days order by service_day desc
```

<Dropdown name=mode data={modes} value=mode_name title="Mode">
  <DropdownOption value="%" valueLabel="All modes" />
</Dropdown>

<Dropdown name=day data={days} value=service_day title="Service day">
  <DropdownOption value="%" valueLabel="All days" />
</Dropdown>

<Dropdown name=kind title="Kind">
  <DropdownOption value="%" valueLabel="Never seen or vanished" />
  <DropdownOption value="missing" valueLabel="Never seen" />
  <DropdownOption value="vanished" valueLabel="Vanished" />
</Dropdown>

```sql by_hour
select
    local_hour,
    mode_name,
    count(*) as ghost_trips
from transit.ghost_trips
where mode_name like '${inputs.mode.value}'
    and service_date::varchar like '${inputs.day.value}'
    and ghost_kind like '${inputs.kind.value}'
group by all
order by local_hour
```

<BarChart
  emptySet=pass
  emptyMessage="No ghost trips for this selection"
  data={by_hour}
  x=local_hour
  y=ghost_trips
  series=mode_name
  seriesColors={modeColours}
  type=stacked
  xType=value
  xMin=0
  xMax=23
  title="Ghost trips by scheduled start hour (Sydney time)"
/>

## Routes with the most ghost trips

Routes with at least ten checked trips in the selection. The share is out of the route's checked trips.

```sql routes
select
    mode_name,
    route_short_name as route,
    agency_name as operator,
    sum(trips_judged) as trips_checked,
    sum(trips_missing) as never_seen,
    sum(trips_missing_high) as never_seen_high_confidence,
    sum(trips_vanished) as vanished,
    sum(trips_missing + trips_vanished) / sum(trips_judged) as ghost_share
from transit.route_honesty
where mode_name like '${inputs.mode.value}'
    and service_date::varchar like '${inputs.day.value}'
group by all
having sum(trips_judged) >= 10 and sum(trips_missing + trips_vanished) > 0
order by sum(trips_missing + trips_vanished) desc, ghost_share desc
limit 50
```

<DataTable data={routes} emptySet=pass emptyMessage="No route lost a trip in this selection" rows=15 search=true>
  <Column id=mode_name title="Mode" />
  <Column id=route title="Route" />
  <Column id=operator title="Operator" />
  <Column id=trips_checked title="Checked" fmt=num0 />
  <Column id=never_seen title="Never seen" fmt=num0 />
  <Column id=never_seen_high_confidence title="Of which high confidence" fmt=num0 />
  <Column id=vanished title="Vanished" fmt=num0 />
  <Column id=ghost_share title="Ghost share" fmt=pct1 contentType=colorscale />
</DataTable>

## By operator

```sql operators
select
    mode_name,
    agency_name as operator,
    sum(trips_judged) as trips_checked,
    sum(trips_ran) / sum(trips_judged) as ran_share,
    sum(trips_missing) / sum(trips_judged) as never_seen_share,
    sum(trips_vanished) / sum(trips_judged) as vanished_share,
    sum(trips_missing + trips_vanished) / sum(trips_judged) as ghost_share
from transit.route_honesty
where mode_name like '${inputs.mode.value}'
    and service_date::varchar like '${inputs.day.value}'
group by all
order by trips_checked desc
```

<DataTable data={operators} emptySet=pass emptyMessage="No rows for this selection" rows=20>
  <Column id=mode_name title="Mode" />
  <Column id=operator title="Operator" />
  <Column id=trips_checked title="Checked" fmt=num0 />
  <Column id=ran_share title="Ran" fmt=pct1 />
  <Column id=never_seen_share title="Never seen" fmt=pct2 />
  <Column id=vanished_share title="Vanished" fmt=pct2 />
  <Column id=ghost_share title="Ghost share" fmt=pct2 contentType=colorscale />
</DataTable>

## Every ghost trip

For never-seen trips, confidence is **high** when other trips of the same route were seen that day and vehicle positions were captured for the whole run, **medium** when other trips of the route were seen, and **low** otherwise. Vanished trips are always **medium**: the feed saw them, so the question is only why they left it early. A sample of flagged trips is checked by hand against the raw snapshots for every report.

```sql trips
select
    service_date,
    sched_start as due,
    mode_name as mode,
    route_short_name as route,
    trip_headsign as towards,
    agency_name as operator,
    case ghost_kind when 'missing' then 'never seen' else ghost_kind end as kind,
    confidence,
    stops_done,
    stop_count,
    trip_id
from transit.ghost_trips
where mode_name like '${inputs.mode.value}'
    and service_date::varchar like '${inputs.day.value}'
    and ghost_kind like '${inputs.kind.value}'
order by service_date desc, due
```

<DataTable data={trips} emptySet=pass emptyMessage="No ghost trips for this selection" rows=25 search=true>
  <Column id=service_date title="Day" />
  <Column id=due title="Due" />
  <Column id=mode title="Mode" />
  <Column id=route title="Route" />
  <Column id=towards title="Towards" />
  <Column id=operator title="Operator" />
  <Column id=kind title="Kind" />
  <Column id=confidence title="Confidence" />
  <Column id=stops_done title="Stops passed" fmt=num0 />
  <Column id=stop_count title="Stops" fmt=num0 />
  <Column id=trip_id title="Trip id" />
</DataTable>
