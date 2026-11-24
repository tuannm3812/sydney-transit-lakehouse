-- Public extract written by `python -m transit_lakehouse.gold publish`.
select * from read_parquet('../data/gold/public/trip_match.parquet')
