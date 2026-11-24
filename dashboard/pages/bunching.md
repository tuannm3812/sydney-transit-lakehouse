---
title: Bunching radar
---

<script>
  const modeColours = {
    bus: '#00B5EF', train: '#F6891F', metro: '#168388',
    lightrail: '#EE343F', ferry: '#5AB031', coach: '#742283'
  };
</script>

Vehicles on the same route and direction that arrive in clumps instead of evenly spaced. A gap between two consecutive vehicles at a stop is **bunched** when it is under a third of the timetabled gap between the same two trips. A **bunch** is a pair that stays bunched for three or more stops.

```sql day_types
select distinct day_type from transit.bunching_hotspots order by day_type desc
```

<Dropdown name=day_type data={day_types} value=day_type title="Days">
  <DropdownOption value="%" valueLabel="All days" />
</Dropdown>

```sql spots
select
    stop_name,
    stop_lat,
    stop_lon,
    mode,
    route_short_name as route,
    sum(headways) as gaps,
    sum(bunched) as bunched_gaps,
    sum(bunched) / sum(headways) as bunched_share
from transit.bunching_hotspots
where day_type like '${inputs.day_type.value}'
group by all
having sum(headways) >= 5
```

<BubbleMap
  data={spots}
  lat=stop_lat
  long=stop_lon
  size=bunched_gaps
  value=bunched_share
  valueFmt=pct0
  pointName=stop_name
  startingLat={-33.87}
  startingLong={151.1}
  startingZoom={10}
  height={520}
  title="Where gaps are bunched"
  tooltipType=hover
  tooltip={[
    {id: 'stop_name', showColumnName: false, valueClass: 'font-semibold'},
    {id: 'route'},
    {id: 'bunched_gaps', fmt: 'num0'},
    {id: 'gaps', fmt: 'num0'},
    {id: 'bunched_share', fmt: 'pct0'}
  ]}
/>

## When it happens

```sql by_hour
select
    local_hour,
    mode,
    sum(bunched) / sum(headways) as bunched_share
from transit.bunching_hotspots
where day_type like '${inputs.day_type.value}'
group by all
order by local_hour
```

<LineChart
  data={by_hour}
  x=local_hour
  y=bunched_share
  series=mode
  seriesColors={modeColours}
  yFmt=pct0
  title="Bunched share of gaps by hour (Sydney time)"
/>

## Routes that bunch most

```sql routes
select
    mode,
    route_short_name as route,
    count(*) as bunches,
    median(stops_bunched) as median_stops_together,
    median(min_gap_s) / 60 as median_closest_gap_min,
    median(scheduled_gap_s) / 60 as median_timetabled_gap_min
from transit.bunching_events
where day_type like '${inputs.day_type.value}'
group by all
order by bunches desc
limit 50
```

<DataTable data={routes} rows=15 search=true>
  <Column id=mode />
  <Column id=route />
  <Column id=bunches fmt=num0 />
  <Column id=median_stops_together title="Stops together (median)" fmt=num0 />
  <Column id=median_closest_gap_min title="Closest gap, min" fmt=num1 />
  <Column id=median_timetabled_gap_min title="Timetabled gap, min" fmt=num1 />
</DataTable>

## Every bunch

```sql events
select
    service_date,
    first_bunched as from_time,
    mode,
    route_short_name as route,
    stops_bunched,
    stops_paired,
    min_gap_s,
    scheduled_gap_s,
    lead_trip_id,
    follow_trip_id
from transit.bunching_events
where day_type like '${inputs.day_type.value}'
order by service_date desc, from_time
```

<DataTable data={events} rows=25 search=true />
