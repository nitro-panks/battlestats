# Production Data Refresh Strategy — Spec

**Date:** 2026-03-18  
**Status:** Partially implemented — Phase 1 (`incremental_player_refresh_task`) landed; enrichment has migrated to DigitalOcean Functions (see `spec-serverless-background-workers-2026-04-04.md`).  
**Scope:** Replace development-era full-population nightly crawl with a targeted, incremental refresh strategy suitable for production.  
**QA Review:** 2026-03-18 — Conditional GO for Phase 1. Critical/High findings addressed below.

> **Architecture update (2026-04-05):** Background enrichment — the heaviest sustained background workload — now runs as a DigitalOcean Function (`enrichment/enrich-batch`) outside the droplet, not as a Celery background task. This materially changes the capacity assumptions in this spec. The `background` queue is no longer contended by enrichment, so warmer/crawler coexistence is more viable than originally assessed. The `ENRICH_REALMS` env var controls realm targeting. See `runbook-enrichment-crawler-2026-04-03.md` for progress tracking.

---

## Problem Statement

The nightly clan crawl (`crawl_all_clans_task`, 3:00 AM UTC) fetches **every clan and every player** in the game database from the Wargaming API every night. This was appropriate during development for populating the corpus, but in production it:

1. **Over-fetches**: The majority of players in the DB are dormant (haven't played in months/years). Refreshing them nightly wastes API budget.
2. **Long runtime**: A full crawl takes hours, consuming the Celery worker and API rate limit budget for half the night.
3. **Monolithic**: A single long-running task with lock/heartbeat/watchdog complexity. A stall blocks all other crawl work.
4. **Redundant for active players**: Players who visit the site already get refreshed on page load (15-minute staleness). The nightly crawl re-fetches the same data again hours later.

### Goal

Maintain up-to-date records for **active players** (played within 30 days or viewed on-site within 14 days) with at least 24-hour freshness — without crawling the entire player database nightly. Dormant players who return to the game should get picked up within 24 hours of their first new battle.

---

## Data Refresh Matrix

| #   | Data Category                                      | Storage                                              | WG API Endpoint                      | Current Refresh                                       | Current Trigger                  | Recommended Prod Cadence                          | Recommended Trigger                  |
| --- | -------------------------------------------------- | ---------------------------------------------------- | ------------------------------------ | ----------------------------------------------------- | -------------------------------- | ------------------------------------------------- | ------------------------------------ |
| 1   | **Core player stats** (battles, WR, survival, KDR) | `Player` model fields                                | `account/info/`                      | Every night (full crawl) + 15 min on page load        | Nightly crawl + page load        | **24 hr for active players; skip dormant**        | Incremental crawler + page load      |
| 2   | **Per-ship stats** (`battles_json`)                | `Player.battles_json`                                | `ships/stats/`                       | On page load only (15 min)                            | Page load                        | **On-demand only** (no change)                    | Page load                            |
| 3   | **Tier aggregates** (`tiers_json`)                 | `Player.tiers_json`                                  | None (derived)                       | On page load (24 hr)                                  | Page load                        | **On-demand only** (no change)                    | Page load                            |
| 4   | **Type aggregates** (`type_json`)                  | `Player.type_json`                                   | None (derived)                       | On page load (24 hr)                                  | Page load                        | **On-demand only** (no change)                    | Page load                            |
| 5   | **Randoms top-20** (`randoms_json`)                | `Player.randoms_json`                                | None (derived)                       | On page load (24 hr)                                  | Page load                        | **On-demand only** (no change)                    | Page load                            |
| 6   | **Daily activity** (29-day rolling)                | `Snapshot` + `Player.activity_json`                  | None (derived from cumulative diffs) | On page load (15 min)                                 | Page load                        | **On-demand only** (no change)                    | Page load                            |
| 7   | **Ranked seasons** (`ranked_json`)                 | `Player.ranked_json`                                 | `account/rankinfo/` + `ships/stats/` | Nightly incremental (150/day) + 1 hr on page load     | Scheduled (10:30 AM) + page load | **Keep incremental; widen pool to 300**           | Scheduled incremental + page load    |
| 8   | **Efficiency badges** (`efficiency_json`)          | `Player.efficiency_json`                             | `ships/achievements/`                | Nightly crawl side-effect + 24 hr on page load        | Nightly crawl + page load        | **24 hr for active players; skip dormant**        | Incremental crawler + page load      |
| 9   | **Achievements** (`achievements_json`)             | `Player.achievements_json` + `PlayerAchievementStat` | `account/achievements/`              | Nightly crawl side-effect + 24 hr on page load        | Nightly crawl + page load        | **24 hr for active players; skip dormant**        | Incremental crawler + page load      |
| 10  | **Clan metadata** (name, tag, members)             | `Clan` model                                         | `clans/list/` + `clans/info/`        | Every night (full scan) + 12 hr on page load          | Nightly crawl + page load        | **24 hr for active clans; weekly for dormant**    | Incremental clan crawler + page load |
| 11  | **Clan members** (roster)                          | `Player.clan` FK                                     | `clans/info/` (member list)          | Every night + on page load if incomplete              | Nightly crawl + page load        | **24 hr for active clans; on-demand for dormant** | Incremental clan crawler + page load |
| 12  | **Clan battle seasons** (per-player)               | Redis + `PlayerExplorerSummary`                      | `ships/stats/`                       | On page load (6 hr Redis TTL)                         | Page load                        | **On-demand only** (no change)                    | Page load                            |
| 13  | **Clan battle summary** (roster aggregate)         | Redis                                                | `ships/stats/` (parallel)            | Every 30 min (configured clans) + on page load (1 hr) | Warm task + page load            | **On-demand + configured warm** (no change)       | Page load + warm task                |
| 14  | **Explorer summary** (denormalized)                | `PlayerExplorerSummary`                              | None (local computation)             | When source data changes                              | Source data triggers             | **Triggered by source refresh** (no change)       | Source data writes                   |
| 15  | **Efficiency rank** (population percentiles)       | `PlayerExplorerSummary`                              | None (local computation)             | On-demand / nightly                                   | Manual or scheduled              | **Daily after incremental crawl completes**       | Post-crawl trigger                   |
| 16  | **Player verdict**                                 | `Player.verdict`                                     | None (local computation)             | When stats change                                     | Crawl + page load                | **Triggered by source refresh** (no change)       | Source data writes                   |
| 17  | **Ship catalog**                                   | `Ship` model                                         | `encyclopedia/ships/`                | Manual only                                           | Manual command                   | **Weekly scheduled**                              | Scheduled task (low priority)        |
| 18  | **Landing page caches**                            | Redis                                                | None (local computation)             | Every 55 min + on boot                                | Warm task                        | **Every 55 min** (no change)                      | Warm task                            |
| 19  | **Visit analytics**                                | `EntityVisitEvent` / `EntityVisitDaily`              | None                                 | Real-time + nightly agg                               | Page load + management cmd       | **No change**                                     | Page load + nightly agg              |
| 20  | **Population distributions**                       | Redis                                                | None (local computation)             | On-demand (1 hr TTL)                                  | API call                         | **No change**                                     | On-demand                            |

---

## Proposed Architecture: Incremental Player Refresh

### Core Concept

Replace the monolithic `crawl_all_clans_task` with a **targeted incremental player refresh** that follows the same proven pattern as `incremental_ranked_data`. The existing ranked incremental crawler already demonstrates the right candidate selection, checkpoint durability, prioritization, and error handling — we extend that model to cover core stats, achievements, and efficiency badges.

### Player Tiers

Players are segmented into refresh tiers based on their activity signals:

| Tier        | Definition                                       | Refresh Target                           | Estimated Population |
| ----------- | ------------------------------------------------ | ---------------------------------------- | -------------------- |
| **Hot**     | `last_lookup` within 14 days (site visitors)     | Every 12 hours                           | Small (hundreds)     |
| **Active**  | `last_battle_date` within 30 days AND not Hot    | Every 24 hours                           | Medium (thousands)   |
| **Warm**    | `last_battle_date` within 90 days AND not Active | Every 72 hours                           | Medium               |
| **Dormant** | `last_battle_date` > 90 days ago OR null         | **Skip entirely** (refresh on page load) | Large (majority)     |

### New Task: `incremental_player_refresh_task`

**Schedule:** Runs twice daily (e.g. 3:00 AM and 3:00 PM UTC), staggered from ranked incrementals.

**Candidate Selection (priority order):**

1. **Hot players** — `last_lookup >= now - 14 days` AND `last_fetch < now - 12 hours`
   - Order by: `last_lookup DESC`, `last_fetch ASC`
   - Take all (uncapped, expected to be small)

2. **Active players** — `last_battle_date >= now - 30 days` AND `last_fetch < now - 24 hours` AND not in Hot set
   - Order by: `last_battle_date DESC`, `pvp_battles DESC`
   - Cap: configurable, e.g. `PLAYER_REFRESH_ACTIVE_LIMIT = 500`

3. **Warm players** — `last_battle_date >= now - 90 days` AND `last_fetch < now - 72 hours`
   - Order by: `last_battle_date DESC`
   - Cap: configurable, e.g. `PLAYER_REFRESH_WARM_LIMIT = 200`

**Per-player refresh actions (reuse `save_player()` from `clan_crawl.py`):**

The incremental refresh must reuse the existing `save_player(player_data, clan=None)` function rather than building new field-update logic. This ensures:

- Clan FK is **preserved** (not updated) during incremental refresh because `account/info/` returns no clan data. True clan-change detection requires Phase 2 clan refresh or a page-load refresh. The nightly crawl still updates clan FK while it remains active.
- `days_since_last_battle`, `creation_date`, `last_battle_date` stay consistent
- Hidden-player logic (clear efficiency/verdict) is inherited
- `compute_player_verdict()` and `refresh_player_explorer_summary()` are called

After `save_player()`, conditionally run:

1. `update_achievements_data(player_id)` if `achievements_updated_at` stale (>24 hr)
2. `update_player_efficiency_data(player)` if `efficiency_updated_at` stale (>24 hr)

**Hidden players:** Included in candidate selection (their `last_battle_date` is still valid). `save_player()` already handles clearing efficiency/verdict for hidden profiles. They are refreshed on the same cadence as visible players — their hidden status may change.

**Durability:** Checkpoint file (`logs/incremental_player_refresh_state.json`) with pending IDs, current index, error tracking — same pattern as ranked incrementals.

**Error budget:** `PLAYER_REFRESH_MAX_ERRORS = 25` (configurable). Stop and resume next cycle.

**Rate limiting:** Same 0.25s delay between API calls.

**Concurrency guard:** Redis lock (`warships:tasks:incremental_player_refresh:lock`), 6-hour timeout.

---

### Lock Exclusion Matrix

Multiple tasks hit the WG API. They must coordinate to avoid aggregate rate-limit violations.

| Task                         | Can run with Player Refresh? | Can run with Ranked Incremental? | Can run with Clan Crawl (legacy)? | Can run with Clan Refresh? |
| ---------------------------- | ---------------------------- | -------------------------------- | --------------------------------- | -------------------------- |
| **Player Refresh**           | —                            | Yes (staggered schedule)         | **No** (must yield)               | Yes (different endpoints)  |
| **Ranked Incremental**       | Yes                          | —                                | **No** (existing behavior)        | Yes                        |
| **Full Clan Crawl (legacy)** | **No**                       | **No**                           | —                                 | **No**                     |
| **Clan Refresh**             | Yes                          | Yes                              | **No** (must yield)               | —                          |
| **Page-load refreshes**      | Yes (async, single-player)   | Yes                              | Yes                               | Yes                        |

**Implementation:** The new `incremental_player_refresh_task` and `incremental_clan_refresh_task` must check `cache.get(CLAN_CRAWL_LOCK_KEY)` at startup and skip their cycle if the legacy crawl is running — same pattern as `incremental_ranked_data_task`. This is only needed during the Phase 3 parallel-run period.

**Aggregate rate budget:** With 0.25s delay per call, a single crawler does ~240 calls/minute. Page-load refreshes are sporadic. Two concurrent crawlers (player + ranked) would peak at ~480 calls/minute. WG API allows 10 requests/second (600/min) per application ID, so two concurrent incremental tasks stay within budget. Three concurrent tasks would not — hence the exclusion of player refresh + clan crawl.

### New Task: `incremental_clan_refresh_task`

**Schedule:** Daily (e.g. 4:00 AM UTC, after player refresh starts).

**Candidate Selection:**

1. **Active clans** — clans with ≥1 member having `last_battle_date >= now - 30 days` AND `clan.last_fetch < now - 24 hours`
   - Order by: member activity signals
   - Cap: `CLAN_REFRESH_ACTIVE_LIMIT = 200`

2. **Viewed clans** — `clan.last_lookup >= now - 14 days` AND `clan.last_fetch < now - 12 hours`
   - Order by: `last_lookup DESC`
   - Uncapped (expected small)

**Per-clan refresh actions:**

1. Fetch clan info from `clans/info/`
2. Refresh member roster
3. For newly discovered members: create Player records (they'll be picked up by the next player refresh cycle)

**Dormant clans** (no member active in 90+ days): Skip entirely. Refresh on page load.

### Returning Players: Re-Discovery

A player who was dormant but starts playing again needs to be picked up. This happens through two mechanisms:

1. **Page load:** If anyone visits their profile, the 15-minute staleness check triggers a full refresh immediately.
2. **Clan crawl propagation:** When an active clan is refreshed, its roster is updated. Newly discovered or re-activated members get Player records created/updated, which promotes them into the "Active" tier for the next player refresh cycle.

For players who return to the game but nobody visits their profile and they're in a dormant clan: the **weekly dormant clan scan** (see below) ensures they're discovered within 7 days.

**Known gap (Phases 1–3):** Until the weekly dormant scan ships in Phase 4, clanless returning players or players in fully dormant clans will not be discovered by the crawler. They will only be refreshed on page load. The blast radius is small: these are players nobody on the site is looking at, in clans nobody is viewing, who started playing again silently. Once _anyone_ visits their profile or their clan, the page-load staleness check picks them up immediately. This is an accepted risk for Phases 1–3.

### Weekly Dormant Clan Scan

**Schedule:** Weekly (e.g. Sunday 2:00 AM UTC).

**Purpose:** Detect returning players in dormant clans by doing a lightweight scan of clan rosters.

**Approach:**

1. Fetch clan list from WG API (paginated, same as current crawl)
2. For each clan, compare `members_count` from API with stored value
3. If changed: refresh the clan's roster and member stats
4. If unchanged: skip entirely

This is much cheaper than a full crawl — it only fetches `clans/list/` pages and selectively refreshes clans that changed.

---

## Capacity Planning

Estimated population sizes (to be validated against live DB during Phase 1):

| Tier                            | Estimated Count | Refresh Cadence | Cycles/Day | Players/Cycle (cap) | Days to Full Coverage |
| ------------------------------- | --------------- | --------------- | ---------- | ------------------- | --------------------- |
| **Hot** (site visitors, 14d)    | ~200–500        | 12 hr           | 2          | Uncapped            | < 1 day               |
| **Active** (battled within 30d) | ~2,000–5,000    | 24 hr           | 2          | 500                 | 2–5 days              |
| **Warm** (battled within 90d)   | ~3,000–8,000    | 72 hr           | 2          | 200                 | 7.5–20 days           |
| **Dormant** (>90d or null)      | Majority        | Skip            | —          | —                   | On page load only     |

**SLA analysis for Active tier:** At 500/cycle × 2 cycles/day = 1,000 players/day. If the Active population is 3,000, the worst-case freshness for the least-priority Active player is 72 hours (3 days to cycle through). This exceeds the 24-hour target.

**Mitigation:** The Active tier cap should be tuned after measuring the real population. If Active > 1,000, either:

- Increase `PLAYER_REFRESH_ACTIVE_LIMIT` to `ceil(active_count / 2)`
- Add a third daily cycle
- Accept 48-hour effective freshness for low-priority Active players (still much better than nightly full crawl for the majority)

**API budget per cycle:** Hot (500 × ~3 calls) + Active (500 × ~3) + Warm (200 × ~3) = ~3,600 API calls × 0.25s = ~15 minutes wall-clock time per cycle. Well within the 6-hour task timeout.

---

## Database Index Recommendations

The candidate selection queries filter on `last_fetch` (not currently indexed) combined with `last_lookup` or `last_battle_date` (both indexed). On a large Player table, the unindexed `last_fetch` column could cause slow scans.

**Phase 1 implementation should add:**

```sql
CREATE INDEX idx_player_last_fetch ON warships_player (last_fetch);
```

If query plans show sequential scans, consider composite indexes:

```sql
CREATE INDEX idx_player_hot_candidates ON warships_player (last_lookup DESC, last_fetch ASC)
    WHERE last_lookup IS NOT NULL;
CREATE INDEX idx_player_active_candidates ON warships_player (last_battle_date DESC, last_fetch ASC)
    WHERE last_battle_date IS NOT NULL;
```

Validate with `EXPLAIN ANALYZE` on the actual candidate queries during Phase 1.

---

## Migration Plan

### Phase 1: Build Incremental Player Refresh (This PR)

- [ ] Implement `incremental_player_refresh` management command following the ranked incremental pattern
- [ ] Reuse `save_player()` from `clan_crawl.py` for per-player refresh (do not duplicate field-update logic)
- [ ] Implement corresponding Celery task with Celery Beat schedule
- [ ] Add lock-exclusion check: skip if `CLAN_CRAWL_LOCK_KEY` is held
- [ ] Add checkpoint file support and error budget
- [ ] Wire up per-player actions via `save_player()` + conditional achievements/efficiency refresh
- [ ] Add env-configurable constants for all limits, thresholds, and schedule
- [ ] Add `last_fetch` index migration
- [ ] Measure live population per tier; tune caps to hit freshness SLAs
- [ ] Write focused tests:
  - Candidate selection logic (per tier, boundary dates, ordering)
  - Checkpoint resume after interruption
  - Error budget halt and carry-over
  - Hidden player inclusion and `save_player()` clearing behavior
  - Lock-exclusion behavior (skip when clan crawl running)
  - Tier boundary edge cases (exactly 30 days, exactly 90 days)

### Phase 2: Build Incremental Clan Refresh

- [ ] Implement `incremental_clan_refresh` management command
- [ ] Implement corresponding Celery task with Celery Beat schedule
- [ ] Wire up per-clan actions: clan info, roster diff, new member creation
- [ ] Write focused tests

### Phase 3: Deprecate Full Nightly Crawl

- [ ] Add `ENABLE_FULL_CLAN_CRAWL` env flag (default `True` initially)
- [ ] Run both systems in parallel for 1–2 weeks, monitoring:
  - API call volume (should decrease significantly)
  - Data freshness for Hot/Active players (should be ≤24 hr)
  - Dormant player re-discovery latency
- [ ] Set `ENABLE_FULL_CLAN_CRAWL=False` when confident
- [ ] Remove crawl watchdog task (no longer needed)

### Phase 4: Weekly Dormant Scan

- [ ] Implement lightweight weekly clan roster diff
- [ ] Schedule on Celery Beat

### Phase 5: Ship Catalog Auto-Refresh

- [ ] Schedule `sync_ship_catalog` weekly (low priority, WG ship data changes rarely)

---

## Configuration Reference

All new constants are env-configurable with sensible defaults:

| Env Variable                          | Default  | Description                                |
| ------------------------------------- | -------- | ------------------------------------------ |
| `PLAYER_REFRESH_SCHEDULE_HOURS`       | `"3,15"` | Hours (UTC) to run player refresh          |
| `PLAYER_REFRESH_HOT_STALE_HOURS`      | `12`     | Staleness threshold for Hot tier           |
| `PLAYER_REFRESH_ACTIVE_STALE_HOURS`   | `24`     | Staleness threshold for Active tier        |
| `PLAYER_REFRESH_WARM_STALE_HOURS`     | `72`     | Staleness threshold for Warm tier          |
| `PLAYER_REFRESH_ACTIVE_LIMIT`         | `500`    | Max Active-tier players per cycle          |
| `PLAYER_REFRESH_WARM_LIMIT`           | `200`    | Max Warm-tier players per cycle            |
| `PLAYER_REFRESH_MAX_ERRORS`           | `25`     | Error budget before stopping               |
| `PLAYER_REFRESH_HOT_LOOKBACK_DAYS`    | `14`     | Hot tier: last_lookup recency              |
| `PLAYER_REFRESH_ACTIVE_LOOKBACK_DAYS` | `30`     | Active tier: last_battle_date recency      |
| `PLAYER_REFRESH_WARM_LOOKBACK_DAYS`   | `90`     | Warm tier: last_battle_date recency        |
| `CLAN_REFRESH_SCHEDULE_HOUR`          | `4`      | Hour (UTC) to run clan refresh             |
| `CLAN_REFRESH_ACTIVE_LIMIT`           | `200`    | Max active clans per cycle                 |
| `CLAN_DORMANT_SCAN_DAY`               | `0`      | Day of week for dormant scan (0=Monday)    |
| `ENABLE_FULL_CLAN_CRAWL`              | `True`   | Kill switch for legacy nightly crawl       |
| `RANKED_INCREMENTAL_LIMIT`            | `300`    | Widened from 150 (see justification below) |

**RANKED_INCREMENTAL_LIMIT justification:** At 150/cycle, ranked data for the Active population (~2,000–5,000 players with ranked history) cycles through in 13–33 days. Widening to 300/cycle (still only ~300 API calls × 0.25s = ~75s extra runtime) cuts this to 7–17 days. The additional API cost is negligible relative to the aggregate budget.

---

## Observability

### Metrics to Log (per cycle)

- `hot_candidates` / `active_candidates` / `warm_candidates`: queue sizes before cap
- `players_refreshed` / `players_skipped` / `players_errored`: outcome counts
- `api_calls_made`: total WG API calls this cycle
- `cycle_duration_seconds`: wall-clock time
- `clans_refreshed` / `clans_skipped` (for clan refresh)

### Health Signals

- Alert if Hot-tier average freshness exceeds 18 hours
- Alert if Active-tier average freshness exceeds 36 hours
- Alert if incremental refresh fails 3 consecutive cycles
- Alert if cycle API error rate exceeds 10%
- Log structured events: `{event: "player_refresh_cycle", phase: "start|complete|error", tier_counts: {...}, duration_s: N}`

### Snapshot Continuity Note

Removing the nightly full crawl means `Snapshot` records (used for 29-day activity charts) will stop accumulating for dormant players. This is intentional — dormant players' activity charts are already flat. When a dormant player returns and their profile is visited, the page-load refresh creates a new snapshot, and activity accumulates from that point forward. There will be a visible gap in their activity history covering the dormant period. This is acceptable and accurate.

---

## Doctrine Alignment

| Doctrine Principle                                                           | How This Spec Aligns                                                                 |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| "Prefer incremental evolution over big-bang rewrites"                        | Phases 1–5 are additive; legacy crawl runs in parallel until proven redundant        |
| "Favor non-blocking background hydration over synchronous page-load fan-out" | Page load remains lightweight (enqueue async); crawler handles freshness proactively |
| "Avoid unbounded polling, queue fan-out, or retry loops"                     | Capped candidate pools, error budgets, cooldown periods                              |
| "Bounded local and upstream API load"                                        | Per-cycle caps, rate limiting, tiered freshness thresholds                           |
| "Keep rollback steps close to each material change"                          | `ENABLE_FULL_CLAN_CRAWL` flag allows instant rollback                                |
| "Validate touched areas with focused tests before widening scope"            | Each phase has its own test requirements                                             |
| "When an endpoint or payload changes, update contract docs"                  | Crawl patterns change; this spec serves as the updated contract doc                  |

---

## QA Review Summary (2026-03-18)

**Verdict:** Conditional GO for Phase 1.

**Critical findings addressed:**

- Lock exclusion matrix added (prevents concurrent WG API overload)
- `save_player()` reuse specified (prevents field-update divergence and clan FK drift)

**High findings addressed:**

- Returning player gap documented as accepted risk for Phases 1–3
- Capacity planning section added with SLA arithmetic
- Hidden player handling specified

**Medium findings addressed:**

- Database index recommendations added
- Checkpoint file ephemerality acknowledged (ranked incremental already handles this gracefully)
- Snapshot continuity impact documented

**Remaining for implementation:**

- Measure live population per tier to validate cap defaults
- `EXPLAIN ANALYZE` candidate queries to validate index strategy
- Tune `PLAYER_REFRESH_ACTIVE_LIMIT` based on actual Active population
