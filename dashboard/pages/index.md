---
title: Sydney timetable honesty
---

<script>
  // TfNSW mode colours, the same as the decks and the poster.
  const modeColours = {
    bus: '#00B5EF', train: '#F6891F', metro: '#168388',
    lightrail: '#EE343F', ferry: '#5AB031', coach: '#742283'
  };
</script>

Did each timetabled trip on Sydney's network show up in the live feed? This site checks every trip of every operator that publishes realtime data, from Transport for NSW's open GTFS-realtime feeds, collected every 15 to 30 seconds since 17 November 2026.

A trip counts only when the feed was being captured from before it was due to start until three hours after it was due to end, so a gap in our own collection never reads as a missing bus.

```sql days
select * from transit.service_days order by service_date
```

```sql totals
select
    min(service_date) as first_day,
    max(service_date) as last_day,
    count(*) as service_days,
    sum(trips_judged) as trips_judged,
    sum(trips_ran) / sum(trips_judged) as ran_share,
    sum(trips_missing) / sum(trips_judged) as missing_share,
    sum(trips_vanished) / sum(trips_judged) as vanished_share
from transit.service_days
```

```sql bunch_total
select count(*) as bunches from transit.bunching_events
```

<BigValue data={totals} value=trips_judged title="Trips checked" fmt=num0 />
<BigValue data={totals} value=ran_share title="Showed up and ran" fmt=pct1 />
<BigValue data={totals} value=missing_share title="Never seen live" fmt=pct1 />
<BigValue data={totals} value=vanished_share title="Vanished mid-route" fmt=pct1 />
<BigValue data={bunch_total} value=bunches title="Bunches spotted" fmt=num0 />

Data from <Value data={totals} column=first_day fmt="d mmm yyyy" /> to <Value data={totals} column=last_day fmt="d mmm yyyy" /> (service days, Sydney time).

## By day and mode

```sql by_mode_day
select
    service_date,
    mode,
    sum(trips_missing + trips_vanished) / sum(trips_judged) as ghost_share,
    sum(trips_judged) as trips_judged
from transit.route_honesty
group by all
order by service_date
```

<LineChart
  emptySet=pass
  emptyMessage="No data for this selection yet"
  data={by_mode_day}
  x=service_date
  y=ghost_share
  series=mode
  seriesColors={modeColours}
  yFmt=pct1
  title="Share of checked trips that were never seen or vanished"
/>

## What the words mean

- **Ran**: the trip appeared in the live feed and got within two stops of its last stop.
- **Never seen**: the trip was in the timetable, the feed was being captured the whole time, the operator published other trips that day, and no trip update or vehicle position ever mentioned it. That is a ghost candidate, not proof the bus did not run: a vehicle's tracker can fail.
- **Vanished**: the trip appeared, then left the feed with more than two stops to go.
- **Bunch**: two consecutive vehicles on a route ran closer than a third of their timetabled gap at three or more stops in a row.

See [Ghost trips](/ghosts), [Bunching radar](/bunching), [Map replay](/replay) and [Data quality](/quality).
