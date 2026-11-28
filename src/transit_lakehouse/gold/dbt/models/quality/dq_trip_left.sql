-- Trips leaving the feed (D-036), per service day and mode, for the
-- Phase 2 gate (D-057): the REMOVED rows and how many still listed
-- stops, and the snapshots that ended more than mass_removal_share of
-- the trips the feed carried before them (at least
-- mass_removal_min_trips). A snapshot that is unreadable never counts
-- as the one before (D-049); an ending at an empty but readable
-- snapshot is counted apart, as the Sydney Trains feed empties for
-- about 90 s at 1 am Sydney (D-059). Kept per day, because the staging
-- rows it reads hold only the latest build's window (D-060).
{{ per_day() }}
select * from (
with removals as (
    select
        service_date,
        feed,
        mode,
        fetched_at,
        count(*) as removed,
        count(*) filter (coalesce(stop_count, -1) <> 0) as with_stops
    from {{ ref('stg_rt_trip_state') }}
    where trip_status = 'REMOVED' and service_date is not null
    group by all
),

snaps as (
    select
        feed,
        fetched_at,
        entities,
        lag(entities) over (partition by feed order by fetched_at)
            as prev_entities
    from {{ source('bronze', 'snapshot') }}
    where kind = 'trip_updates'
        and (decode_error is null or decode_error like 'partial_decode%')
),

joined as (
    select
        r.*,
        s.entities,
        s.prev_entities,
        s.prev_entities >= {{ var('mass_removal_min_trips') }}
            and r.removed / s.prev_entities
                > {{ var('mass_removal_share') }} as mass
    from removals r
    left join snaps s on s.feed = r.feed and s.fetched_at = r.fetched_at
)

select
    service_date,
    mode,
    sum(removed) as removed_rows,
    sum(with_stops) as removed_with_stops,
    max(removed / prev_entities) filter (prev_entities > 0)
        as worst_removed_share,
    count(*) filter (mass and entities > 0) as mass_endings,
    count(*) filter (mass and entities = 0) as empty_endings
from joined
group by all
) as day_rows
{{ window_rows() }}
