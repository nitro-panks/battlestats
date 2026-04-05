#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PYTHON_BIN="python"

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi

PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"

run_backend_release_tests() {
  local sqlite_dir="${ROOT_DIR}/.tmp"
  local sqlite_db="${sqlite_dir}/release-gate.sqlite3"

  (
    mkdir -p "${sqlite_dir}"
    rm -f "${sqlite_db}"
    cd "${ROOT_DIR}/server"
    DB_ENGINE=sqlite3 \
    DB_NAME="${sqlite_db}" \
    DB_SSLMODE='' \
    DB_SSLROOTCERT='' \
    DJANGO_SECRET_KEY=release-gate-test-secret-key \
    REDIS_URL='' \
    CELERY_BROKER_URL=memory:// \
    CELERY_RESULT_BACKEND=cache+memory:// \
      "${PYTHON_BIN}" -m pytest --nomigrations \
        warships/tests/test_views.py \
        warships/tests/test_landing.py \
        warships/tests/test_realm_isolation.py \
        warships/tests/test_data_product_contracts.py \
        -x --tb=short
  )
}

echo "[1/4] Running client lint"
(
  cd "${ROOT_DIR}/client"
  npm run lint
)

echo "[2/4] Running client release tests"
(
  cd "${ROOT_DIR}/client"
  npm run test:ci
)

echo "[3/4] Running client production build"
(
  cd "${ROOT_DIR}/client"
  npm run build
)

echo "[4/4] Running server release tests"
run_backend_release_tests

echo "Release gate passed"