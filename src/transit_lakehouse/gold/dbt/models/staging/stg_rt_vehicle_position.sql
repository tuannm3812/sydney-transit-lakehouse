-- silver.vehicle_position with start_date filled for runs dated from the timetable
-- (stg_rt_run, D-059). Rows that carry a start_date pass through as
-- they are. A row is given the run of its trip id that started last at
-- or before it, allowing 2 hours for rows seen just before the run.
select * from {{ source('silver', 'vehicle_position') }}
where start_date is not null

union all by name

select s.* replace (r.start_date as start_date)
from {{ source('silver', 'vehicle_position') }} s
asof join {{ ref('stg_rt_run') }} r
    on r.mode = s.mode
    and r.trip_id = s.trip_id
    and r.run_from - interval 2 hour <= s.fetched_at
where s.start_date is null and s.trip_id is not null

union all by name

select * from {{ source('silver', 'vehicle_position') }}
where start_date is null and trip_id is null
