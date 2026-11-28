{{ config(materialized="table") }}
-- Each realtime trip stop's latest prediction, for fct_trip_outcome.
-- The time and the trip delay come from the same, latest row: two
-- separate arg_max calls skip nulls on their own and could pair one
-- row's time with another row's delay.
-- A table, so the aggregate over every prediction runs on its own
-- (I-23).
with last_row as (
    select
        mode,
        trip_id,
        start_date,
        stop_sequence,
        arg_max(
            {
                'predicted_at': coalesce(arrival_time, departure_time),
                'trip_delay_s': trip_delay_s
            },
            fetched_at
        ) as latest,
        max(fetched_at) as changed_at
    from {{ ref('stg_rt_stop_prediction') }}
    where trip_id is not null
    group by all
)

select
    mode,
    trip_id,
    start_date,
    stop_sequence,
    latest.predicted_at as raw_predicted_at,
    latest.trip_delay_s as row_trip_delay_s,
    changed_at
from last_row
