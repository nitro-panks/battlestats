# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

**This file is a dispatch table, not an encyclopedia.** It is always-loaded context for every session, capped at ~1,500 words by `scripts/check_claude_md.sh` (pre-commit). When you learn something durable, put it in a runbook and leave a pointer here. Re-slim procedure: `agents/runbooks/runbook-claude-md-durability.md`.

## Permissions & Autonomy

Operate autonomously. Do not pause for confirmation on: file reads/edits/creation/deletion in this repo; git operations (add, commit, branch, checkout, rebase, push); tests, linters, builds, dev servers; shell commands (curl, npm, npx, python, pip, pipenv, docker compose, ssh); deploy scripts in `client/deploy/` and `server/deploy/`; dependency installs; database migrations.

Only confirm before: force-pushing to main, dropping database tables, or deleting remote branches.

## Project

Battlestats is a World of Warships player and clan statistics platform. Live at https://battlestats.online. Version is in `VERSION` at the repo root (semver, surfaced in the client footer).

- **Frontend**: Next.js 16 (App Router) + React 18 + Tailwind + D3 charts — `client/`
- **Backend**: Django 6 + DRF + Celery (RabbitMQ + Redis) + PostgreSQL — `server/` (`django==6.0.7` since 2026-07-30, pinned in `server/requirements.txt`)
- **Agents**: markdown personas, knowledge base, and operational runbooks — `agents/` (not a runtime)

## Common Commands

```bash
docker compose up -d                                      # Full stack
./run_test_suite.sh                                       # Lean release gate (docker)
./client/deploy/deploy_to_droplet.sh battlestats.online   # Deploy frontend
./server/deploy/deploy_to_droplet.sh battlestats.online   # Deploy backend
./scripts/release.sh patch|minor|major                    # Bump VERSION, commit, tag, push
./server/scripts/switch_db_target.sh cloud|local          # Switch DB target
```

```bash
cd server
DJANGO_SECRET_KEY=k DB_ENGINE=sqlite3 python -m pytest warships/tests/ --nomigrations --tb=short
python manage.py makemigrations && python manage.py migrate
```

```bash
cd client
npm run dev          # port 3000
npm run build && npm run lint
npm test             # lean frontend release gate
```

**Test-harness invariant.** `server/conftest.py` defaults `CELERY_BROKER_URL=memory://` for every run. Without it, task-dispatching tests each pay a broker connection-retry timeout — **1020s vs 5.1s** for the same suite, with no failure to show for it. `DJANGO_SECRET_KEY` cannot be set there: pytest-django imports settings before conftest runs, so it must come from the command line.

**Python 3.12 everywhere** (CI, `server/Dockerfile`, local venv). It is the ceiling for the pinned celery/kombu/billiard/gunicorn/redis set, not caution. Upgrade analysis, and the `npm audit` production-dependency gate: `agents/runbooks/runbook-django-6-upgrade-2026-07-30.md`.

**Deploys and the release gate run from any worktree** — gitignored material resolves from the main checkout, and prerequisites are validated up front. `client/node_modules` is the exception; run `npm ci`. Runbook: `agents/runbooks/runbook-worktree-local-prereqs-2026-08-13.md`.

## Operations

```bash
./server/scripts/check_enrichment_crawler.sh [host]                    # crawler health (or /enrichment-status)
cd server && python manage.py backfill_clan_battle_data --realm na --batch 500
cd server && python manage.py populate_shiptool_codes [--dry-run]      # run on WoWS patches adding ships
```

- **Background enrichment** runs on the Celery `background` worker, self-chaining, kickstarted by Beat. Pool maintenance and the daily/weekly reclassify bucket families: `agents/runbooks/runbook-enrichment-pool-maintenance-2026-06-09.md`, `agents/runbooks/runbook-post-deploy-verification-2026-08-07.md`.
- **Two unattended emails** run off **systemd timers** on the droplet (not cron, not Beat), both stdlib-only and fail-loud: the ops digest (11:30 UTC, **exception-only** — a deterministic Python verdict decides whether to mail at all) and the traffic digest (**Mondays 10:30 UTC**, the completed Mon-Sun week from Umami; weekly since 2026-08-25). `agents/runbooks/runbook-ops-email-exception-only-2026-08-09.md`, `agents/runbooks/runbook-weekly-traffic-email-2026-08-09.md`.

## Architecture

### Routing

- `/` — Landing: search, filter-correlated ship treemap, inline ship leaderboard. **Static — keep it that way**; reading request state here costs the prerender on the most-hit route.
- `/player/[playerName]` · `/clan/[clanSlug]` (`<clan_id>-<slug>`) · `/ship/[shipSlug]` (`<ship_id>-<slug>`) · `/umami`
- `/ships/[bucket]` (`t10-battleships`) — shareable ship standings, 15 indexable buckets. On this route **the URL outranks localStorage and is never written back**, or a shared link renders the recipient's view instead of the sharer's: `agents/runbooks/runbook-shareable-ship-leaderboard-2026-08-20.md`.

### API proxy

Next.js rewrites `/api/*` to `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`). **The frontend never calls the Wargaming API directly** — all data flows through Django.

### Key backend modules

`data.py` (~7.7K lines) — hydration, chart payloads, cache/warming, distributions/correlations; analytical queries use `_elevated_work_mem()`. `tasks.py` — Celery tasks. `signals.py` — registers all Beat periodic tasks via `@receiver(post_migrate)`, so schedule changes need a migrate. `views.py` — DRF views and `@api_view` endpoints. (`landing.py` was deleted in 3.0 with the featured-boards decommission: `agents/runbooks/runbook-landing-featured-boards-decommission-2026-06-22.md`.)

### Key frontend patterns

A file index, not a spec. Charts: `client/app/components/*SVG.tsx`. Theme tokens: `app/lib/chartTheme.ts` + `app/globals.css` (`--bg-*`/`--text-*`/`--accent-*`, `[data-theme="dark"]`). WR colour: `app/lib/wrColor.ts`. URL encode/decode: `app/lib/entityRoutes.ts`. Search: `HeaderSearch.tsx` + `SearchModeToggle.tsx`.

- **Request layer** — all `/api/` traffic flows through `app/lib/sharedJsonFetch.ts`: dedup, SWR cache, priority queue (cap 6), 429 backoff, degradation monitor, per-page cancellation. Design: `agents/runbooks/runbook-player-fetch-orchestration-2026-06-21.md`.
- **Player-page charts + clan roster** — the tier figure, its Ships-tab drill-down, `ClanActivityRoster`, `ActivityIcon`, and the classification icons: `agents/runbooks/runbook-player-page-charts-and-roster-2026-08-15.md`.
- **Locale (en/ko/ja)** — `app/context/LocaleContext.tsx`; `?lang=` > `bs-locale` > autodetect > English. Autodetect **never persists**, and its mapping is duplicated in `app/lib/bootScript.ts` — change one, change both. Spec: `agents/work-items/client-locale-toggle-spec.md`.
- **Ship surfaces** — `ShipRouteView.tsx`, `ShipToolLink.tsx` (`agents/runbooks/runbook-shiptool-integration-2026-06-22.md`), `ShipTopPlayerBanner.tsx`.

### Caching strategy

Cache-first / lazy-refresh with a durable last-published fallback. Policy: `agents/runbooks/spec-cache-first-lazy-refresh-policy-2026-03-19.md`. Families and TTLs: `agents/runbooks/runbook-cache-audit.md`. Redis in production (3 GB, `allkeys-lru`); LocMemCache in tests.

**The load-bearing rule: no `/api/fetch/*` endpoint may block the request thread** — not on the WG API, and **not on a heavy DB aggregation either**. A cold path serves a durable `:published` copy, or `pending: true` + an `X-*-Pending` header, and queues a warm. **Clients must branch on `pending` before payload length** — a pending payload carries no rows and otherwise reads as "no data". (The ShipStats combat profile violated this until 2026-08-12: a 36s aggregation on the request thread blew the 25s gunicorn timeout and returned a 500 with an empty body.) **A `task.delay()` is a blocking network call too** — enqueue from a request only via `warships/broker.py`: `agents/runbooks/runbook-broker-publish-request-thread-2026-09-09.md`.

Ship-standings pipeline — nightly snapshot, warm-before-evict, the WR-percentile buckets, and the daily-rollup source: start at `agents/runbooks/runbook-ship-leaderboard-architecture-2026-06-18.md`. **A cold *fresh* key there never means an error** — the durable copy is serving, so staleness is silent by design; check warm completions, not the page.

### Celery queues

Five queues with dedicated workers: **default** (`-c 3`), **hydration** (`-c 3`), **background** (`-c 3`, warmers/snapshots/enrichment), **floor** (`-c 2`), **crawls** (`-c 1`). `CELERY_TASK_ACKS_LATE = True`; RabbitMQ `consumer_timeout` disabled; a consumer watchdog restarts zombie workers. Assessment: `agents/runbooks/runbook-celery-queue-strategy.md`.

### Per-realm schedule striping

Per-realm periodic tasks are striped so at most one realm is mid-cycle. Mechanism, and the index of engines riding on it (observation floor, daily snapshots, lapsed-player recapture, hot-players, enrichment reclassify, clan crawl): `agents/runbooks/runbook-realm-schedule-striping-2026-08-15.md`.

**Two non-obvious reads:** for the recapture sweep, check `partial` before `scanned` — a truncated pass looks exactly like a healthy one; and `aborted` is a separate axis from `partial`.

### Infra notes

App droplet **2 vCPU / 8 GB**; managed Postgres **2 vCPU / 4 GB** (PG 18). **Do not plan against a 1-vCPU DB** — that assumption is stale. Sizing: `agents/runbooks/ops-infra-resources.md`. HTTP/2 on the nginx 443 listeners. `CONN_HEALTH_CHECKS` enabled. SEO, dynamic OG cards, Umami analytics and the durable visitor id: `agents/runbooks/runbook-seo.md`, `agents/runbooks/runbook-audience-growth-instrumentation-2026-07-29.md`.

**Korean outreach** — our only real language community. Venue map, the board rules, the measured register rules (they say **공방** not 랜덤전, **10티어** not 티어10, **전투** not 판), and the monthly ASIA post generator (`scripts/monthly_asia_post.py`): `agents/runbooks/runbook-korean-community-outreach.md`. **Never automate a forum login**; the operator posts, the agent translates.

### Data models (`server/warships/models.py`)

Player, Clan, Ship, Snapshot, PlayerExplorerSummary, EntityVisitEvent/Daily, PlayerAchievementStat, DeletedAccount, MvPlayerDistributionStats, ShipTopPlayerSnapshot, StreamerSubmission, Feedback, HotPlayer, RankedSeason, ClanBattleSeason.

Battle-history pipeline: BattleObservation → BattleEvent → PlayerDailyShipStats → ShipPopDailyAgg. **Retention `BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS` prod=105 since 2026-07-24, pinned in `server/deploy/deploy_to_droplet.sh`** — sized to sustain a 90-day rolling read, so **retention reduction is not a disk lever**; the binding constraint is the player pool (`agents/work-items/db-growth-capacity-2026-08-05.md`).

## Team Doctrine (Pre-commit Requirements)

**Read `agents/knowledge/agentic-team-doctrine.json` before planning or executing multi-step work.** It is authoritative for decision rules, the pre-commit checklist, quality gates, and the rules governing this file. Do not restate it here.

## Claude Code Skills

Project skills live in `.claude/skills/<name>/SKILL.md`, auto-loaded on trigger phrases. Read-only unless noted: `doctrine-precommit`, `release-gate`, `enrichment-status`, `observation`, `crawl-yield`, `recapture`, `feedback`, `event-check`, `crawl`. Mutating: `deploy-droplet` (production), `ops-alert` (investigates the morning ops mail, then remedies), `warm-damage-averages` (cache), `runbook-author` / `runbook-archive` (stage files).

## Versioning

Root `VERSION` is the single source of truth, surfaced in the client footer at build time via `NEXT_PUBLIC_APP_VERSION`.

- **patch** — fixes, perf, docs · **minor** — features, UX changes · **major** — breaking model/API/UX changes
- Conventional Commits: `feat:` (minor); `fix:`/`perf:`/`refactor:`/`docs:`/`chore:`/`test:` (patch); `!` for breaking
- `minor`/`major` run the release gate first; `patch` may skip it

### MANDATORY: Rebuild client after every version bump

`NEXT_PUBLIC_APP_VERSION` is captured at frontend **build time**, so a `release.sh` bump alone leaves the production footer on the old version. After **every** bump, even backend-only, run `./client/deploy/deploy_to_droplet.sh battlestats.online`. Non-negotiable.

## Environment

Env files, the full runtime env-var catalog, Umami and Docker ports live in `agents/runbooks/ops-env-reference.md`.

- Values are canonical in **Pass**; on-disk env files are generated from it. Update Pass and regenerate — do not hand-edit a file as the source of truth.
- **A code default is not a live value.** When quoting one, name its authority: value, date set, and the file that pins it. Prod pins live in `server/deploy/deploy_to_droplet.sh`.
- Docker ports: 8888 Django · 3001 Next.js (dev) · 3002 Umami (prod) · 15672 RabbitMQ.
