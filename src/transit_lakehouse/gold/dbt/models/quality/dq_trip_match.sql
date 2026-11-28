-- Referential integrity, the join the ghost-trip product rests on:
-- the share of realtime trips (scheduled ones, not ADDED) whose
-- trip_id and service day match a trip the timetable in effect
-- schedules that day. The Phase 2 plan's bar is 95%.
-- Two kinds of realtime trip are not trips the timetable could hold,
-- so they are counted apart and left out of the share (D-059):
-- Sydney Trains' unplanned runs, which the feed marks SCHEDULED but
-- puts on route ids starting RTTA (about 10% of train runs on 25 and
-- 26 Nov 2026, none in any timetable), and trip ids the feed shows for
-- less than min_trip_seen_minutes (mostly one snapshot, a placeholder
-- the feed replaces at once).
{{ per_day() }}
select * from (
with rt as (
    select
        mode,
        trip_id,
        start_date,
        service_date,
        bool_or(starts_with(rt_route_id, '{{ var("unplanned_route_prefix") }}'))
            as unplanned,
        max(fetched_at) - min(fetched_at)
            < interval {{ var('min_trip_seen_minutes') }} minute as blip
    from {{ ref('stg_rt_trip_state') }}
    where trip_status in ('SCHEDULED', 'CANCELED')
        and service_date is not null {{ in_service_window('service_date') }}
    group by all
)

select
    rt.service_date,
    rt.mode,
    count(*) filter (not unplanned and not blip) as realtime_trips,
    count(s.trip_id) filter (not unplanned and not blip) as matched_trips,
    count(s.trip_id) filter (not unplanned and not blip)
        / nullif(count(*) filter (not unplanned and not blip), 0)
        as match_share,
    count(*) filter (unplanned) as unplanned_trips,
    count(*) filter (blip and not unplanned) as blip_trips
from rt
left join {{ ref('stg_scheduled_trip') }} s
    on s.trip_id = rt.trip_id and s.service_date = rt.service_date
group by all
) as day_rows
{{ window_rows() }}
