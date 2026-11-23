# 10. Incident log

Every problem the project has hit, what it cost, and what fixed it. Outages and upstream errors are normal for a live feed. The point is to record each one, keep every failed attempt in the manifest (D-007), and have a known response. Add a row whenever something breaks or surprises us. Numbers trace to `STATUS.md`, `docs/reports/` or the decision cited.

Status: **fixed** (a change in the repo or on the server prevents a repeat), **handled** (upstream, so it can't be fixed here; the response is in place), **open** (still needs work).

## Live data incidents

| # | Date (UTC) | What happened | Impact | Cause | Response | Status |
|---|---|---|---|---|---|---|
| I-01 | 2026-11-16 | Gateway returned 429 on bursts | A few failed polls in the first live run | TfNSW rate limits bursts of about 3 requests/s and sends no rate-limit headers | `MAX_RPS = 1`, staggered schedule, capped exponential backoff (D-008, D-013) | fixed |
| I-02 | 2026-11-17 | Service Alerts failed to decode | Alerts landed but could not be parsed fully | TfNSW puts a string in `TripDescriptor` field 7, which breaks the standard proto | Shallow parse in the collector (D-020); silver repairs the field (D-030) | fixed |
| I-03 | 2026-11-18 | Light rail `/v1` paths returned 404 | Light rail could not be enabled as planned | The Inner West feed is only served on `/v2` | Config uses the `/v2` paths, verified live (D-025) | fixed |
| I-04 | 2026-11-19 14:44 | One 401 on each of 4 feeds while 8 feeds kept getting 200 on the same key | Gaps of 315–360 s; day 2 broke the gap rule (capture still ≥ 99.58%) | A one-off TfNSW gateway auth blip, made worse by our 300 s auth backoff | A lone 401 now retries at normal cadence; long backoff only after 3 in a row (D-031, live since 21 Nov 11:38). Mike counted day 2 | fixed |
| I-05 | 2026-11-20 10:46 | Three lone 401s, each a 5-minute gap | Small gaps on day 3 | Same as I-04, before the D-031 fix was deployed | Covered by D-031 | fixed |
| I-06 | 2026-11-20 14:26–15:02 | TfNSW gateway answered almost every realtime request with 502 "Error collecting requested resource" (662 × 502, 18 network errors; intermittent 502s until 15:58) | Day 3 failed on 10 of 12 feeds (lowest 93.82%, gaps up to 2,648 s); about 36 minutes of ghost-bus and bunching evidence lost | Upstream outage. The collector stayed up, kept polling and recorded every attempt | Logged as an upstream outage; Mike chose a strict gate, so the 7-day clock restarted, with the earliest pass about 27 Nov 16:00 UTC (D-033). Gold must mark the window as unobserved, not as missing trips | handled |
| I-18 | 2026-11-23 02:35–02:52 | TfNSW API slowdown: 110 read timeouts and 30 × 504 Gateway Time-out across every feed, service alerts included | Gaps of 164 – 285 s per feed (longest bus VP, 02:49:00 – 02:53:45), about 10 – 20 lost polls per feed; capture for the day expected about 99.5% but the gap rule fails on most feeds | Upstream outage. The collector stayed up (39 h, low load) and recorded every attempt | Live watch (D-034) alerted Mike's phone at 02:50 and sent the recovery at 02:55 UTC with no action needed. Mike chose a bounded allowance (D-037): a fully recorded upstream outage up to 30 min a day and 2 a week does not fail the gate | handled |

## Platform and operations incidents

| # | Date (UTC) | What happened | Impact | Cause | Response | Status |
|---|---|---|---|---|---|---|
| I-07 | 2026-11-17 | Oracle Always Free sign-up failed repeatedly | No free host | Oracle account checks | Moved to AWS EC2 in Sydney on free credits (D-015) | fixed |
| I-08 | 2026-11-17 | Planned t4g.small not allowed | Instance type change | The AWS Free plan only allows free-tier types | t3.small, x86, 2 GB (D-016) | fixed |
| I-09 | 2026-11-17 | Raw volume far above the estimate (about 5.9 GiB/day by day) | Disk would fill within days | Bus trip updates are about 6.3 MB per snapshot | Gzip on landing (D-017), disk grown to 80 GB (D-018), hourly S3 offload (D-026), daily prune keeping 3 days (from 22 Nov) | fixed |
| I-10 | 2026-11-17 | Static timetable (about 287 MB) re-downloaded on every collector restart | Wasted quota and bandwidth | No validators kept between runs | Conditional requests (D-021); 3 copies a day are real TfNSW publishes | fixed |
| I-11 | 2026-11-18 | The capture check could pass a leading outage, miss old gaps and fail a healthy week | Gate results could be wrong | Found by the Codex review | Check scored from first-ever poll, every gap enforced, unknown freshness fails (D-022, PR #2) | fixed |
| I-12 | 2026-11-18 | The bootstrap script put the API key in process arguments | Key visible to other users on the VM during setup | `sed` with the key in its argv | Key passed via stdin (PR #6) | fixed |
| I-13 | 2026-11-18 | First S3 upload got 403 | Offload blocked for an hour | The IAM policy did not name the bucket | Mike fixed the policy; `offload` now checks bucket access before it runs (PR #12) | fixed |
| I-14 | 2026-11-18 | 24 h evidence files were silently left out of a commit | The report cited files that weren't in the repo | `docs/reports/*` is gitignored | Curated report files whitelisted (PR #13) | fixed |
| I-15 | 2026-11-21 | SSH to the collector timed out from the UTS network | Server work waited until Mike was home | The security group allows SSH only from Mike's home IP | Set the SSH rule's Source to "My IP" from a new network, or wait. The collector itself is unaffected (outbound only) | handled |
| I-16 | 2026-11-22 | No live alert when a feed went silent or the disk ran low (was O-01) | I-06 was only seen in the next day's 00:05 UTC cron | Nothing watched between daily checks | `checks watch` every 5 minutes with ntfy push alerts (D-034), live on the server since 22 Nov; test push and recovery confirmed on Mike's phone at 03:51 UTC | fixed |
| I-17 | 2026-11-22 | The alert topic line appended to the server's `.env` landed on the same line as the API key | The key line was corrupted on disk; the running collector was unaffected (it loaded the key at start) but would have failed at its next restart | `.env` had no trailing newline, so `echo ... >> .env` glued the new variable onto the last line | Mike split the line with `sed` and checked variable names only; docs now append with a newline guard (`printf '\nNAME=%s\n' value >> .env`) and check with `cut -d= -f1 .env` | fixed |

## Open problems

| # | Problem | Why it matters | Plan |
|---|---|---|---|
| O-02 | Outcome labels must reflect coverage | Without it, an outage like I-06 would look like cancelled trips | Gold uses observed / cancelled / unobserved despite coverage / unknown, and excludes windows with missing polls (Codex review, Phase 1) |
| O-03 | NSW TrainLink and the other light rail lines are unverified | They may need different paths, as I-03 did | Probe before enabling; a decision row if the feed set changes |

## How we respond to an upstream outage

1. Keep polling at the normal cadence. Don't stop, and don't retry faster.
2. Every attempt goes into the manifest, including 4xx, 5xx and network errors (D-007).
3. Record the incident here and in the `STATUS.md` daily log, with times, counts and the feeds hit.
4. Decide with Mike whether it counts against the gate, and record the decision.
5. Downstream, mark the affected window as unobserved, so it is never read as service that didn't run.
