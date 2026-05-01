#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${CLIENT_DIR}/.." && pwd)"

# Hard gate: local tree must be at or ahead of origin/main. Prevents a
# stale worktree from rsync'ing older code over production and silently
# reverting already-shipped fixes (see scripts/check_local_tree.sh).
"${REPO_ROOT}/scripts/check_local_tree.sh"

# Hard gate: CI must be passing before deploy
"${REPO_ROOT}/scripts/check_ci_status.sh"

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

# VERSION file lives at repo root; next.config.mjs reads ../VERSION
scp "${CLIENT_DIR}/../VERSION" "${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}/VERSION"

ssh "${DEPLOY_USER}@${HOST}" \
  APP_ROOT="${APP_ROOT}" \
  APP_USER="${APP_USER}" \
  REMOTE_RELEASE="${REMOTE_RELEASE}" \
  KEEP_RELEASES="${KEEP_RELEASES}" \
  'bash -s' <<'REMOTE'
set -euo pipefail

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
    echo "Failed to activate client release: expected ${REMOTE_RELEASE}, got ${active_release}" >&2
    exit 1
  fi
}

cd "${REMOTE_RELEASE}/client"

# Source env so NEXT_PUBLIC_* vars are available at build time
set -a
. /etc/battlestats-client.env
set +a

npm ci
npm run build

chown -R "${APP_USER}:${APP_USER}" "${REMOTE_RELEASE}"
activate_release

# Kill any stale Next.js processes from prior deploys before restarting.
# The systemd unit restarts the service, but orphaned node processes from
# previous releases can linger and waste 100-200 MB of RAM each.
pkill -f 'next-server' 2>/dev/null || true
sleep 1

systemctl restart battlestats-client
active_release_after_restart="$(readlink -f "${APP_ROOT}/current")"
if [[ "${active_release_after_restart}" != "${REMOTE_RELEASE}" ]]; then
  echo "Client release activation drifted after restart: expected ${REMOTE_RELEASE}, got ${active_release_after_restart}" >&2
  exit 1
fi
systemctl --no-pager --full status battlestats-client | sed -n '1,25p'

find "${APP_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d | sort | head -n -"${KEEP_RELEASES}" | xargs -r rm -rf
REMOTE

"${REPO_ROOT}/scripts/post_deploy_operations.sh" "${HOST}" verify --skip-backend --expect-client-release "${REMOTE_RELEASE}"

echo "Client deployed to ${DEPLOY_USER}@${HOST}:${REMOTE_RELEASE}"
