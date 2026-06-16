#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SERVER_DIR}/.." && pwd)"

# Hard gate: local tree must be at or ahead of origin/main. Prevents a
# stale worktree from rsync'ing older code over production and silently
# reverting already-shipped fixes (see scripts/check_local_tree.sh).
"${REPO_ROOT}/scripts/check_local_tree.sh"

# Hard gate: CI must be passing before deploy
"${REPO_ROOT}/scripts/check_ci_status.sh"

HOST="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
APP_ROOT="${APP_ROOT:-/opt/battlestats-server}"
APP_USER="${APP_USER:-battlestats}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS="${AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS:-0}"
MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS="${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS:-}"
MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS="${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS:-}"
RELEASE_ID="$(date +%Y%m%d%H%M%S)"
REMOTE_RELEASE="${APP_ROOT}/releases/${RELEASE_ID}"
REMOTE_TMP_ENV="/tmp/battlestats-server.env.${RELEASE_ID}"
REMOTE_TMP_SECRETS="/tmp/battlestats-server.secrets.env.${RELEASE_ID}"
REMOTE_TMP_CERT="/tmp/battlestats-do-ca.${RELEASE_ID}.crt"
EXTRA_ALLOWED_HOSTS="${EXTRA_ALLOWED_HOSTS:-}"
DEFAULT_PUBLIC_ALLOWED_HOSTS="${DEFAULT_PUBLIC_ALLOWED_HOSTS:-battlestats.online,www.battlestats.online}"
POST_DEPLOY_VERIFY_REALMS="${POST_DEPLOY_VERIFY_REALMS:-na,eu}"

case "${AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS,,}" in
  1|true|yes|on)
    AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS=1
    ;;
  *)
    AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS=0
    ;;
esac

DJANGO_ALLOWED_HOSTS="$({
  printf '%s\n' localhost 127.0.0.1 "${HOST}"
  printf '%s' "${DEFAULT_PUBLIC_ALLOWED_HOSTS},${EXTRA_ALLOWED_HOSTS}" | tr ',' '\n'
} | awk 'NF && !seen[$0]++' | paste -sd, -)"

if [[ -z "${HOST}" ]]; then
  echo "Usage: $0 <droplet-ip-or-hostname>" >&2
  exit 1
fi

ssh "${DEPLOY_USER}@${HOST}" \
  APP_ROOT="${APP_ROOT}" \
  APP_USER="${APP_USER}" \
  REMOTE_RELEASE="${REMOTE_RELEASE}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/releases"
install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}"
install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}/server"
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/shared/logs"
touch "${APP_ROOT}/shared/logs/django.log"
chown "${APP_USER}:${APP_USER}" "${APP_ROOT}/shared/logs/django.log"
rm -rf "${REMOTE_RELEASE}/server/logs"
ln -sfn "${APP_ROOT}/shared/logs" "${REMOTE_RELEASE}/server/logs"
REMOTE

scp "${SERVER_DIR}/.env.cloud" "${DEPLOY_USER}@${HOST}:${REMOTE_TMP_ENV}"
scp "${SERVER_DIR}/.env.secrets.cloud" "${DEPLOY_USER}@${HOST}:${REMOTE_TMP_SECRETS}"
scp "${SERVER_DIR}/ca-certificate.crt" "${DEPLOY_USER}@${HOST}:${REMOTE_TMP_CERT}"

rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.coverage' \
  --exclude 'db.sqlite3' \
  --exclude 'logs' \
  --exclude 'media' \
  --exclude 'static' \
  --exclude 'staticfiles' \
  --exclude 'deploy' \
  "${SERVER_DIR}/" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/server/"

scp "${REPO_ROOT}/docker-compose.yml" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/docker-compose.yml"

REMOTE_DEPLOY_SCRIPT="/tmp/battlestats-deploy-${RELEASE_ID}.sh"

# Write the deploy script body to a file on the remote first, then execute it
# in a separate ssh call. This eliminates the silent-truncation bug we hit on
# 2026-04-10 where `'bash -s' <<'REMOTE'` would deliver the script via stdin
# and any child process that touched stdin (or any heredoc parse anomaly)
# could desync the bash command stream, causing the script to silently exit 0
# after configure_local_rabbitmq with migrate/collectstatic/restart all
# skipped. Writing to a file removes stdin from the picture entirely.
ssh "${DEPLOY_USER}@${HOST}" "cat > ${REMOTE_DEPLOY_SCRIPT}" <<'REMOTE'
#!/usr/bin/env bash
set -euo pipefail

# Stop-first barrier: no app services may be running while /etc/battlestats-server.env
# is being mutated below. `set_env_value` uses `sed -i`, which replaces the file inode,
# and `configure_local_rabbitmq` rotates the broker password and restarts rabbitmq-server
# mid-script. On 2026-04-08 a celery worker booted against an EnvironmentFile snapshot
# that was missing CELERY_BROKER_URL and entered an infinite reconnect loop. Stopping
# services up front eliminates the race — they are started cleanly at the end of the
# remote block (line ~460) after all env writes are finalized.
systemctl stop battlestats-beat battlestats-celery-crawls battlestats-celery-background battlestats-celery-hydration battlestats-celery battlestats-gunicorn 2>/dev/null || true

cp "${REMOTE_TMP_ENV}" /etc/battlestats-server.env
cp "${REMOTE_TMP_SECRETS}" /etc/battlestats-server.secrets.env
cp "${REMOTE_TMP_CERT}" /etc/ssl/certs/battlestats-do-ca-certificate.crt
chgrp "${APP_USER}" /etc/battlestats-server.secrets.env
chmod 640 /etc/battlestats-server.secrets.env
chmod 644 /etc/ssl/certs/battlestats-do-ca-certificate.crt
rm -f "${REMOTE_TMP_ENV}" "${REMOTE_TMP_SECRETS}" "${REMOTE_TMP_CERT}"

cat > /etc/sysctl.d/99-battlestats-memory.conf <<'EOF'
vm.swappiness=10
EOF
sysctl --system >/dev/null 2>&1 || true

if [[ -s /etc/battlestats-server.env ]] && [[ -n "$(tail -c1 /etc/battlestats-server.env 2>/dev/null || true)" ]]; then
  printf '\n' >> /etc/battlestats-server.env
fi

sed -i 's|^DB_SSLROOTCERT=.*|DB_SSLROOTCERT=/etc/ssl/certs/battlestats-do-ca-certificate.crt|' /etc/battlestats-server.env

if grep -q '^DJANGO_ALLOWED_HOSTS=' /etc/battlestats-server.env; then
  sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}|" /etc/battlestats-server.env
else
  echo "DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}" >> /etc/battlestats-server.env
fi

if grep -q '^DJANGO_DEBUG=' /etc/battlestats-server.env; then
  sed -i 's|^DJANGO_DEBUG=.*|DJANGO_DEBUG=False|' /etc/battlestats-server.env
else
  echo 'DJANGO_DEBUG=False' >> /etc/battlestats-server.env
fi

if grep -q '^DJANGO_LOGLEVEL=' /etc/battlestats-server.env; then
  sed -i 's|^DJANGO_LOGLEVEL=.*|DJANGO_LOGLEVEL=INFO|' /etc/battlestats-server.env
else
  echo 'DJANGO_LOGLEVEL=INFO' >> /etc/battlestats-server.env
fi

if grep -q '^REDIS_URL=' /etc/battlestats-server.env; then
  sed -i 's|^REDIS_URL=.*|REDIS_URL="redis://127.0.0.1:6379/0"|' /etc/battlestats-server.env
else
  echo 'REDIS_URL="redis://127.0.0.1:6379/0"' >> /etc/battlestats-server.env
fi

# Ranked-capture seam (BATTLE_HISTORY_RANKED_*) — must be re-asserted on
# every deploy because .env.cloud is gitignored (droplet-specific) and
# any manual edit to /etc/battlestats-server.env gets overwritten by the
# `cp ${REMOTE_TMP_ENV} ...` step at the top of this remote block.
# Without these set, the third WG call to seasons/shipstats/ in
# update_battle_data is silently skipped and the ranked diff lane stays
# dark. Caught 2026-05-07: five consecutive deploys (v1.12.21 through
# v1.12.25) wiped the live-droplet manual edits and lil_boots' tonight
# games never captured. Runbook:
# agents/runbooks/runbook-ranked-battle-history-rollout-2026-05-02.md.
if grep -q '^BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=.*|BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1|' /etc/battlestats-server.env
else
  echo 'BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1' >> /etc/battlestats-server.env
fi
if grep -q '^BATTLE_HISTORY_RANKED_CAPTURE_REALMS=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_HISTORY_RANKED_CAPTURE_REALMS=.*|BATTLE_HISTORY_RANKED_CAPTURE_REALMS=na,eu,asia|' /etc/battlestats-server.env
else
  echo 'BATTLE_HISTORY_RANKED_CAPTURE_REALMS=na,eu,asia' >> /etc/battlestats-server.env
fi

# Pin the rolling-6h BattleObservation floor staleness threshold to 8h
# (tightened from 22h on 2026-05-09 alongside the cadence promotion).
# Code defaults to 8h too; setting it explicitly here makes the live
# value visible and overrideable without a redeploy.
if grep -q '^BATTLE_OBSERVATION_FLOOR_HOURS=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_OBSERVATION_FLOOR_HOURS=.*|BATTLE_OBSERVATION_FLOOR_HOURS=8|' /etc/battlestats-server.env
else
  echo 'BATTLE_OBSERVATION_FLOOR_HOURS=8' >> /etc/battlestats-server.env
fi

# Bulk-batched observation floor + change-detector gate (R1, enabled on all
# realms 2026-06-07). Re-asserted here because the cp of .env.cloud above wipes
# manual /etc edits — same trap as ranked-capture above. The bulk path captures
# random observations via bulk account/info + per-player ships fallback (WG
# ships/stats can't bulk); the gate fetches ships only for players whose battle
# count moved. Runbook: runbook-bulk-battle-observation-capture-2026-06-06.md.
if grep -q '^BATTLE_OBSERVATION_FLOOR_BULK_ENABLED=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_OBSERVATION_FLOOR_BULK_ENABLED=.*|BATTLE_OBSERVATION_FLOOR_BULK_ENABLED=1|' /etc/battlestats-server.env
else
  echo 'BATTLE_OBSERVATION_FLOOR_BULK_ENABLED=1' >> /etc/battlestats-server.env
fi
if grep -q '^BATTLE_OBSERVATION_FLOOR_BULK_REALMS=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_OBSERVATION_FLOOR_BULK_REALMS=.*|BATTLE_OBSERVATION_FLOOR_BULK_REALMS=na,eu,asia|' /etc/battlestats-server.env
else
  echo 'BATTLE_OBSERVATION_FLOOR_BULK_REALMS=na,eu,asia' >> /etc/battlestats-server.env
fi
if grep -q '^BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED=.*|BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED=1|' /etc/battlestats-server.env
else
  echo 'BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED=1' >> /etc/battlestats-server.env
fi
# Ranked-sweep gate (enabled + validated 2026-06-07: gated_out 105/276 with all
# movers still captured, 369 events, 0 failures). Gates the dominant ranked
# sweep via account/info last_battle_time.
if grep -q '^BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED=.*|BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED=1|' /etc/battlestats-server.env
else
  echo 'BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED=1' >> /etc/battlestats-server.env
fi

# Random-first routing (enabled na 2026-06-07: dry-run + watch validated
# routing=current-season(1029,1028), bulk_random 1947 vs ranked 1053 — moves
# ~half the candidates off the slow ranked path). Per-realm during rollout via
# _REALMS; expand as each realm is validated. Needs ranked_last_season_id
# populated (backfill_ranked_last_season) — done for active-30d ranked-known.
if grep -q '^BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED=.*|BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED=1|' /etc/battlestats-server.env
else
  echo 'BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED=1' >> /etc/battlestats-server.env
fi
if grep -q '^BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_REALMS=' /etc/battlestats-server.env; then
  sed -i 's|^BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_REALMS=.*|BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_REALMS=na,eu,asia|' /etc/battlestats-server.env
else
  echo 'BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_REALMS=na,eu,asia' >> /etc/battlestats-server.env
fi

# R2: clan crawl core-only (enabled 2026-06-07). Skips the redundant per-player
# efficiency+achievements enrichment (~85% of the crawl's WG cost) that made it
# hold the realm lock for hours and pre-empt the battle-history floor. Discovery
# + Best Clans aggregates still run. ~6x less crawl WG; frees the floor.
if grep -q '^CLAN_CRAWL_CORE_ONLY=' /etc/battlestats-server.env; then
  sed -i 's|^CLAN_CRAWL_CORE_ONLY=.*|CLAN_CRAWL_CORE_ONLY=1|' /etc/battlestats-server.env
else
  echo 'CLAN_CRAWL_CORE_ONLY=1' >> /etc/battlestats-server.env
fi

# R3 (2026-06-08). With R2 freeing the crawl's grip on the box, the floor worker
# sat idle ~5.5/6h. Two levers: bigger normal-cycle cap (LIMIT 3000->7500, then
# ->12000 on 2026-06-10 to push fresh_frac up — WG/DB have headroom) AND higher
# frequency (each realm every 3h not 6h, striped 60min apart) to use the idle
# capacity + drain the now-honest active backlog. Coexist LIMIT pinned at 3000
# to stay gentle on the 1-vCPU DB while a crawl holds the realm lock (so the 12k
# normal-cycle cap only fully applies on non-crawl cycles).
# CYCLE_MINUTES takes effect via post_migrate schedule re-registration.
for kv in \
  'BATTLE_OBSERVATION_FLOOR_LIMIT=12000' \
  'BATTLE_OBSERVATION_FLOOR_CRAWL_LIMIT=3000' \
  'BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES=180'; do
  k="${kv%%=*}"
  if grep -q "^${k}=" /etc/battlestats-server.env; then
    sed -i "s|^${k}=.*|${kv}|" /etc/battlestats-server.env
  else
    echo "${kv}" >> /etc/battlestats-server.env
  fi
done

# Feed-timing optimization (2026-06-08). Move the ASIA clan crawl out of its
# measured 12:00-15:00 activity peak into its overnight quiet window so it stops
# throttling the floor while fresh ASIA battles land (NA 09:00 / EU 03:00 already
# sit in their troughs). Enable the nightly per-realm activity-curve aggregate
# that persists the last-battle-hour histogram driving peak-aware scheduling.
# Runbook: agents/runbooks/analysis-feed-schedule-optimization-2026-06-08.md
for kv in \
  'CLAN_CRAWL_SCHEDULE_HOUR_ASIA=22' \
  'ACTIVITY_CURVE_ENABLED=1'; do
  k="${kv%%=*}"
  if grep -q "^${k}=" /etc/battlestats-server.env; then
    sed -i "s|^${k}=.*|${kv}|" /etc/battlestats-server.env
  else
    echo "${kv}" >> /etc/battlestats-server.env
  fi
done

# Enable the weekly per-realm T10 top-ship-player badge snapshot
# (snapshot_ship_top_players_task self-gates on this flag; the schedule is
# always registered but no-ops without it). Pinned here because the cp of
# .env.cloud above wipes any manual /etc edit — same trap that left the
# ranked-capture flag dark for five deploys. Drives /ship/<id> boards +
# profile badges for all realms (now a nightly rolling recompute). Runbook:
# agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md.
if grep -q '^SHIP_BADGE_SNAPSHOT_ENABLED=' /etc/battlestats-server.env; then
  sed -i 's|^SHIP_BADGE_SNAPSHOT_ENABLED=.*|SHIP_BADGE_SNAPSHOT_ENABLED=1|' /etc/battlestats-server.env
else
  echo 'SHIP_BADGE_SNAPSHOT_ENABLED=1' >> /etc/battlestats-server.env
fi

# Hot-players engagement-queue per-realm cap (2026-06-15). Raise the ceiling
# 500->800 so a full nightly hot-player capture/refresh pass still fits inside a
# ~1h worst-case budget (~4-5s/player when every member needs a fresh WG call,
# measured from the freshness-sweep refresh path) with headroom as the qualified
# set grows. Inert at today's set size (~133/1500 across realms) — a future-proof
# ceiling, not a throughput change; the real lever for more coverage is the
# HOT_PROMOTE_* gates. Pinned here because the cp of .env.cloud above wipes manual
# /etc edits. Runbook: agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md.
if grep -q '^HOT_PLAYERS_MAX=' /etc/battlestats-server.env; then
  sed -i 's|^HOT_PLAYERS_MAX=.*|HOT_PLAYERS_MAX=800|' /etc/battlestats-server.env
else
  echo 'HOT_PLAYERS_MAX=800' >> /etc/battlestats-server.env
fi

# Hot-players engagement queue DISABLED 2026-06-16: live prod set was ~98.5%
# active-7d (2363/2400), i.e. already guaranteed daily capture by the observation
# floor — only ~7 engagement+inactive players were a genuine queue-only win. The
# daily maintain/capture sweeps were near-pure overlap with the floor on the
# shared -c3 background pool, so the queue is off (default is ON). Reversible:
# set HOT_PLAYERS_ENABLED=1 (and revisit only if the seed flips to floor-missed).
# Pinned here for the same .env.cloud-overwrite reason as the flags above.
if grep -q '^HOT_PLAYERS_ENABLED=' /etc/battlestats-server.env; then
  sed -i 's|^HOT_PLAYERS_ENABLED=.*|HOT_PLAYERS_ENABLED=0|' /etc/battlestats-server.env
else
  echo 'HOT_PLAYERS_ENABLED=0' >> /etc/battlestats-server.env
fi

# Ship standings span Tiers 8–10 (per-tier density study, 2026-06-05). Pinned
# here for the same .env.cloud-overwrite reason as the flag above.
if grep -q '^SHIP_BADGE_TIERS=' /etc/battlestats-server.env; then
  sed -i 's|^SHIP_BADGE_TIERS=.*|SHIP_BADGE_TIERS=8,9,10|' /etc/battlestats-server.env
else
  echo 'SHIP_BADGE_TIERS=8,9,10' >> /etc/battlestats-server.env
fi

get_env_value() {
  local key="$1"
  local raw
  raw="$(grep -E "^${key}=" /etc/battlestats-server.env | tail -n1 | cut -d= -f2-)"
  # Strip surrounding double quotes if present
  raw="${raw#\"}"
  raw="${raw%\"}"
  echo "${raw}"
}

if grep -q '^CELERY_RESULT_BACKEND=' /etc/battlestats-server.env; then
  sed -i 's|^CELERY_RESULT_BACKEND=.*|CELERY_RESULT_BACKEND="rpc://"|' /etc/battlestats-server.env
else
  echo 'CELERY_RESULT_BACKEND="rpc://"' >> /etc/battlestats-server.env
fi

# ENABLE_CRAWLER_SCHEDULES removed — crawlers migrated to DO Functions

set_env_value() {
  local key="$1"
  local value="$2"
  # Wrap in double quotes so systemd EnvironmentFile parses values with
  # special characters (e.g. // in AMQP URLs) correctly.
  local quoted_value="\"${value}\""
  if grep -q "^${key}=" /etc/battlestats-server.env; then
    sed -i "s|^${key}=.*|${key}=${quoted_value}|" /etc/battlestats-server.env
  else
    echo "${key}=${quoted_value}" >> /etc/battlestats-server.env
  fi
}

migrate_env_value() {
  local key="$1"
  local legacy_key="$2"
  local default_value="$3"
  local value

  value="$(get_env_value "${key}" || true)"
  if [[ -z "${value}" && -n "${legacy_key}" ]]; then
    value="$(get_env_value "${legacy_key}" || true)"
  fi
  if [[ -z "${value}" ]]; then
    value="${default_value}"
  fi

  set_env_value "${key}" "${value}"
  sed -i "/^${legacy_key}=.*/d" /etc/battlestats-server.env 2>/dev/null || true
}

extract_existing_broker_password() {
  local broker_url="${1:-}"
  python3 - "${broker_url}" <<'PY'
import sys
from urllib.parse import unquote, urlparse

url = urlparse(sys.argv[1])
username = unquote(url.username or "")
password = unquote(url.password or "")
host = (url.hostname or "").lower()

if url.scheme not in {"amqp", "pyamqp"}:
    raise SystemExit(1)
if username != "battlestats" or not password:
    raise SystemExit(1)
if password in {"guest", "changeme"}:
    raise SystemExit(1)
if host not in {"127.0.0.1", "localhost"}:
    raise SystemExit(1)

print(password)
PY
}

configure_local_rabbitmq() {
  local broker_password=""
  local current_broker_url=""

  install -d /etc/rabbitmq
  cat > /etc/rabbitmq/rabbitmq.conf <<'EOF'
listeners.tcp.default = 127.0.0.1:5672
EOF

  # Disable consumer_timeout so RabbitMQ does not kill channels when a
  # long-running task (enrichment batch, clan crawl) holds an unacked
  # message for more than 30 min. With CELERY_TASK_ACKS_LATE=True the
  # default 1800000ms timeout causes PRECONDITION_FAILED → zombie worker.
  # The new-style .conf format does not support `false`/`undefined` for
  # this key in RabbitMQ 3.12, so we use advanced.config (Erlang term).
  cat > /etc/rabbitmq/advanced.config <<'EOF'
[{rabbit, [{consumer_timeout, undefined}]}].
EOF

  systemctl enable rabbitmq-server >/dev/null 2>&1 || true
  systemctl restart rabbitmq-server
  rabbitmqctl await_startup

  current_broker_url="$(get_env_value CELERY_BROKER_URL || true)"
  if [[ -n "${current_broker_url}" ]]; then
    broker_password="$(extract_existing_broker_password "${current_broker_url}" 2>/dev/null || true)"
  fi

  if [[ -z "${broker_password}" ]]; then
    broker_password="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
)"
  fi

  set_env_value CELERY_BROKER_URL "amqp://battlestats:${broker_password}@127.0.0.1:5672//"

  if rabbitmqctl list_users | awk 'NR>1 {print $1}' | grep -qx 'battlestats'; then
    rabbitmqctl change_password battlestats "${broker_password}"
  else
    rabbitmqctl add_user battlestats "${broker_password}"
  fi
  rabbitmqctl set_user_tags battlestats administrator
  rabbitmqctl set_permissions -p / battlestats '.*' '.*' '.*'

  if rabbitmqctl list_users | awk 'NR>1 {print $1}' | grep -qx 'guest'; then
    rabbitmqctl delete_user guest || true
  fi
}

verify_broker_connection() {
  cd "${APP_ROOT}/current/server"
  set -a
  source /etc/battlestats-server.env
  source /etc/battlestats-server.secrets.env
  set +a
  "${APP_ROOT}/venv/bin/python" - <<'PY'
import os
from kombu import Connection

with Connection(os.environ["CELERY_BROKER_URL"], connect_timeout=10) as connection:
    connection.connect()

print("RabbitMQ broker authentication OK")
PY

  # Canary: every celery worker's MainPID must have CELERY_BROKER_URL in its
  # process env, matching the finalized env file. If the EnvironmentFile was
  # mutated after systemd started the unit, /proc/<pid>/environ will lack it
  # and the worker will silently fall back to the settings.py default. Fail
  # the deploy hard — see the 2026-04-08 incident in
  # agents/runbooks/runbook-enrichment-crawler-2026-04-03.md.
  for svc in battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-celery-crawls; do
    local pid
    pid="$(systemctl show -p MainPID --value "${svc}")"
    if [[ -z "${pid}" || "${pid}" == "0" ]]; then
      echo "FATAL: ${svc} has no MainPID after restart" >&2
      exit 1
    fi
    if ! tr '\0' '\n' < "/proc/${pid}/environ" | grep -q '^CELERY_BROKER_URL=amqp://battlestats:'; then
      echo "FATAL: ${svc} MainPID ${pid} missing or wrong CELERY_BROKER_URL in process env" >&2
      echo "       env file shows: $(grep '^CELERY_BROKER_URL=' /etc/battlestats-server.env || echo '(missing)')" >&2
      exit 1
    fi
  done
  echo "Celery worker process env verified (CELERY_BROKER_URL present in all 3 workers)"
}

activate_release() {
  local release_name=""
  local tmp_link=""
  local active_release=""

  release_name="$(basename "${REMOTE_RELEASE}")"
  tmp_link="${APP_ROOT}/.current-${release_name}"

  ln -sfn "${REMOTE_RELEASE}" "${tmp_link}"
  mv -Tf "${tmp_link}" "${APP_ROOT}/current"

  active_release="$(readlink -f "${APP_ROOT}/current")"
  if [[ "${active_release}" != "${REMOTE_RELEASE}" ]]; then
    echo "Failed to activate release: expected ${REMOTE_RELEASE}, got ${active_release}" >&2
    exit 1
  fi
}

materialize_best_player_snapshots() {
  local args=()
  local realm=""
  local sort=""

  # Defaults to disabled — see deploy script header. The
  # `landing-best-player-snapshot-materializer-{realm}` Celery cron refreshes
  # the persisted snapshots daily at 01:15 UTC + per-realm offset, and the
  # snapshots survive deploys, so re-running the materialize on every deploy
  # is redundant. It also competes with the celery workers / cache warmers /
  # gunicorn workers that have just been (re)started moments earlier on a
  # 4 GB droplet, and OOM-killed the deploy on 2026-04-11. Operators that
  # need a forced rebuild (e.g. after adding a new sort) can set
  # AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS=1 on the deploy invocation
  # or run `manage.py materialize_landing_player_best_snapshots` directly.
  if [[ "${AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS}" != "1" ]]; then
    echo "Skipping landing Best-player snapshot materialization (disabled by default; daily celery cron handles refresh)"
    return 0
  fi

  if [[ -n "${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS}" ]]; then
    while IFS= read -r realm; do
      [[ -n "${realm}" ]] || continue
      args+=("--realm" "${realm}")
    done < <(printf '%s' "${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS}" | tr ',' '\n' | awk 'NF')
  fi

  if [[ -n "${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS}" ]]; then
    while IFS= read -r sort; do
      [[ -n "${sort}" ]] || continue
      args+=("--sort" "${sort}")
    done < <(printf '%s' "${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS}" | tr ',' '\n' | awk 'NF')
  fi

  cd "${APP_ROOT}/current/server"
  set -a
  source /etc/battlestats-server.env
  source /etc/battlestats-server.secrets.env
  set +a
  "${APP_ROOT}/venv/bin/python" manage.py materialize_landing_player_best_snapshots "${args[@]}"
}

# Startup cache warming forced ON (2026-05-28): pre-warms score_best_clans +
# landing sequentially on the background worker after deploy, replacing the
# request-driven CONCURRENT cold-cache recompute that spiked the 1-vCPU DB to
# load ~8 immediately post-deploy. set_env_value (not migrate_*) so every
# deploy enforces it regardless of the prior on-host value (which was 0).
# Droplet headroom is ample (7.8 GB + 2 GB swap; the 2026-03-30 OOM was at
# 3.8 GB) and the warm is Celery-dispatched, not a subprocess (v1.2.14).
# See runbook-db-cpu-saturation-2026-05-24.md.
set_env_value WARM_CACHES_ON_STARTUP 1
sed -i '/^WARM_LANDING_PAGE_ON_STARTUP=.*/d' /etc/battlestats-server.env 2>/dev/null || true
migrate_env_value CACHE_WARMUP_START_DELAY_SECONDS LANDING_WARMUP_START_DELAY_SECONDS 5

set_env_value CELERY_DEFAULT_CONCURRENCY 3
set_env_value CELERY_HYDRATION_CONCURRENCY 3
set_env_value CELERY_BACKGROUND_CONCURRENCY 3
set_env_value MAX_CONCURRENT_REALM_CRAWLS 1
set_env_value CLAN_CRAWL_RATE_LIMIT_DELAY 0.25
set_env_value CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY 0.10
set_env_value CELERY_DEFAULT_MAX_TASKS_PER_CHILD 200
set_env_value CELERY_HYDRATION_MAX_TASKS_PER_CHILD 200
set_env_value CELERY_BACKGROUND_MAX_TASKS_PER_CHILD 50
set_env_value CELERY_DEFAULT_MAX_MEMORY_PER_CHILD_KB 393216
set_env_value CELERY_HYDRATION_MAX_MEMORY_PER_CHILD_KB 393216
set_env_value CELERY_BACKGROUND_MAX_MEMORY_PER_CHILD_KB 786432
set_env_value BEST_CLAN_EXCLUDED_IDS 1000068602
set_env_value PLAYER_REFRESH_STATE_FILE "${APP_ROOT}/shared/logs/incremental_player_refresh_state.json"
set_env_value RANKED_INCREMENTAL_STATE_FILE "${APP_ROOT}/shared/logs/incremental_ranked_data_state.json"
set_env_value ENRICH_BATCH_SIZE 500
set_env_value ENRICH_MIN_PVP_BATTLES 500
# WR floor relaxed to 0 + activity window tightened to 7d: enrich only the
# genuinely-active base (re-fetched within days by the floor/refresh tiers).
# The low penalty for the tight window is covered by ENRICH_ON_VIEW_ENABLED,
# which fast-paths a returning player the moment their profile is viewed
# instead of waiting for the daily drift reclassify. ENRICH_MAX_INACTIVE_DAYS
# is read in lockstep by the live crawler gate + reclassify/retry (ed27912).
set_env_value ENRICH_MIN_WR 0.0
set_env_value ENRICH_MAX_INACTIVE_DAYS 7
set_env_value ENRICH_ON_VIEW_ENABLED 1
set_env_value ENRICH_DELAY 0.2
# Enrichment now COEXISTS with clan crawls (was: deferred entirely, which
# starved backlog drain through multi-day crawls — `pending` piled up while
# `enriched` stayed flat). Pinned to the code defaults for visibility/override:
# DEFER=0 keeps coexist on; DELAY_DURING_CRAWL paces enrichment gentler while a
# crawl shares the WG budget (no shared limiter yet — keep combined rate <~10/s).
# Kill switch: set ENRICH_DEFER_DURING_CRAWL=1 to restore the old defer behavior.
set_env_value ENRICH_DEFER_DURING_CRAWL 0
set_env_value ENRICH_DELAY_DURING_CRAWL 0.5
set_env_value ENRICH_PAUSE_BETWEEN_BATCHES 10
set_env_value PLAYER_REFRESH_INTERVAL_MINUTES 180
set_env_value RANKED_REFRESH_INTERVAL_MINUTES 120
set_env_value CELERY_BROKER_HEARTBEAT 0
set_env_value ENABLE_CRAWLER_SCHEDULES 1
# Global WG token-bucket rate limiter (api/rate_limiter.py), shared across all
# worker processes + gunicorn request threads via Redis. Enforces the single
# WG_APP_ID's ~10 req/s budget at the WG egress, replacing the crude per-caller
# delays as the real ceiling. Kept conservative (<10/s) since per-component
# delays still exist; tighten/loosen here. PER_SEC=0 or ENABLED=0 disables.
set_env_value WG_RATE_LIMIT_ENABLED 1
set_env_value WG_RATE_LIMIT_PER_SEC 9
set_env_value WG_RATE_LIMIT_BURST 18
set_env_value WG_RATE_LIMIT_MAX_WAIT 8
set_env_value WG_RATE_LIMIT_REQUEST_MAX_WAIT 0.5

ln -sfn /etc/battlestats-server.env "${REMOTE_RELEASE}/server/.env"
ln -sfn /etc/battlestats-server.secrets.env "${REMOTE_RELEASE}/server/.env.secrets"

"${APP_ROOT}/venv/bin/python" -m pip install --upgrade pip
"${APP_ROOT}/venv/bin/pip" install --no-cache-dir -r "${REMOTE_RELEASE}/server/requirements.txt"

cd "${REMOTE_RELEASE}/server"
# Ensure CELERY_BROKER_URL is populated in /etc/battlestats-server.env before
# any Django management command runs — the hardened settings.py now raises
# ImproperlyConfigured if the var is unset in production, and manage.py imports
# settings at startup even for commands (migrate, check) that do not use Celery.
configure_local_rabbitmq
"${APP_ROOT}/venv/bin/python" manage.py migrate --noinput
"${APP_ROOT}/venv/bin/python" manage.py collectstatic --noinput
"${APP_ROOT}/venv/bin/python" manage.py check

chown -R "${APP_USER}:${APP_USER}" "${REMOTE_RELEASE}"
touch "${APP_ROOT}/shared/logs/django.log"
chown "${APP_USER}:${APP_USER}" "${APP_ROOT}/shared/logs/django.log"
activate_release

cat > /etc/systemd/system/battlestats-celery.service <<EOF
[Unit]
Description=Battlestats Celery worker (default queue — user-facing tasks)
After=network.target redis-server.service rabbitmq-server.service battlestats-gunicorn.service
Wants=redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=/bin/bash -lc 'exec "${APP_ROOT}/venv/bin/celery" -A battlestats worker -l INFO -Q default -c "${CELERY_DEFAULT_CONCURRENCY:-3}" --time-limit=600 --prefetch-multiplier=1 --max-tasks-per-child="${CELERY_DEFAULT_MAX_TASKS_PER_CHILD:-200}" --max-memory-per-child="${CELERY_DEFAULT_MAX_MEMORY_PER_CHILD_KB:-393216}" --without-gossip --without-mingle -n default@%%h'
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/battlestats-celery-hydration.service <<EOF
[Unit]
Description=Battlestats Celery worker (hydration queue — ranked + efficiency refresh)
After=network.target redis-server.service rabbitmq-server.service battlestats-gunicorn.service
Wants=redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=/bin/bash -lc 'exec "${APP_ROOT}/venv/bin/celery" -A battlestats worker -l INFO -Q hydration -c "${CELERY_HYDRATION_CONCURRENCY:-3}" --time-limit=600 --prefetch-multiplier=1 --max-tasks-per-child="${CELERY_HYDRATION_MAX_TASKS_PER_CHILD:-200}" --max-memory-per-child="${CELERY_HYDRATION_MAX_MEMORY_PER_CHILD_KB:-393216}" --without-gossip --without-mingle -n hydration@%%h'
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/battlestats-celery-background.service <<EOF
[Unit]
Description=Battlestats Celery worker (background queue — warmers, snapshots, incrementals, enrichment)
After=network.target redis-server.service rabbitmq-server.service battlestats-gunicorn.service
Wants=redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=/bin/bash -lc 'exec "${APP_ROOT}/venv/bin/celery" -A battlestats worker -l INFO -Q background -c "${CELERY_BACKGROUND_CONCURRENCY:-3}" --time-limit=21600 --prefetch-multiplier=1 --max-tasks-per-child="${CELERY_BACKGROUND_MAX_TASKS_PER_CHILD:-50}" --max-memory-per-child="${CELERY_BACKGROUND_MAX_MEMORY_PER_CHILD_KB:-786432}" --without-gossip --without-mingle -n background@%%h'
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

# Dedicated worker for the multi-day clan crawl. Lives on its own queue so it
# never camps a `background` slot that incrementals / warmers / enrichment need.
# -c 1: only one crawl runs at a time (one realm per cron tick anyway).
# --time-limit=1209600: 14 days, the upper bound on a single full-realm crawl.
# --max-tasks-per-child=1: recycle the worker process between crawls so the
#   long-running fork doesn't accumulate memory drift.
# See agents/runbooks/runbook-clan-crawl-blocker-2026-04-30.md for the incident
# chain that motivated this carve-out.
cat > /etc/systemd/system/battlestats-celery-crawls.service <<EOF
[Unit]
Description=Battlestats Celery worker (crawls queue — multi-day clan crawl)
After=network.target redis-server.service rabbitmq-server.service battlestats-gunicorn.service
Wants=redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=/bin/bash -lc 'exec "${APP_ROOT}/venv/bin/celery" -A battlestats worker -l INFO -Q crawls -c 1 --time-limit=1209600 --prefetch-multiplier=1 --max-tasks-per-child=1 --without-gossip --without-mingle -n crawls@%%h'
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

# Celery consumer watchdog: detects zombie workers (process alive, AMQP
# consumer dead) and restarts them. Runs every 5 minutes via systemd timer.
cat > /usr/local/bin/battlestats-celery-watchdog.sh <<'WATCHDOG'
#!/usr/bin/env bash
set -euo pipefail
check_worker() {
  local queue_name="$1" service_name="$2"
  systemctl is-active --quiet "${service_name}" || return 0
  local consumers
  consumers=$(rabbitmqctl -q list_queues name consumers 2>/dev/null \
    | awk -v q="${queue_name}" '$1 == q { print $2 }')
  if [[ "${consumers}" == "0" ]]; then
    logger -t battlestats-watchdog "ALERT: ${service_name} has 0 consumers on queue '${queue_name}' — restarting"
    systemctl restart "${service_name}"
  fi
}
check_worker default   battlestats-celery
check_worker hydration battlestats-celery-hydration
check_worker background battlestats-celery-background
check_worker crawls    battlestats-celery-crawls
WATCHDOG
chmod +x /usr/local/bin/battlestats-celery-watchdog.sh

cat > /etc/systemd/system/battlestats-celery-watchdog.service <<'EOF'
[Unit]
Description=Battlestats Celery consumer watchdog
After=rabbitmq-server.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/battlestats-celery-watchdog.sh
EOF

cat > /etc/systemd/system/battlestats-celery-watchdog.timer <<'EOF'
[Unit]
Description=Run Battlestats Celery consumer watchdog every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
# Enable the new crawls unit on first install so it auto-starts on reboot.
# Idempotent — `systemctl enable` is a no-op when the unit is already enabled.
systemctl enable battlestats-celery-crawls 2>/dev/null || true
systemctl enable --now battlestats-celery-watchdog.timer 2>/dev/null || true
# configure_local_rabbitmq already ran before `manage.py migrate` above — the
# env file and broker credentials are finalized at this point.
redis-cli --scan --pattern 'warships:tasks:crawl_all_clans:*' | xargs -r redis-cli DEL >/dev/null 2>&1 || true
redis-cli DEL warships:tasks:crawl_all_clans:lock warships:tasks:crawl_all_clans:heartbeat 2>/dev/null || true
systemctl restart redis-server rabbitmq-server battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-celery-crawls battlestats-beat
verify_broker_connection
materialize_best_player_snapshots
active_release_after_restart="$(readlink -f "${APP_ROOT}/current")"
if [[ "${active_release_after_restart}" != "${REMOTE_RELEASE}" ]]; then
  echo "Release activation drifted after restart: expected ${REMOTE_RELEASE}, got ${active_release_after_restart}" >&2
  exit 1
fi
systemctl --no-pager --full status battlestats-gunicorn | sed -n '1,25p'

# Release cleanup runs unconditionally — must not be gated by prior failures
find "${APP_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d | sort | head -n -"${KEEP_RELEASES}" | xargs -r rm -rf || true
REMOTE

# Execute the script on the remote without stdin redirection. Env vars are
# passed inline (ssh concatenates trailing args into the remote command, so
# `VAR=val bash ...` runs in the remote login shell).
ssh "${DEPLOY_USER}@${HOST}" \
  APP_ROOT="${APP_ROOT}" \
  APP_USER="${APP_USER}" \
  AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS="${AUTO_MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOTS}" \
  MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS="${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_REALMS}" \
  MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS="${MATERIALIZE_LANDING_PLAYER_BEST_SNAPSHOT_SORTS}" \
  REMOTE_RELEASE="${REMOTE_RELEASE}" \
  REMOTE_TMP_ENV="${REMOTE_TMP_ENV}" \
  REMOTE_TMP_SECRETS="${REMOTE_TMP_SECRETS}" \
  REMOTE_TMP_CERT="${REMOTE_TMP_CERT}" \
  DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS}" \
  KEEP_RELEASES="${KEEP_RELEASES}" \
  "bash ${REMOTE_DEPLOY_SCRIPT}; rc=\$?; rm -f ${REMOTE_DEPLOY_SCRIPT}; exit \$rc"

VERIFY_ARGS=("${HOST}" verify --skip-client --expect-backend-release "${REMOTE_RELEASE}")
if [[ -n "${POST_DEPLOY_VERIFY_REALMS}" ]]; then
  while IFS= read -r realm; do
    [[ -n "${realm}" ]] || continue
    VERIFY_ARGS+=(--realm "${realm}")
  done < <(printf '%s' "${POST_DEPLOY_VERIFY_REALMS}" | tr ',' '\n' | awk 'NF')
fi
"${REPO_ROOT}/scripts/post_deploy_operations.sh" "${VERIFY_ARGS[@]}"

echo "Backend deployed to ${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}"
