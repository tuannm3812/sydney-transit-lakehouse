{{ config(materialized="table") }}
-- Each passed stop's last prediction made while it was still listed:
-- the latest stop_prediction row of the same trip and stop fetched
-- before the poll that dropped it. An equi-join and an aggregate, which
-- DuckDB can spill to disk, in place of an as-of join, which held every
-- prediction in memory (I-23).
select
    x.mode,
    x.trip_id,
    x.start_date,
    x.stop_sequence,
    arg_max(
        {
            'fetched_at': r.fetched_at,
            'arrival_time': r.arrival_time,
            'arrival_delay_s': r.arrival_delay_s,
            'departure_delay_s': r.departure_delay_s,
            'stop_status': r.stop_status,
            'trip_delay_s': r.trip_delay_s
        },
        r.fetched_at
    ) as p
from {{ ref('stg_stop_pass_last') }} x
join {{ ref('stg_rt_stop_prediction') }} r
    on r.mode = x.mode
    and r.trip_id = x.trip_id
    and r.start_date = x.start_date
    and r.stop_sequence = x.stop_sequence
    and r.fetched_at < x.left_feed_at
group by all
