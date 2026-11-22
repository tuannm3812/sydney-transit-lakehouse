-- Referential integrity, the join the ghost-trip product rests on:
-- the share of realtime trips (scheduled ones, not ADDED) whose
-- trip_id and service day match a trip the timetable in effect
-- schedules that day. The Phase 2 plan's bar is 95%.
with rt as (
    select distinct mode, trip_id, start_date, service_date
    from {{ ref('stg_rt_trip_state') }}
    where trip_status in ('SCHEDULED', 'CANCELED')
        and service_date is not null {{ in_service_window('service_date') }}
)

select
    rt.service_date,
    rt.mode,
    count(*) as realtime_trips,
    count(s.trip_id) as matched_trips,
    count(s.trip_id) / count(*) as match_share
from rt
left join {{ ref('stg_scheduled_trip') }} s
    on s.trip_id = rt.trip_id and s.service_date = rt.service_date
group by all
