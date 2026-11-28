-- silver.stop_prediction with start_date filled for runs dated from the
-- timetable (stg_rt_run, D-059), and stop_sequence filled from the
-- trip's timetable pattern where the feed gives only the stop id: Sydney
-- Trains send neither (O-04). A stop the pattern calls at twice takes
-- its first call. Rows that carry both pass through as they are.
with src as (
    select * from {{ source('silver', 'stop_prediction') }}
),

dated as (
    select * from src where start_date is not null

    union all by name

    select s.* replace (r.start_date as start_date)
    from src s
    asof join {{ ref('stg_rt_run') }} r
        on r.mode = s.mode
        and r.trip_id = s.trip_id
        and r.run_from - interval 2 hour <= s.fetched_at
    where s.start_date is null and s.trip_id is not null

    union all by name

    select * from src where start_date is null and trip_id is null
),

trip_pattern as (
    select trip_id, mode, arg_max(pattern_id, service_date) as pattern_id
    from {{ ref('stg_scheduled_trip') }}
    where trip_id in (
        select distinct trip_id from src where stop_sequence is null
    )
    group by all
),

stop_call as (
    select t.trip_id, t.mode, p.stop_id, min(p.stop_sequence) as stop_sequence
    from trip_pattern t
    join {{ ref('stg_pattern_stop') }} p on p.pattern_id = t.pattern_id
    group by all
)

select * from dated where stop_sequence is not null

union all by name

select d.* replace (c.stop_sequence as stop_sequence)
from dated d
left join stop_call c
    on c.trip_id = d.trip_id and c.mode = d.mode and c.stop_id = d.stop_id
where d.stop_sequence is null
