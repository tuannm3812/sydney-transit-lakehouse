# sydney-transit-lakehouse

A live Sydney transport lakehouse on Transport for NSW GTFS-realtime feeds: Python collector → Redpanda → Spark Structured Streaming → Apache Iceberg on object storage → dbt on DuckDB → dashboard, plus a text-to-SQL "ask the network" analyst. The headline products are a ghost bus / timetable honesty tracker and a live bunching radar. It runs on real live data only, never on a historical dump standing in for streaming.

## Standards

Follow the master standard at `~/Documents/GitHub/coding-standards/` (package-first layout, Shape B docs, §14 tooling, §15 pipelines and secrets). Project-specific rules and deliberate overrides: @docs/0_coding_standards.md

## Start here

1. [STATUS.md](STATUS.md): the running checklist and the current phase gate. It is the only doc rewritten in place.
2. [docs/3_decisions.md](docs/3_decisions.md): dated `D-NNN` decisions. Read the rows before you change the feed set, cadence, partitioning or SLAs.
3. [docs/2_architecture.md](docs/2_architecture.md): data flow and on-disk layout.
4. [docs/7_data_model.md](docs/7_data_model.md): the data model, the layers and the storage plan (draft).
5. [docs/9_phase1.md](docs/9_phase1.md): the Phase 1 plan, its work items and what waits for the Phase 0 gate.

## Evidence locations

- `docs/reports/`: generated capture and quota reports, and phase reports (`R<phase>-<nn>`). Any claim about capture rate, quota or volume traces to one of these. Phase reports follow [docs/reports/README.md](docs/reports/README.md) and render to PDF with `python -m transit_lakehouse.reports`.
- `data/manifest/`: every poll attempt (gitignored, lives on the collector host).

## Open risks

- The source plan was received and reconciled on 2026-11-17 (D-019).
- The 6 original feed paths were verified live on 2026-11-16 (D-013). Service Alerts and static GTFS were added on 2026-11-17 (D-019). Ferries (`/v1`) and Inner West light rail (`/v2`) were verified live on 2026-11-18 (D-025); NSW TrainLink and the other light rail lines are still unverified. The gateway 429s on bursts, so keep MAX_RPS at 1.
- The API key is a secret. It lives in `.env` only. Never print it, log it or paste it in chat.
