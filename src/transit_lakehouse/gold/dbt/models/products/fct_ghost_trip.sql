-- The timetable honesty tracker (Phase 3, D-045): one row per judged
-- trip that never showed up ("missing", outcome never_seen) or that
-- left the feed well before its last stop ("vanished", outcome
-- partial), for operators that publish realtime that day (D-038).
-- "Missing" is a ghost candidate, not proof the trip did not run: the
-- confidence comes from fct_trip_outcome, and the hand-check
-- (`gold handcheck`) tests a sample against the raw snapshots.
with t as (
    select * from {{ ref('fct_trip_outcome') }}
    where operator_covered and outcome in ('never_seen', 'partial')
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
    case outcome when 'never_seen' then 'missing' else 'vanished' end
        as ghost_kind,
    confidence,
    sched_start_utc,
    sched_end_utc,
    timezone('{{ var("sydney_tz") }}', sched_start_utc) as sched_start_local,
    hour(timezone('{{ var("sydney_tz") }}', sched_start_utc)) as local_hour,
    case when isodow(service_date) >= 6 then 'weekend' else 'weekday' end
        as day_type,
    stop_count,
    -- vanished trips: how far they got before they left the feed
    case when outcome = 'partial'
        then stop_count - min_stops_left end as stops_done,
    case when outcome = 'partial' and stop_count > 0
        then round((stop_count - min_stops_left) / stop_count, 3) end
        as share_done,
    case when outcome = 'partial'
        then coalesce(left_feed_at, last_state_at) end as vanished_at,
    route_trips_seen,
    seen_vp,
    vp_covered,
    first_seen_at,
    last_state_at,
    timetable_version
from t
