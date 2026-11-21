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
- [x] Light rail (Inner West) and ferries enabled after the 24 h report (D-025); 12 feeds, 70% of quota
- [ ] Day 1 to day 7 capture ≥ 99% (record each day below)
- [ ] Gate passed and recorded as a decision

## Daily capture log

| day | date | lowest-feed capture | p95 freshness | notes |
|---|---|---:|---:|---|
| 1 (partial, from 06:45 UTC) | 2026-11-17 | 100.00% (all 8 feeds) | ≤ 15 s realtime VP/TU; 86 s alerts | Server cron `capture-20261118.json`. 0 limit hits; preview quota 45% (27,299/day over 17.3 h). |
| 24 h report window | 17 Nov 07:12 – 18 Nov 07:12 UTC | 100.00% (all 8 feeds) | ≤ 14 s realtime VP/TU; 86 s alerts | 27,339 requests (46%), 0 limit hits, 4.94 GB/day on disk; runway to about 1 Dec (D-024). Redeployed to `main` 83592ba at 04:06 UTC with no lost polls. |
| 2 | 2026-11-19 | 99.58% (service alerts); all 12 feeds ≥ 99% | not recorded | Server cron 19 Nov 00:05 – 20 Nov 00:05 UTC. **Gap rule failed on 4 feeds** (train VP 315 s, metro TU 330 s, alerts 360 s, light rail VP 315 s): a one-off TfNSW 401 on each at 14:44–14:45 UTC while the other 8 feeds got 200s on the same key, and each 401 triggered a 300 s backoff. Logged as an upstream incident; Mike counted day 2 toward the gate (2026-11-21). Fix in D-031. Offload: 36 hourly runs, 801 objects, 0 failed. |

## Blocked on Mike

- Approval to enable the daily prune (keep 3 days locally, delete only hours re-verified in S3). Needed before about 1 Dec, when the disk fills (D-024, D-026). Hourly uploads have run clean since 2026-11-18 15:20 UTC.

## Reports

Phase reports follow one standard and render to PDF (docs/reports/README.md): R0-01 Phase 0 first 24 hours (interim), R1-01 light worker dry run.

## Phase 1, started 2026-11-18 on branches (docs/9_phase1.md)

Built against tests and captured data while the capture run finishes; nothing is deployed to the collector host before the gate.

- [x] Light worker core: bronze snapshot index, silver vehicle positions, stop predictions and trip status in Iceberg (D-027)
- [x] Real-data dry run on a peak and an overnight hour (R1-01, docs/reports/worker-dryrun-20261119.md)
- [x] Replay from the hour tars and `verify`: 30 of 30 table-hours identical on real data (R1-01)
- [x] Static timetable versions (D-029) and Service Alerts history (D-030); alerts not yet run on real data
- [x] Bus stop-prediction size: stored relative to the trip delay, 29% smaller (D-028)
- [ ] Compaction
- [ ] **After the Phase 0 gate:** deploy the worker (Mike's approval)
- [ ] Redpanda + Spark benchmark (needs an on-demand instance, Mike's approval)
- [ ] Re-decide the long-term host before the AWS credits run out (D-015)
