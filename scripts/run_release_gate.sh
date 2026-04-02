#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PYTHON_BIN="python"

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi

PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON_BIN}}"

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
if command -v docker >/dev/null 2>&1; then
  (
    cd "${ROOT_DIR}"

    if [[ -f "${ROOT_DIR}/server/.env" ]]; then
      set -a
      . "${ROOT_DIR}/server/.env"
      set +a
    fi

    if [[ -f "${ROOT_DIR}/server/.env.secrets" ]]; then
      set -a
      . "${ROOT_DIR}/server/.env.secrets"
      set +a
    fi

    docker compose up -d db redis rabbitmq server >/dev/null
    docker compose exec -T server python -m pytest \
      warships/tests/test_views.py \
      warships/tests/test_landing.py \
      warships/tests/test_realm_isolation.py \
      warships/tests/test_data_product_contracts.py \
      --reuse-db \
      -x --tb=short
  )
else
  (
    cd "${ROOT_DIR}/server"
    "${PYTHON_BIN}" -m pytest \
      warships/tests/test_views.py \
      warships/tests/test_landing.py \
      warships/tests/test_realm_isolation.py \
      warships/tests/test_data_product_contracts.py \
      --reuse-db \
      -x --tb=short
  )
fi

echo "Release gate passed"