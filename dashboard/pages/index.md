---
title: Did the bus turn up?
hide_title: true
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

<div class="lede">
  <span class="eyebrow">Sydney timetable honesty</span>
  <h1 class="question">Did the bus turn up?</h1>
  <p class="context">Every timetabled trip we can judge, checked against what Transport for NSW's live feeds showed. Vehicle positions are polled every 15 seconds (ferries 30) and trip updates every 30, and every number here is built from those raw snapshots.</p>
  <ul class="chips" aria-label="Modes">
    <li class="chip" style="--c: #00B5EF"><span class="dot"></span>Bus</li>
    <li class="chip" style="--c: #00959A"><span class="dot"></span>Metro</li>
    <li class="chip" style="--c: #EE343F"><span class="dot"></span>Light rail</li>
    <li class="chip" style="--c: #5AB031"><span class="dot"></span>Ferries</li>
    <li class="chip later" style="--c: #F6891F"><span class="dot"></span>Trains, not judged yet</li>
  </ul>
</div>

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

```sql worst_route
select
    route_short_name as route,
    agency_name as operator,
    sum(trips_missing + trips_vanished) as ghosts,
    sum(trips_judged) as checked
from transit.route_honesty
group by all
having sum(trips_judged) >= 10
order by ghosts desc, checked
limit 1
```

{#if totals.length > 0 && totals[0].service_days > 0}

<div class="figures">
  <div class="figure lead">
    <span class="label">Showed up and ran</span>
    <span class="value"><Value data={totals} column=ran_share fmt=pct1 /></span>
    <span class="note">of <Value data={totals} column=trips_judged fmt=num0 /> trips checked</span>
  </div>
  <div class="figure">
    <span class="label">Never seen live</span>
    <span class="value"><Value data={totals} column=trips_missing fmt=num0 /></span>
    <span class="note"><Value data={totals} column=missing_share fmt=pct2 /> of trips checked</span>
  </div>
  <div class="figure">
    <span class="label">Vanished mid-route</span>
    <span class="value"><Value data={totals} column=trips_vanished fmt=num0 /></span>
    <span class="note"><Value data={totals} column=vanished_share fmt=pct2 /> of trips checked</span>
  </div>
  <div class="figure">
    <span class="label">Bunches spotted</span>
    <span class="value"><Value data={bunch_total} column=bunches fmt=num0 /></span>
    <span class="note">on <Value data={bunch_total} column=routes fmt=num0 /> routes</span>
  </div>
</div>

{#if worst_route.length > 0 && worst_route[0].ghosts > 0}
<p>The route that lost the most trips was <b>route <Value data={worst_route} column=route /></b> (<Value data={worst_route} column=operator />): <Value data={worst_route} column=ghosts fmt=num0 /> of its <Value data={worst_route} column=checked fmt=num0 /> checked trips never showed up or vanished. See <a href="/ghosts">Ghost trips</a> for every route.</p>
{/if}

{#if totals[0].service_days < 7}
<p class="notice"><b>Early results.</b> This edition covers <Value data={totals} column=service_days /> service days, <Value data={totals} column=first_day fmt="ddd d mmm" /> to <Value data={totals} column=last_day fmt="ddd d mmm yyyy" />, rebuilt from the raw archive. A few days are not a pattern, so treat route rankings as leads, not verdicts.</p>
{/if}

{/if}

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

<div class="next">
  <a href="/ghosts"><b>Ghost trips</b>Which routes and operators lose trips, and every flagged trip.</a>
  <a href="/bunching"><b>Bunching radar</b>Where and when buses arrive in clumps.</a>
  <a href="/replay"><b>Map replay</b>A whole day of vehicles, played back.</a>
  <a href="/quality"><b>Data quality</b>The checks every build has to pass.</a>
  <a href="/about"><b>About</b>The data, the method and its limits.</a>
</div>

## What the words mean

<dl class="terms">
  <div><dt>Ran</dt><dd>The trip appeared in the live feed and got within two stops of its last stop, or left the feed within ten minutes of its scheduled end.</dd></div>
  <div><dt>Never seen</dt><dd>The trip was in the timetable, the feed was captured the whole time, the operator published other trips that day, and nothing in the feed ever named it. A ghost candidate, not proof: a vehicle's tracker can fail.</dd></div>
  <div><dt>Vanished</dt><dd>The trip appeared, then left the feed with more than two stops to go, more than ten minutes before its scheduled end.</dd></div>
  <div><dt>Bunch</dt><dd>Two consecutive vehicles on a route and direction ran closer than a third of their timetabled gap at three or more stops.</dd></div>
</dl>
