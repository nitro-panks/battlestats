#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT_DIR"

echo "[1/5] Ensuring docker services are running"
docker compose up -d db redis rabbitmq server react-app task-runner >/dev/null

echo "[2/5] Running backend release gate"
docker compose exec -T server python -m pytest \
	warships/tests/test_views.py \
	warships/tests/test_landing.py \
	warships/tests/test_realm_isolation.py \
	warships/tests/test_data_product_contracts.py \
	-x --tb=short

echo "[3/5] Running frontend release gate"
docker compose run --rm --no-deps react-app sh -c "npm run lint && npm run test:ci && rm -rf /app/.next/* && npm run build"

echo "[4/5] Release gate uses the curated twelve-test suite"

echo "[5/5] Release gate complete"

echo "Release gate passed"