-- Plausibility of vehicle positions per poll date and mode: the share
-- the worker did not quarantine (inside NSW, under 150 km/h, a
-- position present, clock not in the future; D-027). Kept per poll
-- date (D-060): the build of day D replaces poll dates D - 1 and D,
-- the files it reads, so the night after rewrites D once it is whole.
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
where true {{ in_service_window('obs_date', days_before=1) }}
group by all
