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
- **Agentic system**: LangGraph + CrewAI workflows live in `server/warships/agentic/`, but the production droplet keeps this runtime opt-in via `DEPLOY_AGENTIC_RUNTIME=1` and `ENABLE_AGENTIC_RUNTIME=1`
- **Agent personas & runbooks**: Role definitions, knowledge base, and operational runbooks — in `agents/`

## Common Commands

### Docker (full stack)

```bash
docker compose up -d                              # Start all services
docker compose up -d db redis rabbitmq server react-app task-runner  # Selective
./run_test_suite.sh                               # Lean release gate (docker-based)
```

### Backend (Django)

```bash
cd server
python -m pytest warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py -x --tb=short  # Release gate
python -m pytest warships/tests/test_views.py -x --tb=short   # Single release-gate file
python -m pytest warships/tests/test_views.py::TestPlayerViewSet::test_player_detail -x  # Single test
python manage.py makemigrations && python manage.py migrate  # Migrations
```

### Frontend (Next.js)

```bash
cd client
npm run dev                                       # Dev server (port 3000)
npm run build                                     # Production build
npm run lint                                      # ESLint
npm test                                          # Lean frontend release gate
npm test -- app/components/__tests__/PlayerDetail.test.tsx  # Single release-gate file
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
DEPLOY_AGENTIC_RUNTIME=1 ./server/deploy/deploy_to_droplet.sh battlestats.online  # Deploy backend with LangGraph/CrewAI extras
./umami/deploy/bootstrap_umami.sh battlestats.online       # Bootstrap/update Umami analytics
```

Backend deploy defaults to the core site runtime only. Base dependencies install from `server/requirements.txt`; agentic extras install from `server/requirements-agentic.txt` only when `DEPLOY_AGENTIC_RUNTIME=1`.

### Operations

```bash
./server/scripts/check_enrichment_crawler.sh [host]  # Enrichment crawler status (default: battlestats.online)
```

Single SSH call to the droplet. Reports worker health (memory/swap/CPU/uptime/OOM risk), Redis lock state, batch history + throughput + ETA, errors (enrichment/WorkerLost/SIGTERM/SIGKILL), live progress, clan crawl interference, and periodic task state. See `agents/runbooks/runbook-enrichment-crawler-2026-04-03.md` for the progress log.

```bash
cd server
python manage.py backfill_clan_battle_data --realm eu --batch 500  # CB backfill for enriched players missing CB data
python manage.py backfill_clan_battle_data --realm na --batch 500 --partition 0 --num-partitions 2  # Partitioned for parallelism
```

Backfills per-player clan battle data (`clan_battle_total_battles`, `clan_battle_seasons_participated`, `clan_battle_overall_win_rate`) for enriched players whose `PlayerExplorerSummary` is missing CB fields. The enrichment pipeline now includes CB fetch (Phase 3e), so this command is only needed for players enriched before 2026-04-05.

### Background enrichment

Player enrichment runs on the droplet's Celery `background` worker via `warships.tasks.enrich_player_data_task`. The task self-chains between batches (~17–20 min per 500 players at steady state) and is kickstarted periodically by Celery Beat (`player-enrichment-kickstart`, every 15 min — a no-op if a batch is already running). Kickstart is also dispatched by the Gunicorn `when_ready` startup warmer.

**Historical note:** An experimental DigitalOcean Functions migration (`functions/enrichment/enrich-batch`) was reverted on 2026-04-08 because DO Functions egress from a rotating IP pool that cannot be whitelisted by the Wargaming `application_id`, causing every call to fail with `407 INVALID_IP_ADDRESS`. See `agents/runbooks/archive/spec-serverless-background-workers-2026-04-04.md` for the post-mortem. The `functions/` directory and `db-test` function remain for potential future workers that do not touch the WG API.

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
- `/trace` — Agentic workflow trace dashboard when `ENABLE_AGENTIC_RUNTIME=1`
- `/umami` — Umami analytics dashboard (admin login required)

### API proxy

Next.js rewrites `/api/*` to `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`). The frontend never calls the Wargaming API directly — all data flows through Django.

### Key backend modules

- `server/warships/data.py` (~5K lines) — Core hydration, chart payload assembly, cache warming, hot entity warming, population correlations/distributions, `score_best_clans()` composite ranking. Analytical queries use elevated `work_mem` via `_elevated_work_mem()` context manager.
- `server/warships/landing.py` — Landing page modes (Best, Random, Sigma, Popular) with published-cache + durable fallback
- `server/warships/tasks.py` — Celery tasks: player/clan refresh, ranked incrementals, landing warmup, distribution/correlation warming
- `server/warships/signals.py` — Registers all Celery Beat periodic tasks via `@receiver(post_migrate)` (landing warmer, hot entity warmer, clan crawl, player refresh, etc.)
- `server/warships/views.py` — DRF views and `@api_view` endpoints

### Key frontend patterns

- D3-based SVG chart components (TierSVG, TypeSVG, ActivitySVG, RankedWRBattlesHeatmapSVG, etc.)
- `client/app/context/ThemeContext.tsx` — Dark/light theme with localStorage persistence
- `client/app/components/ThemeToggle.tsx` — Theme selection dropdown (light/dark/system)
- `client/app/lib/chartTheme.ts` — D3 color schemes keyed to active theme
- `client/app/lib/wrColor.ts` — Shared win-rate → color mapping used across all surfaces
- `client/app/lib/sharedJsonFetch.ts` — Fetch with retry, cache, and chart fetch priority counter (`chartFetchesInFlight`) for coordinating request priority between chart rendering and hydration polling
- `client/app/lib/entityRoutes.ts` — URL encoding/decoding for player/clan routes
- `client/app/globals.css` — CSS custom properties for theming (`--bg-*`, `--text-*`, `--accent-*`), dark mode via `[data-theme="dark"]`
- `client/app/components/HeaderSearch.tsx` — Player search autocomplete with client-side suggestion cache and themed input
- Shared icon components in `client/app/components/` — 7 player classification icons (HiddenAccountIcon, EfficiencyRankIcon, LeaderCrownIcon, PveEnjoyerIcon, InactiveIcon, RankedPlayerIcon, ClanBattleShieldIcon) with `size` prop for surface variants

### Caching strategy

- **Cache-first with lazy-refresh**: Return cached payload immediately, queue background refresh
- **Durable fallback**: Keep last-published copy after TTL expiry
- **Stale-while-revalidate**: `X-Clan-Plot-Pending: true` header signals pending warm-up
- **Hot entity warmer**: Periodic task (every 30 min) keeps top-visited + pinned + recently-viewed players/clans warm. Pinned players configured via `HOT_ENTITY_PINNED_PLAYER_NAMES` env var. Recently-viewed players (last N visitors within M minutes) configured via `RECENTLY_VIEWED_PLAYER_LIMIT` and `RECENTLY_VIEWED_WARM_MINUTES` env vars.
- **Bulk entity cache loader**: Periodic task (every 12h) pre-loads top 50 players + members of 25 best-scored clans + top 25 clans into Redis. Uses `score_best_clans()` composite ranking (WR 30%, activity 25%, member score 20%, CB recency 15%, volume 10%). See `runbook-best-clan-eligibility.md`.
- **Landing page warmer**: Periodic task (every 55 min) refreshes all landing payloads + population distributions + population correlations; Best clan mode also uses `score_best_clans()`
- **Distribution & correlation warming**: Proactive warming of player population distributions (WR, battles, avg tier) and correlations (tier-type, ranked WR-battles, WR-survival) every 55 min via the landing page task and on startup. TTL is 2 hours. Eliminates cold-cache penalty (10-30s full table scans).
- **Startup cache warming**: Gunicorn `when_ready` hook (`gunicorn.conf.py`) dispatches `startup_warm_caches_task` to the Celery background queue — sequentially warms landing page, hot entities, bulk cache, distributions, and correlations. Runs inside an existing worker rather than spawning a subprocess. Controlled by `WARM_CACHES_ON_STARTUP` env var (default `1`). See `runbook-deploy-oom-startup-warmers.md`.
- **Player search suggestions**: Three-tier cache — client-side `Map` (instant, session-scoped, 200-entry cap) → Redis (10 min TTL, `suggest:<query>` keys) → Postgres with `pg_trgm` GIN index (`player_name_trgm_idx`). Minimum 3-character query. Raw `ILIKE` in `views.py` (Django's `icontains` generates `UPPER()` which bypasses trigram indexes).
- **Clan battle seasons (clan-level)**: Request-driven — first visit queues `update_clan_battle_summary_task` which calls `refresh_clan_battle_seasons_cache()`. This fetches per-member CB stats from the WG API via ThreadPoolExecutor, aggregates by season, and writes to **Redis only** (TTL-based). Configured clans are pre-warmed by `warm_clan_battle_summaries_task` (env: `CLAN_BATTLE_WARM_CLAN_IDS`). Subsequent visits hit Redis until TTL expiry.
- **Clan battle summary (per-player)**: Per-player CB stats (`clan_battle_total_battles`, `clan_battle_seasons_participated`, `clan_battle_overall_win_rate`) are persisted to **Postgres** on `PlayerExplorerSummary` via `_persist_player_clan_battle_summary()`. Populated by: enrichment pipeline (Phase 3e), player CB tab visits, and the `backfill_clan_battle_data` management command.
- Redis-backed in production, LocMemCache in tests

### Celery queue architecture

Three queues with dedicated workers:

- **default** (`-c 3`) — lightweight API-triggered entity refreshes and general work
- **hydration** (`-c 3`) — heavier request-driven upstream/data refreshes. Tasks include ranked, efficiency, battle-data, clan-members, clan-battle, and clan-battle-summary refreshes
- **background** (`-c 2`) — long-running crawls, warmers, incremental refreshes, startup warmers, and enrichment

### Nginx / HTTP

- **HTTP/2** enabled on the production nginx 443 listeners. Eliminates the browser's 6-connection-per-origin limit under HTTP/1.1, allowing all concurrent chart and hydration requests to proceed without slot contention.

### Frontend fetch priority

Player detail pages coordinate chart rendering vs hydration polling:

- Tab warmup fires 4 parallel chart requests via `requestIdleCallback` (250ms timeout)
- Clan member fetch is deferred until warmup settles (with 10s hard timeout fallback)
- `sharedJsonFetch.ts` exposes a `chartFetchesInFlight` counter; `useClanMembers` backs off to 6s poll intervals while charts are in-flight
- See `runbook-player-page-load-priority.md` for full diagnosis and architecture

### Database optimizations

- **`CONN_HEALTH_CHECKS`**: Enabled — Django validates connections before use, preventing stale-connection errors with managed Postgres
- **Elevated `work_mem`**: Analytical queries (distribution bins, tier-type/ranked/survival correlations) use `SET LOCAL work_mem` within `transaction.atomic()` to get 8MB (configurable via `ANALYTICAL_WORK_MEM`) instead of the default 2MB. This improves sort/hash performance for full table scans over ~194K players.

### SEO

- **Dynamic metadata**: Player and clan pages export `generateMetadata()` with per-page title, description, OG tags, Twitter cards, and canonical URLs
- **Dynamic sitemap**: `app/sitemap.ts` fetches recently-visited entities from `/api/sitemap-entities/` (hourly revalidation). Backend endpoint queries `EntityVisitDaily` for players/clans with ≥2 deduped views in last 30 days
- **Structured data**: Homepage includes `WebSite` + `SearchAction` JSON-LD for Google sitelinks search box
- **Google Analytics**: GA4 measurement ID configured via `NEXT_PUBLIC_GA_MEASUREMENT_ID` env var (build-time). Deploy script sources `/etc/battlestats-client.env` before `npm run build`

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

- `patch` releases may skip the release gate.
- `minor` and `major` releases run the curated release gate before bumping the version.

## Environment

### Server env files (in `server/`)

- `.env` — Non-secret connection values (DB_HOST, DB_ENGINE, DJANGO_ALLOWED_HOSTS)
- `.env.secrets` — Secrets (WG_APP_ID, DB_PASSWORD, DJANGO_SECRET_KEY)
- `.env.cloud` / `.env.secrets.cloud` — Cloud database overrides

### Server runtime env (configurable, not secrets)

- `HOT_ENTITY_PINNED_PLAYER_NAMES` — Comma-separated player names to always keep warm (default: `lil_boots`)
- `CLAN_BATTLE_WARM_CLAN_IDS` — Comma-separated clan IDs for clan battle summary warming
- `BEST_CLAN_EXCLUDED_IDS` — Comma-separated clan IDs excluded from Best clan ranking
- `HOT_ENTITY_PLAYER_LIMIT` / `HOT_ENTITY_CLAN_LIMIT` — Hot entity cache size (defaults: 20/10)
- `ENABLE_CRAWLER_SCHEDULES` — Enable daily clan crawl (set `1` in production)
- `ENABLE_AGENTIC_RUNTIME` — Enable `/trace` and optional agentic runtime paths (default: `0` on the droplet)
- `BATTLESTATS_HINDSIGHT_ENABLED` — Enable the optional Hindsight-backed LangGraph store (default: `0`)
- `BATTLESTATS_HINDSIGHT_API_URL` — Base URL for the Hindsight API when the optional store is enabled
- `HINDSIGHT_API_KEY` — Optional Hindsight API key when the service requires authentication
- `BATTLESTATS_HINDSIGHT_BUDGET` — Default Hindsight recall budget for the LangGraph store (`low`, `mid`, `high`; default `mid`)
- `BATTLESTATS_HINDSIGHT_MAX_TOKENS` — Max recall tokens for the LangGraph store (default `4096`)
- `BATTLESTATS_HINDSIGHT_TAGS` — Comma-separated default tags applied to Hindsight retain operations

For local SDLC, prefer a separate Hindsight service via the `agentic-memory` Docker Compose profile and point host-based commands at `http://127.0.0.1:8899`. Keep Hindsight off the production droplet unless agentic memory is intentionally part of that environment.

- `ANALYTICAL_WORK_MEM` — Per-query `work_mem` for analytical queries (default: `8MB`)
- `RECENTLY_VIEWED_PLAYER_LIMIT` — Max recently-viewed players to warm (default: 10)
- `RECENTLY_VIEWED_WARM_MINUTES` — Time window for recently-viewed player warming (default: 60)
- `ENRICH_REALMS` — Comma-separated realm list for enrichment crawler (e.g. `na`, `na,eu`). Empty or unset means all realms

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
