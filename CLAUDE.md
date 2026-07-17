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
python -m pytest warships/tests/ --tb=short  # Full release gate (~820 tests, ~15s on Postgres / ~7s sqlite)
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
cd server && python manage.py populate_shiptool_codes [--dry-run]   # refresh Ship.shiptool_code (run on WoWS patches that add ships)
```

`check_enrichment_crawler.sh` is a single SSH call reporting worker health, Redis lock state, batch throughput/ETA, errors, live progress, and periodic-task state. `backfill_clan_battle_data` fills per-player CB fields on `PlayerExplorerSummary` (only needed for players enriched before the Phase 3e enrichment CB fetch).

Background enrichment runs on the Celery `background` worker via `enrich_player_data_task`, self-chaining between batches and kickstarted every 15 min by Beat (`player-enrichment-kickstart`). Two daily DB-only Beat families keep the `pending` pool complete (both **coexist with crawls**, kill switch `ENRICHMENT_POOL_MAINTENANCE_ENABLED`): `enrichment_pool_maintenance_task` (`enrichment-pool-maintenance`, 08:17 UTC) re-queues `empty` false-negatives with a per-row cooldown (`ENRICHMENT_EMPTY_RETRY_AFTER_DAYS`); `enrichment_reclassify_drift_task` (`enrichment-reclassify-drift-{realm}`, striped na/eu/asia 08:20/08:40/09:00) does an **incremental** per-realm `reclassify_enrichment_status --recent-hours 25` (skipped_* drift rescue scoped to recently-fetched rows via the `player_last_fetch_idx` index, ~6–11 min/realm observed). The full-catalog reclassify (one-time backlog + pure-calendar inactivity drift) stays a **supervised manual op** (~36 min/run). Runbook: `agents/runbooks/runbook-enrichment-pool-maintenance-2026-06-09.md`. With a tight `ENRICH_MAX_INACTIVE_DAYS` (prod=7), `enrich_player_on_view_task` (kill switch `ENRICH_ON_VIEW_ENABLED`) fast-paths a returning, now-eligible player the moment a profile view refreshes them, instead of waiting for the daily drift reclassify (see `ops-env-reference.md`).

## Architecture

### Routing

- `/` — Landing: search, a filter-correlated ship treemap (mirrors the ship-leaderboard tier/type/WR selection), inline ship leaderboard
- `/player/[playerName]` — Player detail (URL-encoded name, reload-safe)
- `/clan/[clanSlug]` — Clan detail (`<clan_id>-<optional-slug>`)
- `/ship/[shipSlug]` — Ship standings (`<ship_id>-<optional-slug>`). Snapshot-backed T10 leaderboard for the active realm (`GET /api/realm/<realm>/ship/<ship_id>/leaderboard`)
- `/umami` — Umami analytics dashboard (admin login)

### API proxy

Next.js rewrites `/api/*` to `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`). The frontend never calls the Wargaming API directly — all data flows through Django.

### Key backend modules

- `data.py` (~7.5K lines) — hydration, chart payloads, cache/hot-entity warming, distributions/correlations. Analytical queries use `_elevated_work_mem()`.
- `landing.py` — landing payload helpers + published-cache/durable fallback (ship treemap / tier-type list). The featured Best-players/Best-clans **boards were decommissioned 2026-06-22** (near-zero clicks; landing is now search → ship treemap → ship leaderboard) and the **backend was fully removed in 3.0**: the `landing_players`/`landing_clans`/`landing_best_warmup`/`landing_activity_attrition`/`analytics_top_entities` endpoints, `score_best_clans()`, all Best/Popular landing builders + warmers, and the `LandingPlayerBestSnapshot` model (table dropped) are gone. Runbook: `agents/runbooks/runbook-landing-featured-boards-decommission-2026-06-22.md`
- `tasks.py` — Celery tasks: player/clan refresh, ranked incrementals, landing/distribution/correlation warming
- `signals.py` — registers all Celery Beat periodic tasks via `@receiver(post_migrate)`
- `views.py` — DRF views, `@api_view` endpoints, player/clan name suggestion autocompletes

### Key frontend patterns

- D3-based SVG chart components (TierSVG, TypeSVG, ActivitySVG, RankedWRBattlesHeatmapSVG, …)
- `app/context/ThemeContext.tsx` + `app/components/ThemeToggle.tsx` — dark/light/system theme, localStorage-persisted; `app/lib/chartTheme.ts` D3 colors + shared chart helpers (`wrColorByRatio`, `formatCompactCount`, `resolveChartWidth`); `app/globals.css` CSS custom properties (`--bg-*`/`--text-*`/`--accent-*`, `[data-theme="dark"]`)
- `app/lib/wrColor.ts` — shared win-rate → color mapping
- `app/lib/sharedJsonFetch.ts` — the client request layer all `/api/` traffic flows through: in-flight dedup + settled SWR cache + opt-in retry, ref-counted per-caller cancellation (`signal`, `isAbortError`), 15s per-attempt timeout, 429/Retry-After + jittered-exponential backoff, a global priority concurrency queue (`app/lib/requestQueue.ts`, cap 6, `critical`/`high`/`low`), and telemetry (`app/lib/fetchTelemetry.ts`). A degradation monitor (`app/lib/degradationMonitor.ts` + `DegradationContext` + `ConnectionHint`) consumes telemetry → drops the cap 6→2 + slows polls + shows a subtle "connection slow" hint while degraded. `PlayerRequestScopeContext` carries one per-(player,realm) abort signal so nav/realm-switch cancels the whole page's requests. Full architecture: `agents/runbooks/runbook-player-fetch-orchestration-2026-06-21.md`. (`chartFetchesInFlight` is the live warmup-gate counter.)
- `app/lib/entityRoutes.ts` — URL encode/decode for player/clan routes
- `app/components/HeaderSearch.tsx` + `SearchModeToggle.tsx` — dual-mode player/clan search, debounced autocomplete, per-mode client cache
- Player classification icons (HiddenAccountIcon, EfficiencyRankIcon, LeaderCrownIcon, PveEnjoyerIcon, InactiveIcon, RankedPlayerIcon, ClanBattleShieldIcon, TopShipIcon) — each a shared single-purpose component file imported across surfaces. PveEnjoyerIcon is **hidden everywhere** (deprecation candidate, 2026-07-15) behind the `PVE_ENJOYER_ICON_ENABLED` kill switch exported from `PveEnjoyerIcon.tsx`; the component, `is_pve_player` payload flag, and backend classification remain intact — flip the constant to restore it. RankedPlayerIcon and ClanBattleShieldIcon both carry **current-season semantics** (server-computed flags; ranked spec `agents/work-items/ranked-enjoyer-current-season-spec.md`, CB runbook `agents/runbooks/runbook-cb-icon-current-season-2026-07-15.md` — the CB shield means "logged clan battles in the current CB season", tinted by current-season WR; the Clan Battles tab opens on career 40/2 OR the same current-season criteria, so shield wearers always get the tab). The shared top-ship medal tail (`ship_badges` → up-to-3 `TopShipIcon`s) is factored into `TopShipBadges.tsx`.
- `ActivityIcon.tsx` — graded "rise-to-bed" recency icon keyed on `activity_bucket`. The backend still classifies five-way (`_classify_clan_member_activity`, payload contract unchanged) but the UI collapses to **three phases** via `collapseActivityBucket` (`clanMembersShared.ts`): sun Active ≤30d (`active_7d`+`active_30d`) → half-moon Cooling ≤180d (`cooling_90d`+`dormant_180d`) → bed Gone dark 181d+; labels/colors mirror the clan-chart legend. Accepts an explicit `bucket` or derives one from `daysSinceLastBattle` (`activityBucketFromDays`, same raw thresholds as the backend). Replaces the old `Nd idle` text + bed-only badge on the player-detail header. Both roster surfaces (clan page `ClanDetail` and the player page's `PlayerClanSection`) render `ClanActivityRoster` (2026-07-15, replacing the removed `ClanMembers` columns/stacked/inline layouts): one flowing name paragraph per collapsed phase, ✦ dividers, hidden accounts named but not linked, and the classification-badge tail per name. Presentation is per-surface (`phaseStyle`, 2026-07-16): the clan page groups one paragraph per collapsed phase under an icon+color header (`headers`); the player page's clan section splits the roster in two `text-base` blocks (`split`) — active members under the Active label, an `<hr>`, then everyone else in one unlabeled alphabetical block (the scatterplot above carries finer recency); each split block is a column grid sized to its member count (max 4 columns, 2 below `sm`), rows growing with clan size. Badge-dispatch logic (which classification icons render, in what order) lives in `ClanActivityRoster.tsx` for rosters and inlined in `PlayerDetail.tsx` for the player-header tray, driven by `ship_badges` / classification flags
- `ShipTopPlayerBanner.tsx` — current T10 top-3 cards above Battle History (rolling nightly window; badge tracks the current board generation — dropped the moment the player is displaced), fed by `ship_badges` (`data.get_player_ship_badges`), links to `/ship/<id>`
- `ShipRouteView.tsx` — the `/ship/<id>` leaderboard page: a de-cluttered masthead (2026-06-29) — ship name with an inline bullet-separated tier/class/nation line to its right + a ranking-method info tooltip that names the window + Premium marker via `app/lib/shipIdentity.ts` + right-aligned `ShipToolLink` (no class glyph, no provenance/cadence box — those duplicated the identity line and tooltip), restrained champion treatment (`--metal-gold`/`--champion-tint`/`--champion-edge` tokens, `TopShipIcon size="podium"`; no per-row podium divider), metric hierarchy, and a responsive desktop-table / mobile-card split. Identity is payload-only (no new fetch); presentation refresh spec: `agents/work-items/ship-leaderboard-ux-refresh-spec.md`
- `ShipToolLink.tsx` — external "View on Ship Tool" deep-link chip (logo on an always-light chip for dark-mode legibility) rendered next to the ship name on both the inline `ShipLeaderboard` drilldown and the `ShipRouteView` masthead. URL is `https://shiptool.st/params?S=<code>` where `<code>` is the ship payload's `shiptool_code` (e.g. Moskva→`RC110`); renders nothing when absent. The code is **derived, not scraped** — it's the WoWS GameParams index (e.g. `PRSC110`) passed through Ship Tool's own `createShortIndex` (`P<N>S<T><digits>` → `<N><T><digits>`, leading zeros stripped), populated server-side from WG Vortex by `populate_shiptool_codes`. The inline drilldown's old `· T<n> <class>` subtitle was removed (tier/type are already pinned in the selector bar above). Provenance + refresh: `agents/runbooks/runbook-shiptool-integration-2026-06-22.md`

### Caching strategy

- **Cache-first / lazy-refresh** — serve cached payload, queue background refresh; **durable fallback** keeps last-published copy past TTL; `X-Clan-Plot-Pending: true` signals pending warm-up. **No `/api/fetch/*` endpoint blocks the request thread on the WG API**: cold `ranked_data` and `player_clan_battle_seasons` serve `[]` + queue async + set `X-Ranked-Pending` / `X-Clan-Battle-Seasons-Pending` (the per-player CB request path passes `allow_remote_fetch=False`, skipping the WG fetch + persist so it never zero-clobbers the stored summary)
- **Warmers** (Beat periodic tasks): hot-entity (30 min), bulk entity loader (12h; warms only pinned + recently-viewed — the Best-* prewarm was removed with the landing-boards decommission), distributions + correlations + efficiency-rank snapshot (**daily**, `*_WARM_MINUTES=1440`; efficiency-rank is a daily Beat with its event-triggers neutered via `EFFICIENCY_RANK_EVENT_TRIGGER_ENABLED=0` — it was the #1 WAL hog), startup warmer via Gunicorn `when_ready`. Cadence-reduction rationale + confirmed cost-delta: `agents/runbooks/ops-env-reference.md`
- **Search suggestions** — three-tier: client `Map` → Redis (10 min TTL) → Postgres `pg_trgm` GIN index; raw `ILIKE` (Django `icontains` bypasses trigram indexes). Player and clan endpoints; clan matches name OR tag
- **Clan battle seasons** — request-driven, Redis-only TTL; configured clans pre-warmed (`CLAN_BATTLE_WARM_CLAN_IDS`). Per-player CB stats persist to Postgres on `PlayerExplorerSummary`
- **Clan roster idle freshness** — `clan_members` derives "X days idle" live from `last_battle_date`, which only the per-player refresh wrote (so cold long-tail members stayed frozen until viewed). On a cache miss the endpoint queues `refresh_clan_member_idle_task` (one bulk `account/info` for the whole roster, ~once/hour/clan), serves stored values now, and signals `X-Clan-Idle-Pending` so the `useClanMembers` poll picks up the corrected idle. The task writes **only** `last_battle_date` + `days_since_last_battle` (never `last_fetch`, which would suppress the real per-player full refresh)
- **Ship standings** — precomputed nightly: `snapshot_ship_top_players_task` rewrites `ShipTopPlayerSnapshot` each night over a trailing `SHIP_LEADERBOARD_WINDOW_DAYS` (30) window (no durable ledger); `ship_leaderboard` serves via thin Redis read-cache. Profile badges (`get_player_ship_badges` + bulk) anchor on the **realm's current generation** (latest `captured_on`, same as the board) — a player displaced off the board drops the badge immediately, not after the `SHIP_BADGE_RETENTION_DAYS` (5) row-retention window (fix 2026-07-08: the read previously keyed on the player's own latest row, so a dethroned #1 wore a stale badge until it pruned). The landing **filter-correlated ship treemap** + inline **tier-type list** both read `realm_ships_by_tier_type` (the treemap moved off `realm_top_ships` — now FE-idle — to mirror the leaderboard's tier/type/WR filters, 2026-07-01; runbook `agents/runbooks/runbook-landing-treemap-filter-correlation-2026-07-01.md`) and are **warm-before-evict**: their window-date-keyed fresh key rotates cold when the snapshot advances, so each also writes a window-independent durable `:published` key (`timeout=None`, write-new-then-overwrite) and on a cold fresh key serves that last-good payload + queues a warm (`queue_realm_top_ships_warm`) instead of blocking on the `BattleEvent` aggregation — the snapshot task chains the warmer so the new window warms immediately. Runbook: `agents/runbooks/runbook-shipleaderboard-warm-before-evict-2026-06-18.md`. The inline list's **WR filter** (`?wr_pct=50|25`, **default 50%**) re-pools each ship's stats over the top 50%/25% of its players by win rate (ship set unchanged — gated on full-population battles; only the numbers narrow). It's a heavy per-`(ship,player)` aggregation (~15–28s, over the 15s client timeout), so it is **never computed on the request thread**. **All** tier×type pct buckets are **pre-warmed nightly** per realm by `warm_realm_ships_pct_task` (chained from `warm_realm_top_ships_task`): it walks the grid serially (default bucket first), computes each at `wr_pct=50` (one query materializes both 50 & 25; since 3.1.1 each pct bucket **also writes its own durable `:published` fallback** — incl. empty buckets — so a cold fresh key serves last-good + queues a warm instead of `pending`), is **skip-if-warm** (the Beat + 2×/day-snapshot triggers collapse to one real pass/window), pauses `SHIP_LIST_WR_PCT_WARM_PAUSE_SECONDS` (default 5s) between buckets, and holds a per-realm lock (`SHIP_LIST_WR_PCT_WARM_LOCK_TIMEOUT`, 40min > 30min hard limit). The writer + the warmer's skip-if-warm check share one fresh-key builder (`_ships_by_fresh_cache_key`) so they can't drift. The **lazy fallback** (`warm_ships_by_pct_task` → `pending`/`X-Ships-WR-Pending` + client poll `ttlMs:0`) now fires only on a bucket's first-ever computation (no fresh key AND no published copy). [Strategy reversed from "warm default only, rest lazy" on 2026-06-23 — the per-bucket crunch was too much visitor burden.] Floors: ship-listing `SHIP_LIST_MIN_BATTLES` vs per-player-ranking `SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES` (default 15). Runbook: `agents/runbooks/runbook-ship-list-wr-percentile-2026-06-23.md`
- Redis in production (3 GB cap, `allkeys-lru`); LocMemCache in tests

### Celery queues

Five queues with dedicated workers: **default** (`-c 3`, light API refreshes), **hydration** (`-c 3`, request-driven upstream refreshes), **background** (`-c 3`, warmers/incrementals/snapshots/enrichment), **floor** (`-c 2`, the observation-floor capture cycles — recency-first, random-only, self-chaining), **crawls** (`-c 1`, the multi-day clan crawl + watchdog only).

Resilience: `CELERY_TASK_ACKS_LATE = True` (at-least-once delivery); RabbitMQ `consumer_timeout` disabled (long tasks); consumer watchdog systemd timer restarts zombie workers (alive process, 0 consumers); soft systemd deps (`Wants=`, not `Requires=`).

### Per-realm schedule striping

Per-realm periodic tasks are striped via `REALM_INTERVAL_OFFSETS = {'na': 0, 'eu': 1, 'asia': 2}` in `signals.py` so at most one realm is mid-cycle at a time. `_realm_crontab_for_cycle()` computes per-realm crontabs. Daily/weekly-cron families use `REALM_CRAWL_CRON_HOURS = {'eu': 0, 'na': 6, 'asia': 12}`. The rolling BattleObservation floor (cadence `BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES`, **prod=180** = 3h/realm, per-realm striped) guarantees no active-7d player goes >`BATTLE_OBSERVATION_FLOOR_HOURS` without a fresh observation. It runs on its **own `floor` queue / `battlestats-celery-floor` (`-c 2`) worker** (off the user-facing `default` lane), with candidates ordered **recency-first** (`-last_battle_date`), **random-only** per cycle (`BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED=1` — ranked sweep only on each realm's primary daily slot), and **self-chaining** (`BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED=1`, all realms) — two realms continuously chew their per-realm backlogs (re-dispatch while stale backlog ≥ threshold, ~120s countdown; yields during crawls) while a third queues, filling the `-c 2` concurrency. The shared 2-vCPU managed-PG is the binding constraint (periodically saturated by analytical warmers — the floor's own writes are a minor DB cost), watched by a standing load monitor (alarm on sustained `load15 > 2.3`); if it sustain-saturates, back off (self-chain off / `-c 1`) and optimize the warmers or resize 2→4 vCPU (full arc in `runbook-floor-throughput-tuning-2026-06-13.md`). As of 2026-06-14 the floor's observation path can **also refresh the player's displayed `battles_json` + `battles_updated_at` from the same `ships/stats` response** (no extra WG call), so active players' shown stats stay fresh without relying on page-visits (kill switch `FLOOR_REFRESH_BATTLES_JSON_ENABLED`; **prod=1, re-enabled 2026-07-08** after the backlog catch-up phase ended — self-chain now drains each realm's stale backlog daily; runbook `agents/runbooks/runbook-floor-battles-json-refresh-2026-06-14.md`). The daily-snapshot engine (`snapshot_active_players_task`, per-realm striped, **coexists with crawls** — does not defer) writes a daily `Snapshot` row for every active player via bulk account/info; kill switch `SNAPSHOT_ACTIVE_PLAYERS_ENABLED`. Runbook: `agents/runbooks/runbook-daily-active-snapshots-2026-06-09.md`. The lapsed-player recapture sweep (`recapture_lapsed_players_task`, per-realm striped daily, `background` queue, **coexists with crawls**; kill switch `RECAPTURE_LAPSED_ENABLED`) cheaply re-checks the dormant pool (8–365d) the floor structurally can't see: a bulk `account/info` pass detects players whose `last_battle_time` advanced back inside active-7d and rewrites their `last_battle_date` (never `last_fetch`) so the existing floor harvests them next cycle ("let the floor catch it"); `Player.last_idle_check_at` is the LRU rotation cursor that walks the whole pool over ~a week. `RECAPTURE_LAPSED_APPLY` gates writes (off = detect-only yield logging). Readout: `/recapture` skill. Runbook: `agents/runbooks/runbook-recapture-lapsed-players-2026-06-26.md`. The hot-players engagement queue (kill switch `HOT_PLAYERS_ENABLED`; **DISABLED in prod 2026-06-16** — the live set was ~98.5% active-7d, i.e. already floor-covered, so the queue was near-pure overlap with the observation floor; reversible via `=1`, see hot-players runbook) lets *durable visitor interest* — not the player's own activity/skill — qualify a player for guaranteed daily capture: `maintain_hot_players_task` (DB-only daily — promote/evict the `HotPlayer` set by view-recurrence across days over `EntityVisitDaily`, with hysteresis + a per-realm `HOT_PLAYERS_MAX` cap) and `capture_hot_player_observations_task` (per-realm striped, `background` queue, **coexists with crawls**, **skip-if-fresh** against the floor) which guarantees a daily observation + gap-free `Snapshot` for the hot set — a ≥24h battle-history pull is the queue's sole purpose. (A per-12-min freshness sweep that kept `battles_updated_at` inside the visit window for sub-second loads — latency-runbook Tier 3 — was **retired 2026-06-15**.) The hot set can be seeded to the cap with the most-active players via `backfill_hot_players` (`source='backfill'`: ranked below engagement, protected from inactivity-eviction, cap-trimmed first, captured but excluded from any per-visit refresh). Runbooks: `agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md`, `agents/runbooks/runbook-player-refresh-latency-2026-06-10.md`.

### Infra notes

- **Resources** — app droplet **2 vCPU / 8 GB**; managed Postgres **2 vCPU / 4 GB** (`db-s-2vcpu-4gb`, PG 18, ~97 usable connections), resized up from 1 vCPU / 2 GB on 2026-05-28. **Do not plan against a 1-vCPU DB** — that assumption is stale; `system_load15` saturates around 2. Full sizing + re-verify recipe: `agents/runbooks/ops-infra-resources.md`.
- **HTTP/2** on the nginx 443 listeners (removes the HTTP/1.1 6-connection-per-origin limit)
- **Frontend fetch priority** — a global priority queue (cap 6) serves visible content first: detail (`critical`) → clan members + battle history (`high`) → non-visible-tab warmup prefetch (`low`). The clan-members fetch is **de-waterfalled** (flag `NEXT_PUBLIC_PLAYER_DEWATERFALL=1`) — it fetches in parallel with the charts, no longer gated behind warmup. Whole-page cancellation aborts the abandoned page's requests on nav/realm-switch. Full design: `agents/runbooks/runbook-player-fetch-orchestration-2026-06-21.md`. **The player page's left clan rail (PlayerRailLayout) was removed 2026-07-15 (3.9.0)** — the clan surface is now `PlayerClanSection` at the bottom of `PlayerDetail` (clan-page scatterplot via shared `ClanSVG` + roster paragraphs per collapsed activity phase, in a `DeferredSection`); the site chrome (header/content/footer) shares one 850px column (`app/layout.tsx`), so `runbook-player-rail-soft-nav-2026-06-23.md` is historical.
- **DB** — `CONN_HEALTH_CHECKS` enabled; analytical queries use elevated `work_mem` (`ANALYTICAL_WORK_MEM`, default 8MB) via `SET LOCAL`
- **SEO** — per-page `generateMetadata()`; dynamic `app/sitemap.ts` from `/api/sitemap-entities/`; `WebSite`+`SearchAction` JSON-LD; analytics via Umami + first-party entity tracking

### Data models (`server/warships/models.py`)

Player, Clan, Ship (incl. `shiptool_code` — derived Ship Tool short index, populated by `populate_shiptool_codes`), Snapshot (daily summaries), PlayerExplorerSummary, EntityVisitEvent/EntityVisitDaily, PlayerAchievementStat, DeletedAccount (GDPR blocklist), MvPlayerDistributionStats, ShipTopPlayerSnapshot (ephemeral current standing per ship — recomputed nightly over a trailing window, pruned; backs `/ship/<id>` + profile badges), StreamerSubmission, HotPlayer (engagement capture queue — durable visitor-interest membership + audit, feeding the daily hot-player observation/snapshot sweep; **queue disabled in prod 2026-06-16**, `HOT_PLAYERS_ENABLED=0`, rows retained), RankedSeason (durable WG ranked-season dates — upserted on every `seasons/info/` fetch; drives the Ranked Enjoyer icon's current-season criteria, "latest season persists"; spec `agents/work-items/ranked-enjoyer-current-season-spec.md`), ClanBattleSeason (durable WG clan-battle-season dates — upserted on every `clans/season/` fetch; drives the CB shield's current-season criteria via max-start-date + brawl-id<100 guard, not max id; runbook `agents/runbooks/runbook-cb-icon-current-season-2026-07-15.md`).

Battle-history pipeline: BattleObservation (raw `ships/stats/` JSON), BattleEvent (per-event deltas + Phase 7 widening columns), PlayerDailyShipStats (per-day per-ship aggregate). The weekly/monthly/yearly period rollup tiers were dropped 2026-06-15 (DB-growth followup, step 2 KILL); all UI windows (day/week/month/year) resolve to the daily layer.

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
- **`observation`** ("/observation", "observation readout") — day-over-day observation-floor coverage/freshness from the nightly snapshots. Read-only.
- **`crawl-yield`** ("/crawl-yield", "is the crawler still earning its cost") — per-pass clan-crawl yield (discovery + dormant→active re-detection) vs. floor overlap, from the per-pass snapshots. Read-only.
- **`recapture`** ("/recapture", "recapture readout") — last lapsed-player recapture sweep yield (returning dormant players found, into-7d/clanless split) from the background worker journal. Read-only.

## Versioning

Semantic versioning with root `VERSION` as the single source of truth, surfaced in the client footer at build time via `NEXT_PUBLIC_APP_VERSION`.

- **patch** — bug fixes, perf, docs · **minor** — features, new surfaces, UX changes · **major** — breaking model/API/UX changes
- Commit prefixes (Conventional Commits): `feat:` (minor), `fix:`/`perf:`/`refactor:`/`docs:`/`chore:`/`test:` (patch); append `!` for breaking (major)
- Releases cut with `./scripts/release.sh`; `patch` may skip the release gate, `minor`/`major` run it first

### MANDATORY: Rebuild client after every version bump

`NEXT_PUBLIC_APP_VERSION` is captured at frontend **build time**, so a `release.sh` bump alone leaves the production footer on the old version. After **every** bump (even backend-only), run `./client/deploy/deploy_to_droplet.sh battlestats.online`. Non-negotiable.

## Environment

Env files, the full runtime env-var catalog (defaults), Umami, and Docker ports live in `agents/runbooks/ops-env-reference.md`. Quick orientation:

- Env-var values are kept canonically in Pass (the operator's `pass` store); the on-disk env files (`server/.env`, `.env.secrets`, the `*.cloud` overrides, the droplet `/etc/battlestats-*.env`) are generated from it. Update Pass and regenerate the file; do not hand-edit a file as the source of truth.
- Master kill switches: `ENABLE_CRAWLER_SCHEDULES` (crawlers), `BATTLE_HISTORY_*_ENABLED` (battle-history phases), `SHIP_BADGE_SNAPSHOT_ENABLED` (ship standings), `ENRICHMENT_POOL_MAINTENANCE_ENABLED` (daily enrichment pool reclassify/retry).
- Client: `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`); `NEXT_PUBLIC_PLAYER_DEWATERFALL=1` (clan-rail de-waterfall, build-time, set in `/etc/battlestats-client.env` on the droplet).
- Docker ports: 8888 Django · 3001 Next.js (dev) · 3002 Umami (prod) · 15672 RabbitMQ.
