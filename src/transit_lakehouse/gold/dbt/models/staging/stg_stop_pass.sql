{{ config(materialized="table") }}
-- Every time a realtime trip's stop dropped out of its remaining-stop
-- list (fct_stop_arrival explains the rule). A table, and the step
-- after it another, so that no query holds this join, the window that
-- keeps a stop's last pass and the prediction lookup at once: together
-- they did not fit the build's 300 MB on a real day (I-23).
with sched as (
    select service_date, trip_id, pattern_id, day_origin_utc
    from {{ ref('stg_scheduled_trip') }}
),

passes as (
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
        p.pos_from_start = 1 as at_origin,
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
        {{ in_service_window('s.service_date') }}
        and s.trip_status not in ('CANCELED', 'REMOVED')
        and (
            s.next_trip_status is distinct from 'REMOVED'
            or s.stop_count <= {{ var('ran_max_stops_left') }}
        )
)

select * from passes
