# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Permissions & Autonomy

Operate autonomously. Do not pause for confirmation on: file reads/edits/creation/deletion in this repo; git operations (add, commit, branch, checkout, rebase, push); tests, linters, builds, dev servers; shell commands (curl, npm, npx, python, pip, pipenv, docker compose, ssh); deploy scripts in `client/deploy/` and `server/deploy/`; dependency installs; database migrations.

Only confirm before: force-pushing to main, dropping database tables, or deleting remote branches.

## Project

Battlestats is a World of Warships player and clan statistics platform. Live at https://battlestats.online. Version is in `VERSION` at the repo root (semver, surfaced in the client footer).

- **Frontend**: Next.js 16 (App Router) + React 18 + Tailwind + D3 charts — `client/`
- **Backend**: Django 5 + DRF + Celery (RabbitMQ + Redis) + PostgreSQL — `server/`
- **Agents**: markdown personas, knowledge base, and operational runbooks for Claude Code subagents — `agents/` (not a runtime)

## Common Commands

### Docker (full stack)

```bash
docker compose up -d                              # Start all services
./run_test_suite.sh                               # Lean release gate (docker-based)
```

### Backend (Django)

```bash
cd server
python -m pytest warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py -x --tb=short  # Release gate
python -m pytest warships/tests/test_views.py::TestPlayerViewSet::test_player_detail -x  # Single test
python manage.py makemigrations && python manage.py migrate
```

### Frontend (Next.js)

```bash
cd client
npm run dev          # Dev server (port 3000)
npm run build        # Production build
npm run lint         # ESLint
npm test             # Lean frontend release gate
npm test -- app/components/__tests__/PlayerDetail.test.tsx  # Single file
```

### Database / Deploy / Release

```bash
./server/scripts/switch_db_target.sh cloud|local          # Switch DB target
./client/deploy/deploy_to_droplet.sh battlestats.online   # Deploy frontend
./server/deploy/deploy_to_droplet.sh battlestats.online   # Deploy backend
./scripts/release.sh patch|minor|major                    # Bump VERSION, commit, tag, push
```

### Operations

```bash
./server/scripts/check_enrichment_crawler.sh [host]   # Enrichment crawler health (default host: battlestats.online)
cd server && python manage.py backfill_clan_battle_data --realm na --batch 500 [--partition 0 --num-partitions 2]
```

`check_enrichment_crawler.sh` is a single SSH call reporting worker health, Redis lock state, batch throughput/ETA, errors, live progress, and periodic-task state. `backfill_clan_battle_data` fills per-player CB fields on `PlayerExplorerSummary` (only needed for players enriched before the Phase 3e enrichment CB fetch).

Background enrichment runs on the Celery `background` worker via `enrich_player_data_task`, self-chaining between batches and kickstarted every 15 min by Beat (`player-enrichment-kickstart`).

## Architecture

### Routing

- `/` — Landing: search, featured players/clans, discovery charts
- `/player/[playerName]` — Player detail (URL-encoded name, reload-safe)
- `/clan/[clanSlug]` — Clan detail (`<clan_id>-<optional-slug>`)
- `/ship/[shipSlug]` — Ship standings (`<ship_id>-<optional-slug>`). Snapshot-backed T10 leaderboard for the active realm (`GET /api/realm/<realm>/ship/<ship_id>/leaderboard`)
- `/umami` — Umami analytics dashboard (admin login)

### API proxy

Next.js rewrites `/api/*` to `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`). The frontend never calls the Wargaming API directly — all data flows through Django.

### Key backend modules

- `data.py` (~5.7K lines) — hydration, chart payloads, cache/hot-entity warming, distributions/correlations, `score_best_clans()`. Analytical queries use `_elevated_work_mem()`.
- `landing.py` — landing modes (Best, Random, Sigma, Popular) with published-cache + durable fallback
- `tasks.py` — Celery tasks: player/clan refresh, ranked incrementals, landing/distribution/correlation warming
- `signals.py` — registers all Celery Beat periodic tasks via `@receiver(post_migrate)`
- `views.py` — DRF views, `@api_view` endpoints, player/clan name suggestion autocompletes

### Key frontend patterns

- D3-based SVG chart components (TierSVG, TypeSVG, ActivitySVG, RankedWRBattlesHeatmapSVG, …)
- `app/context/ThemeContext.tsx` + `app/components/ThemeToggle.tsx` — dark/light/system theme, localStorage-persisted; `app/lib/chartTheme.ts` D3 colors; `app/globals.css` CSS custom properties (`--bg-*`/`--text-*`/`--accent-*`, `[data-theme="dark"]`)
- `app/lib/wrColor.ts` — shared win-rate → color mapping
- `app/lib/sharedJsonFetch.ts` — fetch with retry/cache + `chartFetchesInFlight` priority counter
- `app/lib/entityRoutes.ts` — URL encode/decode for player/clan routes
- `app/components/HeaderSearch.tsx` + `SearchModeToggle.tsx` — dual-mode player/clan search, debounced autocomplete, per-mode client cache
- Player classification icons (HiddenAccountIcon, EfficiencyRankIcon, LeaderCrownIcon, PveEnjoyerIcon, InactiveIcon, RankedPlayerIcon, ClanBattleShieldIcon, TopShipIcon) — inlined per surface in `PlayerDetail.tsx`, `ClanMembers.tsx`, `PlayerSearch.tsx` (NOT a shared component), driven by each row's `ship_badges`
- `ShipTopPlayerBanner.tsx` — per-fortnight T10 top-3 cards above Battle History, fed by `ship_badges` (`data.get_player_ship_badges`), links to `/ship/<id>`
- `ShipHonors.tsx` — durable per-ship career record from the append-only `ShipAward` ledger, fed by `ship_awards` (`data.get_player_ship_awards`)

### Caching strategy

- **Cache-first / lazy-refresh** — serve cached payload, queue background refresh; **durable fallback** keeps last-published copy past TTL; `X-Clan-Plot-Pending: true` signals pending warm-up
- **Warmers** (Beat periodic tasks): hot-entity (30 min), bulk entity loader (12h, uses `score_best_clans()`), landing page + distributions/correlations (55 min), startup warmer via Gunicorn `when_ready`
- **Search suggestions** — three-tier: client `Map` → Redis (10 min TTL) → Postgres `pg_trgm` GIN index; raw `ILIKE` (Django `icontains` bypasses trigram indexes). Player and clan endpoints; clan matches name OR tag
- **Clan battle seasons** — request-driven, Redis-only TTL; configured clans pre-warmed (`CLAN_BATTLE_WARM_CLAN_IDS`). Per-player CB stats persist to Postgres on `PlayerExplorerSummary`
- **Ship standings** — fully precomputed: `snapshot_ship_top_players_task` writes `ShipTopPlayerSnapshot` once per fixed 2-week season; `ship_leaderboard` serves via thin Redis read-cache
- Redis in production (3 GB cap, `allkeys-lru`); LocMemCache in tests

### Celery queues

Four queues with dedicated workers: **default** (`-c 3`, light API refreshes), **hydration** (`-c 5`, request-driven upstream refreshes), **background** (`-c 3`, warmers/incrementals/snapshots/enrichment), **crawls** (`-c 1`, the multi-day clan crawl + watchdog only).

Resilience: `CELERY_TASK_ACKS_LATE = True` (at-least-once delivery); RabbitMQ `consumer_timeout` disabled (long tasks); consumer watchdog systemd timer restarts zombie workers (alive process, 0 consumers); soft systemd deps (`Wants=`, not `Requires=`).

### Per-realm schedule striping

Per-realm periodic tasks are striped via `REALM_INTERVAL_OFFSETS = {'na': 0, 'eu': 1, 'asia': 2}` in `signals.py` so at most one realm is mid-cycle at a time. `_realm_crontab_for_cycle()` computes per-realm crontabs. Daily/weekly-cron families use `REALM_CRAWL_CRON_HOURS = {'eu': 0, 'na': 6, 'asia': 12}`. The rolling 6-hourly BattleObservation floor guarantees no active-7d player goes >`BATTLE_OBSERVATION_FLOOR_HOURS` without a fresh observation.

### Infra notes

- **HTTP/2** on the nginx 443 listeners (removes the HTTP/1.1 6-connection-per-origin limit)
- **Frontend fetch priority** — player pages fire 4 chart requests via `requestIdleCallback`; clan-member fetch deferred until warmup settles; `useClanMembers` backs off while charts in-flight
- **DB** — `CONN_HEALTH_CHECKS` enabled; analytical queries use elevated `work_mem` (`ANALYTICAL_WORK_MEM`, default 8MB) via `SET LOCAL`
- **SEO** — per-page `generateMetadata()`; dynamic `app/sitemap.ts` from `/api/sitemap-entities/`; `WebSite`+`SearchAction` JSON-LD; GA4 via `NEXT_PUBLIC_GA_MEASUREMENT_ID`

### Data models (`server/warships/models.py`)

Player, Clan, Ship, Snapshot (daily summaries), PlayerExplorerSummary, EntityVisitEvent/EntityVisitDaily, PlayerAchievementStat, DeletedAccount (GDPR blocklist), LandingPlayerBestSnapshot/LandingRecentPlayersSnapshot (landing fallbacks), MvPlayerDistributionStats, ShipTopPlayerSnapshot (ephemeral current standing per ship per season — pruned; backs `/ship/<id>` + profile badges), ShipAward (append-only career ledger — never pruned; backs Ship Honors), StreamerSubmission.

Battle-history pipeline: BattleObservation (raw `ships/stats/` JSON), BattleEvent (per-event deltas + Phase 7 widening columns), PlayerDailyShipStats (per-day per-ship aggregate), PlayerWeekly/Monthly/YearlyShipStats (period rollup tiers, populated only when the period writer is reactivated).

## Team Doctrine (Pre-commit Requirements)

**Read `agents/knowledge/agentic-team-doctrine.json` before planning or executing multi-step work** — it holds the authoritative decision rules, pre-commit checklist, and quality gates.

Every commit must: (1) update durable docs for new behavior/contracts/state; (2) reconcile uncertain docs against live code/tests; (3) keep touched behavior under automated test coverage; (4) archive superseded runbooks to `agents/runbooks/archive/`; (5) update contract docs + API tests when an endpoint/payload changes; (6) reconcile any runbook/spec being implemented.

**Decision rules:** smallest safe vertical slice; correctness before optimization; preserve user-facing behavior unless the task changes it; avoid unbounded polling/fan-out/retry loops; avoid new browser-triggered WG API calls when stored data exists; avoid large unscoped refactors during feature delivery.

## Claude Code Skills

Project skills live in `.claude/skills/<name>/SKILL.md`, auto-loaded on trigger phrases:

- **`doctrine-precommit`** ("ready to commit", "doctrine check") — runs the pre-commit checklist against the diff. Read-only.
- **`release-gate`** ("run the release gate") — runs the lean release gate in parallel. Read-only.
- **`runbook-author`** ("write a runbook for X") — creates a runbook with project conventions. Stages.
- **`runbook-archive`** ("archive this runbook") — `git mv`s to `archive/`, updates `doc_registry.json`. Stages.
- **`deploy-droplet`** ("deploy frontend/backend", "ship to prod") — deploys then verifies. Mutates production.
- **`enrichment-status`** ("how's enrichment") — runs the crawler health check and interprets it. Read-only.

## Versioning

Semantic versioning with root `VERSION` as the single source of truth, surfaced in the client footer at build time via `NEXT_PUBLIC_APP_VERSION`.

- **patch** — bug fixes, perf, docs · **minor** — features, new surfaces, UX changes · **major** — breaking model/API/UX changes
- Commit prefixes (Conventional Commits): `feat:` (minor), `fix:`/`perf:`/`refactor:`/`docs:`/`chore:`/`test:` (patch); append `!` for breaking (major)
- Releases cut with `./scripts/release.sh`; `patch` may skip the release gate, `minor`/`major` run it first

### MANDATORY: Rebuild client after every version bump

`NEXT_PUBLIC_APP_VERSION` is captured at frontend **build time**, so a `release.sh` bump alone leaves the production footer on the old version. After **every** bump (even backend-only), run `./client/deploy/deploy_to_droplet.sh battlestats.online`. Non-negotiable.

## Environment

### Server env files (`server/`)

- `.env` — non-secret connection values (DB_HOST, DB_ENGINE, DJANGO_ALLOWED_HOSTS)
- `.env.secrets` — secrets (WG_APP_ID, DB_PASSWORD, DJANGO_SECRET_KEY)
- `.env.cloud` / `.env.secrets.cloud` — cloud database overrides

### Server runtime env (defaults in parentheses)

Cache/warming:
- `HOT_ENTITY_PINNED_PLAYER_NAMES` (empty), `HOT_ENTITY_PLAYER_LIMIT`/`HOT_ENTITY_CLAN_LIMIT` (20/10)
- `RECENTLY_VIEWED_PLAYER_LIMIT` (10), `RECENTLY_VIEWED_WARM_MINUTES` (60), `WARM_CACHES_ON_STARTUP` (1)
- `CLAN_BATTLE_WARM_CLAN_IDS`, `BEST_CLAN_EXCLUDED_IDS`, `ANALYTICAL_WORK_MEM` (8MB)

Crawlers/refresh (`ENABLE_CRAWLER_SCHEDULES`=1 in prod is the master kill switch):
- `CLAN_CRAWL_SCHEDULE_HOUR`/`_MINUTE` (3/0), `CLAN_CRAWL_WATCHDOG_MINUTES` (5)
- `PLAYER_REFRESH_INTERVAL_MINUTES` (180); tier staleness `PLAYER_REFRESH_HOT/ACTIVE/WARM_STALE_HOURS` (12/24/72)
- `RANKED_REFRESH_INTERVAL_MINUTES` (120)
- BattleObservation floor: `BATTLE_OBSERVATION_FLOOR_HOUR`/`_MINUTE` (1/15), `_HOURS` (8), `_LIMIT`/`_DELAY` (3000/0.3), `_CRAWL_DELAY`/`_CRAWL_LIMIT` (0.8/falls back to LIMIT — floor coexists with crawls instead of skipping)
- `CELERY_BROKER_HEARTBEAT` (0; rely on TCP keepalive)

Enrichment:
- `ENRICH_REALMS` (all), `ENRICH_BATCH_SIZE` (500), `ENRICH_MIN_PVP_BATTLES` (500), `ENRICH_MIN_WR` (48.0), `ENRICH_DELAY` (0.2), `ENRICH_PAUSE_BETWEEN_BATCHES` (10)

Battle-history pipeline (phased gates, all default 0):
- `BATTLE_HISTORY_CAPTURE_ENABLED` (write BattleObservation/BattleEvent as a side-effect of `update_battle_data`)
- `BATTLE_HISTORY_ROLLUP_ENABLED` + `_HOUR`/`_MINUTE` (4/30) (fill PlayerDailyShipStats + nightly rebuild)
- `BATTLE_HISTORY_API_ENABLED` (exposes `GET /api/player/<name>/battle-history?days=N`, 404 when off)
- `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED` + `_REALMS` (`na`) (third WG call `seasons/shipstats/`, ranked-mode events)
- `BATTLE_TRACKING_PLAYER_NAMES`/`_POLL_SECONDS` (60) — incremental-battle PoC dispatcher

Ship badges / standings (master gate `SHIP_BADGE_SNAPSHOT_ENABLED`=0):
- `SHIP_BADGE_MIN_BATTLES` (15), `SHIP_BADGE_MIN_SHIP_POPULATION` (20), `SHIP_BADGE_LIST_SIZE` (15), `SHIP_BADGE_TOP_N` (3), `SHIP_BADGE_TIERS` (default `10`; prod pins `8,9,10`; legacy `SHIP_BADGE_TIER` fallback), `SHIP_BADGE_RETENTION_DAYS` (30)
- Ranking: composite of win-rate/damage/kills z-scores with empirical-Bayes shrinkage — `SHIP_BADGE_PRIOR_BATTLES` (50), `SHIP_BADGE_PRIOR_WR` (0.5), weights `SHIP_BADGE_WEIGHT_WINS`/`_DAMAGE`/`_KILLS` (0.5/0.35/0.15). Read at task-call time (re-tune without redeploy)
- `SHIP_BADGE_SNAPSHOT_DAY_OF_WEEK`/`_HOUR` (1 Mon/2). Fixed non-overlapping 2-week seasons anchored to `SHIP_SEASON_EPOCH` (Mon 11 May 2026 UTC) in `data.py`, mirrored by `client/app/lib/shipSeason.ts`. Board/badges show the most recently completed season; task self-gates on `is_season_boundary()`. Backfill: `python manage.py backfill_ship_seasons --wipe`

Local-dev only: `BATTLESTATS_DISABLE_LIVE_REFRESH` (serve stale snapshots, no live WG refresh), `BATTLESTATS_ENABLE_STALE_RECENT_PLAYERS` (landing fallback ordering without the battle-history pipeline).

See `agents/runbooks/` for the rationale, dates, and incident history behind these settings.

### Client env

- `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`), `NEXT_PUBLIC_GA_MEASUREMENT_ID` (optional)

### Umami analytics

Standalone Next.js app (v2.20.2) on port 3002 (`127.0.0.1`) behind nginx at `/umami/`; dashboard + admin API restricted to a home-IP allowlist (collection endpoints public). Uses the shared managed Postgres (separate `umami` DB, least-privilege `umami_app` role). Bootstrap: `umami/deploy/bootstrap_umami.sh`. Custom events via `client/app/lib/umami.ts` `trackEvent(name, data)` — keep names kebab-case and properties low-cardinality. See `agents/runbooks/runbook-umami-hardening-2026-06-02.md`.

### Docker ports

8888 Django/Gunicorn · 3001 Next.js (Docker dev) · 3002 Umami (prod only) · 15672 RabbitMQ management
