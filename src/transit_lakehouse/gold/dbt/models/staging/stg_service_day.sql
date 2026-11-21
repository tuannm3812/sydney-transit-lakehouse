-- One row per service day with realtime trips, with the UTC origin of
-- its GTFS clock and the timetable version in effect: the latest one
-- fetched before the day started. If none was fetched yet (the first
-- days of the archive), the earliest version is used and flagged.
-- The day before each realtime start date is included, because TfNSW
-- dates a trip that runs after midnight by the calendar day
-- (stg_rt_trip_state).
with rt_days as (
    select distinct {{ gtfs_date('start_date') }} as d
    from {{ source('silver', 'trip_status') }}
    where start_date is not null
),

days as (
    select d as service_date from rt_days
    union
    select (d - interval 1 day)::date from rt_days
),

windowed as (
    select service_date, {{ service_day_origin('service_date') }} as day_origin_utc
    from days
    where service_date is not null {{ in_service_window('service_date') }}
),

versions as (
    select version, fetched_at from {{ source('silver', 'timetable_version') }}
),

picked as (
    select
        w.service_date,
        w.day_origin_utc,
        (
            select arg_max(v.version, v.fetched_at)
            from versions v
            where v.fetched_at <= w.day_origin_utc
        ) as before_day,
        (select arg_min(v.version, v.fetched_at) from versions v) as earliest
    from windowed w
)

select
    service_date,
    day_origin_utc,
    coalesce(before_day, earliest) as version,
    before_day is null as version_fallback
from picked
where coalesce(before_day, earliest) is not null
