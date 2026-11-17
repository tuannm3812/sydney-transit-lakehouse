# Sydney Transit Lakehouse

A live lakehouse built on Transport for NSW's real-time GTFS-realtime feeds. It streams vehicle positions and trip updates for Sydney's buses, trains and metro into Apache Iceberg tables, then runs two products on them: a **ghost bus / timetable honesty tracker** (scheduled trips that never actually ran) and a **live bunching radar** (buses on the same route arriving in clumps).

The project runs on real live data, not a historical dump.

```
TfNSW Open Data Hub ──► Python collector ──► Redpanda ──► Spark Structured Streaming
   (GTFS-realtime)        (Phase 0: raw                    │
                           protobuf landing)               ▼
                                                    Iceberg on object storage
                                                           │
                                     dbt on DuckDB ◄───────┘
                                           │
                         dashboard + "ask the network" text-to-SQL analyst
```

## Status

**Phase 0, "Collect first" (week 1), in progress.** The collector is built and tested, and it is waiting on the API key and the first 24 hours of live capture. The running checklist is in [STATUS.md](STATUS.md), and every decision with its date is in [docs/3_decisions.md](docs/3_decisions.md).

Phase 0 gate: **7 consecutive days at ≥ 99% capture on every enabled feed, and the real API quota confirmed.**

## Quick start

```bash
cp .env.example .env            # then paste your key, see docs/5_api_token_setup.md
docker compose up -d collector  # polls, dedupes and lands snapshots in ./data
docker compose logs -f collector

# Data quality: capture rate and freshness against SLA (exit 1 on breach)
docker compose run --rm checks capture --window 1h

# After 24 hours: observed quota behaviour and snapshot sizes
docker compose run --rm checks quota --window 24h --out docs/reports/quota-24h.md

# Phase 0 gate
docker compose run --rm checks capture --window 7d --gate
```

Without Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # macOS's python3 is 3.9; use Homebrew Python ≥ 3.12
pip install -e ".[dev]"
set -a; source .env; set +a
python -m transit_lakehouse.collector --check-config   # quota projection, no requests
python -m transit_lakehouse.collector
```

## What the Phase 0 collector does

| Requirement | How |
|---|---|
| Vehicle positions every 15 s, trip updates every 30 s | `config/feeds.toml`; drift-free schedule per feed |
| Stagger requests | feeds start 1.5 s apart, and a global limiter keeps requests ≤ 1/s (TfNSW publishes 5/s, but bursts get 429, D-013) |
| Back off on 429/503 | exponential backoff with jitter, `Retry-After` honoured; TfNSW's documented 403 "Over Rate Limit" also counts; 403 "Over Quota Limit" waits for the midnight-AEST reset |
| Stamp fetch time | UTC fetch time in the filename and in the manifest, next to the feed's own header timestamp |
| Skip unchanged snapshots | sha256 of the payload, and the last hash is recovered from the manifest after a restart |
| Land raw protobuf by feed and hour | `data/raw/feed=<feed>/date=YYYY-MM-DD/hour=HH/<fetched_at>_<sha>.pb.gz`, gzip-compressed and written atomically |
| Capture-rate and freshness check | `transit_lakehouse.checks.capture`, which reads the append-only poll manifest |
| 24-hour quota and size report | `transit_lakehouse.checks.quota_report` |
| Quota guard | refuses to start if enabled feeds would use more than 80% of the daily quota |

Every poll attempt, including failures and unchanged snapshots, is written as a line in `data/manifest/…/hour=HH.jsonl`. That manifest is what the capture rate is measured against, so a failed poll can never vanish from the numbers.

## Repository map

- `src/transit_lakehouse/collector/`: config, backoff policy, poller and landing
- `src/transit_lakehouse/checks/`: capture/freshness SLA check and the quota report
- `config/feeds.toml`: which feeds are polled and how often
- `docs/`: brief, architecture, decisions log, next steps, API token setup
- `STATUS.md`: the running checklist

## Data licence

TfNSW open data is published under CC BY 4.0. Source: Transport for NSW.
