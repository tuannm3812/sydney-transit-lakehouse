-- One row per bunch (Phase 3, D-045): two consecutive vehicles of a
-- route and direction that were bunched at bunch_min_stops stops or
-- more on one service day. One short gap at one stop is often a
-- prediction blip; a pair that stays together for several stops is
-- what a rider waiting further down the route sees.
with h as (
    select * from {{ ref('fct_headway') }}
    where scheduled_headway_s > 0
),

pairs as (
    select
        service_date,
        mode,
        region,
        route_id,
        any_value(route_short_name) as route_short_name,
        direction_id,
        prev_trip_id as lead_trip_id,
        trip_id as follow_trip_id,
        count(*) as stops_paired,
        count(*) filter (bunched) as stops_bunched,
        min(observed_arrival_utc) filter (bunched) as first_bunched_at,
        max(observed_arrival_utc) filter (bunched) as last_bunched_at,
        arg_min(stop_id, stop_sequence) filter (bunched)
            as first_bunched_stop_id,
        arg_max(stop_id, stop_sequence) filter (bunched)
            as last_bunched_stop_id,
        min(observed_headway_s) filter (bunched) as min_gap_s,
        round(median(scheduled_headway_s)) as scheduled_gap_s
    from h
    group by service_date, mode, region, route_id, direction_id,
        prev_trip_id, trip_id
)

select
    *,
    timezone('{{ var("sydney_tz") }}', first_bunched_at) as first_bunched_local,
    hour(timezone('{{ var("sydney_tz") }}', first_bunched_at)) as local_hour,
    case when isodow(service_date) >= 6 then 'weekend' else 'weekday' end
        as day_type,
    round(stops_bunched / stops_paired, 3) as share_of_stops
from pairs
where stops_bunched >= {{ var('bunch_min_stops') }}
