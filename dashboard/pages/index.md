---
title: Did the bus turn up?
sidebar_position: 1
---

<script>
  // TfNSW mode colours, the same as the decks and the poster
  // (lakehouse-deck scripts/colours.py). Every other chart uses Viridis.
  const modeColours = {
    'Bus': '#00B5EF', 'Trains': '#F6891F', 'Metro': '#00959A',
    'Light rail': '#EE343F', 'Ferries': '#5AB031', 'NSW TrainLink': '#B9C0C7', 'Coaches': '#B9C0C7'
  };
</script>

Timetabled trips on Sydney's network, checked against what Transport for NSW's live feeds actually showed. A collector has polled the open GTFS-realtime feeds since 17 November 2026, vehicle positions every 15 seconds (ferries 30) and trip updates every 30, and every number here is built from those raw snapshots. Buses, Metro, Inner West light rail and ferries are judged; Sydney Trains not yet (see [About](/about)).

```sql totals
select
    min(service_date) as first_day,
    max(service_date) as last_day,
    count(*) as service_days,
    sum(trips_scheduled) as trips_scheduled,
    sum(trips_judged) as trips_judged,
    sum(trips_ran) / sum(trips_judged) as ran_share,
    sum(trips_missing) as trips_missing,
    sum(trips_missing) / sum(trips_judged) as missing_share,
    sum(trips_vanished) as trips_vanished,
    sum(trips_vanished) / sum(trips_judged) as vanished_share
from transit.service_days
```

```sql bunch_total
select
    count(*) as bunches,
    count(distinct mode || ':' || route_id) as routes
from transit.bunching_events
```

{#if totals.length > 0 && totals[0].service_days > 0 && totals[0].service_days < 7}

<Alert status="info">
<b>Early results.</b> This edition covers <Value data={totals} column=service_days /> service days, <Value data={totals} column=first_day fmt="d mmm yyyy" /> to <Value data={totals} column=last_day fmt="d mmm yyyy" />, rebuilt from the raw archive. A few days are not a pattern: treat route rankings as leads, not verdicts. Days are added as the live pipeline builds them.
</Alert>

{/if}

<BigValue data={totals} value=trips_judged title="Trips checked" fmt=num0 />
<BigValue data={totals} value=ran_share title="Showed up and ran" fmt=pct1 />
<BigValue data={totals} value=trips_missing title="Never seen live" fmt=num0 comparison=missing_share comparisonFmt=pct2 comparisonTitle="of trips checked" comparisonDelta=false />
<BigValue data={totals} value=trips_vanished title="Vanished mid-route" fmt=num0 comparison=vanished_share comparisonFmt=pct2 comparisonTitle="of trips checked" comparisonDelta=false />
<BigValue data={bunch_total} value=bunches title="Bunches spotted" fmt=num0 />

A trip is checked only when the feed was being captured from ten minutes before it was due to start until three hours after it was due to end, and only for operators that published live data that day. A gap in our own collection never reads as a missing bus.

## By mode

```sql by_mode
select
    mode_name,
    sum(trips_judged) as trips_judged,
    sum(trips_ran) / sum(trips_judged) as ran_share,
    sum(trips_missing) / sum(trips_judged) as missing_share,
    sum(trips_vanished) / sum(trips_judged) as vanished_share,
    sum(trips_missing + trips_vanished) / sum(trips_judged) as ghost_share
from transit.route_honesty
group by all
order by trips_judged desc
```

<DataTable data={by_mode} emptySet=pass emptyMessage="No data yet">
  <Column id=mode_name title="Mode" />
  <Column id=trips_judged title="Trips checked" fmt=num0 />
  <Column id=ran_share title="Ran" fmt=pct1 />
  <Column id=missing_share title="Never seen" fmt=pct2 />
  <Column id=vanished_share title="Vanished" fmt=pct2 />
  <Column id=ghost_share title="Ghost share" fmt=pct2 contentType=colorscale />
</DataTable>

```sql by_mode_day
select
    strftime(service_date, '%a %-d %b') as service_day,
    mode_name,
    sum(trips_missing + trips_vanished) / sum(trips_judged) as ghost_share,
    sum(trips_judged) as trips_judged
from transit.route_honesty
group by all
order by min(service_date), mode_name
```

<BarChart
  emptySet=pass
  emptyMessage="No data yet"
  data={by_mode_day}
  x=service_day
  sort=false
  y=ghost_share
  series=mode_name
  seriesColors={modeColours}
  type=grouped
  xType=category
  yFmt=pct1
  title="Ghost share by service day"
  subtitle="Checked trips never seen or vanished"
/>

## Where to look next

- [Ghost trips](/ghosts): which routes and operators lose trips, and every flagged trip.
- [Bunching radar](/bunching): where and when buses arrive in clumps.
- [Map replay](/replay): a whole day of vehicles, played back.
- [Data quality](/quality): the checks every build has to pass.
- [About](/about): the data, the method and its limits.

## What the words mean

- **Ran**: the trip appeared in the live feed and got within two stops of its last stop, or left the feed within ten minutes of its scheduled end.
- **Never seen**: the trip was in the timetable, the feed was being captured the whole time, the operator published other trips that day, and no trip update or vehicle position ever mentioned it. That makes it a ghost candidate, not proof the bus did not run: a vehicle's tracker can fail.
- **Vanished**: the trip appeared, then left the feed with more than two stops to go, more than ten minutes before its scheduled end.
- **Bunch**: two consecutive vehicles on a route and direction ran closer than a third of their timetabled gap at three or more stops.
