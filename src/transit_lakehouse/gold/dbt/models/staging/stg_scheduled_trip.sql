-- Every trip the timetable in effect schedules on each service day,
-- with its route, operator, mode, region and UTC start and end.
with day as (
    select * from {{ ref('stg_service_day') }}
),

cal as (
    select
        d.service_date,
        c.service_id
    from day d
    join {{ source('silver', 'timetable_calendar') }} c
        on c.version = d.version
        and d.service_date between {{ gtfs_date('c.start_date') }}
            and {{ gtfs_date('c.end_date') }}
        and case isodow(d.service_date)
            when 1 then c.monday when 2 then c.tuesday
            when 3 then c.wednesday when 4 then c.thursday
            when 5 then c.friday when 6 then c.saturday
            else c.sunday
        end = 1
),

exceptions as (
    select
        d.service_date,
        x.service_id,
        x.exception_type
    from day d
    join {{ source('silver', 'timetable_calendar_date') }} x
        on x.version = d.version
        and {{ gtfs_date('x.date') }} = d.service_date
),

active as (
    select service_date, service_id from cal
    where not exists (
        select 1 from exceptions e
        where e.service_date = cal.service_date
            and e.service_id = cal.service_id
            and e.exception_type = 2
    )
    union
    select service_date, service_id from exceptions where exception_type = 1
),

pattern as (
    select
        pattern_id,
        arg_min(stop_id, stop_sequence) as first_stop_id,
        max(coalesce(arrival_s, departure_s)) as last_arrival_s
    from {{ source('silver', 'timetable_pattern_stop') }}
    group by pattern_id
),

box as (
    select
        {{ var('sydney_box')['min_lat'] }} as min_lat,
        {{ var('sydney_box')['max_lat'] }} as max_lat,
        {{ var('sydney_box')['min_lon'] }} as min_lon,
        {{ var('sydney_box')['max_lon'] }} as max_lon
)

select
    d.service_date,
    d.version,
    d.version_fallback,
    t.trip_id,
    -- the start_date TfNSW's realtime feeds give this trip
    strftime(
        case when t.first_departure_s >= 86400
            then d.service_date + interval 1 day
            else d.service_date end,
        '%Y%m%d'
    ) as start_date,
    t.route_id,
    r.route_short_name,
    r.route_type,
    r.route_desc as network,
    r.agency_id,
    a.agency_name,
    coalesce(n.mode, {{ mode_of_route_type('r.route_type') }}) as mode,
    case
        when n.region in ('sydney', 'regional') then n.region
        when s.stop_lat between box.min_lat and box.max_lat
            and s.stop_lon between box.min_lon and box.max_lon
            then 'sydney'
        else 'regional'
    end as region,
    t.direction_id,
    t.trip_headsign,
    t.pattern_id,
    t.stop_count,
    {{ gtfs_to_utc('d.day_origin_utc', 't.first_departure_s') }} as sched_start_utc,
    {{ gtfs_to_utc('d.day_origin_utc', 'p.last_arrival_s') }} as sched_end_utc,
    d.day_origin_utc
from day d
join active act on act.service_date = d.service_date
join {{ source('silver', 'timetable_trip') }} t
    on t.version = d.version and t.service_id = act.service_id
left join {{ source('silver', 'timetable_route') }} r
    on r.version = d.version and r.route_id = t.route_id
left join {{ source('silver', 'timetable_agency') }} a
    on a.version = d.version and a.agency_id = r.agency_id
left join {{ ref('network_region') }} n on n.network = r.route_desc
left join pattern p on p.pattern_id = t.pattern_id
left join {{ source('silver', 'timetable_stop') }} s
    on s.version = d.version and s.stop_id = p.first_stop_id
cross join box
