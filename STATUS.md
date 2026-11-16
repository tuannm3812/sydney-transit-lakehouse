# Status

*Rewritten in place. Last updated 2026-11-16.*

**Phase 0, "Collect first" (week 1).** The collector is built, tested and verified live on all 6 enabled feeds (D-013). It is waiting on an always-on host to start the 7-day run.

**Gate:** 7 consecutive days at ≥ 99% capture on every enabled feed (`checks capture --window 7d --gate` exits 0), and the real API quota confirmed by the 24-hour report.

## Phase 0 checklist

- [x] Repo scaffold: README, Docker Compose, `.env.example`, CI (ruff and pytest)
- [x] Collector: VP every 15 s, TU every 30 s, staggered, with backoff on 429/503 and TfNSW's 403s
- [x] Fetch-time stamping, sha256 dedupe, raw protobuf landing by feed/date/hour
- [x] Capture-rate and freshness SLA check
- [x] 24-hour quota and snapshot-size report
- [x] API token instructions (docs/5_api_token_setup.md)
- [x] Decisions log started
- [x] **Mike:** API key created and placed in `.env`
- [x] **Mike:** open decisions: single VM, public dashboard, `sydney-transit-lakehouse` (D-014)
- [ ] **Mike:** send the plan files (D-012)
- [x] First live run: all 6 paths 200, 90 s at 100% capture, no limit headers exist; bursts get 429 (D-013)
- [ ] Collector running on the always-on host
- [ ] 24-hour report committed to `docs/reports/quota-24h.md`, quota row in D-004 updated
- [ ] Day 1 to day 7 capture ≥ 99% (record each day below)
- [ ] Gate passed and recorded as a decision

## Daily capture log

| day | date | lowest-feed capture | p95 freshness | notes |
|---|---|---:|---:|---|
| | | | | |

## Blocked on Mike

- An empty GitHub repo `sydney-transit-lakehouse`, a VM (Oracle Always Free sign-up), and the plan files.

## Next after the gate

Phase 1 replays the landed raw snapshots into Redpanda and switches the collector to produce to it as well, then Spark Structured Streaming writes bronze Iceberg tables. See docs/4_next_steps.md.
