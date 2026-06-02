# Runbook: Battle-history read-before-write empty-cache race

_Created: 2026-05-24_
_Context: User played randoms as `Punkhunter25` (na) but the battle-history card showed nothing. Investigation found the data was present — the default "week" window's `as_of` timestamp (15:37:13) was 2 seconds *before* the observation that wrote the events (15:37:15). A page-load read had raced ahead of the same visit's capture write and cached an empty-window payload for the full 5-minute `BATTLE_HISTORY_CACHE_TTL`. The "month" window, fetched after the write, showed the battles correctly._
_Status: shipped 2026-05-24. Invalidation wired into the `record_observation_from_payloads` on_commit hook; new test `BattleHistoryEndpointTests::test_capture_invalidates_empty_cached_window`; full incremental-battles suite 124/124 + curated release gate 243/243 green. Backend-only deploy._

## The race

A single page visit fires two independent flows against the same player:

1. **Read** — `BattleHistoryCard` fetches `GET /api/player/<name>/battle-history?window=week` on mount (`client/app/components/BattleHistoryCard.tsx:459-508`). The view (`server/warships/views.py:battle_history`) computes the payload and caches it for `BATTLE_HISTORY_CACHE_TTL = 5 min` (`server/warships/views.py:1180`) — **including empty payloads**.
2. **Write** — the player-detail fetch on the same visit dispatches `update_battle_data_task` (async). That task records a `BattleObservation`, diffs it against the prior one, and writes `BattleEvent` / `PlayerDailyShipStats` rows.

When (1) lands before (2) commits — the common case for a player who just played and immediately opens their page — the read returns an empty window and caches it. Even after the write completes ~2 s later, the empty payload is served for the rest of the 5-minute TTL. The user reloads, still sees nothing.

The 24h ("day") and other windows have *independent* cache keys (`battle-history:v9:{name}:{period}:{windows}:{mode}`), so whichever window happened to be fetched after the write shows data while the racing one stays empty — exactly the split observed for `Punkhunter25` (week empty, month populated).

## Fix

Invalidate the battle-history cache when a capture writes new events, reusing the `transaction.on_commit` hook already added in `record_observation_from_payloads` for the player-detail cache (see `runbook-last-battle-date-from-observation-2026-05-23.md`).

- **`invalidate_battle_history_cache(realm, player_name)`** — new helper in `server/warships/views.py`, placed next to `_battle_history_cache_key` so it stays in sync with the key format. It enumerates `BATTLE_HISTORY_WINDOWS × BATTLE_HISTORY_MODES` (4 × 3 = 12 keys) and `cache.delete_many`s them. Self-maintaining: a new window or mode added to those constants is covered automatically.
- **on_commit hook** — `record_observation_from_payloads` (`server/warships/incremental_battles.py`) now calls both `invalidate_player_detail_cache` and `invalidate_battle_history_cache` from its post-commit callback, fired only when `events or ranked_events` is non-empty (i.e. real new battles).

After the write commits, the stale empty entries are gone, so the next fetch (a reload, or a window/mode switch) recomputes against the freshly written rows. Non-empty payloads keep the full 5-min cache, and genuinely-empty players keep caching normally — no recompute-load increase (the reason this was chosen over a short empty-TTL: a 30s empty TTL would still show nothing on a sub-30s reload, whereas on-write invalidation surfaces data on any reload after the write lands).

### Why not invalidate from `update_battle_data` directly

`record_observation_from_payloads` is the single chokepoint for "new BattleEvents written" across every capture path (visit-driven `update_battle_data`, the PoC poll, the daily floor sweep, baseline establishment). Hooking the invalidation there covers all of them uniformly and reuses the existing commit callback.

### Import note

The on_commit callback does a lazy `from warships.views import invalidate_battle_history_cache`. Lazy because `views` is a heavy module and the capture path lives below it in the import graph; deferring to call-time (only when events are created) avoids any import-order coupling and matches the existing lazy `from warships.data import invalidate_player_detail_cache` in the same callback.

## Residual UX gap (not fixed here)

This fixes the *caching* — the empty window no longer persists. It does **not** auto-refresh the user's *current* render: the page that fetched the empty payload still shows an empty card until the user reloads or switches windows. Closing that gap cleanly would mean mirroring the existing `X-Ranked-Observation-Pending` pattern (`server/warships/views.py:1188-1192` + the frontend retry loop at `BattleHistoryCard.tsx:479-490`) with an `X-Battle-History-Pending` signal driven by an in-flight marker for `update_battle_data_task`. That task has no clean dispatch-dedup key today (it goes through `_delay_task_safely` / `_dispatch_async_refresh`, neither of which exposes a queryable "running" marker like `queue_ranked_observation_refresh` does), so adding one is a larger change deferred as a follow-up.

## Verification

```bash
cd server
python -m pytest warships/tests/test_incremental_battles.py::BattleHistoryEndpointTests -x --tb=short
```

On production, after deploy: for a player who just played, load their page, then reload — the battle-history card should populate on the reload rather than staying empty for ~5 min. (Confirmed mechanically by the new test; the residual gap above means the *first* render may still be empty until a reload.)
