# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Permissions & Autonomy

Operate autonomously. Do not pause for confirmation on:
- File reads, edits, creation, and deletion within this repo
- Git operations (add, commit, branch, checkout, rebase, push)
- Running tests, linters, builds, and dev servers
- Shell commands: curl, npm, npx, python, pip, pipenv, docker compose, ssh
- Deployment scripts in `client/deploy/` and `server/deploy/`
- Installing dependencies (npm install, pipenv install)
- Database migrations (makemigrations, migrate)

Only confirm before: force-pushing to main, dropping database tables, or deleting remote branches.

## Project

Battlestats is a World of Warships player and clan statistics platform. Live at https://battlestats.online.

- **Frontend**: Next.js 16 (App Router) + React 18 + Tailwind CSS + D3 charts — in `client/`
- **Backend**: Django 5 + DRF + Celery (RabbitMQ + Redis) + PostgreSQL — in `server/`
- **Agentic system**: LangGraph + CrewAI workflows with durable Postgres checkpoints — in `server/warships/agentic/`
- **Agent personas & runbooks**: Role definitions, knowledge base, and operational runbooks — in `agents/`

## Common Commands

### Docker (full stack)
```bash
docker compose up -d                              # Start all services
docker compose up -d db redis rabbitmq server react-app task-runner  # Selective
./run_test_suite.sh                               # Full test suite (docker-based)
```

### Backend (Django)
```bash
cd server
python -m pytest warships/tests/ -x --tb=short              # All backend tests
python -m pytest warships/tests/test_data.py -x --tb=short   # Single test file
python -m pytest warships/tests/test_views.py::TestPlayerViewSet::test_player_detail -x  # Single test
python manage.py test --keepdb warships.tests                # Via Django test runner (docker)
python manage.py makemigrations && python manage.py migrate  # Migrations
```

### Frontend (Next.js)
```bash
cd client
npm run dev                                       # Dev server (port 3000)
npm run build                                     # Production build
npm run lint                                      # ESLint
npm test -- --runInBand                           # Jest unit tests
npm test -- --runInBand path/to/test.tsx          # Single Jest test
npx playwright test                               # All E2E tests
npx playwright test e2e/some-spec.spec.ts         # Single E2E test
npx playwright test --grep "test name"            # E2E by name
npm run test:e2e:install                          # Install Playwright browsers
```

### Database
```bash
./server/scripts/switch_db_target.sh cloud        # Use cloud-managed DB
./server/scripts/switch_db_target.sh local        # Use local Postgres
```

### Deployment
```bash
./client/deploy/deploy_to_droplet.sh battlestats.online   # Deploy frontend
./server/deploy/deploy_to_droplet.sh battlestats.online   # Deploy backend
```

## Architecture

### Routing
- `/` — Landing page with search, featured players/clans, discovery charts
- `/player/[playerName]` — Player detail (URL-encoded name, reload-safe)
- `/clan/[clanSlug]` — Clan detail (`<clan_id>-<optional-slug>`, reload-safe)
- `/trace` — Agentic workflow trace dashboard

### API proxy
Next.js rewrites `/api/*` to `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`). The frontend never calls the Wargaming API directly — all data flows through Django.

### Key backend modules
- `server/warships/data.py` (4.5K lines) — Core hydration, chart payload assembly, cache warming
- `server/warships/landing.py` — Landing page modes (Best, Trending, Recent, Random) with published-cache + durable fallback
- `server/warships/tasks.py` — Celery tasks: player/clan refresh, ranked incrementals, landing warmup
- `server/warships/views.py` — DRF views and `@api_view` endpoints

### Key frontend patterns
- D3-based SVG chart components (TierSVG, TypeSVG, ActivitySVG, RankedWRBattlesHeatmapSVG, etc.)
- `client/app/context/ThemeContext.tsx` — Dark/light theme with localStorage persistence
- `client/app/lib/chartTheme.ts` — D3 color schemes keyed to active theme
- `client/app/lib/sharedJsonFetch.ts` — Fetch with retry and cache
- `client/app/lib/entityRoutes.ts` — URL encoding/decoding for player/clan routes

### Caching strategy
- **Cache-first with lazy-refresh**: Return cached payload immediately, queue background refresh
- **Durable fallback**: Keep last-published copy after TTL expiry
- **Stale-while-revalidate**: `X-Clan-Plot-Pending: true` header signals pending warm-up
- Redis-backed in production, LocMemCache in tests

### Data models (server/warships/models.py)
Player, Clan, Ship, Snapshot (daily battle summaries), PlayerExplorerSummary, EntityVisitEvent/EntityVisitDaily (analytics), PlayerAchievementStat.

## Team Doctrine (Pre-commit Requirements)

These rules from `agents/knowledge/agentic-team-doctrine.json` apply to every commit:

1. **Documentation review** — Update or synthesize durable docs that describe new behavior, contracts, or operational state.
2. **Doc-vs-code reconciliation** — When documentation is uncertain, verify against live code and tests before committing.
3. **Test coverage** — Ensure touched behavior has automated coverage; add or update focused tests when the current suite no longer proves the changed behavior.
4. **Runbook archiving** — Move superseded runbooks from `agents/runbooks/` to `agents/runbooks/archive/`.
5. **Contract safety** — When an endpoint or payload changes, update contract docs and API-facing tests in the same commit.

### Decision rules
- Smallest safe vertical slice. Reversible changes over clever shortcuts.
- Correctness before optimization. Preserve existing user-facing behavior unless the task explicitly changes it.
- Avoid unbounded polling, queue fan-out, or retry loops.
- Avoid new browser-triggered WG API calls when stored data already exists.
- Avoid large unscoped refactors during feature delivery.

## Environment

### Server env files (in `server/`)
- `.env` — Non-secret connection values (DB_HOST, DB_ENGINE, DJANGO_ALLOWED_HOSTS)
- `.env.secrets` — Secrets (WG_APP_ID, DB_PASSWORD, DJANGO_SECRET_KEY)
- `.env.cloud` / `.env.secrets.cloud` — Cloud database overrides

### Client env
- `BATTLESTATS_API_ORIGIN` — Backend URL (default `http://localhost:8888`)
- `NEXT_PUBLIC_GA_MEASUREMENT_ID` — GA4 measurement ID (optional)

### Docker ports
- 8888: Django/Gunicorn
- 3001: Next.js (Docker dev)
- 15672: RabbitMQ management
