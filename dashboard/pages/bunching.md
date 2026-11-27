---
title: Bunching radar
sidebar_position: 3
---

<script>
  // TfNSW mode colours (lakehouse-deck scripts/colours.py); the map's
  // colour scale is Viridis, from the site theme.
  const modeColours = {
    'Bus': '#00B5EF', 'Trains': '#F6891F', 'Metro': '#00959A',
    'Light rail': '#EE343F', 'Ferries': '#5AB031', 'NSW TrainLink': '#B9C0C7', 'Coaches': '#B9C0C7'
  };
</script>

Vehicles on the same route and direction that arrive in clumps instead of evenly spaced. A gap between two consecutive vehicles at a stop is **bunched** when it is under a third of the timetabled gap between the same two trips. A **bunch** is a pair that stays bunched for three or more stops.

```sql day_types
select distinct day_type from transit.bunching_hotspots order by day_type desc
```

<Dropdown name=day_type data={day_types} value=day_type title="Days">
  <DropdownOption value="%" valueLabel="Weekdays and weekends" />
</Dropdown>

Each circle is a stop on one route, both directions and all hours together, with at least ten gaps between vehicles and at least three of them bunched. Only stop-hours with three or more gaps count towards a circle, so quiet hours are left out; the by-hour chart below counts every gap. Bigger circles saw more bunched gaps; the colour is the share of the stop's gaps that were bunched.

```sql spots
select
    stop_name,
    stop_lat,
    stop_lon,
    mode_name as mode,
    region,
    route_id,
    route_short_name as route,
    sum(headways) as gaps,
    sum(bunched) as bunched_gaps,
    sum(bunched) / sum(headways) as bunched_share
from transit.bunching_hotspots
where day_type like '${inputs.day_type.value}'
group by all
having sum(headways) >= 10 and sum(bunched) >= 3
```

<BubbleMap
  emptySet=pass
  emptyMessage="No data for this selection"
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
    {id: 'bunched_gaps', title: 'Bunched gaps', fmt: 'num0'},
    {id: 'gaps', title: 'Gaps', fmt: 'num0'},
    {id: 'bunched_share', title: 'Bunched share', fmt: 'pct0'}
  ]}
/>

## When it happens

```sql by_hour
select
    local_hour,
    mode_name,
    sum(headways) as gaps,
    sum(bunched) / sum(headways) as bunched_share
from transit.bunching_by_hour
where day_type like '${inputs.day_type.value}'
group by all
having sum(headways) >= 50
order by local_hour
```

<LineChart
  emptySet=pass
  emptyMessage="No data for this selection"
  data={by_hour}
  x=local_hour
  y=bunched_share
  series=mode_name
  seriesColors={modeColours}
  xType=value
  xMin=0
  xMax=23
  yFmt=pct1
  title="Share of gaps that were bunched, by hour (Sydney time)"
  subtitle="Hours with at least 50 gaps"
/>

## Routes that bunch most

```sql routes
select
    mode_name,
    region,
    route_id,
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

<DataTable data={routes} emptySet=pass emptyMessage="No rows for this selection" rows=15 search=true>
  <Column id=mode_name title="Mode" />
  <Column id=route />
  <Column id=region />
  <Column id=bunches fmt=num0 />
  <Column id=median_stops_together title="Stops bunched (median)" fmt=num0 />
  <Column id=median_closest_gap_min title="Closest gap, min" fmt=num1 />
  <Column id=median_timetabled_gap_min title="Timetabled gap, min" fmt=num1 />
</DataTable>

## Every bunch

```sql events
select
    service_date,
    first_bunched as from_time,
    mode_name as mode,
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

<DataTable data={events} emptySet=pass emptyMessage="No bunches for this selection" rows=25 search=true>
  <Column id=service_date title="Day" />
  <Column id=from_time title="From" />
  <Column id=mode />
  <Column id=route />
  <Column id=stops_bunched title="Stops bunched" fmt=num0 />
  <Column id=stops_paired title="Stops compared" fmt=num0 />
  <Column id=min_gap_s title="Closest gap, s" fmt=num0 />
  <Column id=scheduled_gap_s title="Timetabled gap, s" fmt=num0 />
  <Column id=lead_trip_id title="Leading trip" />
  <Column id=follow_trip_id title="Following trip" />
</DataTable>
