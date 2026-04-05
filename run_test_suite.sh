#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
	PYTHON_BIN="python"
fi

cd "$ROOT_DIR"

echo "[1/5] Ensuring docker services are running"
docker compose up -d react-app >/dev/null

echo "[2/5] Running backend release gate"
	mkdir -p .tmp
	rm -f .tmp/release-gate.sqlite3
	cd server
	DB_ENGINE=sqlite3 \
	DB_NAME="$ROOT_DIR/.tmp/release-gate.sqlite3" \
	DB_SSLMODE='' \
	DB_SSLROOTCERT='' \
	REDIS_URL='' \
	CELERY_BROKER_URL=memory:// \
	CELERY_RESULT_BACKEND=cache+memory:// \
	"${PYTHON_BIN}" -m pytest --nomigrations \
	warships/tests/test_views.py \
	warships/tests/test_landing.py \
	warships/tests/test_realm_isolation.py \
	warships/tests/test_data_product_contracts.py \
	-x --tb=short

echo "[3/5] Running frontend release gate"
cd "$ROOT_DIR"
docker compose run --rm --no-deps react-app sh -c "npm run lint && npm run test:ci && rm -rf /app/.next/* && npm run build"

echo "[4/5] Release gate uses the curated backend/frontend release suite"

echo "[5/5] Release gate complete"

echo "Release gate passed"