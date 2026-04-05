#!/usr/bin/env bash

set -euo pipefail

HOST="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
APP_ROOT="${APP_ROOT:-/opt/battlestats-server}"
APP_USER="${APP_USER:-battlestats}"
EXTRA_ALLOWED_HOSTS="${EXTRA_ALLOWED_HOSTS:-}"
DEFAULT_PUBLIC_ALLOWED_HOSTS="${DEFAULT_PUBLIC_ALLOWED_HOSTS:-battlestats.online,www.battlestats.online}"

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
  DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

set_env_value() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" /etc/battlestats-server.env; then
    sed -i "s|^${key}=.*|${key}=${value}|" /etc/battlestats-server.env
  else
    echo "${key}=${value}" >> /etc/battlestats-server.env
  fi
}

get_env_value() {
  local key="$1"
  grep -E "^${key}=" /etc/battlestats-server.env | tail -n1 | cut -d= -f2-
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

apt-get update
apt-get install -y python3 python3-venv python3-pip redis-server rabbitmq-server rsync

# Ensure a swap file exists as an OOM safety net.  Startup cache warmers can
# transiently spike memory ~300 MB above steady-state; a small swap file
# prevents hard SIGKILL during those short-lived spikes.
if [[ ! -f /swapfile ]]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  if ! grep -q '/swapfile' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  echo "Created 2 GB swap file"
elif ! swapon --show | grep -q '/swapfile'; then
  swapon /swapfile 2>/dev/null || true
  if ! grep -q '/swapfile' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
fi

# Prefer RAM over swap for the hot Django/Celery working set. Swap stays
# available as an OOM safety net for transient warm-up spikes.
cat > /etc/sysctl.d/99-battlestats-memory.conf <<'EOF'
vm.swappiness=10
EOF
sysctl --system >/dev/null 2>&1 || true

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_ROOT}" --shell /usr/sbin/nologin "${APP_USER}"
fi

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}"
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/releases"
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/shared"

if [[ ! -x "${APP_ROOT}/venv/bin/python" ]]; then
  python3 -m venv "${APP_ROOT}/venv"
fi

if [[ ! -f /etc/battlestats-server.env ]]; then
  cat > /etc/battlestats-server.env <<'EOF'
DB_ENGINE=postgresql_psycopg2
DB_NAME=defaultdb
DB_USER=doadmin
DB_HOST=db-postgresql-nyc3-11231-do-user-8591796-0.m.db.ondigitalocean.com
DB_PORT=25060
DB_SSLMODE=require
DB_SSLROOTCERT=/etc/ssl/certs/battlestats-do-ca-certificate.crt
DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}
DJANGO_DEBUG=False
DJANGO_LOGLEVEL=INFO
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=rpc://
ENABLE_CRAWLER_SCHEDULES=1
WARM_CACHES_ON_STARTUP=0
CACHE_WARMUP_START_DELAY_SECONDS=5
HOT_ENTITY_PINNED_PLAYER_NAMES=lil_boots
BEST_CLAN_EXCLUDED_IDS=1000068602
MAX_CONCURRENT_REALM_CRAWLS=1
CLAN_CRAWL_RATE_LIMIT_DELAY=0.25
CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY=0.10
CELERY_DEFAULT_CONCURRENCY=3
CELERY_HYDRATION_CONCURRENCY=3
CELERY_BACKGROUND_CONCURRENCY=2
CELERY_DEFAULT_MAX_TASKS_PER_CHILD=200
CELERY_HYDRATION_MAX_TASKS_PER_CHILD=200
CELERY_BACKGROUND_MAX_TASKS_PER_CHILD=50
CELERY_DEFAULT_MAX_MEMORY_PER_CHILD_KB=393216
CELERY_HYDRATION_MAX_MEMORY_PER_CHILD_KB=393216
CELERY_BACKGROUND_MAX_MEMORY_PER_CHILD_KB=786432
EOF
fi

if [[ ! -f /etc/battlestats-server.secrets.env ]]; then
  cat > /etc/battlestats-server.secrets.env <<'EOF'
# WG_APP_ID=
# DB_PASSWORD=
# DJANGO_SECRET_KEY=
EOF
fi

chgrp "${APP_USER}" /etc/battlestats-server.secrets.env
chmod 640 /etc/battlestats-server.secrets.env

migrate_env_value WARM_CACHES_ON_STARTUP WARM_LANDING_PAGE_ON_STARTUP 0
migrate_env_value CACHE_WARMUP_START_DELAY_SECONDS LANDING_WARMUP_START_DELAY_SECONDS 5

cat > /etc/systemd/system/battlestats-gunicorn.service <<EOF
[Unit]
Description=Battlestats Django gunicorn
After=network.target redis-server.service rabbitmq-server.service
Requires=redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=${APP_ROOT}/venv/bin/gunicorn --config gunicorn.conf.py battlestats.wsgi:application --bind 127.0.0.1:8888
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

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

cat > /etc/systemd/system/battlestats-beat.service <<EOF
[Unit]
Description=Battlestats Celery beat
After=network.target redis-server.service rabbitmq-server.service battlestats-celery.service
Requires=redis-server.service rabbitmq-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_ROOT}/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=${APP_ROOT}/venv/bin/celery -A battlestats beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
Restart=always
RestartSec=5
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable redis-server rabbitmq-server battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat
systemctl restart redis-server
configure_local_rabbitmq

if [[ -d "${APP_ROOT}/current/server" ]]; then
  # Clear crawl locks before restarting workers to prevent stale-lock watchdog triggers
  # or a stranded EU resume crawl after an interrupted deploy.
  redis-cli --scan --pattern 'warships:tasks:crawl_all_clans:*' | xargs -r redis-cli DEL >/dev/null 2>&1 || true
  redis-cli DEL warships:tasks:crawl_all_clans:lock warships:tasks:crawl_all_clans:heartbeat 2>/dev/null || true
  systemctl restart battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat
fi
REMOTE

echo "Backend droplet bootstrap complete for ${DEPLOY_USER}@${HOST}"
