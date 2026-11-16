# 1. Brief

*Written 2026-11-16 from Mike's kickoff message. Reconcile with `sydney-transit-lakehouse-plan.md` when it arrives (D-012).*

## What

A Sydney transport live lakehouse built on Transport for NSW's real-time GTFS-realtime feeds (vehicle positions and trip updates).

## For whom

A flagship portfolio project for Applied AI Engineer and data engineering roles, shown as a card on tuannm3812.github.io. It has to demonstrate:

- real streaming on live data, not historical replays posing as streams
- incremental loads at volume
- a lakehouse table format (Apache Iceberg)
- data quality checks with SLAs
- written cost and scale trade-offs

## Products

1. **Ghost buses / timetable honesty tracker**: scheduled trips that never appear in the realtime feed, or that vanish mid-route, by route, operator and time of day.
2. **Live bunching radar**: consecutive vehicles on the same route and direction running closer together than the scheduled headway.
3. **"Ask the network"**: a text-to-SQL analyst over the gold tables.

## Stack (decided)

Python collector → Redpanda → Spark Structured Streaming → Iceberg on object storage → dbt on DuckDB → dashboard.

## Constraints

- Budget under **AUD 30 a month**, all in.
- The API key is provided by Mike as a secret and never committed.

## Phases

- **Phase 0: Collect first (week 1).** Land raw snapshots reliably. Gate: 7 days at ≥ 99% capture and the real API quota confirmed.
- Later phases are defined in the plan document (pending).
