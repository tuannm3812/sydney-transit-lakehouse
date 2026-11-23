-- Gap between consecutive vehicles of a route and direction at each
-- stop, observed against the timetable's gap between the same two
-- trips; bunched when the observed gap is under bunching_ratio of the
-- scheduled one (docs/7_data_model.md).
-- A loop route calls at some stops twice (often its terminus), so
-- vehicles are paired per visit: the first call of one trip with the
-- first call of the next, never with its last.
with arr as (
    select
        a.service_date,
        a.mode,
        t.route_id,
        t.route_short_name,
        t.region,
        t.direction_id,
        a.stop_id,
        a.stop_sequence,
        p.visit,
        a.trip_id,
        a.sched_arrival_utc,
        a.observed_arrival_utc
    from {{ ref('fct_stop_arrival') }} a
    join {{ ref('stg_scheduled_trip') }} t
        on t.trip_id = a.trip_id and t.service_date = a.service_date
    join {{ ref('stg_pattern_stop') }} p
        on p.pattern_id = t.pattern_id and p.stop_sequence = a.stop_sequence
    where not a.skipped
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
        order by observed_arrival_utc, trip_id
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
    epoch(sched_arrival_utc - prev_sched_utc) > 0
        and epoch(observed_arrival_utc - prev_observed_utc)
            < {{ var('bunching_ratio') }} * epoch(sched_arrival_utc - prev_sched_utc)
        as bunched
from paired
where prev_trip_id is not null
