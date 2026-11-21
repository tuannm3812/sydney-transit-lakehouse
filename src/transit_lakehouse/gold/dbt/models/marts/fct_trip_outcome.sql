-- One row per scheduled trip x service day for every mode whose trip
-- updates were captured that day: ran, partial, cancelled, never_seen
-- (the "ghost" candidate) or not_judged, with a confidence level.
--
-- A trip is judged only when its trip-update feed was captured from
-- outcome_lead_minutes before its scheduled start to
-- outcome_grace_minutes after its scheduled end, so capture gaps and
-- late running are never read as missing buses. "never_seen" means no
-- trip update and no vehicle position mentioned the trip; it is not
-- proof the bus did not run (docs/7_data_model.md).
with sched as (
    select * from {{ ref('stg_scheduled_trip') }}
),

captured_modes as (
    select distinct
        mode,
        kind,
        covered_from,
        covered_to
    from {{ ref('stg_capture_window') }}
),

rt as (
    select
        trip_id,
        start_date,
        min(fetched_at) as first_seen_at,
        max(fetched_at) as last_state_at,
        min(stop_count) as min_stops_left,
        arg_max(trip_status, fetched_at) as last_status,
        count(*) as states
    from {{ ref('stg_rt_trip_state') }}
    group by all
),

vp as (
    select
        trip_id,
        start_date,
        min(fetched_at) as first_position_at,
        max(fetched_at) as last_position_at,
        count(*) as positions
    from {{ source('silver', 'vehicle_position') }}
    where trip_id is not null and not coalesce(quarantined, false)
    group by all
),

judged as (
    select
        s.*,
        exists (
            select 1 from captured_modes c
            where c.mode = s.mode
                and c.kind = 'trip_updates'
                and c.covered_from <= s.sched_start_utc
                    - to_minutes({{ var('outcome_lead_minutes') }})
                and c.covered_to >= s.sched_end_utc
                    + to_minutes({{ var('outcome_grace_minutes') }})
        ) as tu_covered,
        exists (
            select 1 from captured_modes c
            where c.mode = s.mode
                and c.kind = 'vehicle_positions'
                and c.covered_from <= s.sched_start_utc
                and c.covered_to >= s.sched_end_utc
        ) as vp_covered
    from sched s
    where s.mode in (
        select mode from captured_modes where kind = 'trip_updates'
    )
),

base as (
    select
        j.*,
        rt.first_seen_at,
        rt.last_state_at,
        rt.min_stops_left,
        rt.last_status,
        vp.first_position_at,
        vp.last_position_at,
        coalesce(vp.positions, 0) as positions,
        rt.trip_id is not null as seen_tu,
        vp.trip_id is not null as seen_vp
    from judged j
    left join rt on rt.trip_id = j.trip_id and rt.start_date = j.start_date
    left join vp on vp.trip_id = j.trip_id and vp.start_date = j.start_date
),

route_seen as (
    select service_date, route_id, count(*) filter (seen_tu or seen_vp) as seen
    from base
    group by all
),

outcome as (
    select
        b.*,
        coalesce(rs.seen, 0) - (b.seen_tu or b.seen_vp)::int as route_trips_seen,
        case
            when b.last_status = 'CANCELED' then 'cancelled'
            when b.min_stops_left <= {{ var('ran_max_stops_left') }} then 'ran'
            when (b.seen_tu or b.seen_vp) and b.tu_covered then 'partial'
            when not (b.seen_tu or b.seen_vp) and b.tu_covered then 'never_seen'
            else 'not_judged'
        end as outcome
    from base b
    left join route_seen rs
        on rs.service_date = b.service_date and rs.route_id = b.route_id
)

select
    service_date,
    trip_id,
    start_date,
    mode,
    region,
    route_id,
    route_short_name,
    network,
    agency_id,
    agency_name,
    direction_id,
    trip_headsign,
    stop_count,
    sched_start_utc,
    sched_end_utc,
    outcome,
    case
        when outcome = 'never_seen' and route_trips_seen > 0 and vp_covered
            then 'high'
        when outcome = 'never_seen' and route_trips_seen > 0 then 'medium'
        when outcome = 'never_seen' then 'low'
        when outcome = 'ran' and seen_tu then 'high'
        when outcome = 'not_judged' then null
        else 'medium'
    end as confidence,
    seen_tu,
    seen_vp,
    tu_covered,
    vp_covered,
    first_seen_at,
    last_state_at,
    min_stops_left,
    last_status,
    first_position_at,
    last_position_at,
    positions,
    route_trips_seen,
    version as timetable_version,
    version_fallback
from outcome
