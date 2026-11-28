-- One row per realtime trip x stop that the trip was seen to pass,
-- with its scheduled time and the observed time and delay.
--
-- A stop is passed when it drops out of the trip's remaining-stop
-- list: between two trip states the stop count fell past the stop's
-- position from the end (stg_pattern_stop). Its observed time is the
-- last prediction made while it was still listed, which is how
-- GTFS-realtime consumers usually read an arrival; left_feed_at, the
-- first poll without it, bounds it from above to within one interval.
-- The prediction is rebuilt from the D-028 encoding: the stop's latest
-- row, shifted by the trip delay at that moment minus the row's own.
-- When that prediction is later than left_feed_at, the feed contradicts
-- itself (on 17 Nov, mostly a stale late prediction for several stops
-- dropped at once); the stop was passed by left_feed_at, so the
-- observed time is capped there and method says so.
-- A cap is a time only when few stops dropped at once. When more than
-- capped_group_max_stops are capped together (a stale late prediction
-- for a run of stops, then the feed catching up), they all get the same
-- instant, which is no stop's real arrival: on 23 Nov 2026 such stops
-- were in 13% of bunched bus gaps (D-054). They keep their row, with
-- time_known false, and fct_headway and mart_route_reliability skip it.
-- A trip's last stops drop out when the trip leaves the feed (the
-- REMOVED row, D-036). That counts as passing them only when at most
-- ran_max_stops_left stops were still listed; a trip that vanishes
-- further from its end gives no arrivals for the stops it never
-- reached.
with sched as (
    select service_date, trip_id, pattern_id, day_origin_utc
    from {{ ref('stg_scheduled_trip') }}
),

passed as (
    select
        s.mode,
        s.trip_id,
        s.start_date,
        s.service_date,
        s.fetched_at as last_listed_at,
        s.next_fetched_at as left_feed_at,
        s.current_delay_s as trip_delay_at_exit,
        p.stop_sequence,
        p.stop_id,
        p.arrival_s,
        p.departure_s,
        t.day_origin_utc,
        s.stop_count - greatest(s.next_stop_count, 0) as dropped_with
    from {{ ref('stg_rt_trip_state') }} s
    join sched t
        on t.trip_id = s.trip_id and t.service_date = s.service_date
    join {{ ref('stg_pattern_stop') }} p
        on p.pattern_id = t.pattern_id
        and p.pos_from_end <= s.stop_count
        and p.pos_from_end > s.next_stop_count
    where s.next_fetched_at is not null
        and s.trip_status not in ('CANCELED', 'REMOVED')
        and (
            s.next_trip_status is distinct from 'REMOVED'
            or s.stop_count <= {{ var('ran_max_stops_left') }}
        )
    -- a stop count that rises again can pass a stop twice; keep the last
    qualify row_number() over (
        partition by s.mode, s.trip_id, s.start_date, p.stop_sequence
        order by s.next_fetched_at desc
    ) = 1
),

pred as (
    select
        mode, trip_id, start_date, stop_sequence, fetched_at,
        arrival_time, arrival_delay_s, departure_delay_s,
        stop_status, trip_delay_s
    from {{ ref('stg_rt_stop_prediction') }}
),

joined as (
    select
        x.*,
        r.fetched_at as prediction_row_at,
        r.arrival_time as pred_arrival_time,
        r.arrival_delay_s as pred_arrival_delay_s,
        r.stop_status,
        coalesce(r.arrival_delay_s, r.departure_delay_s) as row_delay_s,
        r.trip_delay_s as row_trip_delay_s
    from passed x
    asof left join pred r
        on r.mode = x.mode
        and r.trip_id = x.trip_id
        and r.start_date = x.start_date
        and r.stop_sequence = x.stop_sequence
        and x.left_feed_at > r.fetched_at
),

final as (
    select
        *,
        case
            when row_trip_delay_s is null or trip_delay_at_exit is null
                then row_delay_s
            else row_delay_s - row_trip_delay_s + trip_delay_at_exit
        end as delay_s,
        {{ gtfs_to_utc('day_origin_utc', 'coalesce(arrival_s, departure_s)') }}
            as sched_arrival_utc
    from joined
),

timed as (
    select
        *,
        sched_arrival_utc + to_seconds(delay_s::bigint) as predicted_arrival_utc
    from final
    where delay_s is not null
)

select
    service_date,
    mode,
    trip_id,
    start_date,
    stop_sequence,
    stop_id,
    sched_arrival_utc,
    least(predicted_arrival_utc, left_feed_at) as observed_arrival_utc,
    epoch(least(predicted_arrival_utc, left_feed_at) - sched_arrival_utc)::integer
        as delay_s,
    predicted_arrival_utc,
    coalesce(stop_status = 'SKIPPED', false) as skipped,
    dropped_with,
    last_listed_at,
    left_feed_at,
    -- the realtime feed's own scheduled time against the timetable's,
    -- in seconds; non-zero means the trip_id join picked the wrong trip
    epoch(pred_arrival_time - to_seconds(pred_arrival_delay_s::bigint))
        - epoch(sched_arrival_utc) as schedule_disagreement_s,
    case when predicted_arrival_utc > left_feed_at
        then 'capped_at_exit' else 'last_prediction' end as method,
    not (
        predicted_arrival_utc > left_feed_at
        and dropped_with > {{ var('capped_group_max_stops') }}
    ) as time_known
from timed
