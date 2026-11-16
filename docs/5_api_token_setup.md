# 5. API token setup

*Checked 2026-11-16 against the TfNSW Open Data Hub User Guide and API Basics pages. The site has changed its flow before, so if a step below doesn't match, follow the site and update this file.*

## Get the key

1. Go to <https://opendata.transport.nsw.gov.au> and register (it's free), then log in.
2. Click your **profile icon**, then **API Tokens**.
3. Enter a name (e.g. `sydney-transit-lakehouse`) and click **Create API token**.
4. **Copy the key immediately.** It is shown only once.
5. If the site asks which APIs the key or application may use, add:
   - Public Transport - Realtime Vehicle Positions (v1 and v2 if both are listed)
   - Public Transport - Realtime Trip Update (v1 and v2 if both are listed)
   - Timetables Complete GTFS (the static schedule, which the ghost-bus tracker needs in Phase 2)

   A 401 "unauthenticated" on a feed usually means that API isn't selected for the key.

## Where it goes

1. In the repo root: `cp .env.example .env`
2. Open `.env` and set `TFNSW_API_KEY=<your key>`. Use no quotes, no `apikey ` prefix and no spaces; the collector adds the `apikey ` prefix itself.
3. `.env` is gitignored, and so is every `.env.*` except the example. Check with `git check-ignore .env`, which should print `.env`.
4. On the VM, put the same `.env` next to `docker-compose.yml`, with `chmod 600 .env`. Compose passes it to the container through `env_file`.
5. Never paste the key in chat, an issue, a commit or a log.

## Check it works

```bash
set -a; source .env; set +a
python -m transit_lakehouse.collector --check-config   # no requests, shows the quota plan
python -m transit_lakehouse.checks quota --probe       # one request per feed, prints status + headers
```

## Limits (Bronze plan, as published)

- 60,000 requests/day, resetting at midnight AEST, and 5 requests/second.
- Over a limit you get **403** with `X-Error-Detail: Account Over Rate Limit` or `Account Over Quota Limit`, sometimes 503 from the gateway, and occasionally 401.
- Developers can't see their usage, so the 24-hour report establishes it from our own request counts.
- To ask for more, email OpenDataHelp@transport.nsw.gov.au.

## If the key leaks

Revoke it under API Tokens, create a new one, and update `.env` on every host. Deleting the message is not enough.
