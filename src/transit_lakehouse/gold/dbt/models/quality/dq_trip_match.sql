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
-- A third kind is counted apart for unlisted_trip_modes only (Mike,
-- 28 Nov 2026): trip ids that are in no published timetable version
-- at all (about 6% of train runs on 25-26 Nov 2026, D-059). Bus,
-- ferry, light rail and metro ids missing from every timetable still
-- count against the share, as do train ids that some timetable lists
-- but not for that day.
{{ per_day() }}
select * from (
with listed as (
    select distinct trip_id from {{ source('silver', 'timetable_trip') }}
),

rt_trips as (
    select
        mode,
        trip_id,
        start_date,
        service_date,
        -- a trip with no route id is not an unplanned run
        coalesce(bool_or(starts_with(
            rt_route_id, '{{ var("unplanned_route_prefix") }}'
        )), false) as unplanned,
        -- silver is change-only, so a trip whose state never changed
        -- has one row: it was seen until the next row (its REMOVED row)
        max(coalesce(next_fetched_at, fetched_at)) - min(fetched_at)
            < interval {{ var('min_trip_seen_minutes') }} minute as blip
    from {{ ref('stg_rt_trip_state') }}
    where trip_status in ('SCHEDULED', 'CANCELED')
        and service_date is not null {{ in_service_window('service_date') }}
    group by all
),

rt as (
    select
        r.*,
        not r.unplanned
            and r.mode in {{ mode_list('unlisted_trip_modes') }}
            and l.trip_id is null as unlisted
    from rt_trips r
    left join listed l on l.trip_id = r.trip_id
)

select
    rt.service_date,
    rt.mode,
    count(*) filter (judged) as realtime_trips,
    count(s.trip_id) filter (judged) as matched_trips,
    count(s.trip_id) filter (judged)
        / nullif(count(*) filter (judged), 0) as match_share,
    count(*) filter (unplanned) as unplanned_trips,
    count(*) filter (blip and not unplanned) as blip_trips,
    count(*) filter (unlisted and not blip) as unlisted_trips
from (
    select *, not unplanned and not blip and not unlisted as judged
    from rt
) as rt
left join {{ ref('stg_scheduled_trip') }} s
    on s.trip_id = rt.trip_id and s.service_date = rt.service_date
group by all
) as day_rows
{{ window_rows() }}
