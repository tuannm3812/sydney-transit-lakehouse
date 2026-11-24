-- The bunching radar's hot spots (Phase 3, D-039): route x direction x
-- stop x day type x local hour, over every service day built. A gap is
-- bunched when the observed gap is under bunching_ratio of the
-- timetabled one (fct_headway). The stop's name and position come
-- from the timetable version in effect that day.
with h as (
    select
        h.*,
        case when isodow(h.service_date) >= 6 then 'weekend'
            else 'weekday' end as day_type,
        hour(timezone('{{ var("sydney_tz") }}', h.observed_arrival_utc))
            as local_hour,
        d.version
    from {{ ref('fct_headway') }} h
    join {{ ref('stg_service_day') }} d on d.service_date = h.service_date
    where h.scheduled_headway_s > 0
),

agg as (
    select
        mode,
        region,
        route_id,
        any_value(route_short_name) as route_short_name,
        direction_id,
        stop_id,
        day_type,
        local_hour,
        arg_max(version, service_date) as version,
        count(distinct service_date) as service_days,
        count(*) as headways,
        count(*) filter (bunched) as bunched,
        round(avg(bunched::int), 4) as bunched_share,
        round(median(observed_headway_s / scheduled_headway_s), 3)
            as median_gap_ratio,
        round(median(scheduled_headway_s)) as median_scheduled_s,
        round(median(observed_headway_s)) as median_observed_s
    from h
    group by mode, region, route_id, direction_id, stop_id, day_type,
        local_hour
)

select
    a.* exclude (version),
    s.stop_name,
    s.stop_lat,
    s.stop_lon
from agg a
left join {{ source('silver', 'timetable_stop') }} s
    on s.version = a.version and s.stop_id = a.stop_id
