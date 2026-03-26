#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT_DIR"

echo "[1/5] Ensuring docker services are running"
docker compose up -d db redis rabbitmq server react-app task-runner >/dev/null

echo "[2/5] Running backend test suite"
docker compose exec -T server python manage.py test --keepdb --noinput warships.tests

echo "[3/5] Running frontend production build"
docker compose run --rm --no-deps react-app sh -c "rm -rf /app/.next/* && npm run build"

echo "[4/5] Warming clan battle smoke fixtures"
docker compose exec -T server python manage.py shell -c "from warships.tasks import warm_clan_battle_summaries_task; print(warm_clan_battle_summaries_task.run())"

echo "[5/5] Running API smoke tests"
docker compose exec -T server python scripts/smoke_test_site_endpoints.py

echo "Full test suite passed"