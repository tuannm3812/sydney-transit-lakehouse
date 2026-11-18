# API quota and snapshot size report

Window: 2026-11-17 07:12 to 2026-11-18 07:12 UTC (24.0 h of data). Generated 2026-11-18 07:12 UTC.

## Requests and limit signals

| feed | requests | req/day | ok | unchanged | limit hits | other errors |
|---|---:|---:|---:|---:|---:|---:|
| buses_tripupdates | 2,881 | 2,881 | 2,881 | 0 | 0 | 0 |
| buses_vehiclepos | 5,760 | 5,761 | 5,760 | 0 | 0 | 0 |
| metro_tripupdates | 2,881 | 2,881 | 2,881 | 0 | 0 | 0 |
| metro_vehiclepos | 5,760 | 5,761 | 5,759 | 1 | 0 | 0 |
| service_alerts | 1,410 | 1,410 | 896 | 514 | 0 | 0 |
| static_gtfs | 6 | 6 | 3 | 3 | 0 | 0 |
| sydneytrains_tripupdates | 2,881 | 2,881 | 2,880 | 1 | 0 | 0 |
| sydneytrains_vehiclepos | 5,760 | 5,761 | 5,760 | 0 | 0 | 0 |

Total: **27,339 requests (27,342/day)**, 46% of the assumed 60,000/day quota.

No rate or quota rejections across 27,339 requests. Over a full 24 h window this confirms the real quota is at least 27,342 requests/day.

Outcome breakdown: new 26,820, unchanged 519

## Snapshot sizes (successful polls)

| feed | p50 | p95 | max | received/day | on disk/day | on disk/30 days |
|---|---:|---:|---:|---:|---:|---:|
| buses_tripupdates | 4.69 MB | 8.92 MB | 9.24 MB | 11,848.92 MB | 3,376.23 MB | 101,286.75 MB |
| buses_vehiclepos | 288.3 kB | 697.7 kB | 747.2 kB | 1,583.63 MB | 400.57 MB | 12,017.07 MB |
| metro_tripupdates | 78.9 kB | 103.5 kB | 106.2 kB | 180.47 MB | 52.18 MB | 1,565.52 MB |
| metro_vehiclepos | 9.7 kB | 12.4 kB | 12.6 kB | 43.42 MB | 9.23 MB | 276.75 MB |
| service_alerts | 467.0 kB | 478.5 kB | 480.5 kB | 419.97 MB | 74.56 MB | 2,236.76 MB |
| static_gtfs | 286.25 MB | 286.70 MB | 286.70 MB | 859.72 MB | 859.72 MB | 25,791.71 MB |
| sydneytrains_tripupdates | 218.9 kB | 274.8 kB | 287.6 kB | 558.31 MB | 85.10 MB | 2,552.89 MB |
| sydneytrains_vehiclepos | 47.4 kB | 60.1 kB | 61.5 kB | 262.85 MB | 84.54 MB | 2,536.32 MB |

Downloaded 16,857.80 MB/day, of which 15,757.30 MB/day was new. On disk after gzip: **4,942.13 MB/day, 148.3 GB per 30 days** before compaction into Iceberg.

## Response headers observed

- `etag`: "1794937513.432-286251913-1981158128"
- `last-modified`: Tue, 17 Nov 2026 17:45:13 GMT, Wed, 18 Nov 2026 04:06:19 GMT, Wed, 18 Nov 2026 04:06:21 GMT, Wed, 18 Nov 2026 04:06:28 GMT, Wed, 18 Nov 2026 04:06:30 GMT
- `x-amz-cf-pop`: CGK60-P1, SYD3-P1, SYD3-P3, SYD62-P1, SYD62-P2
- `x-cache`: Hit from cloudfront, Miss from cloudfront
- `x-cacheable`: NO - TTL = 0
- `x-content-type-options`: nosniff, nosniff, nosniff
- `x-frame-options`: DENY
- `x-ttl`: 0.000
- `x-xss-protection`: 1; mode=block
