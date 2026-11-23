-- Stretches of time each realtime feed was captured, from
-- bronze.snapshot: a new stretch starts after a gap longer than
-- coverage_max_gap_s. Outcomes are judged only inside a stretch, so a
-- missing hour of raw data never reads as missing buses. A snapshot
-- that could not be read or decoded as a whole is not capture: it
-- shows no trips, so a run of them would turn every trip into a ghost.
-- A partial decode keeps its readable entities and still counts.
with snaps as (
    select
        feed,
        mode,
        kind,
        fetched_at,
        lag(fetched_at) over (partition by feed order by fetched_at) as prev_at
    from {{ source('bronze', 'snapshot') }}
    where kind in ('trip_updates', 'vehicle_positions')
        and (decode_error is null or decode_error like 'partial_decode%')
),

flagged as (
    select
        *,
        case
            when prev_at is null
                or epoch(fetched_at - prev_at) > {{ var('coverage_max_gap_s') }}
            then 1 else 0
        end as new_stretch
    from snaps
),

numbered as (
    select
        *,
        sum(new_stretch) over (
            partition by feed order by fetched_at
            rows unbounded preceding
        ) as stretch
    from flagged
)

select
    feed,
    mode,
    kind,
    stretch,
    min(fetched_at) as covered_from,
    max(fetched_at) as covered_to,
    count(*) as snapshots
from numbered
group by all
