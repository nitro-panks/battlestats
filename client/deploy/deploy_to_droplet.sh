#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-root}"
APP_ROOT="${APP_ROOT:-/opt/battlestats-client}"
APP_USER="${APP_USER:-battlestats}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
RELEASE_ID="$(date +%Y%m%d%H%M%S)"
REMOTE_RELEASE="${APP_ROOT}/releases/${RELEASE_ID}"

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
REMOTE

rsync -az --delete \
  --exclude '.git' \
  --exclude '.next' \
  --exclude 'coverage' \
  --exclude 'node_modules' \
  --exclude 'playwright-temp' \
  "${CLIENT_DIR}/" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/client/"

ssh "${DEPLOY_USER}@${HOST}" \
  APP_ROOT="${APP_ROOT}" \
  APP_USER="${APP_USER}" \
  REMOTE_RELEASE="${REMOTE_RELEASE}" \
  KEEP_RELEASES="${KEEP_RELEASES}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

cd "${REMOTE_RELEASE}/client"
npm ci
npm run build

chown -R "${APP_USER}:${APP_USER}" "${REMOTE_RELEASE}"
ln -sfn "${REMOTE_RELEASE}" "${APP_ROOT}/current"

systemctl restart battlestats-client
systemctl --no-pager --full status battlestats-client | sed -n '1,25p'

find "${APP_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d | sort | head -n -"${KEEP_RELEASES}" | xargs -r rm -rf
REMOTE

echo "Client deployed to ${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}"
