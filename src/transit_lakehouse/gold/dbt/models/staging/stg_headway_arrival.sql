{{ config(materialized="table") }}
-- The stop arrivals fct_headway pairs, with route, direction and visit.
-- A table, so the pairing window runs on its own (I-23).
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
-- a capped run of stops has no real arrival time (D-054)
where not a.skipped and a.time_known
