-- One row per scheduled trip x service day for every mode whose trip
-- updates were captured that day: ran, partial, cancelled, never_seen
-- (the "ghost" candidate) or not_judged, with a confidence level.
-- REMOVED rows (D-036) only set left_feed_at: a trip that left the
-- feed with more than ran_max_stops_left stops to go is partial.
-- Stops to go is the smaller of the fewest stops a trip update listed
-- and the stops whose latest predicted time was still ahead when the
-- trip left the feed (or, if it never left, when a stop of it last
-- changed). Bus feeds drop each stop once passed, so the first count
-- shrinks; the metro, light rail and ferry feeds keep passed stops
-- listed to the end, so only the second one does (D-047).
-- Stop predictions are stored relative to the trip delay (D-028): a
-- late bus whose every stop moves by the same amount writes only a new
-- trip delay. So a stop's latest prediction is shifted by the trip's
-- delay at exit minus the delay its row was written against, as in
-- fct_stop_arrival; without it a late trip's stale times look passed.
-- A trip that left the feed less than partial_min_early_minutes before
-- its scheduled end also ran: on 22 and 23 Nov 2026 most trips that
-- left with stops to go left within minutes of their scheduled end,
-- which is the feed retiring them on schedule, not a trip cut short.
--
-- A trip is judged only when its trip-update feed was captured from
-- outcome_lead_minutes before its scheduled start to
-- outcome_grace_minutes after its scheduled end, so capture gaps and
-- late running are never read as missing buses. That holds for every
-- outcome, ran and cancelled included: judging only the trips that
-- were seen in a gap would inflate the ran share (D-035, D-048). "never_seen" means no
-- trip update and no vehicle position mentioned the trip; it is not
-- proof the bus did not run (docs/7_data_model.md).
--
-- A mode's feed does not carry every operator of that mode: the
-- light rail feed is the Inner West line only (agency LR, not L2/L3
-- or Parramatta), the ferry feed is Sydney Ferries only, and some bus
-- operators publish no realtime. So a trip can be never_seen only when
-- another trip of its operator and mode appeared in trip updates that
-- service day (operator_covered, D-038); otherwise it is not_judged.
{{ per_day() }}
select * from (
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

-- Every realtime CTE is keyed on mode as well as trip and start date,
-- as stg_rt_trip_state is, so a trip id two modes share never mixes.
rt as (
    select
        mode,
        trip_id,
        start_date,
        min(fetched_at) as first_seen_at,
        max(fetched_at) filter (trip_status <> 'REMOVED') as last_state_at,
        max(fetched_at) filter (trip_status = 'REMOVED') as left_feed_at,
        min(stop_count) filter (trip_status <> 'REMOVED') as min_stops_left,
        arg_max(trip_status, fetched_at) filter (trip_status <> 'REMOVED')
            as last_status,
        arg_max(current_delay_s, fetched_at)
            filter (trip_status <> 'REMOVED') as exit_delay_s,
        count(*) filter (trip_status <> 'REMOVED') as states
    from {{ ref('stg_rt_trip_state') }}
    group by all
),

last_prediction as (
    select * from {{ ref('stg_rt_last_prediction') }}
),

stops_ahead as (
    select
        rt.mode,
        rt.trip_id,
        rt.start_date,
        count(*) filter (
            where case
                when p.row_trip_delay_s is null or rt.exit_delay_s is null
                    then p.raw_predicted_at
                else p.raw_predicted_at + to_seconds(
                    (rt.exit_delay_s - p.row_trip_delay_s)::bigint
                )
            end > ref.left_at
        ) as stops_ahead
    from rt
    join last_prediction p
        on p.mode = rt.mode
        and p.trip_id = rt.trip_id
        and p.start_date = rt.start_date
    join (
        select
            p.mode,
            p.trip_id,
            p.start_date,
            coalesce(any_value(r.left_feed_at), max(p.changed_at)) as left_at
        from last_prediction p
        join rt r
            on r.mode = p.mode
            and r.trip_id = p.trip_id
            and r.start_date = p.start_date
        group by all
    ) ref
        on ref.mode = rt.mode
        and ref.trip_id = rt.trip_id
        and ref.start_date = rt.start_date
    group by all
),

vp as (
    select
        mode,
        trip_id,
        start_date,
        min(fetched_at) as first_position_at,
        max(fetched_at) as last_position_at,
        count(*) as positions
    from {{ ref('stg_rt_vehicle_position') }}
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
        rt.left_feed_at,
        least(rt.min_stops_left, sa.stops_ahead) as min_stops_left,
        rt.last_status,
        vp.first_position_at,
        vp.last_position_at,
        coalesce(vp.positions, 0) as positions,
        rt.trip_id is not null as seen_tu,
        vp.trip_id is not null as seen_vp
    from judged j
    left join rt
        on rt.mode = j.mode
        and rt.trip_id = j.trip_id
        and rt.start_date = j.start_date
    left join stops_ahead sa
        on sa.mode = j.mode
        and sa.trip_id = j.trip_id
        and sa.start_date = j.start_date
    left join vp
        on vp.mode = j.mode
        and vp.trip_id = j.trip_id
        and vp.start_date = j.start_date
),

operator_seen as (
    select distinct service_date, mode, agency_id
    from base
    where seen_tu
),

route_seen as (
    select
        service_date,
        mode,
        route_id,
        count(*) filter (seen_tu or seen_vp) as seen
    from base
    group by all
),

outcome as (
    select
        b.*,
        coalesce(rs.seen, 0) - (b.seen_tu or b.seen_vp)::int as route_trips_seen,
        os.mode is not null as operator_covered,
        case
            when not b.tu_covered then 'not_judged'
            when b.last_status = 'CANCELED' then 'cancelled'
            when b.min_stops_left <= {{ var('ran_max_stops_left') }} then 'ran'
            when b.left_feed_at >= b.sched_end_utc
                - to_minutes({{ var('partial_min_early_minutes') }})
                then 'ran'
            -- Only an operator in the trip-update feed that day can
            -- have a partial trip; a trip seen only in positions of an
            -- uncovered operator is not judged, as never_seen is not.
            when (b.seen_tu or b.seen_vp) and os.mode is not null
                then 'partial'
            when os.mode is not null then 'never_seen'
            else 'not_judged'
        end as outcome
    from base b
    left join route_seen rs
        on rs.service_date = b.service_date
        and rs.mode = b.mode
        and rs.route_id = b.route_id
    left join operator_seen os
        on os.service_date = b.service_date
        and os.mode = b.mode
        and os.agency_id is not distinct from b.agency_id
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
    operator_covered,
    first_seen_at,
    last_state_at,
    left_feed_at,
    min_stops_left,
    last_status,
    first_position_at,
    last_position_at,
    positions,
    route_trips_seen,
    version as timetable_version,
    version_fallback
from outcome
) as day_rows
{{ window_rows() }}
