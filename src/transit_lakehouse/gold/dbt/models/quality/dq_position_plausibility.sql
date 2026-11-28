-- Plausibility of vehicle positions per poll date and mode: the share
-- the worker did not quarantine (inside NSW, under 150 km/h, a
-- position present, clock not in the future; D-027). Kept per poll
-- date: a build replaces the dates its silver files cover (D-060).
{{ per_day('obs_date') }}
select
    obs_date,
    mode,
    count(*) as positions,
    avg((not coalesce(quarantined, false))::int) as plausible_share,
    count(*) filter (quarantine_reason = 'outside_nsw') as outside_nsw,
    count(*) filter (quarantine_reason = 'speed_over_150kmh') as too_fast,
    count(*) filter (quarantine_reason = 'no_position') as no_position,
    count(*) filter (quarantine_reason = 'timestamp_in_future') as future_clock
from {{ source('silver', 'vehicle_position') }}
group by all
