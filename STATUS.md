# Status

*Rewritten in place. Last updated 2026-11-28 (Phase 0 gate passed, D-058).*

**Phase 0, "Collect first".** The collector runs on AWS EC2 in Sydney (D-015, D-016) with 12 feeds since 2026-11-18 (D-025), on `main` 9a8185f since 2026-11-21 11:38 UTC. Raw snapshots go to S3 hourly and are pruned locally after 3 days (D-026). The Phase 1 light worker is built but stays off until the gate (D-027).

**Gate:** 7 consecutive days at ≥ 99% capture on every enabled feed (`checks capture --window 7d --gate` exits 0), and the real API quota confirmed by the 24-hour report (done, D-024). **The clock restarted after TfNSW's outage on 20 Nov (D-033). Short TfNSW outages that the collector fully recorded are allowed up to 30 min a day and 2 a week (D-037). Earliest pass: about 2026-11-27 16:00 UTC (28 Nov, 3 am Sydney) for all 12 feeds.**

## Revised plan (Mike, 2026-11-24, D-039 to D-044)

Phases and gates are unchanged; later work now runs in parallel. Targets: Phase 0 gate about 28 Nov, Phase 1 gate about 2 Dec, Phase 2 gate about 9 Dec, Phase 3 gate about 13 Dec, launch about 20 Dec (was about 10 Jan 2027).

- Gate day (pre-approved runbook, D-043, D-056): gate check and R0-02, S3 backfill into the live lakehouse, then the worker starts. The SSH approval names the commit.
- Phase 2's 7-day quality clock starts when the worker goes live (D-039).
- Spark benchmark: one on-demand day around 3 Dec, outside the Phase 1 gate (D-041).
- Phase 3 starts now in the cloud on replayed S3 hours (D-042); dashboard with Evidence (D-044).

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
- [x] Day 1 to day 7 capture ≥ 99% (record each day below)
- [x] Gate passed and recorded as a decision: 28 Nov 00:28 UTC, window 21 Nov 00:28 to 28 Nov 00:28 UTC, all 12 feeds, lowest 99.84% (bus TU); D-058, report R0-02 (`docs/reports/phase0-gate-20261128.md`)

## Daily capture log

| day | date | lowest-feed capture | p95 freshness | notes |
|---|---|---:|---:|---|
| 1 (partial, from 06:45 UTC) | 2026-11-17 | 100.00% (all 8 feeds) | ≤ 15 s realtime VP/TU; 86 s alerts | Server cron `capture-20261118.json`. 0 limit hits; preview quota 45% (27,299/day over 17.3 h). |
| 24 h report window | 17 Nov 07:12 – 18 Nov 07:12 UTC | 100.00% (all 8 feeds) | ≤ 14 s realtime VP/TU; 86 s alerts | 27,339 requests (46%), 0 limit hits, 4.94 GB/day on disk; runway to about 1 Dec (D-024). Redeployed to `main` 83592ba at 04:06 UTC with no lost polls. |
| 2 | 2026-11-19 | 99.58% (service alerts); all 12 feeds ≥ 99% | not recorded | Server cron 19 Nov 00:05 – 20 Nov 00:05 UTC. **Gap rule failed on 4 feeds** (train VP 315 s, metro TU 330 s, alerts 360 s, light rail VP 315 s): a one-off TfNSW 401 on each at 14:44–14:45 UTC while the other 8 feeds got 200s on the same key, and each 401 triggered a 300 s backoff. Logged as an upstream incident; Mike counted day 2 toward the gate (2026-11-21). Fix in D-031. Offload: 36 hourly runs, 801 objects, 0 failed. |
| 3 | 2026-11-20 | **93.82% (bus TU): FAIL on 10 feeds** | not recorded | Server cron `capture-20261121.json`, 20 Nov 00:05 – 21 Nov 00:05 UTC. **TfNSW outage:** 14:26 – about 15:02 UTC the gateway answered almost every realtime request with 502 "Error collecting requested resource" (662 × 502, 18 network errors), with intermittent 502s until 15:58; longest gaps 1,499 – 2,648 s. Service alerts (99.93%) and the timetable passed. The collector kept polling and recorded every attempt. Also three lone 401s at 10:46 – 10:48 UTC, each a 300 s gap (before the D-031 fix went live). **Mike: strict gate, the 7-day clock restarts (D-033).** |
| 4 | 2026-11-21 | 99.98% (light rail VP); all 12 feeds pass | not recorded | Server cron 21 Nov 00:05 – 22 Nov 00:05 UTC, exit 0. One warning: light rail trip updates unchanged for 27 min overnight (stuck warning, not a failure). Redeployed to 9a8185f at 11:38 UTC (D-031 live). First clean day of the restarted gate (D-033). |
| 5 | 2026-11-22 | 100.00% (all 12 feeds) | not recorded | Server cron 22 Nov 00:05 – 23 Nov 00:05 UTC; longest gap 33 s. Second clean day of the restarted gate (D-033). Prune 23 Nov: 266 hours (4.70 GB) removed, 0 failed; uploads 0 failed; 59 GB free. Next day (23 Nov) holds TfNSW outage I-18, within the D-037 allowance. |
| 6 | 2026-11-23 | 99.31% (bus TU); all 12 feeds pass | not recorded | Server cron 23 Nov 00:05 – 24 Nov 00:05 UTC on a869ede (D-037 logic, deployed 23 Nov without a collector restart). TfNSW slowdown I-18 (02:35 – 02:52 UTC, gaps 164 – 285 s) tagged "within D-037 allowance" on 10 realtime feeds. Third clean day of the restarted gate. Uploads 0 failed; 53 GB free before the 01:40 prune. |
| 7 | 2026-11-24 | 100.00% (all 12 feeds) | ≤ 20 s realtime VP/TU; 84 s alerts | Server cron 24 Nov 00:05 – 25 Nov 00:05 UTC; longest realtime gap 33 s, no warnings or D-037 tags. Fourth clean day of the restarted gate. Prune 266 hours (3.99 GB), 0 failed; uploads 0 failed (3,029 objects); 52 GB free. |
| 8 | 2026-11-25 | 100.00% capture (all 12 feeds); ferries VP and TU fail freshness | ≤ 15 s buses, trains, metro, light rail; 85 s alerts; ferries about 3,950 s | Server cron 25 Nov 00:05 – 26 Nov 00:05 UTC. TfNSW froze both ferry feeds on an empty snapshot overnight (I-22), so freshness failed while capture was 100%. Under D-055 empty snapshots are exempt and the day passes once the change is deployed. Other 10 feeds pass. Prune 265 hours (3.32 GB), 0 failed; 53 GB free. |
| 9 | 2026-11-26 | 99.93% (service alerts); all 12 feeds pass | not recorded | Server cron 26 Nov 00:05 – 27 Nov 00:05 UTC on 6625e13 (D-055 live); longest realtime gap 59 s, no D-037 tags, no alerts. Gate preview 27 Nov 00:45 UTC over 20 Nov 16:45 onward: exit 0, all 12 feeds pass (lowest 99.48% bus TU). Uploads 0 failed; 47 GB free before the 01:40 prune. |
| 10 | 2026-11-27 | 99.58% (service alerts); all 12 feeds pass | ≤ 21 s realtime VP/TU; 85 s alerts | Server cron 27 Nov 00:05 – 28 Nov 00:05 UTC, exit 0; longest realtime gap 65 s (light rail TU), a few single missed polls, no D-037 tags, no alerts. Prune 266 hours (4.58 GB), 0 failed; 48 GB free. **Gate check 28 Nov 00:28 UTC over 21 Nov 00:28 – 28 Nov 00:28: exit 0, all 12 feeds pass, lowest 99.84% (bus TU); I-18 allowed under D-037 on 10 feeds (D-058, R0-02).** |

## Blocked on Mike

- Nothing for Phase 0. First daily prune ran 2026-11-22 01:40 UTC: 377 local raw hours deleted after re-checking each in S3, 8.06 GB freed, 0 failed; 58 GB free after (server `docs/reports/offload.log`). Live alerts (D-034) on since 2026-11-22.

## Reports

Phase reports follow one standard and render to PDF (docs/reports/README.md): R0-01 Phase 0 first 24 hours (interim), R1-01 light worker dry run, R2-01 gold marts dry run (interim), R3-01 ghost trips and bunching on two real days (interim), R0-02 Phase 0 seven-day capture gate (final, pass).

## Phase 1, started 2026-11-18 on branches (docs/9_phase1.md)

Built against tests and captured data while the capture run finishes; nothing is deployed to the collector host before the gate.

- [x] Light worker core: bronze snapshot index, silver vehicle positions, stop predictions and trip status in Iceberg (D-027)
- [x] Real-data dry run on a peak and an overnight hour (R1-01, docs/reports/worker-dryrun-20261119.md)
- [x] Replay from the hour tars and `verify`: 30 of 30 table-hours identical on real data (R1-01)
- [x] Static timetable versions (D-029) and Service Alerts history (D-030); alerts replayed on two real hours, 17 Nov 20:00 to 22:00 UTC: 360 rows for 314 alerts, replay identical
- [x] Bus stop-prediction size: stored relative to the trip delay, 29% smaller (D-028)
- [x] Compaction by closed hour and snapshot expiry, hourly in `run --loop` (D-032); real 8 am hour: its 273 files merged into 4, so the bus tables went from 282 files to 13 (the 3 am hour's few files stay as they are), rows unchanged, 426 MB peak
- [x] Memory on Linux (Docker, 900 MB cap): realtime about 530 MB, timetable about 737 MB, no OOM
- [x] Gate-day backfill ready: replay builds straight into the live lakehouse, oldest first, and resumes after a cut; `scripts/backfill.sh` dry-run in Docker on 27 Nov matches a live-only build (D-056)
- [ ] **Gate day, about 28 Nov:** S3 backfill into the live lakehouse, then start the worker (runbook in docs/4_next_steps.md, D-043, D-056)
- [ ] Redpanda + Spark benchmark: one on-demand day around 3 Dec, outside the Phase 1 gate (D-041; Mike starts and stops the instance)
- [ ] Re-decide the long-term host before the AWS credits run out (D-015)

## Phase 2, started 2026-11-21 on branches (docs/11_phase2.md)

Built on silver tables replayed from real hour tars; nothing is deployed. Gate: the data-quality page green for 7 straight days.

- [x] dbt on DuckDB over the silver Iceberg files, results recorded in `gold.dq_results` (D-035)
- [x] Stop arrivals, headways, trip outcomes and the route reliability mart; 33 of 33 dbt nodes pass on two real bus hours (R2-01)
- [x] Quality tests: 99.7% trip match, arrivals a median 48 m from the bus's position, 99.95% timetable agreement (R2-01)
- [x] After-midnight trips: TfNSW dates them by calendar day; gold moves them to their service day (D-035)
- [x] Record when a trip leaves the feed in silver (Mike, 22 Nov, D-036): 4,716 last-stop arrivals on the real hours
- [x] Review of the merged gold models (23 Nov): ghost candidates only for operators with realtime that day (D-038); unreadable snapshots no longer count as capture; loop routes paired per call; route mart keeps regions apart
- [x] Gate check `gold gate` (D-057): each service day judged on its build, quality bars per mode, every mode present, capture, operator coverage (D-038) and trip-left rows (D-036); `gold gate-report` drafts R2-02
- [ ] Excess wait time, incremental nightly builds
- [ ] **After the worker is live:** a full day of every mode, then the 7-day quality clock (D-039, gate about 9 Dec), the volume test and the public quality page

## Phase 3, started 2026-11-24 on branches (docs/12_phase3.md)

Built in the cloud on replayed S3 hours while the Phase 0 gate runs (D-042); nothing is deployed. Gate: 20 ghost flags hand-checked against the raw positions on backfilled history; at least 18 hold up and none is a pipeline miss (D-046).

- [x] Ghost trip tracker and route honesty marts: missing and vanished trips of operators with realtime (D-045)
- [x] Bunching radar: bunches (a pair together for 3+ stops) and hot spots by stop and hour (D-045)
- [x] Hand-check sheet: seeded sample, raw snapshots searched for each trip id (D-045)
- [x] Map replay: hourly export and a deck.gl viewer
- [x] Evidence dashboard over a public Parquet extract (D-044, D-045); CI builds it on synthetic data
- [x] Run on 2 real service days (22 and 23 Nov) from S3 hours: R3-01, `docs/reports/phase3-realdays-20261124.md`. 0.45% of Monday's judged trips missing (161 of 166 at Busways North West; refreshed 25 Nov on D-048, D-052 and D-053), 2,274 bus bunches on D-051 and D-054, no pipeline miss in 40 hand-checked trips; three judging rules fixed (D-047)
- [x] Mike: verdicts on the two hand-check sheets, all 40 agree (25 Nov)
- [x] Replay expires and compacts as it goes (I-20, D-056)
- [x] The gold window keeps the day before (I-21, D-060)
- [x] Trains judged: runs dated from the timetable, stops by stop id, hidden-early and origin arrivals carry no time (D-059); every train bar passes on 25 and 26 Nov
- [ ] Light rail arrivals (O-04)
- [x] Gold builds one service day at a time and fits the host's 300 MB (I-23, D-060)
- [ ] Live radar from the latest trip updates (after the worker is live)
- [ ] Dashboard host and portfolio link (Mike's choice)
- [ ] Hand-check of 20 trips on backfilled history (the gate): pass at 18 of 20 with no pipeline miss (D-046)
