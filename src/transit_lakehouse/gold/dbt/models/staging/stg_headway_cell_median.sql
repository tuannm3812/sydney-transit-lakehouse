{{ config(materialized="table") }}
-- The bunching radar's cell medians (stg_headway_cell). Its own table:
-- in one query with the cell counts it did not fit the build's 300 MB
-- on a real day (I-23).
select
    cell,
    round(median(gap_ratio), 3) as median_gap_ratio,
    round(median(scheduled_s)) as median_scheduled_s,
    round(median(observed_s)) as median_observed_s
from {{ ref('stg_headway_cell') }}
group by cell
