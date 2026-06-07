# Ops: Environment Reference

**Lifecycle:** evergreen · **Owner:** platform

Catalog of environment files and runtime env vars (with defaults). Moved out of
`CLAUDE.md` to keep base context slim (see
`agents/runbooks/runbook-claude-md-durability.md`). See `agents/runbooks/` for the
rationale, dates, and incident history behind specific settings.

## Server env files (`server/`)

- `.env` — non-secret connection values (DB_HOST, DB_ENGINE, DJANGO_ALLOWED_HOSTS)
- `.env.secrets` — secrets (WG_APP_ID, DB_PASSWORD, DJANGO_SECRET_KEY)
- `.env.cloud` / `.env.secrets.cloud` — cloud database overrides

## Server runtime env (defaults in parentheses)

Cache/warming:
- `HOT_ENTITY_PINNED_PLAYER_NAMES` (empty), `HOT_ENTITY_PLAYER_LIMIT`/`HOT_ENTITY_CLAN_LIMIT` (20/10)
- `RECENTLY_VIEWED_PLAYER_LIMIT` (10), `RECENTLY_VIEWED_WARM_MINUTES` (60), `WARM_CACHES_ON_STARTUP` (1)
- `CLAN_BATTLE_WARM_CLAN_IDS`, `BEST_CLAN_EXCLUDED_IDS`, `ANALYTICAL_WORK_MEM` (8MB)
- Clan-battle summary fetch (per-member `clans/seasonstats/`, WG won't batch account_id): `CLAN_BATTLE_SUMMARY_FETCH_CONCURRENCY` (3) caps the per-task thread fan-out to stay under WG's ~10 req/s; `CLAN_BATTLE_PLAYER_STATS_ERROR_TTL` (300) short-caches a failed fetch so a transient `REQUEST_LIMIT_EXCEEDED` isn't persisted as a wrong "0 CB battles" for the 6h player TTL

Crawlers/refresh (`ENABLE_CRAWLER_SCHEDULES`=1 in prod is the master kill switch):
- `CLAN_CRAWL_SCHEDULE_HOUR`/`_MINUTE` (3/0), `CLAN_CRAWL_WATCHDOG_MINUTES` (5)
- `PLAYER_REFRESH_INTERVAL_MINUTES` (180); tier staleness `PLAYER_REFRESH_HOT/ACTIVE/WARM_STALE_HOURS` (12/24/72)
- `RANKED_REFRESH_INTERVAL_MINUTES` (120)
- BattleObservation floor: `BATTLE_OBSERVATION_FLOOR_HOUR`/`_MINUTE` (1/15), `_HOURS` (8), `_LIMIT`/`_DELAY` (3000/0.3), `_CRAWL_DELAY`/`_CRAWL_LIMIT` (0.8/falls back to LIMIT — floor coexists with crawls instead of skipping)
- BattleObservation floor — bulk capture (R1, `runbook-bulk-battle-observation-capture-2026-06-06.md`): `BATTLE_OBSERVATION_FLOOR_BULK_ENABLED` (0 — master switch), `_BULK_REALMS` (csv, empty — per-realm gate; realm must be listed even when ENABLED=1), `_BULK_CHUNK_DELAY` (0.5 — per-chunk pacing), `_BULK_CRAWL_CHUNK_DELAY` (1.0 — per-chunk pacing while a crawl holds the lock). All default to the legacy per-player floor (instant rollback). NB WG `ships/stats` can't bulk (single-account-only), so bulk only saves the `account/info` call (~2×); `account/info` does bulk.
- BattleObservation floor — change-detector gate: `BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED` (0). With bulk on, uses the cheap bulk `account/info` to fetch the expensive per-player `ships/stats` only for players whose random battle count moved since their last observation (~half are skipped). Separate flag from `_BULK_ENABLED` so it rolls out / is measured independently. Command flag: `--change-gate`. **Enabled on na,eu,asia 2026-06-07** (re-asserted in `deploy_to_droplet.sh`).
- BattleObservation floor — ranked-sweep gate: `BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED` (0). With bulk on + ranked capture on, gates the per-player ranked sweep (3 WG calls/player) — runs it only for ranked-known players whose `account/info` `last_battle_time` advanced (any battle type) since their last observation. Separate flag (validate before enabling). Command flag: `--ranked-gate`. This is the largest WG saving since the ranked sweep dominates the floor's cost.
- `CELERY_BROKER_HEARTBEAT` (0; rely on TCP keepalive)

Enrichment:
- `ENRICH_REALMS` (all), `ENRICH_BATCH_SIZE` (500), `ENRICH_MIN_PVP_BATTLES` (500), `ENRICH_MIN_WR` (48.0), `ENRICH_DELAY` (0.2), `ENRICH_PAUSE_BETWEEN_BATCHES` (10)

Battle-history pipeline (phased gates, all default 0):
- `BATTLE_HISTORY_CAPTURE_ENABLED` (write BattleObservation/BattleEvent as a side-effect of `update_battle_data`)
- `BATTLE_HISTORY_ROLLUP_ENABLED` + `_HOUR`/`_MINUTE` (4/30) (fill PlayerDailyShipStats + nightly rebuild)
- `BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS` (3) — trailing-window size the nightly sweeper rebuilds (self-heal); `1` = legacy yesterday-only. See `runbook-battle-history-rollup-durability-2026-06-06.md`.
- `BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED` (0) — gates the nightly weekly/monthly/yearly rebuild; OFF by default because those tiers are dormant/UI-hidden and the yearly-YTD aggregate is the long pole that exceeded the task's 540s soft time limit (the daily layer still builds). Flip to `1` only when the period tiers are reactivated.
- `BATTLE_HISTORY_RECONCILE_ENABLED` (0) — gates the alert-only rollup reconciliation task; **independent** of `BATTLE_HISTORY_ROLLUP_ENABLED` (so it can detect rollup-off / holes)
- `BATTLE_HISTORY_RECONCILE_AUDIT_DAYS` (30) — audit window the reconciliation task scans
- `BATTLE_HISTORY_API_ENABLED` (exposes `GET /api/player/<name>/battle-history?days=N`, 404 when off)
- `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED` + `_REALMS` (`na`) (third WG call `seasons/shipstats/`, ranked-mode events)
- `BATTLE_TRACKING_PLAYER_NAMES`/`_POLL_SECONDS` (60) — incremental-battle PoC dispatcher

Ship badges / standings (master gate `SHIP_BADGE_SNAPSHOT_ENABLED`=0):
- `SHIP_BADGE_MIN_BATTLES` (15), `SHIP_BADGE_MIN_SHIP_POPULATION` (20), `SHIP_BADGE_LIST_SIZE` (15), `SHIP_BADGE_TOP_N` (3), `SHIP_BADGE_TIERS` (default `10`; prod pins `8,9,10`; legacy `SHIP_BADGE_TIER` fallback), `SHIP_BADGE_RETENTION_DAYS` (30)
- Ranking: composite of win-rate/damage/kills z-scores with empirical-Bayes shrinkage — `SHIP_BADGE_PRIOR_BATTLES` (50), `SHIP_BADGE_PRIOR_WR` (0.5), weights `SHIP_BADGE_WEIGHT_WINS`/`_DAMAGE`/`_KILLS` (0.5/0.35/0.15). Read at task-call time (re-tune without redeploy)
- `SHIP_BADGE_SNAPSHOT_DAY_OF_WEEK`/`_HOUR` (1 Mon/2). Fixed non-overlapping 2-week seasons anchored to `SHIP_SEASON_EPOCH` (Mon 11 May 2026 UTC) in `data.py`, mirrored by `client/app/lib/shipSeason.ts`. Board/badges show the most recently completed season; task self-gates on `is_season_boundary()`. Backfill: `python manage.py backfill_ship_seasons --wipe`

Local-dev only: `BATTLESTATS_DISABLE_LIVE_REFRESH` (serve stale snapshots, no live WG refresh), `BATTLESTATS_ENABLE_STALE_RECENT_PLAYERS` (landing fallback ordering without the battle-history pipeline).

## Client env

- `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`), `NEXT_PUBLIC_GA_MEASUREMENT_ID` (optional)

## Umami analytics

Standalone Next.js app (v2.20.2) on port 3002 (`127.0.0.1`) behind nginx at `/umami/`; dashboard + admin API restricted to a home-IP allowlist (collection endpoints public). Uses the shared managed Postgres (separate `umami` DB, least-privilege `umami_app` role). Bootstrap: `umami/deploy/bootstrap_umami.sh`. Custom events via `client/app/lib/umami.ts` `trackEvent(name, data)` — keep names kebab-case and properties low-cardinality. See `agents/runbooks/runbook-umami-hardening-2026-06-02.md`.

## Docker ports

8888 Django/Gunicorn · 3001 Next.js (Docker dev) · 3002 Umami (prod only) · 15672 RabbitMQ management
