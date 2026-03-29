# Runbook: Recently-Viewed Player Cache Warming

**Created**: 2026-03-29
**Status**: Research complete, implementation pending

## Goal

Ensure that players returning to check their stats get instant (cache-hit) responses by keeping a durable queue of ~100 most recently viewed player IDs perpetually warm in the `player:detail:v1:{id}` Redis cache.

## Current Warming Landscape

Five warming systems exist today. Understanding them all is necessary to place the recently-viewed warmer correctly.

| System | Entities | Selection | Frequency | Cost | Cache target |
|--------|----------|-----------|-----------|------|-------------|
| **Startup warmer** | Landing + 20 players + 10 clans + ~500 bulk | Sequential: landing → hot → bulk | On boot (5s delay) | High (WG API for hot, DB for bulk) | Landing keys + `player:detail:v1:*` |
| **Hot entity warmer** | 20 players, 10 clans | Pinned + top-visited(7d) + last_lookup + top-scored | Every 30 min | **High** — ~8 WG API calls/player | Refreshes source data in DB, then **invalidates** `player:detail:v1:*` (does NOT repopulate) |
| **Bulk entity cache loader** | ~500 players, ~100 clans | Top 50 by score + members of 25 best clans + pinned | Every 12h | **Low** — DB reads + serialization only | `player:detail:v1:*` via `cache.set_many()` |
| **Landing page warmer** | 40 recent players (card payloads) | `last_lookup` DESC | Every 55 min | Low — lightweight ORM query | `landing:recent_players:*` (card data, NOT detail cache) |
| **Lazy refresh** | Any player on request | Cache miss + stale detection | On-demand | Variable — WG API if stale | Per-entity on miss |

### The gap for returning visitors

A player who visits once, then returns 2-24 hours later, will likely get a **cache miss** unless they happen to be in one of the warm cohorts:

1. **Hot entity warmer** — Only 20 player slots shared across 4 candidate pools (pinned, top-visited, last_lookup, top-scored). A player viewed 6 hours ago is easily displaced by a high-scored player never actually visited. And each slot costs ~8 WG API calls, so the limit can't simply be raised.
2. **Bulk loader** — Selects by player_score and best-clan membership, not by visit recency. A casual player checking their own stats will never appear here.
3. **Landing warmer** — Builds card payloads (`landing:recent_players:*`) for the homepage, not full detail payloads (`player:detail:v1:*`). Doesn't help the detail endpoint.
4. **Lazy refresh** — Fires on the first request after expiry, so the returning visitor's first page load is always the slow one.

The `player:detail:v1:{id}` entry (24h TTL) either expired or was never written for most returning visitors.

## Proposed Design: Recently-Viewed Cohort in the Bulk Loader

### Why integrate with the bulk loader, not a standalone task

The bulk loader already does exactly what we need: DB-only serialization into `player:detail:v1:*` via `cache.set_many()`. Adding a fourth cohort ("recently viewed") is the smallest change, avoids a new task/schedule/lock, and ensures the recently-viewed set is warmed at the same points as everything else — both on startup and every 12h.

However, **12h is too infrequent** for returning visitors. A player viewed at hour 0 might return at hour 2. The bulk loader's 12h cycle won't have refreshed their cache by then (24h TTL means the entry from the previous cycle may still be alive, but if the player wasn't in the previous cycle's cohorts, there's nothing to keep alive).

**Solution:** Add the recently-viewed cohort to the bulk loader AND run a lightweight supplemental task every 10 minutes that only re-caches recently-viewed players whose `player:detail:v1:*` entry is missing or about to expire. This supplemental task is cheap (100 cache existence checks, serialization only for misses).

### Data structure

```
Key:    recently_viewed:players:v1
Type:   Plain Django cache key holding a JSON list of player_id ints, ordered most-recent-first
Cap:    100 entries (env: RECENTLY_VIEWED_PLAYER_LIMIT, default 100)
TTL:    None (persistent until evicted or Redis restart)
```

The codebase uses Django's built-in `RedisCache` backend (not `django-redis`), so raw Redis commands like ZADD are not available through the cache API. A plain cache key holding a list is framework-portable and works identically under `LocMemCache` (dev/tests) and `RedisCache` (production). The read-modify-write has a small race window, but this is a best-effort queue — a missed push is harmless since the player's `last_lookup` in Postgres serves as a durable fallback.

### Write path

In `PlayerViewSet.get_object()`, after `obj.save()` and `invalidate_landing_recent_player_cache()` (around `views.py:190`):

```python
from warships.data import push_recently_viewed_player
push_recently_viewed_player(obj.player_id)
```

Implementation in `data.py` — read-modify-write with dedup and cap:
```python
def push_recently_viewed_player(player_id: int) -> None:
    current = cache.get(RECENTLY_VIEWED_CACHE_KEY) or []
    if player_id in current:
        current.remove(player_id)
    current.insert(0, player_id)
    cache.set(RECENTLY_VIEWED_CACHE_KEY, current[:RECENTLY_VIEWED_PLAYER_LIMIT], timeout=None)
```

Negligible latency. Silent failure — this is best-effort.

### Bulk loader integration (Cohort 4)

In `bulk_load_player_cache()`, after Cohort 3 (pinned), add:

```python
# Cohort 4: recently-viewed players
recently_viewed_ids = get_recently_viewed_player_ids()
missing_rv = [pid for pid in recently_viewed_ids if pid not in seen_ids]
if missing_rv:
    top_players.extend(
        Player.objects
        .filter(player_id__in=missing_rv)
        .select_related('clan', 'explorer_summary')
    )
```

This means startup warming and the 12h periodic cycle both include recently-viewed players automatically.

### Supplemental warm task (10-minute cycle)

```python
@shared_task(bind=True, soft_time_limit=300)
def warm_recently_viewed_players_task(self):
    """Re-cache recently-viewed players whose detail cache is missing."""
    ...
```

Lock key: `warships:tasks:warm_recently_viewed_players:lock` (15-minute timeout).

Steps:
1. Read player IDs from `recently_viewed:players:v1` cache key
2. `cache.get_many([player:detail:v1:{id} for id in ids])` — single round-trip to check which are cached
3. For each miss, serialize from DB via `PlayerSerializer.to_representation()` and `cache.set()` with 24h TTL
4. Log: `"warm_recently_viewed: {n_total} tracked, {n_hit} cached, {n_miss} re-serialized"`

For 100 players with mostly-warm caches, this is a single `GET_MANY` + a handful of serializations. Negligible cost.

### Startup integration

The `startup_warm_all_caches` management command runs: landing → hot → bulk. Since the recently-viewed cohort is part of the bulk loader (Cohort 4), it's automatically included. No changes needed to the startup sequence.

On a fresh boot (empty Redis), the cache key will also be empty — no recently-viewed players to warm. This is correct: there are no recent visitors yet. The list populates organically from incoming traffic.

## Synergy Analysis: How All Warmers Work Together

### Lifecycle of a returning visitor's cache entry

```
t=0h   Player visits → get_object() sets last_lookup, push_recently_viewed_player() adds to ZSET
       → Response serialized from DB (cache miss), lazy refresh enqueued if stale
       → Bulk cache key player:detail:v1:{id} written by normal response path? NO — only bulk loader writes these.
         But hot entity warmer may pick this player up via last_lookup at next 30m cycle.

t=0.5h Hot entity warmer runs → player may be in top 20 (last_lookup recency).
       If selected: source data refreshed via WG API, detail cache NOT directly written (hot warmer refreshes source, not detail cache).

t=0h+10m  Recently-viewed supplemental task runs → checks ZSET, finds player, checks player:detail:v1:{id}.
           Cache miss → serializes from DB → writes to detail cache. ✅ Player is now warm.

t=12h  Bulk loader runs → recently-viewed cohort included → re-serialized from DB → cache refreshed.

t=24h  Detail cache TTL expires. If player still in ZSET (viewed within last 100 visits),
       next 10m warm cycle re-caches them. If evicted from ZSET, they fall back to lazy refresh.
```

### Overlap matrix

| Player type | Hot warmer (30m) | Bulk loader (12h) | Recently-viewed (10m) | Landing warmer (55m) | Net coverage |
|-------------|:---:|:---:|:---:|:---:|:---|
| Pinned player | Yes (source refresh) | Yes (detail cache) | If recently viewed | If recently viewed | Fully covered |
| Top-scored player | Maybe (if in top 20) | Yes (Cohort 1) | If recently viewed | No | Fully covered |
| Best-clan member | No | Yes (Cohort 2) | If recently viewed | No | Fully covered |
| **Casual returning visitor** | Maybe (if in top 20) | **No** | **Yes** ✅ | Card only (not detail) | **Now covered** |
| One-time visitor (>100 views ago) | No | No | No (evicted from ZSET) | No | Lazy refresh only |

### Complementary roles (no redundant work)

- **Hot entity warmer** refreshes **source data** (WG API → DB). It ensures the DB has fresh numbers. It does NOT write `player:detail:v1:*` directly.
- **Bulk loader** serializes **DB → Redis detail cache**. It ensures the detail endpoint can serve from cache. It does NOT call WG APIs.
- **Recently-viewed warmer** is a **targeted subset of the bulk loader's job**, running more frequently (10m vs 12h) for a smaller set (100 vs 500+). It fills the gap between "source data is fresh" and "detail cache exists."
- **Landing warmer** builds **card payloads** for the homepage. Different cache keys, different data shape. No overlap with detail caching.
- **Lazy refresh** is the **safety net** for everything else. The recently-viewed warmer reduces how often lazy refresh fires for returning visitors.

### Potential concern: invalidation thrashing

When the hot entity warmer refreshes a player's source data via `update_player_data()`, it calls `invalidate_player_detail_cache()` which deletes the `player:detail:v1:{id}` key. If the recently-viewed warmer then runs before the bulk loader, it re-serializes from the now-updated DB — which is actually the **desired behavior** (serving fresh data).

Worst case: a player in both the hot warmer set and the recently-viewed set gets invalidated + re-serialized once per 30m cycle. At ~5-10 KB per payload, this is negligible.

## Implementation Plan

### Files to modify

| File | Change |
|------|--------|
| `server/warships/data.py` | Add `push_recently_viewed_player()`, `get_recently_viewed_player_ids()`, `warm_recently_viewed_players()`. Add Cohort 4 to `bulk_load_player_cache()`. |
| `server/warships/views.py` | Call `push_recently_viewed_player(player_id)` after `obj.save(update_fields=...)` in `get_object()` |
| `server/warships/tasks.py` | Add `warm_recently_viewed_players_task()` |
| `server/warships/signals.py` | Register the 10-minute periodic task in Beat schedule |

### Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `RECENTLY_VIEWED_PLAYER_LIMIT` | 100 | Max players in the recently-viewed ZSET |
| `RECENTLY_VIEWED_WARM_MINUTES` | 10 | Supplemental warm cycle interval |

### Tests to add

- `test_push_recently_viewed_player` — ZADD + trim behavior (or JSON list equivalent under LocMemCache)
- `test_warm_recently_viewed_caches_missing` — Verify cache population for uncached players
- `test_warm_recently_viewed_skips_cached` — No-op for already-cached players
- `test_recently_viewed_cap` — Oldest entries evicted at cap
- `test_bulk_loader_includes_recently_viewed` — Cohort 4 appears in bulk loader output
- `test_view_triggers_push` — Integration: player detail request adds to ZSET

### Rollout considerations

- **Redis memory**: 100 serialized payloads at ~5-10 KB = ~0.5-1 MB. Negligible.
- **No migration**: No model changes — only Redis keys.
- **Startup**: Automatically included via bulk loader. No changes to `startup_warm_all_caches`.
- **Monitoring**: Log warm cycle stats (`n_total`, `n_hit`, `n_miss`, `n_serialized`).

## Open Questions

1. **Should clans get the same treatment?** Clan pages are less likely to be revisited by the same user, but the pattern is identical if needed later.
2. **Redis restart recovery**: The ZSET is volatile. After restart it rebuilds from traffic. Alternatively, on startup before the bulk loader runs, seed the ZSET from `Player.objects.order_by('-last_lookup')[:100]` — the data is already in Postgres.
3. **Should the 10-minute warmer also enqueue lazy refreshes for very stale source data?** Probably not — keep it cheap and let the hot entity warmer or next actual visit handle source freshness.
