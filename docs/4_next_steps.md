# 4. Next steps

*2026-11-16*

1. Mike creates the API key and puts it in `.env` (docs/5_api_token_setup.md).
2. First live run from a machine that can reach `api.transport.nsw.gov.au`: `python -m transit_lakehouse.checks quota --probe`. Every enabled feed should answer 200. Fix any 404 path in `config/feeds.toml` and log it against D-004.
3. Create the AWS t3.small in Sydney (D-015, D-016) and run `scripts/bootstrap_vm.sh` (docs/6_deployment.md, D-014). A laptop that sleeps cannot pass the 99% gate.
4. After 24 h: run `checks quota --window 24h --out docs/reports/quota-24h.md`, commit the report, and update D-004 with the confirmed figures and real snapshot sizes. If the headroom allows, enable light rail and ferries.
5. Days 1–7: run `checks capture --window 24h` daily and log it in STATUS.md. On day 7 run `checks capture --window 7d --gate`.
6. Write the first cost and scale note once real snapshot sizes are known (GB/day raw, R2 cost, what breaks at 10x).
7. Phase 1 (after the gate, D-023): a light worker that turns raw snapshots into bronze and silver Iceberg tables, then a Redpanda + Spark Structured Streaming benchmark on the same feeds, replayed from raw, with latency and cost measured against the light worker.

## Gate day (earliest 2026-11-27 16:00 UTC, D-033, D-037)

Run on the collector host, read-only for the data (needs Mike's SSH approval):

```
cd ~/sydney-transit-lakehouse
docker compose run --rm checks capture --window 7d --gate --json docs/reports/capture-7d-gate.json
docker compose run --rm checks gate-report --gate-json docs/reports/capture-7d-gate.json \
  --daily 'docs/reports/capture-2026112*.json' --commit "$(git rev-parse --short HEAD)" \
  --out docs/reports/phase0-gate-$(date -u +%Y%m%d).md
```

The first command exits 0 only if every feed passes; upstream outages within the D-037 allowance show as warnings, so the server must run a commit that includes D-037. The second writes report R0-02 as a Draft with the gate table and one row per daily check filled in. Then: copy the draft and the gate JSON into the repo, write the Findings by hand, set the status to Final, render the PDF (`python -m transit_lakehouse.reports <file> --out <dir>`), add the gate decision row, tick the gate in STATUS.md, and update both decks. If the gate fails, the report says which feed and why; log the incident (docs/10_incidents.md) and decide with Mike whether the clock restarts, as on day 3.

After a pass, the rest of the pre-approved runbook (D-043) runs the same day: deploy the worker (`docker compose --profile worker up -d worker`, docs/9_phase1.md), then start the overnight backfill of the S3 hours since 17 Nov, newest day first, under the 700 MB cap and outside the gold build (D-040). Stop the backfill if a live-watch alert fires.

## Revised timeline (D-039 to D-044)

1. **Now to 28 Nov:** the gate runs. In the cloud, Phase 3 starts on replayed S3 hours (D-042, needs about 2 days of S3 hours from Mike) and the Phase 4 eval questions are drafted.
2. **28 Nov, gate day:** record the gate, deploy the worker, start the backfill (D-043).
3. **29 Nov to 2 Dec:** replay identical on live and backfilled days (Phase 1 gate); nightly gold build on; first full day of every mode checked.
4. **2 to 9 Dec:** Phase 2's 7-day quality clock (D-039); the Spark benchmark runs for a day around 3 Dec (D-041).
5. **By 13 Dec:** hand-check 20 ghost trips on backfilled history (Phase 3 gate).
6. **7 to 20 Dec:** text-to-SQL analyst, 30-question eval, write-up, demo video, portfolio card, Evidence dashboard (D-044). Launch about 20 Dec.
