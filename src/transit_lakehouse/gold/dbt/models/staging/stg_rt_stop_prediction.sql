-- silver.stop_prediction with start_date filled for runs dated from the
-- timetable (stg_rt_run, D-059), and stop_sequence filled from the
-- trip's timetable pattern where the feed gives only the stop id: Sydney
-- Trains send neither (O-04). A stop the pattern calls at twice takes
-- its first call. Rows that carry both pass through as they are.
-- The source is read once per branch, never through a shared CTE:
-- DuckDB materializes a CTE used twice, which for every bus prediction
-- does not fit the build's memory limit.
with trip_pattern as (
    select trip_id, mode, arg_max(pattern_id, service_date) as pattern_id
    from {{ ref('stg_scheduled_trip') }}
    where mode in (
        select distinct mode
        from {{ source('silver', 'stop_prediction') }}
        where stop_sequence is null
    )
    group by all
),

stop_call as (
    select t.trip_id, t.mode, p.stop_id, min(p.stop_sequence) as stop_sequence
    from trip_pattern t
    join {{ ref('stg_pattern_stop') }} p on p.pattern_id = t.pattern_id
    group by all
),

-- rows missing a start_date or a stop_sequence, the start_date filled
fix as (
    select s.* replace (coalesce(s.start_date, r.start_date) as start_date)
    from {{ source('silver', 'stop_prediction') }} s
    asof left join {{ ref('stg_rt_run') }} r
        on r.mode = s.mode
        and r.trip_id = s.trip_id
        and r.run_from - interval 2 hour <= s.fetched_at
    where s.start_date is null or s.stop_sequence is null
)

select * from {{ source('silver', 'stop_prediction') }}
where start_date is not null and stop_sequence is not null

union all by name

select f.* replace (
    coalesce(f.stop_sequence, c.stop_sequence) as stop_sequence
)
from fix f
left join stop_call c
    on c.trip_id = f.trip_id and c.mode = f.mode and c.stop_id = f.stop_id
