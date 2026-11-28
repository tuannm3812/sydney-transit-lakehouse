{{ config(materialized="table") }}
-- Realtime runs of trips whose trip updates carry no start_date, each
-- dated from the timetable (D-059). Sydney Trains send none (O-04),
-- and a train trip id runs on several service days, so a run, the rows
-- of one trip id with no gap over 4 hours, is matched to the scheduled
-- trip of that id and mode starting nearest the run's first row, from
-- 6 hours before its start to 3 hours after its end. The run gets the
-- calendar day the trip starts on in Sydney, as TfNSW dates the other
-- modes, so stg_rt_trip_state moves after-midnight trips back as usual.
-- A run that matches no scheduled trip is dated by the Sydney day of
-- its first row and stays unmatched, so dq_trip_match still counts it.
with undated as (
    select
        mode,
        trip_id,
        fetched_at,
        case
            when lag(fetched_at) over w is null
                or fetched_at - lag(fetched_at) over w > interval 4 hour
            then 1 else 0
        end as new_run
    from {{ source('silver', 'trip_status') }}
    where start_date is null and trip_id is not null
    window w as (partition by mode, trip_id order by fetched_at)
),

numbered as (
    select
        *,
        sum(new_run) over (
            partition by mode, trip_id order by fetched_at
            rows unbounded preceding
        ) as run
    from undated
),

runs as (
    select
        mode,
        trip_id,
        run,
        min(fetched_at) as run_from,
        max(fetched_at) as run_to
    from numbered
    group by all
),

matched as (
    select
        r.*,
        s.sched_start_utc,
        row_number() over (
            partition by r.mode, r.trip_id, r.run
            order by abs(epoch(r.run_from - s.sched_start_utc))
        ) as nearest
    from runs r
    join {{ ref('stg_scheduled_trip') }} s
        on s.trip_id = r.trip_id
        and s.mode = r.mode
        and r.run_from >= s.sched_start_utc - interval 6 hour
        and r.run_from <= s.sched_end_utc + interval 3 hour
)

select
    r.mode,
    r.trip_id,
    r.run,
    r.run_from,
    r.run_to,
    strftime(
        timezone(
            '{{ var("sydney_tz") }}',
            coalesce(m.sched_start_utc, r.run_from)
        ),
        '%Y%m%d'
    ) as start_date,
    m.sched_start_utc is not null as timetable_dated
from runs r
left join matched m
    on m.mode = r.mode
    and m.trip_id = r.trip_id
    and m.run = r.run
    and m.nearest = 1
