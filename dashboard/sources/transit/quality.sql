-- Public extract written by `python -m transit_lakehouse.gold publish`.
-- built_on is the build's UTC date as text: the browser's DuckDB has no
-- time zone functions, so the pages cannot format run_at themselves.
select
    *,
    strftime(timezone('UTC', run_at::timestamptz), '%-d %b %Y') as built_on
from read_parquet('../data/gold/public/quality.parquet')
