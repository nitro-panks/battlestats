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
python -m pytest warships/tests/ --tb=short  # Full release gate (~600 tests, ~15s on Postgres / ~7s sqlite)
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

Background enrichment runs on the Celery `background` worker via `enrich_player_data_task`, self-chaining between batches and kickstarted every 15 min by Beat (`player-enrichment-kickstart`). Two daily DB-only Beat families keep the `pending` pool complete (both **coexist with crawls**, kill switch `ENRICHMENT_POOL_MAINTENANCE_ENABLED`): `enrichment_pool_maintenance_task` (`enrichment-pool-maintenance`, 08:17 UTC) re-queues `empty` false-negatives with a per-row cooldown (`ENRICHMENT_EMPTY_RETRY_AFTER_DAYS`); `enrichment_reclassify_drift_task` (`enrichment-reclassify-drift-{realm}`, striped na/eu/asia 08:20/08:40/09:00) does an **incremental** per-realm `reclassify_enrichment_status --recent-hours 25` (skipped_* drift rescue scoped to recently-fetched rows via the `player_last_fetch_idx` index, ~6–11 min/realm). The full-catalog reclassify (one-time backlog + pure-calendar inactivity drift) stays a **supervised manual op** (~36 min/run). Runbook: `agents/runbooks/runbook-enrichment-pool-maintenance-2026-06-09.md`. With a tight `ENRICH_MAX_INACTIVE_DAYS` (prod=7), `enrich_player_on_view_task` (kill switch `ENRICH_ON_VIEW_ENABLED`) fast-paths a returning, now-eligible player the moment a profile view refreshes them, instead of waiting for the daily drift reclassify (see `ops-env-reference.md`).

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
- `ShipRouteView.tsx` — the `/ship/<id>` leaderboard page: masthead ship identity (class glyph + tier/class/nation chips + Premium marker via `app/lib/shipIdentity.ts`), restrained champion/podium treatment (`--metal-gold`/`--champion-tint`/`--champion-edge` tokens, `TopShipIcon size="podium"`), metric hierarchy, and a responsive desktop-table / mobile-card split. Identity is payload-only (no new fetch); presentation refresh spec: `agents/work-items/ship-leaderboard-ux-refresh-spec.md`

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

Per-realm periodic tasks are striped via `REALM_INTERVAL_OFFSETS = {'na': 0, 'eu': 1, 'asia': 2}` in `signals.py` so at most one realm is mid-cycle at a time. `_realm_crontab_for_cycle()` computes per-realm crontabs. Daily/weekly-cron families use `REALM_CRAWL_CRON_HOURS = {'eu': 0, 'na': 6, 'asia': 12}`. The rolling BattleObservation floor (cadence `BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES`, per-realm striped) guarantees no active-7d player goes >`BATTLE_OBSERVATION_FLOOR_HOURS` without a fresh observation. The daily-snapshot engine (`snapshot_active_players_task`, per-realm striped, **coexists with crawls** — does not defer) writes a daily `Snapshot` row for every active player via bulk account/info; kill switch `SNAPSHOT_ACTIVE_PLAYERS_ENABLED`. Runbook: `agents/runbooks/runbook-daily-active-snapshots-2026-06-09.md`. The hot-players engagement queue (kill switch `HOT_PLAYERS_ENABLED`) lets *durable visitor interest* — not the player's own activity/skill — qualify a player for guaranteed daily capture: `maintain_hot_players_task` (DB-only daily — promote/evict the `HotPlayer` set by view-recurrence across days over `EntityVisitDaily`, with hysteresis + a per-realm `HOT_PLAYERS_MAX` cap) and `capture_hot_player_observations_task` (per-realm striped, `background` queue, **coexists with crawls**, **skip-if-fresh** against the floor) which guarantees a daily observation + gap-free `Snapshot` for the hot set. Runbook: `agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md`.

### Infra notes

- **Resources** — app droplet **2 vCPU / 8 GB**; managed Postgres **2 vCPU / 4 GB** (`db-s-2vcpu-4gb`, PG 18, ~97 usable connections), resized up from 1 vCPU / 2 GB on 2026-05-28. **Do not plan against a 1-vCPU DB** — that assumption is stale; `system_load15` saturates around 2. Full sizing + re-verify recipe: `agents/runbooks/ops-infra-resources.md`.
- **HTTP/2** on the nginx 443 listeners (removes the HTTP/1.1 6-connection-per-origin limit)
- **Frontend fetch priority** — player pages fire 4 chart requests via `requestIdleCallback`; clan-member fetch deferred until warmup settles; `useClanMembers` backs off while charts in-flight
- **DB** — `CONN_HEALTH_CHECKS` enabled; analytical queries use elevated `work_mem` (`ANALYTICAL_WORK_MEM`, default 8MB) via `SET LOCAL`
- **SEO** — per-page `generateMetadata()`; dynamic `app/sitemap.ts` from `/api/sitemap-entities/`; `WebSite`+`SearchAction` JSON-LD; analytics via Umami + first-party entity tracking

### Data models (`server/warships/models.py`)

Player, Clan, Ship, Snapshot (daily summaries), PlayerExplorerSummary, EntityVisitEvent/EntityVisitDaily, PlayerAchievementStat, DeletedAccount (GDPR blocklist), LandingPlayerBestSnapshot (landing Best fallback), MvPlayerDistributionStats, ShipTopPlayerSnapshot (ephemeral current standing per ship per season — pruned; backs `/ship/<id>` + profile badges), ShipAward (append-only career ledger — never pruned; backs Ship Honors), StreamerSubmission, HotPlayer (engagement capture queue — durable visitor-interest membership + audit, feeding the daily hot-player observation/snapshot sweep).

Battle-history pipeline: BattleObservation (raw `ships/stats/` JSON), BattleEvent (per-event deltas + Phase 7 widening columns), PlayerDailyShipStats (per-day per-ship aggregate), PlayerWeekly/Monthly/YearlyShipStats (period rollup tiers, populated only when the period writer is reactivated).

## Team Doctrine (Pre-commit Requirements)

**Read `agents/knowledge/agentic-team-doctrine.json` before planning or executing multi-step work** — it holds the authoritative decision rules, pre-commit checklist, and quality gates.

Every commit must: (1) update durable docs for new behavior/contracts/state; (2) reconcile uncertain docs against live code/tests; (3) keep touched behavior under automated test coverage; (4) archive superseded runbooks to `agents/runbooks/archive/`; (5) update contract docs + API tests when an endpoint/payload changes; (6) reconcile any runbook/spec being implemented.

**Decision rules:** smallest safe vertical slice; correctness before optimization; preserve user-facing behavior unless the task changes it; avoid unbounded polling/fan-out/retry loops; avoid new browser-triggered WG API calls when stored data exists; avoid large unscoped refactors during feature delivery.

**Keep this file slim** — it is always-loaded context. No env-var catalogs, deep architecture, or inline workflows here (use `agents/runbooks/` + `.claude/skills/`); the `claude_md_rules` in the doctrine JSON are enforced at pre-commit. Full re-slim procedure: `agents/runbooks/runbook-claude-md-durability.md`.

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

Env files, the full runtime env-var catalog (defaults), Umami, and Docker ports live in `agents/runbooks/ops-env-reference.md`. Quick orientation:

- Server secrets in `server/.env.secrets`; cloud overrides in `*.cloud` files.
- Master kill switches: `ENABLE_CRAWLER_SCHEDULES` (crawlers), `BATTLE_HISTORY_*_ENABLED` (battle-history phases), `SHIP_BADGE_SNAPSHOT_ENABLED` (ship standings), `ENRICHMENT_POOL_MAINTENANCE_ENABLED` (daily enrichment pool reclassify/retry).
- Client: `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`).
- Docker ports: 8888 Django · 3001 Next.js (dev) · 3002 Umami (prod) · 15672 RabbitMQ.
