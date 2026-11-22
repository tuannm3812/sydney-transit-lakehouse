-- Route x service day x local hour of scheduled start: trip outcomes,
-- on-time share and delay at stops, and the bunched share of gaps.
with trips as (
    select
        *,
        hour(timezone('{{ var("sydney_tz") }}', sched_start_utc)) as local_hour
    from {{ ref('fct_trip_outcome') }}
),

trip_counts as (
    select
        service_date,
        mode,
        region,
        route_id,
        any_value(route_short_name) as route_short_name,
        any_value(agency_name) as agency_name,
        local_hour,
        count(*) as trips_scheduled,
        count(*) filter (tu_covered) as trips_covered,
        count(*) filter (outcome <> 'not_judged') as trips_judged,
        count(*) filter (outcome = 'ran') as trips_ran,
        count(*) filter (outcome = 'partial') as trips_partial,
        count(*) filter (outcome = 'cancelled') as trips_cancelled,
        count(*) filter (outcome = 'never_seen') as trips_never_seen
    from trips
    group by service_date, mode, region, route_id, local_hour
),

stops as (
    select
        t.service_date,
        t.route_id,
        t.local_hour,
        count(*) as stop_arrivals,
        avg((a.delay_s between -{{ var('on_time_early_s') }}
            and {{ var('on_time_late_s') }})::int) as on_time_share,
        avg(a.delay_s) as mean_delay_s,
        quantile_cont(a.delay_s, 0.9) as p90_delay_s
    from {{ ref('fct_stop_arrival') }} a
    join trips t on t.trip_id = a.trip_id and t.service_date = a.service_date
    where not a.skipped
    group by all
),

gaps as (
    select
        h.service_date,
        h.route_id,
        t.local_hour,
        count(*) as headways,
        avg(h.bunched::int) as bunched_share
    from {{ ref('fct_headway') }} h
    join trips t on t.trip_id = h.trip_id and t.service_date = h.service_date
    group by all
)

select
    c.*,
    -- only trips whose whole run was captured can be never_seen
    case when c.trips_covered > 0
        then c.trips_never_seen / c.trips_covered end as never_seen_share,
    s.stop_arrivals,
    s.on_time_share,
    s.mean_delay_s,
    s.p90_delay_s,
    g.headways,
    g.bunched_share
from trip_counts c
left join stops s using (service_date, route_id, local_hour)
left join gaps g using (service_date, route_id, local_hour)
