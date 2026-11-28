-- The bunching radar's hot spots (Phase 3, D-045): route x direction x
-- stop x day type x local hour, over every service day built. A gap is
-- bunched when the observed gap is under bunching_ratio of the
-- timetabled one (fct_headway), overtakes included (D-051). The stop's name and
-- position come from the timetable version in effect that day.
-- Cells are numbered in stg_headway_cell, and their medians taken in
-- stg_headway_cell_median, to fit the build's 300 MB (I-23).
with agg as (
    select
        cell,
        any_value(mode) as mode,
        any_value(region) as region,
        any_value(route_id) as route_id,
        any_value(route_short_name) as route_short_name,
        any_value(direction_id) as direction_id,
        any_value(stop_id) as stop_id,
        any_value(day_type) as day_type,
        any_value(local_hour) as local_hour,
        arg_max(version, service_date) as version,
        count(distinct service_date) as service_days,
        count(*) as headways,
        count(*) filter (bunched) as bunched,
        count(*) filter (overtaken) as overtaken,
        round(avg(bunched::int), 4) as bunched_share
    from {{ ref('stg_headway_cell') }}
    group by cell
)

select
    a.* exclude (cell, version),
    m.median_gap_ratio,
    m.median_scheduled_s,
    m.median_observed_s,
    s.stop_name,
    s.stop_lat,
    s.stop_lon
from agg a
join {{ ref('stg_headway_cell_median') }} m using (cell)
left join {{ source('silver', 'timetable_stop') }} s
    on s.version = a.version and s.stop_id = a.stop_id
