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
CELERY_BROKER_URL=amqp://guest:guest@127.0.0.1:5672//
CELERY_RESULT_BACKEND=rpc://
ENABLE_CRAWLER_SCHEDULES=1
WARM_LANDING_PAGE_ON_STARTUP=1
LANDING_WARMUP_START_DELAY_SECONDS=5
HOT_ENTITY_PINNED_PLAYER_NAMES=lil_boots
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
ExecStart=${APP_ROOT}/venv/bin/celery -A battlestats worker -l INFO -Q default -c 2 --time-limit=600 --prefetch-multiplier=1 --max-tasks-per-child=200 --without-gossip --without-mingle -n default@%%h
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
ExecStart=${APP_ROOT}/venv/bin/celery -A battlestats worker -l INFO -Q hydration -c 2 --time-limit=600 --prefetch-multiplier=1 --max-tasks-per-child=200 --without-gossip --without-mingle -n hydration@%%h
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
ExecStart=${APP_ROOT}/venv/bin/celery -A battlestats worker -l INFO -Q background -c 2 --time-limit=21600 --prefetch-multiplier=1 --max-tasks-per-child=50 --without-gossip --without-mingle -n background@%%h
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
systemctl restart redis-server rabbitmq-server

if [[ -d "${APP_ROOT}/current/server" ]]; then
  # Clear crawl locks before restarting workers to prevent stale-lock watchdog triggers
  redis-cli DEL warships:tasks:crawl_all_clans:lock warships:tasks:crawl_all_clans:heartbeat 2>/dev/null || true
  systemctl restart battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat
fi
REMOTE

echo "Backend droplet bootstrap complete for ${DEPLOY_USER}@${HOST}"
