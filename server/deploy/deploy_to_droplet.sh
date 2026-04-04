#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SERVER_DIR}/.." && pwd)"

# Hard gate: CI must be passing before deploy
"${REPO_ROOT}/scripts/check_ci_status.sh"

HOST="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
APP_ROOT="${APP_ROOT:-/opt/battlestats-server}"
APP_USER="${APP_USER:-battlestats}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
DEPLOY_AGENTIC_RUNTIME="${DEPLOY_AGENTIC_RUNTIME:-0}"
RELEASE_ID="$(date +%Y%m%d%H%M%S)"
REMOTE_RELEASE="${APP_ROOT}/releases/${RELEASE_ID}"
REMOTE_TMP_ENV="/tmp/battlestats-server.env.${RELEASE_ID}"
REMOTE_TMP_SECRETS="/tmp/battlestats-server.secrets.env.${RELEASE_ID}"
REMOTE_TMP_CERT="/tmp/battlestats-do-ca.${RELEASE_ID}.crt"
EXTRA_ALLOWED_HOSTS="${EXTRA_ALLOWED_HOSTS:-}"
DEFAULT_PUBLIC_ALLOWED_HOSTS="${DEFAULT_PUBLIC_ALLOWED_HOSTS:-battlestats.online,www.battlestats.online}"

case "${DEPLOY_AGENTIC_RUNTIME,,}" in
  1|true|yes|on)
    DEPLOY_AGENTIC_RUNTIME=1
    ;;
  *)
    DEPLOY_AGENTIC_RUNTIME=0
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
  DEPLOY_AGENTIC_RUNTIME="${DEPLOY_AGENTIC_RUNTIME}" \
  REMOTE_RELEASE="${REMOTE_RELEASE}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/releases"
install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}"
install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}/server"
if [[ "${DEPLOY_AGENTIC_RUNTIME}" == "1" ]]; then
  install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}/agents"
fi
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/shared/logs"
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

if [[ "${DEPLOY_AGENTIC_RUNTIME}" == "1" ]]; then
  rsync -az --delete \
    --exclude '.git' \
    --exclude '.DS_Store' \
    "${REPO_ROOT}/agents/" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/agents/"
fi

scp "${REPO_ROOT}/docker-compose.yml" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/docker-compose.yml"

ssh "${DEPLOY_USER}@${HOST}" \
  APP_ROOT="${APP_ROOT}" \
  APP_USER="${APP_USER}" \
  DEPLOY_AGENTIC_RUNTIME="${DEPLOY_AGENTIC_RUNTIME}" \
  REMOTE_RELEASE="${REMOTE_RELEASE}" \
  REMOTE_TMP_ENV="${REMOTE_TMP_ENV}" \
  REMOTE_TMP_SECRETS="${REMOTE_TMP_SECRETS}" \
  REMOTE_TMP_CERT="${REMOTE_TMP_CERT}" \
  DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS}" \
  KEEP_RELEASES="${KEEP_RELEASES}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

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
  sed -i 's|^REDIS_URL=.*|REDIS_URL=redis://127.0.0.1:6379/0|' /etc/battlestats-server.env
else
  echo 'REDIS_URL=redis://127.0.0.1:6379/0' >> /etc/battlestats-server.env
fi

get_env_value() {
  local key="$1"
  grep -E "^${key}=" /etc/battlestats-server.env | tail -n1 | cut -d= -f2-
}

if grep -q '^CELERY_RESULT_BACKEND=' /etc/battlestats-server.env; then
  sed -i 's|^CELERY_RESULT_BACKEND=.*|CELERY_RESULT_BACKEND=rpc://|' /etc/battlestats-server.env
else
  echo 'CELERY_RESULT_BACKEND=rpc://' >> /etc/battlestats-server.env
fi

# ENABLE_CRAWLER_SCHEDULES removed — crawlers migrated to DO Functions

set_env_value() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" /etc/battlestats-server.env; then
    sed -i "s|^${key}=.*|${key}=${value}|" /etc/battlestats-server.env
  else
    echo "${key}=${value}" >> /etc/battlestats-server.env
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
}

migrate_env_value WARM_CACHES_ON_STARTUP WARM_LANDING_PAGE_ON_STARTUP 0
migrate_env_value CACHE_WARMUP_START_DELAY_SECONDS LANDING_WARMUP_START_DELAY_SECONDS 5
configure_local_rabbitmq

set_env_value CELERY_DEFAULT_CONCURRENCY 3
set_env_value CELERY_HYDRATION_CONCURRENCY 3
set_env_value CELERY_BACKGROUND_CONCURRENCY 2
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
set_env_value ENRICH_MIN_WR 48.0
set_env_value ENRICH_DELAY 0.2
set_env_value ENRICH_PAUSE_BETWEEN_BATCHES 10
set_env_value ENABLE_AGENTIC_RUNTIME "${DEPLOY_AGENTIC_RUNTIME}"

ln -sfn /etc/battlestats-server.env "${REMOTE_RELEASE}/server/.env"
ln -sfn /etc/battlestats-server.secrets.env "${REMOTE_RELEASE}/server/.env.secrets"

"${APP_ROOT}/venv/bin/python" -m pip install --upgrade pip
"${APP_ROOT}/venv/bin/pip" install --no-cache-dir -r "${REMOTE_RELEASE}/server/requirements.txt"
if [[ "${DEPLOY_AGENTIC_RUNTIME}" == "1" ]]; then
  "${APP_ROOT}/venv/bin/pip" install --no-cache-dir -r "${REMOTE_RELEASE}/server/requirements-agentic.txt"
fi

cd "${REMOTE_RELEASE}/server"
"${APP_ROOT}/venv/bin/python" manage.py migrate
"${APP_ROOT}/venv/bin/python" manage.py collectstatic --noinput
"${APP_ROOT}/venv/bin/python" manage.py check

chown -R "${APP_USER}:${APP_USER}" "${REMOTE_RELEASE}"
ln -sfn "${REMOTE_RELEASE}" "${APP_ROOT}/current"

cat > /etc/systemd/system/battlestats-celery.service <<EOF
[Unit]
Description=Battlestats Celery worker (default queue — user-facing tasks)
After=network.target redis-server.service rabbitmq-server.service battlestats-gunicorn.service
Requires=redis-server.service rabbitmq-server.service

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
Requires=redis-server.service rabbitmq-server.service

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
Description=Battlestats Celery worker (background queue — crawls, warmers, snapshots)
After=network.target redis-server.service rabbitmq-server.service battlestats-gunicorn.service
Requires=redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=/bin/bash -lc 'exec "${APP_ROOT}/venv/bin/celery" -A battlestats worker -l INFO -Q background -c "${CELERY_BACKGROUND_CONCURRENCY:-2}" --time-limit=21600 --prefetch-multiplier=1 --max-tasks-per-child="${CELERY_BACKGROUND_MAX_TASKS_PER_CHILD:-50}" --max-memory-per-child="${CELERY_BACKGROUND_MAX_MEMORY_PER_CHILD_KB:-786432}" --without-gossip --without-mingle -n background@%%h'
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
redis-cli --scan --pattern 'warships:tasks:crawl_all_clans:*' | xargs -r redis-cli DEL >/dev/null 2>&1 || true
redis-cli DEL warships:tasks:crawl_all_clans:lock warships:tasks:crawl_all_clans:heartbeat 2>/dev/null || true
systemctl restart redis-server rabbitmq-server battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat
verify_broker_connection
systemctl --no-pager --full status battlestats-gunicorn | sed -n '1,25p'

find "${APP_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d | sort | head -n -"${KEEP_RELEASES}" | xargs -r rm -rf
REMOTE

echo "Backend deployed to ${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}"
