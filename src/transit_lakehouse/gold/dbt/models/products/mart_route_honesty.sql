-- Route x service day: how much of the timetable showed up live
-- (Phase 3, D-045). Only trips the feeds could judge count (captured
-- from before the start to the grace after the end, for an operator
-- with realtime that day, D-038), so a capture gap or an operator
-- without realtime never lowers a route's score.
with t as (
    select * from {{ ref('fct_trip_outcome') }}
)

select
    service_date,
    case when isodow(service_date) >= 6 then 'weekend' else 'weekday' end
        as day_type,
    mode,
    region,
    route_id,
    any_value(route_short_name) as route_short_name,
    any_value(agency_id) as agency_id,
    any_value(agency_name) as agency_name,
    count(*) as trips_scheduled,
    count(*) filter (outcome <> 'not_judged') as trips_judged,
    count(*) filter (outcome = 'ran') as trips_ran,
    count(*) filter (outcome = 'cancelled') as trips_cancelled,
    count(*) filter (outcome = 'never_seen') as trips_missing,
    count(*) filter (outcome = 'never_seen' and confidence = 'high')
        as trips_missing_high,
    count(*) filter (outcome = 'partial') as trips_vanished,
    case when count(*) filter (outcome <> 'not_judged') > 0
        then round(
            count(*) filter (outcome = 'ran')
            / count(*) filter (outcome <> 'not_judged'), 4)
    end as ran_share,
    case when count(*) filter (outcome <> 'not_judged') > 0
        then round(
            count(*) filter (outcome in ('never_seen', 'partial'))
            / count(*) filter (outcome <> 'not_judged'), 4)
    end as ghost_share
from t
group by service_date, mode, region, route_id
