-- Timetable stop patterns (D-029) with each stop's position counted
-- from the end. Realtime trip updates list a trip's remaining stops as
-- a contiguous run ending at its last stop (checked on 17 Nov: 9,495
-- of 9,508 first sightings), so a stop is still listed while
-- trip_status.stop_count >= pos_from_end. visit numbers the calls at
-- the same stop, for loop routes that call at a stop twice.
-- pos_from_start 1 is the trip's origin.
select
    pattern_id,
    stop_sequence,
    stop_id,
    arrival_s,
    departure_s,
    count(*) over (partition by pattern_id)
        - row_number() over (partition by pattern_id order by stop_sequence)
        + 1 as pos_from_end,
    row_number() over (partition by pattern_id order by stop_sequence)
        as pos_from_start,
    row_number() over (partition by pattern_id, stop_id order by stop_sequence)
        as visit
from {{ source('silver', 'timetable_pattern_stop') }}
