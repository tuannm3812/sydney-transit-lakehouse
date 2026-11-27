-- Public extract written by `python -m transit_lakehouse.gold publish`,
-- with the mode's public name (the colours are keyed on it).
select
    *,
    case mode
        when 'bus' then 'Bus'
        when 'train' then 'Trains'
        when 'metro' then 'Metro'
        when 'lightrail' then 'Light rail'
        when 'ferry' then 'Ferries'
        when 'nswtrains' then 'NSW TrainLink'
        else mode
    end as mode_name
from read_parquet('../data/gold/public/bunching_by_hour.parquet')
