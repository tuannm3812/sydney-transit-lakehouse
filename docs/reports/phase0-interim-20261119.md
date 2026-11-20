---
id: R0-01
title: Phase 0 collector, first 24 hours
subtitle: The live collector's capture rate, API quota, data volume and first offload to object storage, two days into the seven-day capture run.
phase: 0
status: Interim
verdict: Info
date: 2026-11-19
window: 17 Nov 2026 07:12 to 18 Nov 2026 07:12 UTC (24 h report); offload 18 Nov 14:57 UTC
commit: 83592ba (24 h window), 8f6d15d (offload)
environment: AWS EC2 t3.small in ap-southeast-2 (Sydney), Docker Compose, 80 GB disk (D-015, D-016, D-018)
decisions: D-019, D-022, D-024, D-025, D-026
evidence: docs/reports/quota-24h.md, quota-24h.json, capture-24h-20261118.txt, profile-20261118.md, offload-20261118.md
---

# Phase 0 collector, first 24 hours

## Summary

> The collector is on track for the Phase 0 gate. In its first full 24 hours it captured **100% of polls** on every feed, used 46% of the API quota with no rejections, and stored 4.94 GB a day, which now moves hourly to object storage before the disk fills.

- **Capture was 100% on all 8 feeds,** including through a redeploy at 04:06 UTC. The longest gap was 33 s on a feed polled every 30 s (section 3.1).
- **The quota holds.** 27,339 requests used 46% of the 60,000 a day with no 403, 429 or 503 responses, so the real quota is at least 27,342 a day (D-024, section 3.2).
- **Bus trip updates are 68% of the volume.** Of the 4.94 GB stored a day after gzip, 3.38 GB came from bus trip updates (section 3.3).
- **Four feeds were added after the report.** Light rail and ferries raised the load to 41,764 calls a day, 70% of the quota (D-025, section 3.4).
- **Raw data is now in object storage.** The first offload sent 513 objects (6.13 GB) in 3 min 21 s, and an hourly job has run since (D-026, section 3.6).

## Scope and method

The collector has polled TfNSW's GTFS-realtime feeds from AWS EC2 in Sydney since 17 Nov 2026 06:45 UTC:

```flow
source | TfNSW Open Data | GTFS-realtime and timetable feeds
raw | Collector | polls every 15 to 30 s, records every attempt
raw | Raw on disk | gzipped snapshots by feed, date and hour
store | Object storage | hourly tars, checked on upload
```

Every poll attempt is written to the manifest (D-007). The numbers below come from these commands, run read-only on the host:

| Check | Command | Window |
|---|---|---|
| Capture and freshness | `checks capture --window 24h` | 17 Nov 07:12 to 18 Nov 07:12 UTC |
| Quota and volume | `checks quota --window 24h` | same |
| Change share | `checks profile` | one hour per row, 17 and 18 Nov |
| Offload | `offload upload` | raw hours 17 Nov 06:00 to 18 Nov 13:00 UTC |

Out of scope: the seven-day gate itself (earliest 24 Nov for the original 8 feeds, 25 Nov for the 4 added later) and the tables built from the data (R1-01).

## Results

### Capture and freshness

| Feed | Capture | Polls (got / expected) | p95 freshness | Longest gap | Check |
|---|---:|---:|---:|---:|---|
| `buses_vehiclepos` | 100.00% | 5,760 / 5,760 | 10 s | 18 s | Pass |
| `buses_tripupdates` | 100.00% | 2,881 / 2,880 | 13 s | 33 s | Pass |
| `sydneytrains_vehiclepos` | 100.00% | 5,760 / 5,760 | 9 s | 20 s | Pass |
| `sydneytrains_tripupdates` | 100.00% | 2,881 / 2,880 | 14 s | 32 s | Pass |
| `metro_vehiclepos` | 100.00% | 5,760 / 5,760 | 12 s | 17 s | Pass |
| `metro_tripupdates` | 100.00% | 2,881 / 2,880 | 12 s | 31 s | Pass |
| `service_alerts` | 100.00% | 1,410 / 1,408 | 86 s | 62 s | Pass |
| `static_gtfs` | 100.00% | 6 / 4 | n/a | 6.0 h | Pass |

Freshness is the fetch time minus the feed's own timestamp. Service Alerts are polled every 60 s and the timetable every 6 h, so their gaps match their cadence.

### API quota

| Feed | Requests | Unchanged | Limit hits |
|---|---:|---:|---:|
| `buses_vehiclepos` | 5,760 | 0 | 0 |
| `buses_tripupdates` | 2,881 | 0 | 0 |
| `sydneytrains_vehiclepos` | 5,760 | 0 | 0 |
| `sydneytrains_tripupdates` | 2,881 | 1 | 0 |
| `metro_vehiclepos` | 5,760 | 1 | 0 |
| `metro_tripupdates` | 2,881 | 0 | 0 |
| `service_alerts` | 1,410 | 514 | 0 |
| `static_gtfs` | 6 | 3 | 0 |
| All feeds | 27,339 | 519 | 0 |

An unchanged poll returned the same payload as the one before and was not stored again.

### Data volume

| Feed | Median snapshot | *Received a day* | *Stored a day* |
|---|---:|---:|---:|
| `buses_tripupdates` | 4.69 MB | 11,849 MB | 3,376 MB |
| `static_gtfs` | 286.25 MB | 860 MB | 860 MB |
| `buses_vehiclepos` | 288 kB | 1,584 MB | 401 MB |
| `sydneytrains_tripupdates` | 219 kB | 558 MB | 85 MB |
| `sydneytrains_vehiclepos` | 47 kB | 263 MB | 85 MB |
| `service_alerts` | 467 kB | 420 MB | 75 MB |
| `metro_tripupdates` | 79 kB | 180 MB | 52 MB |
| `metro_vehiclepos` | 10 kB | 43 MB | 9 MB |
| All feeds | | 16,858 MB | 4,942 MB |

```chart
title: Stored a day by feed, after gzip
unit: MB
Bus trip updates: 3376
Timetable: 860
Bus positions: 401
Trains trip updates: 85
Trains positions: 85
Service Alerts: 75
Metro trip updates: 52
Metro positions: 9
```

### Feeds added after the report

D-025, deployed 18 Nov 07:44 UTC.

| | Before | After |
|---|---:|---:|
| Feeds | 8 | 12 |
| Calls a day (projected) | 27,364 | 41,764 |
| Share of quota | 46% | **70%** |

The new feeds are positions and trip updates for Inner West light rail (`/v2`) and Sydney Ferries (`/v1`). All four answered 200 and decoded in a live probe before they were turned on; the `/v1` light rail paths return 404.

### Change share

The share of rows that differ from the previous snapshot of the same feed. It sets how much a change-only table saves (R1-01).

| Hour (UTC) | Sydney | Feed | Rows per snapshot | *Changed* |
|---|---|---|---:|---:|
| 21:00, 17 Nov | 8 am | `buses_tripupdates` | 169,708 | 49.0% |
| 21:00, 17 Nov | 8 am | `buses_vehiclepos` | 3,504 | 84.0% |
| 21:00, 17 Nov | 8 am | `sydneytrains_tripupdates` | 4,516 | 16.3% |
| 03:00, 18 Nov | 2 pm | `buses_tripupdates` | 200,688 | 29.8% |
| 03:00, 18 Nov | 2 pm | `buses_vehiclepos` | 1,916 | 84.5% |
| 03:00, 18 Nov | 2 pm | `sydneytrains_tripupdates` | 4,606 | 9.8% |

### First offload to object storage

D-026, at 8f6d15d, with Mike's approval.

| | |
|---|---|
| Ran | 18 Nov 14:57:34 to 15:00:55 UTC (3 min 21 s) |
| Uploaded | 513 objects, 0 failed |
| Bytes | **6,128,977,423 (6.13 GB)** |
| Objects | 255 hourly raw tars, 258 manifest hours |
| Raw hours | 17 Nov 06:00 to 18 Nov 13:00 UTC |
| Verification | SHA-256 checked on upload and read back; a rerun found all 513 done |

## Findings

- **The collector is reliable enough for the gate.** No feed lost a poll in 24 hours, and a redeploy cost none (section 3.1). The gate needs seven such days in a row at 99% or more.
- **The disk was the limit, not the quota.** At 46% of the quota with no rejections there was room for four more feeds (D-025). At 4.94 GB a day the 80 GB disk would have filled around 1 Dec (D-024); the hourly offload removes that limit.
- **Bus trip updates drive every cost downstream.** They are 68% of stored bytes, and half their rows change between snapshots at peak, which is why Phase 1 stores only changes and encodes stop predictions against the trip delay (D-028, R1-01).
- **The timetable is the second largest feed.** TfNSW published three distinct 287 MB zips on 17 Nov; conditional requests (D-021) now skip unchanged downloads.

## Limits

- One 24-hour weekday window. Weekend volume will differ, and the seven-day run is still in progress.
- The four feeds added on 18 Nov are not in these tables; they reach their own seven-day window a day after the original eight (D-022, D-025).
- Freshness relies on TfNSW's own timestamps, which the collector cannot check.
- The local prune after offload is not on yet, so the host still keeps every raw hour.

## Next steps

- **Each day until the gate:** record the lowest-feed capture in STATUS.md.
- **At the gate, about 24 Nov (original 8) and 25 Nov (new 4):** run `checks capture --window 7d --gate` and publish R0-02, the final Phase 0 report, which supersedes this one.
- **Needs Mike:** turn on the local prune once the offload has run cleanly for a few days.
