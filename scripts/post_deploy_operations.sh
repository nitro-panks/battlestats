#!/usr/bin/env bash

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-root}"
SERVER_APP_ROOT="${SERVER_APP_ROOT:-/opt/battlestats-server}"
CLIENT_APP_ROOT="${CLIENT_APP_ROOT:-/opt/battlestats-client}"
DEFAULT_SMOKE_BASE_URL="${POST_DEPLOY_SMOKE_BASE_URL:-http://127.0.0.1:8888}"
DEFAULT_SMOKE_TIMEOUT="${POST_DEPLOY_SMOKE_TIMEOUT:-30}"

usage() {
  cat <<'EOF'
Usage:
  scripts/post_deploy_operations.sh <host> verify [--realm <realm>]... [--expect-backend-release <path>] [--expect-client-release <path>] [--skip-backend] [--skip-client]
  scripts/post_deploy_operations.sh <host> snapshots [--realm <realm>]... [--sort <sort>]...
  scripts/post_deploy_operations.sh <host> invalidate [--realm <realm>]... [--players] [--clans] [--include-recent]
  scripts/post_deploy_operations.sh <host> warm-landing [--realm <realm>]... [--include-recent] [--force-refresh]
  scripts/post_deploy_operations.sh <host> warm-best-entities [--realm <realm>]... [--player-limit <n>] [--clan-limit <n>] [--force-refresh]
  scripts/post_deploy_operations.sh <host> smoke [--base-url <url>] [--timeout <seconds>]
EOF
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 1
fi

HOST="$1"
SUBCOMMAND="$2"
shift 2

run_remote_server_manage() {
  ssh "${DEPLOY_USER}@${HOST}" APP_ROOT="${SERVER_APP_ROOT}" 'bash -s' -- "$@" <<'REMOTE'
set -euo pipefail
cd "${APP_ROOT}/current/server"
set -a
source /etc/battlestats-server.env
source /etc/battlestats-server.secrets.env
set +a
"${APP_ROOT}/venv/bin/python" manage.py "$@"
REMOTE
}

print_current_release() {
  local label="$1"
  local app_root="$2"
  ssh "${DEPLOY_USER}@${HOST}" APP_ROOT="${app_root}" LABEL="${label}" 'bash -s' <<'REMOTE'
set -euo pipefail
active_release="$(readlink -f "${APP_ROOT}/current")"
echo "${LABEL} release: ${active_release}"
REMOTE
}

verify_release_target() {
  local label="$1"
  local app_root="$2"
  local expected_release="$3"
  ssh "${DEPLOY_USER}@${HOST}" APP_ROOT="${app_root}" EXPECTED_RELEASE="${expected_release}" LABEL="${label}" 'bash -s' <<'REMOTE'
set -euo pipefail
active_release="$(readlink -f "${APP_ROOT}/current")"
if [[ "${active_release}" != "${EXPECTED_RELEASE}" ]]; then
  echo "${LABEL} release mismatch: expected ${EXPECTED_RELEASE}, got ${active_release}" >&2
  exit 1
fi
echo "${LABEL} release verified: ${active_release}"
REMOTE
}

verify_services() {
  local label="$1"
  shift
  ssh "${DEPLOY_USER}@${HOST}" LABEL="${label}" 'bash -s' -- "$@" <<'REMOTE'
set -euo pipefail
label="$1"
shift
if ! systemctl is-active --quiet "$@"; then
  echo "${label} services unhealthy" >&2
  systemctl is-active "$@" >&2
  exit 1
fi
echo "${label} services verified: $(printf '%s ' "$@")"
REMOTE
}

run_remote_smoke() {
  local base_url="$1"
  local timeout="$2"
  ssh "${DEPLOY_USER}@${HOST}" APP_ROOT="${SERVER_APP_ROOT}" BASE_URL="${base_url}" TIMEOUT_SECONDS="${timeout}" 'bash -s' <<'REMOTE'
set -euo pipefail
cd "${APP_ROOT}/current/server"
set -a
source /etc/battlestats-server.env
source /etc/battlestats-server.secrets.env
set +a
"${APP_ROOT}/venv/bin/python" scripts/smoke_test_site_endpoints.py --base-url "${BASE_URL}" --timeout "${TIMEOUT_SECONDS}" --json
REMOTE
}

append_realm_flags() {
  local -n target_ref=$1
  shift
  local realm=""
  for realm in "$@"; do
    target_ref+=(--realm "${realm}")
  done
}

case "${SUBCOMMAND}" in
  verify)
    skip_backend=0
    skip_client=0
    expect_backend_release=""
    expect_client_release=""
    realms=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --realm)
          realms+=("$2")
          shift 2
          ;;
        --expect-backend-release)
          expect_backend_release="$2"
          shift 2
          ;;
        --expect-client-release)
          expect_client_release="$2"
          shift 2
          ;;
        --skip-backend)
          skip_backend=1
          shift
          ;;
        --skip-client)
          skip_client=1
          shift
          ;;
        *)
          echo "Unknown verify option: $1" >&2
          usage >&2
          exit 1
          ;;
      esac
    done

    if [[ "${skip_backend}" != "1" ]]; then
      if [[ -n "${expect_backend_release}" ]]; then
        verify_release_target "backend" "${SERVER_APP_ROOT}" "${expect_backend_release}"
      else
        print_current_release "backend" "${SERVER_APP_ROOT}"
      fi
      verify_services "backend" battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat redis-server rabbitmq-server
      manage_args=(run_post_deploy_operations verify)
      append_realm_flags manage_args "${realms[@]}"
      run_remote_server_manage "${manage_args[@]}"
    fi

    if [[ "${skip_client}" != "1" ]]; then
      if [[ -n "${expect_client_release}" ]]; then
        verify_release_target "client" "${CLIENT_APP_ROOT}" "${expect_client_release}"
      else
        print_current_release "client" "${CLIENT_APP_ROOT}"
      fi
      verify_services "client" battlestats-client nginx
    fi
    ;;
  snapshots)
    manage_args=(run_post_deploy_operations snapshots)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --realm|--sort)
          manage_args+=("$1" "$2")
          shift 2
          ;;
        *)
          echo "Unknown snapshots option: $1" >&2
          usage >&2
          exit 1
          ;;
      esac
    done
    run_remote_server_manage "${manage_args[@]}"
    ;;
  invalidate)
    manage_args=(run_post_deploy_operations invalidate)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --realm|--players|--clans|--include-recent)
          manage_args+=("$1")
          if [[ "$1" == "--realm" ]]; then
            manage_args+=("$2")
            shift 2
          else
            shift
          fi
          ;;
        *)
          echo "Unknown invalidate option: $1" >&2
          usage >&2
          exit 1
          ;;
      esac
    done
    run_remote_server_manage "${manage_args[@]}"
    ;;
  warm-landing)
    manage_args=(run_post_deploy_operations warm-landing)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --realm|--include-recent|--force-refresh)
          manage_args+=("$1")
          if [[ "$1" == "--realm" ]]; then
            manage_args+=("$2")
            shift 2
          else
            shift
          fi
          ;;
        *)
          echo "Unknown warm-landing option: $1" >&2
          usage >&2
          exit 1
          ;;
      esac
    done
    run_remote_server_manage "${manage_args[@]}"
    ;;
  warm-best-entities)
    manage_args=(run_post_deploy_operations warm-best-entities)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --realm|--player-limit|--clan-limit)
          manage_args+=("$1" "$2")
          shift 2
          ;;
        --force-refresh)
          manage_args+=("$1")
          shift
          ;;
        *)
          echo "Unknown warm-best-entities option: $1" >&2
          usage >&2
          exit 1
          ;;
      esac
    done
    run_remote_server_manage "${manage_args[@]}"
    ;;
  smoke)
    base_url="${DEFAULT_SMOKE_BASE_URL}"
    timeout="${DEFAULT_SMOKE_TIMEOUT}"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --base-url)
          base_url="$2"
          shift 2
          ;;
        --timeout)
          timeout="$2"
          shift 2
          ;;
        *)
          echo "Unknown smoke option: $1" >&2
          usage >&2
          exit 1
          ;;
      esac
    done
    run_remote_smoke "${base_url}" "${timeout}"
    ;;
  *)
    echo "Unknown subcommand: ${SUBCOMMAND}" >&2
    usage >&2
    exit 1
    ;;
esac