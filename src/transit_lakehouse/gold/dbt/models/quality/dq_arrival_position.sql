-- An independent check of fct_stop_arrival against the vehicle
-- positions feed, which TfNSW publishes separately: at each observed
-- arrival, take the trip's positions just before and just after it
-- (each at most 30 s away) and measure how far the nearer one is from
-- the stop. If the arrival times are right, the bus is at the stop.
with arr as (
    select
        a.service_date,
        a.mode,
        a.trip_id,
        a.start_date,
        a.observed_arrival_utc as t,
        s.stop_lat,
        s.stop_lon
    from {{ ref('fct_stop_arrival') }} a
    join {{ ref('stg_service_day') }} d using (service_date)
    join {{ source('silver', 'timetable_stop') }} s
        on s.version = d.version and s.stop_id = a.stop_id
    where not a.skipped
),

pos as (
    select trip_id, start_date, event_ts, latitude, longitude
    from {{ source('silver', 'vehicle_position') }}
    where trip_id is not null and not coalesce(quarantined, false)
),

before as (
    select a.*, p.event_ts as t1, p.latitude as lat1, p.longitude as lon1
    from arr a
    asof join pos p
        on p.trip_id = a.trip_id and p.start_date = a.start_date
        and p.event_ts <= a.t
),

bracketed as (
    select b.*, p.event_ts as t2, p.latitude as lat2, p.longitude as lon2
    from before b
    asof join pos p
        on p.trip_id = b.trip_id and p.start_date = b.start_date
        and p.event_ts >= b.t
    where epoch(b.t - b.t1) <= 30
),

measured as (
    select
        service_date,
        mode,
        least(
            {{ haversine_m('stop_lat', 'stop_lon', 'lat1', 'lon1') }},
            {{ haversine_m('stop_lat', 'stop_lon', 'lat2', 'lon2') }}
        ) as metres
    from bracketed
    where epoch(t2 - t) <= 30
)

select
    service_date,
    mode,
    count(*) as arrivals_checked,
    median(metres) as median_m,
    quantile_cont(metres, 0.95) as p95_m,
    avg((metres <= 150)::int) as within_150m_share
from measured
group by all
