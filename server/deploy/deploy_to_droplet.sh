#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SERVER_DIR}/.." && pwd)"

HOST="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
APP_ROOT="${APP_ROOT:-/opt/battlestats-server}"
APP_USER="${APP_USER:-battlestats}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
RELEASE_ID="$(date +%Y%m%d%H%M%S)"
REMOTE_RELEASE="${APP_ROOT}/releases/${RELEASE_ID}"
REMOTE_TMP_ENV="/tmp/battlestats-server.env.${RELEASE_ID}"
REMOTE_TMP_SECRETS="/tmp/battlestats-server.secrets.env.${RELEASE_ID}"
REMOTE_TMP_CERT="/tmp/battlestats-do-ca.${RELEASE_ID}.crt"
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
  REMOTE_RELEASE="${REMOTE_RELEASE}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/releases"
install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}"
install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}/server"
install -d -o "${APP_USER}" -g "${APP_USER}" "${REMOTE_RELEASE}/agents"
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

rsync -az --delete \
  --exclude '.git' \
  --exclude '.DS_Store' \
  "${REPO_ROOT}/agents/" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/agents/"

scp "${REPO_ROOT}/docker-compose.yml" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/docker-compose.yml"

ssh "${DEPLOY_USER}@${HOST}" \
  APP_ROOT="${APP_ROOT}" \
  APP_USER="${APP_USER}" \
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

if grep -q '^CELERY_BROKER_URL=' /etc/battlestats-server.env; then
  sed -i 's|^CELERY_BROKER_URL=.*|CELERY_BROKER_URL=amqp://guest:guest@127.0.0.1:5672//|' /etc/battlestats-server.env
else
  echo 'CELERY_BROKER_URL=amqp://guest:guest@127.0.0.1:5672//' >> /etc/battlestats-server.env
fi

if grep -q '^CELERY_RESULT_BACKEND=' /etc/battlestats-server.env; then
  sed -i 's|^CELERY_RESULT_BACKEND=.*|CELERY_RESULT_BACKEND=rpc://|' /etc/battlestats-server.env
else
  echo 'CELERY_RESULT_BACKEND=rpc://' >> /etc/battlestats-server.env
fi

if grep -q '^ENABLE_CRAWLER_SCHEDULES=' /etc/battlestats-server.env; then
  sed -i 's|^ENABLE_CRAWLER_SCHEDULES=.*|ENABLE_CRAWLER_SCHEDULES=1|' /etc/battlestats-server.env
else
  echo 'ENABLE_CRAWLER_SCHEDULES=1' >> /etc/battlestats-server.env
fi

ln -sfn /etc/battlestats-server.env "${REMOTE_RELEASE}/server/.env"
ln -sfn /etc/battlestats-server.secrets.env "${REMOTE_RELEASE}/server/.env.secrets"

"${APP_ROOT}/venv/bin/python" -m pip install --upgrade pip
"${APP_ROOT}/venv/bin/pip" install --no-cache-dir -r "${REMOTE_RELEASE}/server/requirements.txt"

cd "${REMOTE_RELEASE}/server"
"${APP_ROOT}/venv/bin/python" manage.py migrate
"${APP_ROOT}/venv/bin/python" manage.py collectstatic --noinput
"${APP_ROOT}/venv/bin/python" manage.py check

chown -R "${APP_USER}:${APP_USER}" "${REMOTE_RELEASE}"
ln -sfn "${REMOTE_RELEASE}" "${APP_ROOT}/current"

systemctl daemon-reload
redis-cli DEL warships:tasks:crawl_all_clans:lock warships:tasks:crawl_all_clans:heartbeat 2>/dev/null || true
systemctl restart redis-server rabbitmq-server battlestats-gunicorn battlestats-celery battlestats-celery-background battlestats-beat
systemctl --no-pager --full status battlestats-gunicorn | sed -n '1,25p'

find "${APP_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d | sort | head -n -"${KEEP_RELEASES}" | xargs -r rm -rf
REMOTE

echo "Backend deployed to ${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}"
