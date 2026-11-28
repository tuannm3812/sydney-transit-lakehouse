---
title: Bunching radar
hide_title: true
sidebar_position: 3
---

<script>
  // TfNSW mode colours (lakehouse-deck scripts/colours.py); the map's
  // colour scale is Viridis, from the site theme.
  const modeColours = {
    'Bus': '#00B5EF', 'Trains': '#F6891F', 'Metro': '#00959A',
    'Light rail': '#EE343F', 'Ferries': '#5AB031', 'NSW TrainLink': '#B9C0C7', 'Coaches': '#B9C0C7'
  };
  // OpenStreetMap's own tiles need no key (CARTO's asked for one).
  const osmTiles = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
</script>

<div class="lede">
  <span class="eyebrow">Bunching radar</span>
  <h1 class="question">Where do buses arrive in clumps?</h1>
  <p class="context">A gap between two consecutive buses at a stop is <b>bunched</b> when it is under a third of the gap the timetable planned between the same two trips. A <b>bunch</b> is a pair that stays bunched for three or more stops, so riders further down the route wait longer, then see two buses at once.</p>
</div>

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
    upper(region[1]) || region[2:] as region,
    route_id,
    route_short_name as route,
    sum(headways) as gaps,
    sum(bunched) as bunched_gaps,
    sum(bunched) / sum(headways) as "Bunched share"
from transit.bunching_hotspots
where day_type like '${inputs.day_type.value}'
group by all
having sum(headways) >= 10 and sum(bunched) >= 3
```

<BubbleMap
  colorPalette={['#3b528b', '#21918c', '#5ec962', '#fde725']}
  emptySet=pass
  emptyMessage="No data for this selection"
  data={spots}
  lat=stop_lat
  long=stop_lon
  size=bunched_gaps
  value="Bunched share"
  valueFmt=pct0
  pointName=stop_name
  startingLat={-33.87}
  startingLong={151.1}
  startingZoom={10}
  basemap={osmTiles}
  attribution="© OpenStreetMap contributors"
  height={520}
  title="Where gaps are bunched"
  tooltipType=hover
  tooltip={[
    {id: 'stop_name', showColumnName: false, valueClass: 'font-semibold'},
    {id: 'route'},
    {id: 'bunched_gaps', title: 'Bunched gaps', fmt: 'num0'},
    {id: 'gaps', title: 'Gaps', fmt: 'num0'},
    {id: 'Bunched share', fmt: 'pct0'}
  ]}
/>

## When it happens

```sql by_hour
select
    lpad(local_hour::varchar, 2, '0') || ':00' as hour,
    mode_name,
    sum(headways) as gaps,
    sum(bunched) / sum(headways) as bunched_share
from transit.bunching_by_hour
where day_type like '${inputs.day_type.value}'
group by all
having sum(headways) >= 50 and sum(bunched) > 0
order by hour
```

<LineChart
  emptySet=pass
  emptyMessage="No data for this selection"
  data={by_hour}
  x=hour
  y=bunched_share
  series=mode_name
  seriesColors={modeColours}
  xType=category
  sort=false
  yFmt=pct1
  title="Share of gaps that were bunched, by hour (Sydney time)"
  subtitle="Hours with at least 50 gaps; modes with no bunched gaps are left out"
/>

## Routes that bunch most

```sql routes
select
    mode_name,
    upper(region[1]) || region[2:] as region,
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
  <Column id=route title="Route" />
  <Column id=region title="Region" />
  <Column id=bunches title="Bunches" fmt=num0 />
  <Column id=median_stops_together title="Stops bunched" fmt=num0 />
  <Column id=median_closest_gap_min title="Closest, min" fmt=num1 />
  <Column id=median_timetabled_gap_min title="Planned, min" fmt=num1 />
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
  <Column id=mode title="Mode" />
  <Column id=route title="Route" />
  <Column id=stops_bunched title="Stops bunched" fmt=num0 />
  <Column id=stops_paired title="Stops compared" fmt=num0 />
  <Column id=min_gap_s title="Closest, s" fmt=num0 />
  <Column id=scheduled_gap_s title="Planned, s" fmt=num0 />
  <Column id=lead_trip_id title="Leading trip" />
  <Column id=follow_trip_id title="Following trip" />
</DataTable>
