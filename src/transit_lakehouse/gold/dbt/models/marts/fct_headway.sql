-- Gap between consecutive vehicles of a route and direction at each
-- stop, observed against the timetable's gap between the same two
-- trips; bunched when the observed gap is under bunching_ratio of the
-- scheduled one (docs/7_data_model.md).
-- A loop route calls at some stops twice (often its terminus), so
-- vehicles are paired per visit: the first call of one trip with the
-- first call of the next, never with its last.
-- Vehicles that arrive in the same second are ordered by the timetable,
-- then by trip id (D-051).
{{ per_day() }}
select * from (
with arr as (
    select * from {{ ref('stg_headway_arrival') }}
),

paired as (
    select
        *,
        lag(trip_id) over w as prev_trip_id,
        lag(observed_arrival_utc) over w as prev_observed_utc,
        lag(sched_arrival_utc) over w as prev_sched_utc
    from arr
    window w as (
        partition by service_date, route_id, direction_id, stop_id, visit
        order by observed_arrival_utc, sched_arrival_utc, trip_id
    )
)

select
    service_date,
    mode,
    region,
    route_id,
    route_short_name,
    direction_id,
    stop_id,
    stop_sequence,
    visit,
    prev_trip_id,
    trip_id,
    observed_arrival_utc,
    epoch(observed_arrival_utc - prev_observed_utc)::integer as observed_headway_s,
    epoch(sched_arrival_utc - prev_sched_utc)::integer as scheduled_headway_s,
    -- The later trip in the timetable arrived first (D-051).
    epoch(sched_arrival_utc - prev_sched_utc) < 0 as overtaken,
    -- An overtake is tested against the size of the timetabled gap, so
    -- a bus that passes its leader is bunched, not dropped (D-051).
    epoch(sched_arrival_utc - prev_sched_utc) <> 0
        and epoch(observed_arrival_utc - prev_observed_utc)
            < {{ var('bunching_ratio') }}
                * abs(epoch(sched_arrival_utc - prev_sched_utc))
        as bunched
from paired
where prev_trip_id is not null
) as day_rows
{{ window_rows() }}
