# 6. Deployment: the Phase 0 collector on a single VM

*Written 2026-11-16. Per D-014: one VM running Docker Compose. Oracle's console moves things around now and then, so if a label below doesn't match, look for the nearest equivalent.*

## Option A: Hetzner Cloud (recommended after Oracle sign-up failed, 2026-11-17)

1. Sign up at <https://accounts.hetzner.com/signUp>. Hetzner may ask for ID or a small PayPal or card verification.
2. **Cloud Console → New project → Add server**:

| Setting | Value |
|---|---|
| Location | any. The collector only makes outbound calls every 15 s, so latency to Sydney doesn't matter (Singapore or Helsinki are fine) |
| Image | **Ubuntu 24.04** |
| Type | **Shared vCPU, Arm64 → CAX11** (2 vCPU, 4 GB), about €4/month. Resize to CAX31 (8 vCPU, 16 GB) in place for Phase 1 |
| Networking | public IPv4 on (it's a small extra fee). Without it the VM cannot reach TfNSW's IPv4-only API |
| SSH keys | add your Mac's `~/.ssh/id_ed25519.pub` (run `ssh-keygen -t ed25519` first if you don't have one) |
| Backups | off for Phase 0 |

3. SSH in with `ssh root@<ip>` and run the bootstrap in step 3 below. Hetzner logs you in as `root`, and the script works the same way.

Check Hetzner's current prices before you pick: they adjust them from time to time.

## Option B: Oracle Cloud Always Free

## 1. Account (once)

1. Sign up at <https://www.oracle.com/cloud/free/> and choose **Australia East (Sydney)** as the home region. This is permanent, and Always Free compute runs only in the home region.
2. Recommended: **upgrade to Pay As You Go** and create a **budget alert at AUD 1**. Always Free resources stay free. The upgrade stops Oracle reclaiming "idle" free instances (our collector is mostly idle by CPU) and improves access to A1 capacity.

## 2. Create the VM

Go to **Compute → Instances → Create instance**:

| Setting | Value |
|---|---|
| Name | `transit-collector` |
| Image | Canonical **Ubuntu 24.04** (pick the aarch64 build if asked) |
| Shape | **Ampere → VM.Standard.A1.Flex**, **4 OCPU, 24 GB** (the whole free allowance, which covers later phases) |
| Networking | default VCN with a public subnet, and **assign a public IPv4 address** |
| SSH keys | **Generate a key pair** and download both files, or paste your own `~/.ssh/id_ed25519.pub` |
| Boot volume | default (about 47 GB). Up to 200 GB total is free |

If it says **"Out of capacity"**, try 2 OCPU / 12 GB, a different fault domain, or retry later. Capacity frees up often, and Pay As You Go accounts get priority.

No inbound ports are needed besides SSH (22, open by default). The collector only makes outbound HTTPS calls.

## 3. Bootstrap

From your Mac:

```bash
chmod 600 ~/Downloads/ssh-key-*.key          # if Oracle generated the key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<public-ip>
```

On the VM:

```bash
curl -fsSL https://raw.githubusercontent.com/tuannm3812/sydney-transit-lakehouse/main/scripts/bootstrap_vm.sh | bash
```

The script installs Docker, clones the repo, prompts for the TfNSW key (the input is hidden and saved to `.env` with mode 600), checks the quota plan, starts the collector with `restart: unless-stopped`, and schedules a daily capture check at 00:05 UTC into `docs/reports/`.

Log out and back in once so the `docker` group applies, then check:

```bash
cd ~/sydney-transit-lakehouse
docker compose ps                      # collector: healthy
docker compose run --rm checks capture --window 15m
```

## 4. Day 1 and day 7

- After 24 h: `docker compose run --rm checks quota --window 24h --out docs/reports/quota-24h.md`, then copy the report into the repo and update D-004 and D-013.
- Day 7: `docker compose run --rm checks capture --window 7d --gate`. Exit code 0 means the gate passed.

## Cost

Hetzner: about AUD 7/month for CAX11 plus IPv4. Oracle: $0 inside Always Free (4 OCPU, 24 GB, 200 GB block storage, 10 TB/month egress). Overnight raw volume was about 0.5 GB/day (D-013), so 7 days of Phase 0 fit easily on the boot volume.
