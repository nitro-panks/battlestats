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
- `CLAN_CRAWL_CORE_ONLY` (0) — **R2**: when 1, the clan crawl skips the per-player efficiency+achievements enrichment (2 WG calls/player, ~85% of the crawl's WG cost) that's redundant with `enrich_player_data` and made the crawl hold its realm lock for hours, pre-empting the battle-history floor. Honoured by both the Beat schedule and the watchdog re-dispatch. Clan/Player discovery + clan cached aggregates (Best Clans) still run. `_CORE_ONLY_RATE_LIMIT_DELAY` paces the (now cheaper) core-only pass. Cuts crawl WG ~6× (~120k→~20k/pass) and frees the floor.
- `PLAYER_REFRESH_INTERVAL_MINUTES` (180); tier staleness `PLAYER_REFRESH_HOT/ACTIVE/WARM_STALE_HOURS` (12/24/72)
- `RANKED_REFRESH_INTERVAL_MINUTES` (120)
- BattleObservation floor: `BATTLE_OBSERVATION_FLOOR_HOUR`/`_MINUTE` (1/15), `_HOURS` (8), `_LIMIT`/`_DELAY` (default 3000/0.3; **prod=12000** — normal-cycle candidate cap, raised 7500→12000 on 2026-06-10 to lift `fresh_frac`; only fully applies on non-crawl cycles since `_CRAWL_LIMIT` governs during crawls), `_CRAWL_DELAY`/`_CRAWL_LIMIT` (0.8 / default falls back to LIMIT; **prod=3000** to stay gentle on the DB (2 vCPU / 4 GB, see `ops-infra-resources.md`) while a crawl holds the realm lock — floor coexists with crawls instead of skipping)
- BattleObservation floor — cadence: `BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES` (360 = 6h/realm by default; **prod=180** = 3h/realm, 2× frequency). Each realm fires every `CYCLE_MINUTES`, striped `CYCLE_MINUTES // 3` apart (so na/eu/asia don't pile onto the DB at once). Raising frequency uses the idle worker capacity that R2 (`CLAN_CRAWL_CORE_ONLY`) freed; takes effect on deploy (post_migrate re-registers the `observation-floor-<realm>` Beat crontabs). Use a divisor-friendly value (60/120/180/360/720).
- BattleObservation floor — bulk capture (R1, `runbook-bulk-battle-observation-capture-2026-06-06.md`): `BATTLE_OBSERVATION_FLOOR_BULK_ENABLED` (0 — master switch), `_BULK_REALMS` (csv, empty — per-realm gate; realm must be listed even when ENABLED=1), `_BULK_CHUNK_DELAY` (0.5 — per-chunk pacing), `_BULK_CRAWL_CHUNK_DELAY` (1.0 — per-chunk pacing while a crawl holds the lock). All default to the legacy per-player floor (instant rollback). NB WG `ships/stats` can't bulk (single-account-only), so bulk only saves the `account/info` call (~2×); `account/info` does bulk.
- BattleObservation floor — change-detector gate: `BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED` (0). With bulk on, uses the cheap bulk `account/info` to fetch the expensive per-player `ships/stats` only for players whose random battle count moved since their last observation (~half are skipped). Separate flag from `_BULK_ENABLED` so it rolls out / is measured independently. Command flag: `--change-gate`. **Enabled on na,eu,asia 2026-06-07** (re-asserted in `deploy_to_droplet.sh`).
- BattleObservation floor — ranked-sweep gate: `BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED` (0). With bulk on + ranked capture on, gates the per-player ranked sweep (3 WG calls/player) — runs it only for ranked-known players whose `account/info` `last_battle_time` advanced (any battle type) since their last observation. Separate flag (validate before enabling). Command flag: `--ranked-gate`. This is the largest WG saving since the ranked sweep dominates the floor's cost.
- BattleObservation floor — random-first routing: `BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED` (0) + `_RANDOM_FIRST_REALMS` (csv, optional — empty = all realms; set for a staged rollout). Routes a player to the heavy 3-call ranked path only if they have ranked battles in a **currently-active** season (`Player.ranked_last_season_id`; current season = max of that field, enrichment-fed — robust to `seasons/info` date lag); everyone else — incl. lapsed ranked players — takes the fast bulk-random path so Random coverage isn't throttled by a niche mode. After adding the field (migration 0065), run `backfill_ranked_last_season` once (DB-only) so routing has its signal immediately. Command: `--random-first`. **Enabled on na,eu,asia 2026-06-08** (`_REALMS=na,eu,asia`, re-asserted in `deploy_to_droplet.sh`) — collapses the ever-ranked sweep (eu ~5000→709, asia ~2436→541 candidates/3k cycle), which is what made 7,500-limit cycles on eu/asia bloat to ~9k WG calls. Runbook: `runbook-bulk-battle-observation-capture-2026-06-06.md`. (Standing product rule: Random > Ranked.)
- BattleObservation floor — ranked sweep bound + cadence: `BATTLE_OBSERVATION_FLOOR_RANKED_SWEEP_LIMIT` (5000, the ranked sweep's own bound, separate from `_LIMIT` so it stays small as the random floor scales for R3); `BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED` (0 — run the per-player ranked sweep only on the realm's earliest 6h slot/day, not every cycle; Random keeps the 6h cadence). Command flags: `--ranked-sweep-limit`, `--skip-ranked`.
- `CELERY_BROKER_HEARTBEAT` (0; rely on TCP keepalive)

Enrichment:
- `ENRICH_REALMS` (all), `ENRICH_BATCH_SIZE` (500), `ENRICH_MIN_PVP_BATTLES` (500), `ENRICH_MIN_WR` (48.0; **prod=0** — enrich the active low-WR base too), `ENRICH_DELAY` (0.2), `ENRICH_PAUSE_BETWEEN_BATCHES` (10)
- `ENRICH_DEFER_DURING_CRAWL` (0) — **kill switch.** `enrich_player_data_task` now **coexists with clan crawls** by default (the old blanket defer made enrichment idle for the full duration of a multi-day crawl — `pending` piled up while `enriched` stayed flat). Set to `1` to restore the old defer-entirely behavior. `ENRICH_DELAY_DURING_CRAWL` (0.5) paces enrichment gentler than the 0.2 baseline while a crawl shares the single-app-id WG budget (there is **no shared token-bucket limiter yet** — both paths self-throttle, so keep combined rate under the ~10 req/s cap to avoid 407s). Fixed 2026-06-10 after the backlog was found stalled mid-crawl.
- `ENRICH_MAX_INACTIVE_DAYS` (365; **prod=7**) — the eligibility activity window. Read in lockstep by the live crawler's candidate gate (`enrich_player_data._candidates`) and by the `reclassify_enrichment_status` / `retry_empty_enrichments` classifiers, so lowering it tightens intake everywhere at once. The live gate filters stale rows at selection time regardless of `enrichment_status`; the daily incremental drift reclassify absorbs the change naturally as rows are re-fetched (no full-catalog reclassify / bulk drain required). `ENRICH_MIN_WR=0` similarly relaxes the win-rate floor. At prod's 7d window only the genuinely-active base is enriched proactively; the low penalty for returning players is covered by `ENRICH_ON_VIEW_ENABLED`.
- `ENRICH_ON_VIEW_ENABLED` (0; **prod=1**) — fast-path enrichment for on-demand profile views. `update_player_data_task` (dispatched only from the request/view path) refreshes a player — resetting `days_since_last_battle` / `last_fetch` — then, if that player is now eligible but never enriched, enqueues `enrich_player_on_view_task` (background queue) instead of waiting up to ~24h for the daily drift reclassify. Debounced per player (`ENRICH_ON_VIEW_COOLDOWN`, 6h) and self-guarded against already-enriched/ineligible rows. This is what makes a tight `ENRICH_MAX_INACTIVE_DAYS` cheap: a returning player is enriched the moment someone looks at them; a dormant player nobody views costs nothing.
- `ENRICH_SKIP_RETRY_AFTER_DAYS` (3) — per-row cooldown for **private-at-fetch** enrichment skips. A `PENDING`/`battles_json IS NULL` row whose WG ship stats come back null (private profile) is stamped on `Player.enrichment_skipped_at` and suppressed from `enrich_player_data._candidates()` for this many days, so it stops being re-selected every pass (the ~37s self-chain spin — see `runbook-floor-throughput-tuning-2026-06-13.md`). Shorter than the `EMPTY` retry (14d) because private profiles un-hide relatively often. The row stays `PENDING` (orthogonal to `reclassify_enrichment_status`, which keys on stored fields and would bounce a terminal `skipped_*` back to `pending`), so a **steady small `PENDING` floor (~33 in prod 2026-06-13) is cooldown-suppressed, not stuck** — don't read it as a stall. Transient failures (the `"SKIP"` sentinel / chunk 5xx-timeout) are NOT stamped and keep retrying immediately.

WG rate limiter (`warships/api/rate_limiter.py`; **prod-enabled**):
- `WG_RATE_LIMIT_ENABLED` (1) — global token bucket gating outbound WG requests at the egress (`api/client.py` — note BOTH `_request_api_payload` and `make_api_request_typed` issue GETs and are gated). Process-shared via Redis + an atomic Lua bucket (clock from `redis.call('TIME')`), so the single `WG_APP_ID`'s ~10 req/s budget is enforced across every worker process AND gunicorn request threads. **Fail-open**: no-op when disabled, `REDIS_URL` unset (tests), or Redis errors. This is the real rate ceiling; the per-component delays (`ENRICH_DELAY*`, floor `_DELAY`/`_CRAWL_DELAY`, crawl `request_delay`) are now belt-and-suspenders and can be relaxed once the limiter is proven in prod.
- `WG_RATE_LIMIT_PER_SEC` (9) — sustained refill rate (kept <10 for margin). `WG_RATE_LIMIT_BURST` (18) — bucket capacity. `WG_RATE_LIMIT_MAX_WAIT` (8) — max seconds a **background** task blocks for a token. `WG_RATE_LIMIT_REQUEST_MAX_WAIT` (0.5) — max seconds a **request thread** blocks before failing open (a synchronous WG call still exists on the request path, e.g. `_fetch_player_id_by_name`; this prevents gunicorn thread-pool exhaustion under saturation). `WG_RATE_LIMIT_KEY` (`wg:ratelimit`).
- `WG_REQUEST_THREAD_TIMEOUT_SECONDS` (4) — latency runbook **Tier 2b**. The WG HTTP read/connect timeout used **on the gunicorn request thread** (background tasks keep the 20s default). The request session mounts `Retry(total=2)`, so the cold-lookup path now bounds at ~13.5s (4s×3+backoff) instead of ~60s — well under the gunicorn `timeout`, so a slow WG fails fast (→ fast 404 for a missing player, or a Tier-1 client retry for a transient) instead of hanging into a 502.
- `GUNICORN_TIMEOUT_SECONDS` (25) — latency runbook **Tier 2a**. Explicit gunicorn worker `timeout` (below the implicit 30s) so a wedged worker is recycled before nginx's 60s — the **primary** 502 remediation. (`CELERY_HYDRATION_CONCURRENCY` default also raised 3→5 in `server/deploy/deploy_to_droplet.sh` to drain hydration-queue wait; the WG token bucket still caps the real WG rate, so watch droplet memory not the WG ceiling.)

Hot-players engagement capture queue (`warships/hot_players.py`; kill switch `HOT_PLAYERS_ENABLED`; capture gated on `ENABLE_CRAWLER_SCHEDULES`, maintenance is DB-only/always-enabled). Lets *durable visitor interest* (recurrence over `EntityVisitDaily`) qualify a player for guaranteed daily capture. Runbook: `runbook-hot-players-engagement-queue-2026-06-10.md`.
- `HOT_PLAYERS_ENABLED` (1) — master kill switch (`maintain_hot_players_task` + `capture_hot_player_observations_task` both no-op at 0).
- `HOT_PLAYERS_WINDOW_DAYS` (14) — trailing engagement window `W` for the active-days `GROUP BY`.
- `HOT_PROMOTE_MIN_ACTIVE_DAYS` (3) / `HOT_PROMOTE_MAX_RECENCY_DAYS` (3) / `HOT_PROMOTE_MIN_SESSIONS` (2) — promotion rule: distinct deduped-view days in `W`, recency, and an anti-single-reload session floor. **No visitor-breadth gate** — a single devoted fan (`unique_visitors=1`, many active-days) must qualify.
- `HOT_EVICT_INACTIVITY_DAYS` (14) / `HOT_EVICT_MIN_ACTIVE_DAYS` (2) — eviction with hysteresis (promote ≥3, evict <2 → no flapping).
- `HOT_OBSERVE_FLOOR_HOURS` (20) — skip-if-fresh: skip the observation when a `BattleObservation` is newer (the floor already got them).
- `HOT_PLAYERS_CAPTURE_DELAY` (0.5) — WG pacing between hot captures (crawl-coexist value).
- `HOT_PLAYERS_MAX` (code default 500, **prod=800 since 2026-06-15**) — per-realm cap on the hot set (capture sweep + cap-trim ranking). Sized so a full nightly capture pass fits a ~1h worst-case budget.
- `HOT_BACKFILL_ACTIVE_DAYS` (7) — `backfill_hot_players` selects players with `last_battle_date` within this window (ordered by `pvp_battles` desc) to seed the queue to the cap as `source='backfill'`.
- **Retired 2026-06-15:** `HOT_PLAYERS_FRESH_AFTER_MINUTES` / `HOT_PLAYERS_FRESH_CYCLE_MINUTES` and `refresh_hot_player_freshness_task` (the per-12-min visit-freshness sweep, latency-runbook Tier 3). The hot queue's sole purpose is now a ≥24h battle-history pull, guaranteed by the daily capture sweep + the observation floor; a visit to a stale hot player falls back to the normal live-refresh-on-view path. Schedule rows are in `signals._RETIRED_SCHEDULE_NAMES`.

Battle-history pipeline (phased gates, all default 0):
- `BATTLE_HISTORY_CAPTURE_ENABLED` (write BattleObservation/BattleEvent as a side-effect of `update_battle_data`)
- `BATTLE_HISTORY_ROLLUP_ENABLED` + `_HOUR`/`_MINUTE` (4/30) (fill PlayerDailyShipStats + nightly rebuild)
- `BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS` (3) — trailing-window size the nightly sweeper rebuilds (self-heal); `1` = legacy yesterday-only. See `runbook-battle-history-rollup-durability-2026-06-06.md`.
- `BATTLE_HISTORY_RECONCILE_ENABLED` (0) — gates the alert-only rollup reconciliation task; **independent** of `BATTLE_HISTORY_ROLLUP_ENABLED` (so it can detect rollup-off / holes)
- `BATTLE_HISTORY_RECONCILE_AUDIT_DAYS` (30) — audit window the reconciliation task scans
- `BATTLE_HISTORY_API_ENABLED` (exposes `GET /api/player/<name>/battle-history?days=N`, 404 when off)
- `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED` + `_REALMS` (`na`) (third WG call `seasons/shipstats/`, ranked-mode events)
- `BATTLE_TRACKING_PLAYER_NAMES`/`_POLL_SECONDS` (60) — incremental-battle PoC dispatcher

Ship badges / standings (master gate `SHIP_BADGE_SNAPSHOT_ENABLED`=0):
- `SHIP_BADGE_MIN_BATTLES` (15), `SHIP_BADGE_MIN_SHIP_POPULATION` (20), `SHIP_BADGE_MIN_SHIP_POPULATION_CV` (10) — carrier-only population floor; CVs are a low-volume class (few players grind ≥`MIN_BATTLES` on a single CV per season), so the universal 20 leaves most T10 CVs off the standings, `SHIP_BADGE_MIN_SHIP_POPULATION_SUB` (12) — submarine-only population floor; same niche-class shape as CVs (small hull roster, few grind one boat per season), so the universal 20 dropped legit T8/T10 sub boards (NA: 11→14 boards at floor 12, 2026-06-13), `SHIP_BADGE_LIST_SIZE` (15), `SHIP_BADGE_TOP_N` (3), `SHIP_BADGE_TIERS` (default `10`; prod pins `8,9,10`; legacy `SHIP_BADGE_TIER` fallback), `SHIP_BADGE_RETENTION_DAYS` (30)
- Ranking: composite of win-rate/damage/kills z-scores with empirical-Bayes shrinkage — `SHIP_BADGE_PRIOR_BATTLES` (50), `SHIP_BADGE_PRIOR_WR` (0.5), weights `SHIP_BADGE_WEIGHT_WINS`/`_DAMAGE`/`_KILLS` (0.5/0.35/0.15). Read at task-call time (re-tune without redeploy)
- `SHIP_BADGE_SNAPSHOT_DAY_OF_WEEK`/`_HOUR` (1 Mon/2). Fixed non-overlapping 2-week seasons anchored to `SHIP_SEASON_EPOCH` (Mon 11 May 2026 UTC) in `data.py`, mirrored by `client/app/lib/shipSeason.ts`. Board/badges show the most recently completed season; task self-gates on `is_season_boundary()`. Backfill: `python manage.py backfill_ship_seasons --wipe`

Local-dev only: `BATTLESTATS_DISABLE_LIVE_REFRESH` (serve stale snapshots, no live WG refresh).

## Client env

- `BATTLESTATS_API_ORIGIN` (default `http://localhost:8888`)

## Umami analytics

Standalone Next.js app (v2.20.2) on port 3002 (`127.0.0.1`) behind nginx at `/umami/`; dashboard + admin API restricted to a home-IP allowlist (collection endpoints public). Uses the shared managed Postgres (separate `umami` DB, least-privilege `umami_app` role). Bootstrap: `umami/deploy/bootstrap_umami.sh`. Custom events via `client/app/lib/umami.ts` `trackEvent(name, data)` — keep names kebab-case and properties low-cardinality. See `agents/runbooks/runbook-umami-hardening-2026-06-02.md`.

## Docker ports

8888 Django/Gunicorn · 3001 Next.js (Docker dev) · 3002 Umami (prod only) · 15672 RabbitMQ management
