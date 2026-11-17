# 4. Next steps

*2026-11-16*

1. Mike creates the API key and puts it in `.env` (docs/5_api_token_setup.md).
2. First live run from a machine that can reach `api.transport.nsw.gov.au`: `python -m transit_lakehouse.checks quota --probe`. Every enabled feed should answer 200. Fix any 404 path in `config/feeds.toml` and log it against D-004.
3. Create the AWS t3.small in Sydney (D-015, D-016) and run `scripts/bootstrap_vm.sh` (docs/6_deployment.md, D-014). A laptop that sleeps cannot pass the 99% gate.
4. After 24 h: run `checks quota --window 24h --out docs/reports/quota-24h.md`, commit the report, and update D-004 with the confirmed figures and real snapshot sizes. If the headroom allows, enable light rail and ferries.
5. Days 1–7: run `checks capture --window 24h` daily and log it in STATUS.md. On day 7 run `checks capture --window 7d --gate`.
6. Write the first cost and scale note once real snapshot sizes are known (GB/day raw, R2 cost, what breaks at 10x).
7. Phase 1 (after the gate): Redpanda in Compose, the collector producing to it, a backfill from raw files, and Spark Structured Streaming into bronze Iceberg.
