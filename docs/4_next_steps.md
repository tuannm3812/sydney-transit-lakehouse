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
