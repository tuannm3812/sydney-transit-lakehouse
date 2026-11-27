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

The pre-approved runbook (D-043). Every step runs on the collector host from `~/sydney-transit-lakehouse`, needs Mike's SSH approval naming the commit, and leaves the collector running. Steps 1 and 2 are read-only for the data; step 3 stops at the first failure.

**1. Gate check and R0-02** (on the commit the server already runs, which has D-037 and D-055):

```
docker compose run --rm checks capture --window 7d --gate --json docs/reports/capture-7d-gate.json
docker compose run --rm checks gate-report --gate-json docs/reports/capture-7d-gate.json \
  --daily 'docs/reports/capture-2026112*.json' --commit "$(git rev-parse --short HEAD)" \
  --out docs/reports/phase0-gate-$(date -u +%Y%m%d).md
```

The first command exits 0 only if every feed passes; upstream outages within the D-037 allowance show as warnings. The second writes report R0-02 as a Draft with the gate table and one row per daily check filled in. Then: copy the draft and the gate JSON into the repo, write the Findings by hand, set the status to Final, render the PDF (`python -m transit_lakehouse.reports <file> --out <dir>`), add the gate decision row, tick the gate in STATUS.md, and update both decks. If the gate fails, the report says which feed and why; stop here, log the incident (docs/10_incidents.md) and decide with Mike whether the clock restarts, as on day 3.

**2. Update the code and build the worker image** (`<COMMIT>` is the one Mike names):

```
git fetch origin main && git merge --ff-only origin/main
test "$(git rev-parse --short HEAD)" = <COMMIT>
docker compose --profile worker build worker
docker compose ps      # the collector is still up; building does not restart it
```

**3. Backfill the S3 history into the live lakehouse** (D-040, D-056), with the worker service not yet started:

```
nohup scripts/backfill.sh 2026-11-17T06 > /dev/null 2>&1 &
tail -f docs/reports/backfill.log
```

The script loads every timetable published since 17 Nov 06:00 UTC, then replays the realtime feeds one UTC day at a time, oldest first, straight into `data/lakehouse/`, using `data/backfill/` for the hour files. It goes up to the last hour the offload has shipped, then catches up on the hours shipped while it ran, so the worker starts with about an hour of backlog. It compacts and expires every 6 hours of replay (I-20) and runs under the worker's 900 MB cap. Before each day it reads the live watch state and stops if a feed or disk alert is open (exit 3); stopping loses nothing, since the same command resumes every feed after its watermark. It takes the live worker's lock, so the two cannot run at once. The 23 Nov rehearsal replayed one hour of 11 feeds in 130 s, so about 255 hours take 9 to 10 hours. "missing" lines are expected for hours a feed was not yet enabled (light rail and ferries before 18 Nov 07:44 UTC) and for empty hours of outages. Disk: the tables are expected to stay well under 15 GB, and the watch alerts below 10 GB free.

**4. Start the live worker** once the log says `backfill done`:

```
docker compose --profile worker up -d --no-deps worker
docker compose logs --tail 20 worker    # "run: N files" with no "seed file ... is gone"
docker compose --profile worker run --rm --no-deps worker status
free -m && docker compose ps
```

It carries on from the backfill's watermarks with the local raw files (the prune keeps 3 days), so the last backfilled hour and the first live one join without a gap. Then turn on the nightly gold build (D-039), never at the same time as a backfill.

**Undo.** `docker compose --profile worker stop worker` stops the worker and leaves the collector alone. Raw files and S3 are never changed by steps 3 and 4. A bad build is redone by stopping the worker and deleting `data/lakehouse/` and `data/backfill/` (a delete on the server, so it needs its own approval), then step 3 again.

## Revised timeline (D-039 to D-044)

1. **Now to 28 Nov:** the gate runs. In the cloud, Phase 3 starts on replayed S3 hours (D-042, needs about 2 days of S3 hours from Mike) and the Phase 4 eval questions are drafted.
2. **28 Nov, gate day:** record the gate, backfill the S3 history into the live lakehouse, then start the worker (D-043, D-056).
3. **29 Nov to 2 Dec:** replay identical on live and backfilled days (Phase 1 gate); nightly gold build on; first full day of every mode checked.
4. **2 to 9 Dec:** Phase 2's 7-day quality clock (D-039); the Spark benchmark runs for a day around 3 Dec (D-041).
5. **By 13 Dec:** hand-check 20 ghost trips on backfilled history (Phase 3 gate).
6. **7 to 20 Dec:** text-to-SQL analyst, 30-question eval, write-up, demo video, portfolio card, Evidence dashboard (D-044). Launch about 20 Dec.
