{{ config(materialized="table") }}
-- A stop count that rises again can pass a stop twice; keep the last
-- pass (stg_stop_pass, I-23).
select *
from {{ ref('stg_stop_pass') }}
qualify row_number() over (
    partition by mode, trip_id, start_date, stop_sequence
    order by left_feed_at desc
) = 1
