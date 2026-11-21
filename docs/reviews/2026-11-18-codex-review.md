# Codex review, 2026-11-18

Codex CLI 0.163.0, read-only, on origin/feat/html-report (8cbaeb4) plus the PR #2 diff.

Cited by D-022 in `docs/3_decisions.md`. The review text below is kept as written; line links point at the code as of 8cbaeb4, so they may have moved since.

## What happened to the findings

| Finding | Status |
|---|---|
| Capture check: gate boundary, leading outages, historical gaps, unknown freshness (C) | Fixed in PR #2 (D-022) |
| Bootstrap key in process arguments (C) | Fixed in PR #6 |
| HTML report edge cases and received-byte counting (C) | Fixed in PR #3 (b83ae1b) |
| Always-on Redpanda + Spark (B, Streaming) | Replaced by a light worker plus an on-demand benchmark (D-023) |
| Offload before the disk deadline (B, Bronze; D1) | S3 offload and verified prune (D-026), live since 18 Nov |
| Static refetch on restart (B, Static; D4) | Conditional requests (D-021) |
| Outcome labels, coverage-qualified results (A; D5) | Open: for the gold layer in Phase 1 |
| Continuous silence and disk-runway alerts (B, Quality) | Open: still daily cron checks |

## A. Direction review

**The direction is sound, but the outcomes need stronger evidence labels.** "Timetable honesty" is defensible as disagreement between published schedules and observed realtime information. Neither an absent trip nor a disappearing vehicle proves a bus did not run. README currently overclaims this.

Replace `ran / cancelled / never seen` with **observed running, explicitly cancelled, unobserved despite adequate coverage, and unknown**. Preserve conflicting evidence and subsequent reinstatements. Observing part of a journey does not establish completion.

The strongest research contribution would be **measuring how conclusions change with observation quality**: vary capture coverage, matching confidence, and observation windows; publish uncertainty and route-level missingness alongside results. This would distinguish the project from another realtime map.

ETA accuracy is a useful third measure. Freeze predictions at defined horizons, evaluate against independently inferred arrivals, and disclose uncertainty in GPS-derived arrival labels. Compare against timetable and simple delay-persistence baselines using chronological holdouts. Final trip-update timestamps should not silently become "ground truth."

## B. Per-stage critique and alternatives

| Stage | Assessment and recommendation |
|---|---|
| **Collection** | Keep bytes plus manifest as the durable boundary. Preserve scheduled time, request start, completion, source timestamp, and configuration version separately. Keep MAX_RPS=1: the supplied 45% quota use and zero 429s give no reason to accelerate. Isolate large static downloads and blocking compression/disk work from realtime scheduling. |
| **Static GTFS** | Persist validators and next-due time; use conditional requests **if supported**. "Never on restart" is too absolute: restart should resume the schedule and catch up when overdue. Current hash recovery already prevents byte-identical re-landing when manifests persist. Re-fetching alone therefore does not explain the reported 0.86 GB stored: investigate deployed version, manifest continuity, and ZIP byte changes. ZIP changes also need not mean timetable content changes. |
| **Bronze storage** | Offload before the disk deadline, independently of the seven-day gate. Upload immutable objects and manifests, verify checksums, then mark them safe for local eviction. Test replay before deleting anything. Bundle realtime snapshots into indexed hourly archives to reduce object counts while preserving exact bytes. Give static timetable versions and manifests longer retention than disposable realtime raw history. Lifecycle expiry necessarily limits replay; state that horizon explicitly. |
| **Streaming** | **Always-on Redpanda plus Spark is not justified on this budget or the current 2 GB host.** Use a separate worker consuming committed manifests, with durable checkpoints and idempotent writes, processing live arrivals in short microbatches. This still demonstrates live incremental processing. If Kafka/Spark skills are a portfolio requirement, demonstrate them in a bounded experiment against the same live source and measure their additional latency, memory, and cost. Record a decision superseding D-002. |
| **Silver** | Iceberg is reasonable if table-format learning is an explicit objective; establish one writer, catalog persistence, compaction, and snapshot expiry. Change-only TU storage must retain **revision history**, not overwrite latest state. Namespace trip identity by feed and service instance, including start time when needed, and retain timetable-version lineage. Preserve cancellations, skipped stops, missing-data states, deletions, and disappearance separately. Keep observation intervals/counts so repeated predictions remain reconstructible. Preserve distinct VP observations and their source timestamps; repeated stale positions are not additional movement evidence. |
| **Gold** | Resolve service calendars, exceptions, after-midnight service times, and timetable versions before declaring missing trips. Compute headways at common stops or route-progress points, separated by direction and service pattern; route-hour aggregates alone can hide branch differences. Report bunching duration and passenger-facing waiting-time measures alongside a clearly specified threshold. |
| **Quality / SLAs** | Separate transport capture, payload validity, source freshness, entity coverage, matching quality, and downstream latency. Gate against scheduled slots, not merely successful response counts. Monitor per-feed silence and disk runway continuously; daily cron output and a shared heartbeat are insufficient operational alerts. Zero rejections at roughly 27,000 requests/day supports that operating point, not verification of a 60,000/day ceiling. |
| **Cost** | The supplied 4.3 GB/day implies about **129 GB per 30 days**, or **60 GB for 14 days**, before silver, Iceberg history, logs, and safety headroom. Static savings help, but bus TU remains the dominant workload. Budget compute, disk, IPv4, upload egress, object operations, retention, catalog, and analyst usage together. Credits are temporary funding, not evidence of sustainable cost. Avoid adding feeds until storage and recurring cost are controlled. |

## C. Codebase review

These findings are grounded in the reviewed code. Synthetic, in-memory checks reproduced the capture and report failures; the full pytest suite was not run because its fixtures write files.

- **High — Seven-day gate rejects healthy, unaligned windows.** [`capture.py:110`](../../src/transit_lakehouse/checks/capture.py#L110): the reader excludes observations before `start`, then the gate rejects any first poll after `start`. A complete schedule offset by one second reports 100% capture but fails history coverage. Existing tests align the first poll exactly with the boundary. Establish prior coverage independently of exact timestamp equality.

- **High — Routine checks erase leading outages.** [`capture.py:88`](../../src/transit_lakehouse/checks/capture.py#L88): every non-gate window starts its denominator at its first recorded attempt, confusing an outage with initial deployment. Thirty missing minutes followed by thirty healthy minutes passes at 100%. Persist feed activation/schedule history and test outages at window boundaries.

- **Med — Historical silence does not fail the SLA.** [`capture.py:98`](../../src/transit_lakehouse/checks/capture.py#L98): gaps are calculated but only current staleness is enforced. A 90-second VP gap passes a daily check at 99.913%, contrary to D-007's four-interval limit. Add a historical-gap regression.

- **Med — Unknown freshness passes.** [`capture.py:104`](../../src/transit_lakehouse/checks/capture.py#L104): a whole window of successful HTTP responses with decode errors and no timestamps passes. Keeping raw capture successful is appropriate; declaring the combined freshness check passed is not. Test missing/invalid timestamps and expose validity separately.

- **High — Bootstrap exposes the key through process arguments.** [`bootstrap_vm.sh:43`](../../scripts/bootstrap_vm.sh#L43): shell expansion places the secret inside `sed`'s argument list, contradicting the script's security comment. Supply secret content through stdin or another mechanism that avoids argv. Add a bootstrap-specific check.

- **Med — PR #3 crashes on diagnostic edge cases.** [`report_html.py:725`](../../src/transit_lakehouse/checks/report_html.py#L725) indexes an empty feed list; [`report_html.py:648`](../../src/transit_lakehouse/checks/report_html.py#L648) converts zero runway into `full_day=None`, which later fails formatting. Both were reproduced. Test empty input and zero free space.

- **Med — Received-volume reporting excludes unchanged downloads.** [`quota_report.py:79`](../../src/transit_lakehouse/checks/quota_report.py#L79): `received_per_day` uses only newly landed bytes. Two equal-sized downloads, one unchanged, report half the actual received volume. Separate network bytes from newly retained bytes and test both.

**PR #2:** widening slow-feed lookback addresses the immediate false stale result, but retains the gate and denominator defects above. Its helper-only test does not verify end-to-end missing-poll detection. The output also needs explicit per-feed effective windows.

## D. Top 5 recommended next actions

1. **Protect capture continuity:** implement verified bronze offload, local eviction safeguards, and disk-runway alerts before approximately 2 December.
2. **Repair measurement:** fix gate boundaries, outage denominators, historical gaps, and unknown freshness; extend PR #2 integration tests.
3. **Close concrete defects:** remove bootstrap argv exposure, fix PR #3 edge cases, and correct received-byte accounting.
4. **Resolve static refetch behaviour:** reconcile deployed code and manifests, persist scheduling/validators, and distinguish ZIP changes from timetable changes.
5. **Build one defensible bus-only vertical slice:** lightweight live processing → versioned silver → coverage-qualified trip outcomes, bunching, and ETA evaluation; benchmark cost before introducing Redpanda/Spark.