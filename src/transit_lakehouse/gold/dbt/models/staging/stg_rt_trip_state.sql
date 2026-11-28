-- Realtime trip states (silver.trip_status is change-only), each valid
-- until the next row of the same trip, with the next row's stop count:
-- the stops that dropped out between the two have been passed. A
-- REMOVED row (D-036) is written when the trip leaves the feed, with a
-- stop count of 0.
--
-- service_date is the GTFS service day. TfNSW's start_date is the
-- calendar day the trip runs on, so a trip timetabled after 24:00 of
-- service day D arrives dated D + 1 (checked on 17 Nov: first seen a
-- median 5 minutes before its start when read as D, a day early when
-- read as D + 1). Such a trip is moved back to D.
with states as (
    select
        *,
        {{ gtfs_date('start_date') }} as rt_date
    from {{ ref('stg_rt_trip_status') }}
    where trip_id is not null and start_date is not null
),

after_midnight as (
    select service_date, trip_id
    from {{ ref('stg_scheduled_trip') }}
    where sched_start_utc >= day_origin_utc + interval 24 hour
)

select
    s.mode,
    s.feed,
    s.trip_id,
    s.start_date,
    case when a.trip_id is not null then a.service_date else s.rt_date end
        as service_date,
    s.route_id as rt_route_id,
    s.trip_status,
    s.vehicle_id,
    s.current_delay_s,
    s.stop_count,
    s.observed_at,
    s.fetched_at,
    lead(s.fetched_at) over w as next_fetched_at,
    lead(s.stop_count) over w as next_stop_count,
    lead(s.trip_status) over w as next_trip_status
from states s
left join after_midnight a
    on a.trip_id = s.trip_id
    and a.service_date = (s.rt_date - interval 1 day)::date
window w as (partition by s.mode, s.trip_id, s.start_date order by s.fetched_at)
