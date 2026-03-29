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

Battlestats is a World of Warships player and clan statistics platform. Live at https://battlestats.online. Current version is in `VERSION` at the repo root (semver, surfaced in the client footer).

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
./umami/deploy/bootstrap_umami.sh battlestats.online       # Bootstrap/update Umami analytics
```

### Releases
```bash
./scripts/release.sh patch    # 1.2.0 → 1.2.1  (bug fixes)
./scripts/release.sh minor    # 1.2.0 → 1.3.0  (new features)
./scripts/release.sh major    # 1.2.0 → 2.0.0  (breaking changes)
```

## Architecture

### Routing
- `/` — Landing page with search, featured players/clans, discovery charts
- `/player/[playerName]` — Player detail (URL-encoded name, reload-safe)
- `/clan/[clanSlug]` — Clan detail (`<clan_id>-<optional-slug>`, reload-safe)
- `/trace` — Agentic workflow trace dashboard
- `/umami` — Umami analytics dashboard (admin login required)

### API proxy
Next.js rewrites `/api/*` to `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`). The frontend never calls the Wargaming API directly — all data flows through Django.

### Key backend modules
- `server/warships/data.py` (~5K lines) — Core hydration, chart payload assembly, cache warming, hot entity warming, `score_best_clans()` composite ranking
- `server/warships/landing.py` — Landing page modes (Best, Random, Sigma, Popular) with published-cache + durable fallback
- `server/warships/tasks.py` — Celery tasks: player/clan refresh, ranked incrementals, landing warmup
- `server/warships/signals.py` — Registers all Celery Beat periodic tasks via `@receiver(post_migrate)` (landing warmer, hot entity warmer, clan crawl, player refresh, etc.)
- `server/warships/views.py` — DRF views and `@api_view` endpoints

### Key frontend patterns
- D3-based SVG chart components (TierSVG, TypeSVG, ActivitySVG, RankedWRBattlesHeatmapSVG, etc.)
- `client/app/context/ThemeContext.tsx` — Dark/light theme with localStorage persistence
- `client/app/components/ThemeToggle.tsx` — Theme selection dropdown (light/dark/system)
- `client/app/lib/chartTheme.ts` — D3 color schemes keyed to active theme
- `client/app/lib/wrColor.ts` — Shared win-rate → color mapping used across all surfaces
- `client/app/lib/sharedJsonFetch.ts` — Fetch with retry and cache
- `client/app/lib/entityRoutes.ts` — URL encoding/decoding for player/clan routes
- `client/app/globals.css` — CSS custom properties for theming (`--bg-*`, `--text-*`, `--accent-*`), dark mode via `[data-theme="dark"]`
- `client/app/components/HeaderSearch.tsx` — Player search autocomplete with client-side suggestion cache and themed input
- Shared icon components in `client/app/components/` — 7 player classification icons (HiddenAccountIcon, EfficiencyRankIcon, LeaderCrownIcon, PveEnjoyerIcon, InactiveIcon, RankedPlayerIcon, ClanBattleShieldIcon) with `size` prop for surface variants

### Caching strategy
- **Cache-first with lazy-refresh**: Return cached payload immediately, queue background refresh
- **Durable fallback**: Keep last-published copy after TTL expiry
- **Stale-while-revalidate**: `X-Clan-Plot-Pending: true` header signals pending warm-up
- **Hot entity warmer**: Periodic task (every 30 min) keeps top-visited + pinned players/clans warm. Pinned players configured via `HOT_ENTITY_PINNED_PLAYER_NAMES` env var
- **Bulk entity cache loader**: Periodic task (every 12h) pre-loads top 50 players + members of 25 best-scored clans + top 25 clans into Redis. Uses `score_best_clans()` composite ranking (WR 30%, activity 25%, member score 20%, CB recency 15%, volume 10%). See `runbook-best-clan-eligibility.md`.
- **Landing page warmer**: Periodic task (every 55 min) refreshes all landing payloads; Best clan mode also uses `score_best_clans()`
- **Player search suggestions**: Three-tier cache — client-side `Map` (instant, session-scoped, 200-entry cap) → Redis (10 min TTL, `suggest:<query>` keys) → Postgres with `pg_trgm` GIN index (`player_name_trgm_idx`). Minimum 3-character query. Raw `ILIKE` in `views.py` (Django's `icontains` generates `UPPER()` which bypasses trigram indexes).
- Redis-backed in production, LocMemCache in tests

### Data models (server/warships/models.py)
Player, Clan, Ship, Snapshot (daily battle summaries), PlayerExplorerSummary, EntityVisitEvent/EntityVisitDaily (analytics), PlayerAchievementStat.

## Team Doctrine (Pre-commit Requirements)

**Agents must read `agents/knowledge/agentic-team-doctrine.json` before planning or executing multi-step work.** It contains the authoritative decision rules, pre-commit checklist, and quality gates that govern all changes in this repository.

These rules from that file apply to every commit:

1. **Documentation review** — Update or synthesize durable docs that describe new behavior, contracts, or operational state.
2. **Doc-vs-code reconciliation** — When documentation is uncertain, verify against live code and tests before committing.
3. **Test coverage** — Ensure touched behavior has automated coverage; add or update focused tests when the current suite no longer proves the changed behavior.
4. **Runbook archiving** — Move superseded runbooks from `agents/runbooks/` to `agents/runbooks/archive/`.
5. **Contract safety** — When an endpoint or payload changes, update contract docs and API-facing tests in the same commit.
6. **Runbook reconciliation** — When implementing changes described in a runbook or spec, update it to reflect implementation status, fixes applied, and validation results.

### Decision rules
- Smallest safe vertical slice. Reversible changes over clever shortcuts.
- Correctness before optimization. Preserve existing user-facing behavior unless the task explicitly changes it.
- Avoid unbounded polling, queue fan-out, or retry loops.
- Avoid new browser-triggered WG API calls when stored data already exists.
- Avoid large unscoped refactors during feature delivery.

## Versioning

The project uses semantic versioning with a root `VERSION` file as the single source of truth. The version is surfaced in the client footer at build time via `NEXT_PUBLIC_APP_VERSION`.

### Semver levels
- **patch** — bug fixes, performance tuning, doc-only changes
- **minor** — new features, new surfaces, meaningful UX changes
- **major** — breaking data model migrations, API contract changes, major UX overhauls

### Commit message prefixes (Conventional Commits)
Use these prefixes on all commit messages to enable future automation:
- `feat:` — new feature or surface (maps to **minor**)
- `fix:` — bug fix (maps to **patch**)
- `perf:` — performance improvement (maps to **patch**)
- `refactor:` — code change that neither fixes a bug nor adds a feature (maps to **patch**)
- `docs:` — documentation only (maps to **patch**)
- `chore:` — build, CI, deps, tooling (maps to **patch**)
- `test:` — adding or updating tests (maps to **patch**)
- Append `!` after the prefix (e.g. `feat!:`) for breaking changes (maps to **major**)

### Release workflow
Releases are cut manually with `./scripts/release.sh <patch|minor|major>`, which bumps VERSION, commits, tags, and pushes.

## Environment

### Server env files (in `server/`)
- `.env` — Non-secret connection values (DB_HOST, DB_ENGINE, DJANGO_ALLOWED_HOSTS)
- `.env.secrets` — Secrets (WG_APP_ID, DB_PASSWORD, DJANGO_SECRET_KEY)
- `.env.cloud` / `.env.secrets.cloud` — Cloud database overrides

### Server runtime env (configurable, not secrets)
- `HOT_ENTITY_PINNED_PLAYER_NAMES` — Comma-separated player names to always keep warm (default: `lil_boots`)
- `CLAN_BATTLE_WARM_CLAN_IDS` — Comma-separated clan IDs for clan battle summary warming
- `HOT_ENTITY_PLAYER_LIMIT` / `HOT_ENTITY_CLAN_LIMIT` — Hot entity cache size (defaults: 20/10)
- `ENABLE_CRAWLER_SCHEDULES` — Enable daily clan crawl (set `1` in production)

### Client env
- `BATTLESTATS_API_ORIGIN` — Backend URL (default `http://localhost:8888`)
- `NEXT_PUBLIC_GA_MEASUREMENT_ID` — GA4 measurement ID (optional)

### Umami analytics
- Dashboard: `https://battlestats.online/umami/`
- Runs as a standalone Next.js app on port 3002 behind nginx
- Uses the same managed Postgres (separate `umami` database)
- Bootstrap script: `umami/deploy/bootstrap_umami.sh`
- Tracking script loaded via `<script>` tag in `client/app/layout.tsx`
- Credentials stored on droplet; default user is `admin`

### Docker ports
- 8888: Django/Gunicorn
- 3001: Next.js (Docker dev)
- 3002: Umami analytics (production droplet only)
- 15672: RabbitMQ management
