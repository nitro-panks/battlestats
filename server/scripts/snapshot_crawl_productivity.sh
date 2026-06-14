#!/usr/bin/env bash
#
# Daily reproducible snapshot of the clan-crawl productivity benchmark. Wraps the
# read-only `benchmark_crawl_productivity` management command and writes a
# timestamped JSON file to a durable directory so crawl coverage/discovery can be
# measured day-over-day. Companion to snapshot_observation_floor.sh.
#
# ZERO writes to the database. Intended to run from cron ON THE DROPLET, but is
# safe to run by hand. The droplet runs on UTC, so the cron time == UTC.
#
# Env overrides (all optional):
#   APP_ROOT      deploy root            (default /opt/battlestats-server)
#   ENV_FILE      runtime env file       (default /etc/battlestats-server.env)
#   SECRETS_FILE  secrets env file       (default /etc/battlestats-server.secrets.env)
#   OUT_DIR       snapshot directory     (default $APP_ROOT/shared/benchmarks/crawl-productivity)
#   KEEP          snapshots to retain    (default 180)
#
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/battlestats-server}"
SERVER_DIR="${APP_ROOT}/current/server"
VENV_PY="${APP_ROOT}/venv/bin/python"
ENV_FILE="${ENV_FILE:-/etc/battlestats-server.env}"
SECRETS_FILE="${SECRETS_FILE:-/etc/battlestats-server.secrets.env}"
OUT_DIR="${OUT_DIR:-${APP_ROOT}/shared/benchmarks/crawl-productivity}"
KEEP="${KEEP:-180}"

mkdir -p "$OUT_DIR"
TS="$(TZ=UTC date +%Y-%m-%d_%H%MZ)"
OUT="${OUT_DIR}/${TS}.json"
TMP="${OUT}.partial"

# Load production env (DB target, secrets) the same way the systemd units do.
set -a
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
# shellcheck disable=SC1090
[ -f "$SECRETS_FILE" ] && . "$SECRETS_FILE"
set +a

cd "$SERVER_DIR"

# manage.py prints a stray "Loading environment variables ..." line on stdout
# before the JSON; keep everything from the first line that begins with '{'.
"$VENV_PY" manage.py benchmark_crawl_productivity --json \
  | awk 'seen || /^{/ { seen=1; print }' > "$TMP"

# Validate before publishing, so a partial/garbled run never lands as a snapshot.
"$VENV_PY" - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    json.load(f)
PY

mv -f "$TMP" "$OUT"
echo "wrote $OUT"

# Retention: keep the newest $KEEP snapshots, prune the rest.
ls -1t "$OUT_DIR"/*.json 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
