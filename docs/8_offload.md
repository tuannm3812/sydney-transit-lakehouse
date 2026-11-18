# 8. Raw offload to S3

*2026-11-18. Decision: D-026.*

The collector lands about 4.9 GB a day (D-024), and the 80 GB disk fills around 1 Dec 2026. The offload job copies every closed UTC hour to S3 and, once S3 holds a verified copy, deletes local raw hours older than a few days. Raw snapshots stay the replay source of truth (D-005, D-023); they just live in S3.

## What it does

- **Upload** (hourly, at :20): for each feed and each hour that ended more than 15 minutes ago, it builds one uncompressed tar of that hour's snapshots and uploads it, along with that hour's manifest file:
  - `raw/feed=<feed>/date=YYYY-MM-DD/hour=HH.tar`
  - `manifest/feed=<feed>/date=YYYY-MM-DD/hour=HH.jsonl`

  One object per feed-hour keeps requests near 600 a day, instead of about 40,000 if every snapshot were its own object. S3 checks each upload's SHA-256 on arrival, and the job reads the object back before it records the hour as done under `data/_offload/`.
- **Prune** (daily, off until Mike enables it): deletes local raw hours older than `--keep` (default 3 days) only when S3 still holds a matching copy and the local hour has not changed since upload. Manifests are never deleted, because the capture checks read them.
- Re-runs are safe: a recorded hour is skipped, and an hour whose object already matches is not uploaded again.

## AWS console steps (Mike)

Everything is in **Asia Pacific (Sydney) ap-southeast-2**.

1. **Create the bucket.** S3 → Create bucket.
   - Name: something unique, e.g. `transit-raw-<your initials>-<4 random digits>`.
   - Block *all* public access: on (the default).
   - Bucket versioning: off.
   - Default encryption: SSE-S3 (the default).
2. **Add a lifecycle rule.** Bucket → Management → Create lifecycle rule.
   - Name `archive-after-30d`, applied to all objects.
   - Transition current versions to **Glacier Instant Retrieval after 30 days**.
   - Also tick "Delete expired object delete markers or incomplete multipart uploads", with incomplete uploads deleted after 1 day.
3. **Create the instance role.** IAM → Roles → Create role → Trusted entity "AWS service", use case **EC2** → skip the managed policies → name it `transit-collector-offload` → create. Then open the role → Add permissions → Create inline policy → JSON, and paste the policy below with your bucket name in both places. There is deliberately no delete permission: the server can add data but cannot remove it from S3.

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:PutObject", "s3:GetObject"],
         "Resource": "arn:aws:s3:::BUCKET_NAME/*"
       },
       {
         "Effect": "Allow",
         "Action": ["s3:ListBucket"],
         "Resource": "arn:aws:s3:::BUCKET_NAME"
       }
     ]
   }
   ```
4. **Attach the role to the collector.** EC2 → Instances → `i-07d863a91e1d17cb8` → Actions → Security → Modify IAM role → `transit-collector-offload` → Update.
5. **Let containers reach the role.** Same instance → Actions → Instance settings → Modify instance metadata options → IMDSv2 **Required**, **Metadata response hop limit 2** → Save. Hop limit 1 blocks Docker containers from reading the role's credentials.
6. **Optional:** Billing → Budgets → a monthly cost budget with an email alert.
7. **On the server**, add one line to `~/sydney-transit-lakehouse/.env`: `OFFLOAD_BUCKET=<your bucket name>`. No access keys are needed.

## Turning it on (server, with Mike's approval)

```bash
cd ~/sydney-transit-lakehouse
git pull && docker compose --profile tools build
docker compose run --rm offload upload --dry-run     # lists what it would send
docker compose run --rm offload upload               # first run sends the backlog
```

Then schedule the hourly upload:

```bash
(crontab -l; echo '20 * * * * cd ~/sydney-transit-lakehouse && docker compose run --rm offload upload >> docs/reports/offload.log 2>&1') | crontab -
```

After a day of clean uploads, Mike enables pruning:

```bash
docker compose run --rm offload prune --keep 3d --dry-run
(crontab -l; echo '40 1 * * * cd ~/sydney-transit-lakehouse && docker compose run --rm offload prune --keep 3d >> docs/reports/offload.log 2>&1') | crontab -
```

## Cost (about, Sydney prices)

About 150 GB a month is added. S3 Standard is about USD 0.025 per GB-month and Glacier Instant Retrieval about USD 0.005, so the bill is about USD 2 in the first month and about USD 5 a month after three months. Requests come to under USD 0.10 a month. Uploads from EC2 to S3 in the same region are free.
