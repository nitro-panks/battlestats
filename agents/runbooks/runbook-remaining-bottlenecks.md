# Runbook: Remaining Performance Bottlenecks

**Created**: 2026-03-28
**Implemented**: 2026-03-28
**Status**: Complete — deployed to production, clan aggregation backfill done (35,252 clans)
**Context**: Profiling run on 2026-03-28 after queue separation and efficiency snapshot SQL optimizations. Simulated 12 player + 4 clan concurrent visits (96 API requests at concurrency 8).

## Profiling Baseline (warm cache, 4GB droplet, 2 vCPU)

| Endpoint | N | Median | P95 | Max |
|----------|---|--------|-----|-----|
| Chart data (tier/type/activity/ranked/randoms) | 60 | 210-235ms | 278-423ms | 278-423ms |
| `player_summary` | 12 | 230ms | 304ms | 304ms |
| `player_detail` | 12 | 336ms | 794ms | 794ms |
| `clan_detail` | 4 | 595ms | 617ms | 617ms |
| `clan_members` | 4 | 980ms | 1598ms | 1598ms |
| `clan_battle_seasons` | 4 | 495ms | 580ms | 580ms |

Background task load per burst: 349 tasks, 141s combined worker time.

## B1: `clan_members` endpoint — p95 = 1598ms

### Root Cause

The `/api/fetch/clan_members/<clan_id>/` view (`views.py:527-616`) does too much work per request:

1. **Materializes full member list** with `select_related('explorer_summary')` — for a 50-member clan that's a large join
2. **Three hydration queue passes** over all members:
   - `queue_clan_ranked_hydration(members)` — iterates all, filters stale, queues up to `CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT`
   - `queue_clan_efficiency_hydration(members)` — two sub-passes: eligible players for efficiency data refresh, then publication-stale players for rank snapshot refresh
3. **Response building** loops through every member calling 4 classification functions (`is_pve_player()`, `is_sleepy_player()`, `is_ranked_player()`, `is_clan_battle_enjoyer()`) and extracting ~12 fields from nested objects
4. **Clan battle data refresh** — iterates members again, checking `clan_battle_summary_is_stale()` for each
5. **No response caching** — every request rebuilds the full response from DB

### Why it's slow for large clans

A clan with 100 members means:
- 1 DB query joining Player + PlayerExplorerSummary (100 rows)
- 4 Python-side iterations over all 100 members
- 100 calls to `_get_published_efficiency_rank_payload()`
- 100 calls each to `is_pve_player()`, `is_sleepy_player()`, etc.
- Up to 24 Celery task dispatches (8 ranked + 8 efficiency + 8 clan battle)

### Fix: Cache the serialized member payload

**Approach**: Cache the serialized member list in Redis, keyed by `clan_id`. TTL of 5-10 minutes. Invalidate on clan member updates.

**Changes**:

1. After building the member response list, cache it:
   ```python
   CLAN_MEMBERS_CACHE_KEY = 'clan:members:{clan_id}'
   CLAN_MEMBERS_CACHE_TTL = 300  # 5 minutes
   ```

2. On entry, check cache first. If hit, return cached response and still queue hydration tasks (so data freshens in background).

3. Invalidate the cache in `update_clan_members()` and `update_clan_data()`.

**Expected improvement**: p95 from 1598ms to ~200ms on cache hit. First request still slow.

**Risk**: Low. Stale-while-revalidate pattern matches the rest of the app.

### Fix: Push classification to DB/model

The `is_pve_player()`, `is_sleepy_player()`, `is_ranked_player()`, `is_clan_battle_enjoyer()` calls likely inspect model fields. Precompute these as annotated boolean fields or denormalized columns on PlayerExplorerSummary to avoid per-row Python-side logic.

**Expected improvement**: Removes 4N function calls per request. ~50-100ms savings for large clans.

**Risk**: Medium. Requires migration and backfill.

---

## B2: `warm_landing_page_content_task` — 156-212s

### Root Cause

`warm_landing_page_content()` (`landing.py:1493-1518`) warms 8 landing page surfaces **sequentially**:

| Surface | Est. Time | Why |
|---------|-----------|-----|
| `players_best` | 500-800ms | Fetches 1200 candidates, filters 75% in Python |
| `players_random` | 300-500ms | Random sampling + serialization |
| `clans` (random) | 200-400ms | Full-table aggregation (Sum, Count, Case) |
| `clans_best` | 200-400ms | Same aggregation + Python filtering |
| `players_sigma` | 200-300ms | Efficient single query |
| `players_popular` | 100-250ms | Analytics DB query |
| `recent_clans` | 50-100ms | LIMIT 40 query |
| `recent_players` | 50-80ms | LIMIT 40 query |

Total per-surface time: ~1.5-2.5s. But the task takes 156-212s because each surface builds full player/clan payloads requiring DB queries with explorer_summary joins and Python-side JSON parsing of `battles_json`/`ranked_json` fields. No WG API calls are made — the bottleneck is DB query overhead and serialization across hundreds of candidate rows.

### Fix: Parallelize surface warming

**Approach**: Instead of warming all 8 surfaces sequentially in one task, fan out to per-surface subtasks on the background queue.

**Changes**:

1. Create `warm_landing_surface_task(surface_name)` — warms a single surface
2. Refactor `warm_landing_page_content_task` to be a coordinator:
   ```python
   @app.task
   def warm_landing_page_content_task(self, ...):
       surfaces = ['random', 'best', 'sigma', 'popular', 'recent_players',
                    'clans', 'clans_best', 'recent_clans']
       for surface in surfaces:
           warm_landing_surface_task.delay(surface)
   ```
3. Route subtasks to `background` queue (same as coordinator)

**Expected improvement**: Wall-clock time drops from 156-212s to ~30-50s (limited by slowest surface). Background worker `-c 2` means 2 surfaces warm concurrently.

**Risk**: Low. Surfaces are independent. Cache keys are per-surface. The dirty flag clearing needs to move into each subtask or run after a chord/group completes.

**Complication**: The `_clear_cache_family_dirty()` call at the end should only run after all surfaces complete. Use a Celery `chord` or simply clear dirty flags in each subtask (idempotent).

### Fix: Reduce `players_best` candidate limit

**Approach**: The current `LANDING_PLAYER_BEST_CANDIDATE_LIMIT` is 1200 but only 25 are shown. Most are filtered out by the Python-side `high_tier_battles < 500` check. Push this filter to SQL.

**Changes**:

1. Add a SQL annotation for high-tier battles (or use the existing `explorer_summary` fields that already store this)
2. Reduce candidate limit from 1200 to 200-300
3. Filter `high_tier_battles >= 500` in the queryset WHERE clause

**Expected improvement**: Serialization cost drops ~4x (300 rows vs 1200). Surface time from 500-800ms to ~150-250ms.

**Risk**: Low. The filter logic is deterministic.

### Fix: Denormalize clan aggregations

**Approach**: The clan landing surfaces (`clans`, `clans_best`, `recent_clans`) all run the same expensive aggregation:
```sql
SELECT clan_id, name, tag, members_count,
       SUM(player.pvp_wins) AS total_wins,
       SUM(player.pvp_battles) AS total_battles,
       COUNT(player) FILTER (WHERE days_since_last_battle <= 30) AS active_members
FROM warships_clan
JOIN warships_player ON ...
GROUP BY clan_id
```

This is a group-by aggregation with indexed joins across 275K players every time.

**Changes**:

1. Add denormalized fields to `Clan` model: `total_wins`, `total_battles`, `active_member_count`, `clan_wr`
2. Update these during `update_clan_members()` (already iterates all members)
3. Replace the aggregation queries with simple column reads

**Expected improvement**: 200-400ms per clan surface → <50ms. Saves ~1s total per warm cycle.

**Risk**: Medium. Requires migration. Values may drift slightly between refreshes — acceptable since these are display-only.

---

## B3: Per-visit task fan-out — 349 tasks per 12 visitors

### Root Cause

Each player profile visit triggers lazy-refresh checks that dispatch multiple Celery tasks:
- `update_battle_data_task` — if battles are >1h stale
- `update_snapshot_data_task` — if snapshots are >1h stale
- `update_ranked_data_task` — if ranked data is >24h stale
- `update_activity_data_task` — if activity is >1h stale
- `update_player_efficiency_data_task` — if efficiency is >24h stale
- `update_player_clan_battle_data_task` — if clan battle data is needed

Average per visitor: ~29 tasks. At 8 concurrent visitors, that's ~230 tasks in the queue simultaneously. With 3 hydration workers, drain time is:

| Task | Count | Avg Duration | Worker Time |
|------|-------|-------------|-------------|
| `update_battle_data_task` | 95 | 0.27s | 26s |
| `update_ranked_data_task` | 70 | 0.73s | 51s |
| `update_player_efficiency_data_task` | 64 | 0.50s | 32s |
| `update_snapshot_data_task` | 26 | 1.11s | 29s |
| `update_player_clan_battle_data_task` | 64 | 0.03s | 2s |
| `update_activity_data_task` | 13 | 0.09s | 1s |

Total: 141s worker time / 3 workers = ~47s wall-clock drain time.

### This is not a bug

The lazy-refresh pattern is correct — it ensures visitors see data quickly (cached) while freshening stale data in the background. The 47s drain time doesn't affect user experience because the visitor already got their cached response in <300ms.

### Optimization: Coalesce per-player refresh tasks

**Approach**: Instead of dispatching 5-6 separate tasks per stale player, dispatch a single `refresh_player_cache_task(player_id)` that runs all stale checks and updates in one task.

**Changes**:

1. New task `refresh_player_cache_task(player_id)` that:
   ```python
   player = Player.objects.get(player_id=player_id)
   if player_battle_data_needs_refresh(player):
       update_battle_data(player_id)
   if player_snapshot_needs_refresh(player):
       update_snapshot_data(player_id)
       update_activity_data(player_id)
   if player_ranked_data_needs_refresh(player):
       update_ranked_data(player_id)
   ```

2. Replace the 5-6 individual `delay()` calls in `fetch_player_summary()` with a single `refresh_player_cache_task.delay(player_id)` with a dispatch dedup key.

**Expected improvement**: 349 tasks → ~50-70 tasks (one per unique player + some clan tasks). Eliminates task dispatch overhead and reduces queue depth.

**Risk**: Medium. The individual tasks have their own locking and dedup. Need to ensure the coalesced task respects those semantics. Also, a single failed update shouldn't prevent other updates from running.

**Trade-off**: Individual tasks allow more granular retries and partial success. Coalesced task is all-or-nothing per player. Mitigate with try/except per update block.

---

## B4: `clan_detail` and `clan_battle_seasons` — p95 = 617ms, 580ms

### Root Cause

These endpoints are moderately slow but not critical. The `clan_detail` view fetches clan data and checks freshness. The `clan_battle_seasons` view assembles clan battle season data with member participation stats.

### Optimization: Not urgent

These endpoints are called once per clan visit and p95 < 700ms is acceptable. The clan battle seasons endpoint in particular involves complex aggregations that are already cached in Redis. No immediate action needed.

---

## Implementation Priority

| Fix | Target | Impact | Effort | Risk | Status |
|-----|--------|--------|--------|------|--------|
| B1: Cache clan_members response | p95: 1598ms → ~200ms | High | 1-2h | Low | **Done** |
| B2b: Reduce players_best candidate limit | Surface: 800ms → ~200ms | Medium | 1h | Low | **Done** |
| B2a: Parallelize landing surface warming | 212s → ~40s | Medium | 2-3h | Low | **Done** |
| B2c: Denormalize clan aggregations | Surface: 400ms → ~50ms | Medium | 2-3h | Medium | **Done** |
| B3: Dedup per-player refresh dispatches | 349 → fewer tasks per burst | Low | 1h | Low | **Done** |
| B4: Clan detail/seasons | Not urgent | — | — | — | Skipped |

### Implementation Notes (2026-03-28)

**B1**: Added 5-minute Redis cache for serialized clan member payload in `views.py:clan_members`. Cache key `clan:members:{clan_id}`, invalidated in `update_clan_data()` and `update_clan_members()`.

**B2b**: Reduced `LANDING_PLAYER_BEST_CANDIDATE_LIMIT` from 1200 to 400 in `landing.py`.

**B2a**: Parallelized `warm_landing_page_content()` using `ThreadPoolExecutor(max_workers=4)`. Falls back to sequential execution in tests (`LANDING_WARM_PARALLEL = False`).

**B2c**: Added denormalized fields to Clan model (`cached_total_wins`, `cached_total_battles`, `cached_active_member_count`, `cached_clan_wr`). Updated in `update_clan_members()`. Landing queries use `Coalesce` to prefer cached values with live aggregation fallback. Migration: `0033_clan_cached_aggregations.py`.

**B3**: Simplified from full task coalescing to per-player dispatch dedup (cache key `player:refresh_dispatched:{player_id}`, 60s TTL). Lower risk than full coalescing, still eliminates duplicate fan-out from concurrent page loads.
