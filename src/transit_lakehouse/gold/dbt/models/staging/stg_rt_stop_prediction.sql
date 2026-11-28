{{ config(materialized="table") }}
-- silver.stop_prediction with start_date filled for runs dated from the
-- timetable (stg_rt_run, D-059), and stop_sequence filled from the
-- trip's timetable pattern where the feed gives only the stop id: Sydney
-- Trains send neither (O-04). A stop the pattern calls at twice takes
-- its first call. Rows that carry both pass through as they are; the
-- rest are filled once, in the table stg_rt_stop_prediction_filled.
-- A table itself: DuckDB aggregates over this union as a view ran out
-- of memory where the same aggregate over a table did not (I-23).
select * from {{ source('silver', 'stop_prediction') }}
where start_date is not null and stop_sequence is not null

union all by name

select * from {{ ref('stg_rt_stop_prediction_filled') }}
