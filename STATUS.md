# Status

*Rewritten in place. Last updated 2026-11-18 (after the 24-hour report).*

**Phase 0, "Collect first" (week 1).** The collector runs on AWS EC2 in Sydney (D-015, D-016) and has captured 100% of polls on the 6 original feeds since 2026-11-17 06:45 UTC. Static GTFS and Service Alerts were added the same evening after reconciling the plan (D-019).

**Gate:** 7 consecutive days at ≥ 99% capture on every enabled feed (`checks capture --window 7d --gate` exits 0), and the real API quota confirmed by the 24-hour report. Earliest pass: about 2026-11-24 for the original 6 feeds, a day later for the 2 new ones.

## Phase 0 checklist

- [x] Repo scaffold: README, Docker Compose, `.env.example`, CI (ruff and pytest)
- [x] Collector: VP every 15 s, TU every 30 s, staggered, with backoff on 429/503 and TfNSW's 403s
- [x] Fetch-time stamping, sha256 dedupe, raw protobuf landing by feed/date/hour (gzipped, D-017)
- [x] Capture-rate and freshness SLA check, with a stuck-feed warning (D-019)
- [x] 24-hour quota and snapshot-size report
- [x] API token instructions (docs/5_api_token_setup.md)
- [x] Decisions log started
- [x] **Mike:** API key created and placed in `.env`
- [x] **Mike:** open decisions: single VM, public dashboard, `sydney-transit-lakehouse` (D-014)
- [x] Plan received and reconciled (D-019)
- [x] First live run: all 6 paths 200, bursts get 429 (D-013)
- [x] VM bootstrap script and deployment guide (docs/6_deployment.md)
- [x] AWS t3.small in Sydney, disk grown to 80 GB (D-015, D-016, D-018)
- [x] Collector running on the always-on host
- [x] Static GTFS timetable and Service Alerts collected (D-019)
- [x] 24-hour report committed to `docs/reports/quota-24h.md`; quota and volume confirmed in D-024
- [ ] Decide whether to enable the plan's remaining feeds (after the 24 h report)
- [ ] Day 1 to day 7 capture ≥ 99% (record each day below)
- [ ] Gate passed and recorded as a decision

## Daily capture log

| day | date | lowest-feed capture | p95 freshness | notes |
|---|---|---:|---:|---|
| 1 (partial, from 06:45 UTC) | 2026-11-17 | 100.00% (all 8 feeds) | ≤ 15 s realtime VP/TU; 86 s alerts | Server cron `capture-20261118.json`. 0 limit hits; preview quota 45% (27,299/day over 17.3 h). |
| 24 h report window | 17 Nov 07:12 – 18 Nov 07:12 UTC | 100.00% (all 8 feeds) | ≤ 14 s realtime VP/TU; 86 s alerts | 27,339 requests (46%), 0 limit hits, 4.94 GB/day on disk; runway to about 1 Dec (D-024). Redeployed to `main` 83592ba at 04:06 UTC with no lost polls. |

## Blocked on Mike

- Whether to enable more feeds (light rail, ferries) now that the quota is confirmed.
- Approval to offload raw snapshots to object storage before about 1 Dec, when the disk fills (D-024).

## Next after the gate

Phase 1 adds a light worker that builds bronze/silver Iceberg tables from the landed raw snapshots, with Redpanda + Spark Structured Streaming run as an on-demand benchmark on the same feeds (D-023). Re-decide the long-term host before the AWS credits run out (D-015). See docs/4_next_steps.md.
