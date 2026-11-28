{{ config(materialized="table") }}
-- One row per service day with realtime trips, with the UTC origin of
-- its GTFS clock and the timetable version in effect. That is the
-- version, among those fetched before the day ended, that carries the
-- most of the day's realtime trip ids; a tie goes to the latest one
-- fetched before the day started, then to the earliest after (D-047).
-- TfNSW can put a day's trips in a publish made during that day: the
-- 22 Nov 2026 ferries were in no publish before it. If no version was
-- fetched before the day ended (the first days of the archive), the
-- earliest is used and flagged.
-- The day before each realtime start date is included, because TfNSW
-- dates a trip that runs after midnight by the calendar day
-- (stg_rt_trip_state). A feed that sends no start_date (Sydney Trains,
-- D-059) adds the Sydney dates it was fetched on, so a day carried only
-- by such trips still has its timetable.
with rt_days as (
    select distinct
        coalesce(
            {{ gtfs_date('start_date') }},
            timezone('{{ var("sydney_tz") }}', fetched_at)::date
        ) as d
    from {{ source('silver', 'trip_status') }}
),

days as (
    select d as service_date from rt_days
    union
    select (d - interval 1 day)::date from rt_days
),

windowed as (
    select service_date, {{ service_day_origin('service_date') }} as day_origin_utc
    from days
    where service_date is not null
        {{ in_service_window('service_date', days_before=1) }}
),

versions as (
    select version, fetched_at from {{ source('silver', 'timetable_version') }}
),

rt_trips as (
    select distinct {{ gtfs_date('start_date') }} as service_date, trip_id
    from {{ source('silver', 'trip_status') }}
    where start_date is not null and trip_id is not null
),

-- Only the latest version fetched before the day and those fetched
-- during it compete, so the scoring join stays a few versions a day
-- however long the archive grows (TfNSW publishes about 3 a day).
candidates as (
    select w.service_date, w.day_origin_utc, v.version, v.fetched_at
    from windowed w
    join versions v
        on v.fetched_at < w.day_origin_utc + interval 24 hour
        and v.fetched_at >= coalesce(
            (
                select max(b.fetched_at) from versions b
                where b.fetched_at <= w.day_origin_utc
            ),
            w.day_origin_utc
        )
),

scored as (
    select
        c.service_date,
        c.day_origin_utc,
        c.version,
        c.fetched_at,
        count(r.trip_id) as rt_trips_carried
    from candidates c
    left join {{ source('silver', 'timetable_trip') }} t
        on t.version = c.version
    left join rt_trips r
        on r.service_date = c.service_date and r.trip_id = t.trip_id
    group by all
),

ranked as (
    select
        *,
        row_number() over (
            partition by service_date
            order by
                rt_trips_carried desc,
                fetched_at <= day_origin_utc desc,
                case when fetched_at <= day_origin_utc then fetched_at end
                    desc,
                fetched_at
        ) as rn
    from scored
),

earliest as (
    select arg_min(version, fetched_at) as version from versions
)

select
    w.service_date,
    w.day_origin_utc,
    coalesce(r.version, e.version) as version,
    r.version is null as version_fallback
from windowed w
left join ranked r on r.service_date = w.service_date and r.rn = 1
cross join earliest e
where coalesce(r.version, e.version) is not null
