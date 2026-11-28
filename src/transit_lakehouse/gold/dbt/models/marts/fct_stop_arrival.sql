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
-- Two more kinds of row have no real arrival time, both from the
-- Sydney Trains feed (D-059), checked against vehicle positions on
-- 26 Nov 2026. That feed never reports a train early: of 521,169
-- train stop predictions none had a negative delay and 75% had 0. A
-- 0 there means on time or early by an unknown amount (the train was
-- at the stop a median 51 s before the timetable), so early_hidden is
-- true; the delay is a ceiling, which still says the stop was not
-- late. And at a train's origin the stop only drops out when the
-- train has gone, with a stale departure estimate (6% of such
-- arrivals had the train within 150 m). Both keep their row, with
-- time_known false.
{{ per_day() }}
select * from (
with joined as (
    select
        x.*,
        r.p.fetched_at as prediction_row_at,
        r.p.arrival_time as pred_arrival_time,
        r.p.arrival_delay_s as pred_arrival_delay_s,
        r.p.stop_status as stop_status,
        coalesce(r.p.arrival_delay_s, r.p.departure_delay_s) as row_delay_s,
        r.p.trip_delay_s as row_trip_delay_s
    from {{ ref('stg_stop_pass_last') }} x
    left join {{ ref('stg_stop_pass_prediction') }} r
        on r.mode = x.mode
        and r.trip_id = x.trip_id
        and r.start_date = x.start_date
        and r.stop_sequence = x.stop_sequence
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
    {{ early_hidden() }} as early_hidden,
    not (
        predicted_arrival_utc > left_feed_at
        and dropped_with > {{ var('capped_group_max_stops') }}
    )
    and not {{ early_hidden() }}
    and not (at_origin and mode in {{ mode_list('untimed_origin_modes') }})
        as time_known
from timed
) as day_rows
{{ window_rows() }}
