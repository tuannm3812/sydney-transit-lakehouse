#!/usr/bin/env bash
# One-command setup for the Phase 0 collector on a fresh Ubuntu 22.04/24.04
# VM (tested target: Oracle Cloud Always Free, Ampere A1, Sydney). Safe to
# re-run: every step checks before it acts.
#
#   curl -fsSL https://raw.githubusercontent.com/tuannm3812/sydney-transit-lakehouse/main/scripts/bootstrap_vm.sh | bash
#
# It prompts for the TfNSW key without echoing it and writes it to .env
# with mode 600. The key never appears on the command line or in history.
set -euo pipefail

REPO_URL="https://github.com/tuannm3812/sydney-transit-lakehouse.git"
APP_DIR="${APP_DIR:-$HOME/sydney-transit-lakehouse}"

log() { printf '\n==> %s\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine and the Compose plugin"
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl git
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"  # group membership applies from the next login
fi

if [ ! -d "$APP_DIR/.git" ]; then
  log "Cloning $REPO_URL"
  git clone "$REPO_URL" "$APP_DIR"
else
  log "Updating $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
fi
cd "$APP_DIR"

if ! grep -qs '^TFNSW_API_KEY=.\{20,\}' .env; then
  log "TfNSW API key (input hidden; see docs/5_api_token_setup.md)"
  read -r -s -p "Paste key and press Enter: " KEY </dev/tty
  echo
  umask 077
  # Bash builtins only (read/printf), so the key never appears in any
  # process's argument list (ps, /proc/*/cmdline).
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ $line == TFNSW_API_KEY=* ]]; then
      printf 'TFNSW_API_KEY=%s\n' "$KEY"
    else
      printf '%s\n' "$line"
    fi
  done < .env.example > .env
  unset KEY line
  chmod 600 .env
fi

if [ ! -f /swapfile ] && [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 4096 ]; then
  log "Adding 2 GB swap (small instance, Docker builds need headroom)"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

mkdir -p data docs/reports
# The container runs as uid 1000; make sure it can write the data dir.
sudo chown -R 1000:1000 data docs/reports

log "Checking the feed plan against the quota budget"
# --profile tools so the checks image is rebuilt too; plain `build`
# skips services in inactive profiles and cron would run stale code.
$DOCKER compose --profile tools build --quiet
$DOCKER compose run --rm --entrypoint python collector \
  -m transit_lakehouse.collector --check-config

log "Starting the collector"
$DOCKER compose up -d collector

# cron runs in a fresh login, so the docker group applies: no sudo.
CRON_LINE="5 0 * * * cd $APP_DIR && docker compose run --rm checks capture --window 24h --json docs/reports/capture-\$(date -u +\\%Y\\%m\\%d).json >> docs/reports/capture.log 2>&1"
if ! crontab -l 2>/dev/null | grep -q 'checks capture'; then
  log "Scheduling the daily capture check (00:05 UTC)"
  # `crontab -l` exits 1 for a user with no crontab; under `set -e`
  # that would abort the subshell before the echo.
  (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
fi

log "Done. Useful commands:"
cat <<MSG
  cd $APP_DIR
  $DOCKER compose ps                         # collector should be 'healthy' after ~1 min
  $DOCKER compose logs -f collector
  $DOCKER compose run --rm checks capture --window 1h
  $DOCKER compose run --rm checks quota --window 24h --out docs/reports/quota-24h.md   # after 24 h
MSG
