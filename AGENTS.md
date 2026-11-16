# sydney-transit-lakehouse

A live Sydney transport lakehouse on Transport for NSW GTFS-realtime feeds: Python collector → Redpanda → Spark Structured Streaming → Apache Iceberg on object storage → dbt on DuckDB → dashboard, plus a text-to-SQL "ask the network" analyst. The headline products are a ghost bus / timetable honesty tracker and a live bunching radar. It runs on real live data only, never on a historical dump standing in for streaming.

## Standards

Follow the master standard at `~/Documents/GitHub/coding-standards/` (package-first layout, Shape B docs, §14 tooling, §15 pipelines and secrets). Project-specific rules and deliberate overrides: @docs/0_coding_standards.md

## Start here

1. [STATUS.md](STATUS.md): the running checklist and the current phase gate. It is the only doc rewritten in place.
2. [docs/3_decisions.md](docs/3_decisions.md): dated `D-NNN` decisions. Read the rows before you change the feed set, cadence, partitioning or SLAs.
3. [docs/2_architecture.md](docs/2_architecture.md): data flow and on-disk layout.

## Evidence locations

- `docs/reports/`: generated capture and quota reports. Any claim about capture rate, quota or volume traces to one of these.
- `data/manifest/`: every poll attempt (gitignored, lives on the collector host).

## Open risks

- The source plan (`sydney-transit-lakehouse-plan.md`) had not been received as of 2026-11-16. Phase 0 was built from Mike's message, so reconcile it once the plan arrives (D-012).
- Rail, metro and other non-bus feed paths are unverified until the first run with a real key (D-004).
- The API key is a secret. It lives in `.env` only. Never print it, log it or paste it in chat.
