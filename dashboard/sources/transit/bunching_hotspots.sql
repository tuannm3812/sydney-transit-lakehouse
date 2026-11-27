-- Public extract written by `python -m transit_lakehouse.gold publish`,
-- summed over direction and hour to one row per stop, route and day
-- type: that is all the map draws, and the hour grain made the site
-- ship over 100 MB of rows to every browser (27 Nov build).
select
    mode,
    case mode
        when 'bus' then 'Bus'
        when 'train' then 'Trains'
        when 'metro' then 'Metro'
        when 'lightrail' then 'Light rail'
        when 'ferry' then 'Ferries'
        when 'nswtrains' then 'NSW TrainLink'
        when 'coach' then 'Coaches'
        else mode
    end as mode_name,
    region,
    route_id,
    any_value(route_short_name) as route_short_name,
    stop_id,
    any_value(stop_name) as stop_name,
    any_value(stop_lat) as stop_lat,
    any_value(stop_lon) as stop_lon,
    day_type,
    sum(headways) as headways,
    sum(bunched) as bunched,
    sum(overtaken) as overtaken
from read_parquet('../data/gold/public/bunching_hotspots.parquet')
group by mode, region, route_id, stop_id, day_type
