-- Sanity of the observed stop times, per service day and mode:
-- delay within -30 to +180 minutes (the Phase 2 plan), the realtime
-- feed's scheduled time agreeing with the timetable's to the minute,
-- and the last prediction not later than the poll that dropped the
-- stop (the rest are capped there, fct_stop_arrival).
select
    service_date,
    mode,
    count(*) as arrivals,
    avg((delay_s between -1800 and 10800)::int) as delay_sane_share,
    avg((abs(schedule_disagreement_s) <= 60)::int) as schedule_agree_share,
    avg((predicted_arrival_utc <= left_feed_at + interval 2 minute)::int)
        as before_exit_share,
    avg((method = 'capped_at_exit')::int) as capped_share
from {{ ref('fct_stop_arrival') }}
group by all
