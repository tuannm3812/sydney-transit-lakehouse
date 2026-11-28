{{ config(materialized="table") }}
-- Each headway with a non-zero timetabled gap (fct_headway), with its
-- bunching radar cell (mart_bunching_hotspot): route x direction x stop
-- x day type x local hour, numbered. A table with an integer cell id,
-- because the cell medians over text keys did not fit the build's
-- 300 MB on a real day (I-23).
with h as (
    select
        h.mode,
        h.region,
        h.route_id,
        h.route_short_name,
        h.direction_id,
        h.stop_id,
        h.service_date,
        h.bunched,
        h.overtaken,
        case when isodow(h.service_date) >= 6 then 'weekend'
            else 'weekday' end as day_type,
        hour(timezone('{{ var("sydney_tz") }}', h.observed_arrival_utc))
            as local_hour,
        d.version,
        (h.observed_headway_s / abs(h.scheduled_headway_s))::float
            as gap_ratio,
        abs(h.scheduled_headway_s)::integer as scheduled_s,
        h.observed_headway_s::integer as observed_s
    from {{ ref('fct_headway') }} h
    -- the version from the per-day trip outcomes, not stg_service_day,
    -- which holds only the latest build's window (D-060)
    join (
        select service_date, any_value(timetable_version) as version
        from {{ ref('fct_trip_outcome') }}
        group by 1
    ) d on d.service_date = h.service_date
    where h.scheduled_headway_s <> 0
)

select
    dense_rank() over (
        order by mode, region, route_id, direction_id, stop_id, day_type,
            local_hour
    )::integer as cell,
    *
from h
