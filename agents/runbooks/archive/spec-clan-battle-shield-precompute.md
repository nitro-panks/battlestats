# Clan Battle Shield Precompute — Spec

**Date:** 2026-03-18  
**Status:** Superseded  
**Scope:** Eliminate the on-demand hydration pattern for clan battle shields. Precompute and persist shield-ready data so it is immediately available to the client. Refresh lazily on player invocation.

## Superseded Note

This document reflects the original shield-precompute rollout. The current durability policy is defined in `agents/work-items/clan-list-shield-badge-durability-spec.md`.

The important behavioral difference is that `clan_members()` no longer refreshes stale shield summaries during response generation. Shield freshness now belongs to slower producer lanes with a dedicated badge cadence (`CLAN_BATTLE_BADGE_REFRESH_DAYS`, default `14`).

---

## Problem Statement

Clan battle shield icons (the colored `faShieldHalved` icons indicating a player's CB history) currently rely on a cache-first, hydrate-on-miss pattern:

1. When the clan members view loads, the server checks each member for a Redis cache entry (`clan_battles:player:{account_id}`, TTL 6h).
2. **Cache miss → background Celery task** dispatched per player via `queue_clan_battle_hydration()`, throttled to 8 in-flight slots.
3. The client sees `clan_battle_hydration_pending: true` and must poll or wait for the data to arrive.
4. The player detail page reads from `PlayerExplorerSummary` (DB) when available, falling back to Redis cache.

**Issues with the current approach:**

- **Cold start penalty.** After a cache TTL expires (6h) or Redis restart, every clan view triggers a wave of background tasks. Shields appear blank until hydration completes.
- **Unnecessary API pressure.** Clan battle stats change only when a new CB season ends or the player plays CB. Most refreshes return identical data.
- **Complex orchestration.** The `queue_clan_battle_hydration()` system with in-flight slots, dispatch dedup keys, broker-unavailable fallbacks, and `X-Clan-Battle-Hydration-*` response headers is substantial machinery for what is fundamentally a rarely-changing value.
- **Inconsistent sources.** The serializer consults `PlayerExplorerSummary` (DB) first, then falls back to Redis cache via `get_player_clan_battle_summary()`. Two sources of truth makes debugging harder.

## Design Principle

**Clan battle shield data rarely changes once calculated.** A player's CB eligibility (`is_clan_battle_enjoyer`) and shield color (win rate bracket) will only change when:

- They participate in a new CB season (happens at most a few times per year)
- The current running season accumulates enough games to shift their aggregate win rate across a color bracket

This means the data should be served from a **durable, pre-computed store** (the DB) rather than an **ephemeral cache** that must be continuously refreshed.

---

## Proposed Architecture

### Single Source of Truth: `PlayerExplorerSummary`

The `PlayerExplorerSummary` model already stores the fields needed:

- `clan_battle_seasons_participated` (int)
- `clan_battle_total_battles` (int)
- `clan_battle_overall_win_rate` (float)
- `clan_battle_summary_updated_at` (datetime)

These fields are already populated by `_persist_player_clan_battle_summary()` when hydration completes. The change is to **always read from the DB** and **never fall through to cache-based hydration at request time**.

### Read Path (Fast, Synchronous)

**Clan members view** (`clan_members()` in views.py):

1. Add `select_related('explorer_summary')` to the members queryset (not currently present — must be added to avoid N+1 queries).
2. Compute `is_clan_battle_player` and `clan_battle_win_rate` from the prefetched `PlayerExplorerSummary` fields.
3. Return immediately — no hydration pending, no background tasks, no polling headers.
4. Remove `clan_battle_hydration_pending` from the response. Remove `X-Clan-Battle-Hydration-*` headers.

**Player detail** (`PlayerSerializer`):

1. Read `clan_battle_header_*` fields from `PlayerExplorerSummary` only (already the primary path).
2. Remove the `get_player_clan_battle_summary(allow_fetch=False)` fallback.

**Landing page** (landing players payload):

1. Same — read from `PlayerExplorerSummary`, no cache fallback.

**Result:** Shield data is immediately available on every request. Players whose `PlayerExplorerSummary` has never been populated show no shield (same as `clan_battle_header_eligible: false`).

**Note on `clan_battle_hydration_pending`:** The client already ignores this field — `ClanMembers.tsx` never renders loading/placeholder state for it (unlike `efficiency_hydration_pending` which shows a message). Removal is zero-risk on the client side.

### Write Path (Lazy, On-Invocation)

When a player is "invoked" — meaning their data is actively requested — trigger a **background refresh** of their clan battle data. This covers two entry points:

1. **Player detail page load.** When `/api/player/<name>/` is served, if the player's `clan_battle_summary_updated_at` is stale (configurable, default 7 days) or null, enqueue a single `update_player_clan_battle_data_task` for that player.

2. **Clan members view load.** When `/api/fetch/clan_members/<clan_id>/` is served, identify members whose `clan_battle_summary_updated_at` is stale or null and enqueue refresh tasks for them, respecting the existing in-flight slot limit.

The background task calls `fetch_player_clan_battle_seasons()` → `_persist_player_clan_battle_summary()`, which writes to `PlayerExplorerSummary`. On completion, if the shield data actually changed (win rate crossed a color bracket), the task can optionally flag this for client-side awareness — but this will be rare enough that the client can simply pick up the new value on next page load.

### Staleness Threshold

```
CLAN_BATTLE_SUMMARY_STALE_DAYS = 7  (env: CLAN_BATTLE_SUMMARY_STALE_DAYS, default 7)
```

A 7-day staleness window means:

- Players invoked daily will refresh weekly — sufficient since CB seasons last weeks.
- Players not invoked at all won't waste API budget.
- The incremental player refresh task (Phase 1) already calls `save_player()` which does NOT refresh clan battle data, so this is the only refresh path for CB stats.

### Backfill

Players who have **never** had their `PlayerExplorerSummary` clan battle fields populated will show no shield. There are three organic backfill paths:

1. **On-invocation refresh** (described above) — any player viewed on the site gets populated.
2. **Incremental player refresh** (Phase 1) — extend `_refresh_player()` to optionally call `fetch_player_clan_battle_seasons()` when the player's `clan_battle_summary_updated_at` is null. This provides passive backfill for active players.
3. **One-time backfill management command** (optional) — a lightweight command that iterates players with `clan_battle_summary_updated_at IS NULL` and `pvp_battles >= 40` (likely CB candidates) and populates them. Run once, then discard.

---

## Changes by File

### Server

**`server/warships/views.py` — `clan_members()`**

- Remove call to `queue_clan_battle_hydration(members)`.
- Remove `pending_clan_battle_player_ids` and `clan_battle_hydration_pending` from member row construction.
- Remove `X-Clan-Battle-Hydration-*` response headers.
- Compute `is_clan_battle_player` and `clan_battle_win_rate` directly from prefetched `PlayerExplorerSummary` fields.
- Add stale-check: for members with stale/null `clan_battle_summary_updated_at`, enqueue refresh tasks (via existing `queue_clan_battle_data_refresh()`, reusing the in-flight slot pattern but decoupled from pending status in the response).

**`server/warships/serializers.py` — `PlayerSerializer`**

- In `_get_clan_battle_header_payload()`, remove the `fallback_summary=get_player_clan_battle_summary(...)` call. Read exclusively from `PlayerExplorerSummary`.
- This simplifies the method to a direct DB read with no cache lookup.

**`server/warships/serializers.py` — `ClanMemberSerializer`**

- Remove `clan_battle_hydration_pending` field.

**`server/warships/views.py` — player detail endpoint**

- After serialization, if `clan_battle_summary_updated_at` is stale or null (and player is not hidden), fire a single `update_player_clan_battle_data_task.delay(player_id)` in the background.

**`server/warships/data.py`**

- Remove `clan_battle_player_hydration_needs_refresh()` (cache-miss check — no longer needed).
- Remove `queue_clan_battle_hydration()` (the complex orchestration function).
- Add `clan_battle_summary_is_stale(player)` — simple check: `clan_battle_summary_updated_at is None or (now - updated_at).days >= CLAN_BATTLE_SUMMARY_STALE_DAYS`.
- `get_published_clan_battle_summary_payload()` — remove fallback_summary parameter. Read from `PlayerExplorerSummary` only, return zeros/null when not populated.
- `get_player_clan_battle_summary()` — no longer called in the read path. Keep for internal use by tasks (it backs `fetch_player_clan_battle_seasons()`). Remove any direct calls from views or serializers.
- `get_player_clan_battle_summaries()` (plural/batch) — currently reads from Redis cache, used by `landing.py`. Replace implementation to read from `PlayerExplorerSummary` via a single bulk query, or remove in favor of direct explorer summary reads in `landing.py`.

**`server/warships/landing.py`**

- Replace `get_player_clan_battle_summaries()` cache-based reads with `PlayerExplorerSummary` DB reads. Both featured and recent player payload builders (~L335, ~L623) currently call `get_player_clan_battle_summaries()` → Redis cache → `get_published_clan_battle_summary_payload(fallback_summary=...)`. Switch to reading from `PlayerExplorerSummary` directly (bulk-query explorer summaries for all player IDs in the payload, then compute `is_clan_battle_player` and `clan_battle_win_rate` from the DB fields).
- Remove the `fallback_summary` pattern from landing payload construction.

**`server/warships/management/commands/incremental_player_refresh.py`**

- In `_refresh_player()`, after `save_player()`, add: if `clan_battle_summary_updated_at` is null, call `fetch_player_clan_battle_seasons(player.player_id)` to organically backfill. Note: the player must be reloaded or `PlayerExplorerSummary` queried separately after `save_player()` since the initial query does not `select_related('explorer_summary')`.

**`server/warships/tasks.py`**

- `queue_clan_battle_data_refresh()` — keep as-is (still used for background dispatch).
- `update_player_clan_battle_data_task()` — keep as-is.
- Remove `is_clan_battle_data_refresh_pending()` if no longer referenced by views.
- Add `maybe_refresh_clan_battle_data(player)` — shared helper used by both `clan_members()` and the player detail view. Checks `clan_battle_summary_is_stale(player)`, dispatches `queue_clan_battle_data_refresh()` if stale. Single entry point to avoid duplicating stale-check logic.

### Client

**`client/app/components/ClanMembers.tsx`**

- Remove references to `clan_battle_hydration_pending`.
- Shields render immediately from `is_clan_battle_player` + `clan_battle_win_rate` — no loading/pending state needed.

**`client/app/components/clanMembersShared.ts`**

- Remove `clan_battle_hydration_pending` from the type definition.

**`client/app/components/entityTypes.ts`**

- No changes needed — `clan_battle_header_*` fields remain.

**`client/app/components/PlayerDetail.tsx`**

- No changes needed — already reads from `clan_battle_header_*` which will now always come from DB.

---

## What Is NOT Changing

- **Redis cache for per-player season stats** (`clan_battles:player:{account_id}`, TTL 6h) — still used internally by `_get_player_clan_battle_season_stats()` to avoid redundant API calls within the same task execution. Not exposed to the read path.
- **Clan-level battle summaries** (`fetch_clan_battle_seasons()`, `refresh_clan_battle_seasons_cache()`) — these aggregate across an entire clan's roster for the clan battle seasons page. Separate concern, unchanged.
- **`warm_clan_battle_summaries_task()`** — still useful for pre-caching the clan-level aggregate view.
- **WG API call pattern** — same endpoints (`clans/season/`, per-player stats), same rate limiting.
- **`faShieldHalved` rendering** — same client component, same color logic.
- **Eligibility criteria** — `is_clan_battle_enjoyer()` (>=40 battles, >=2 seasons) unchanged.

---

## Migration Plan

1. **Implement server-side changes** — make read path DB-only, add stale-check dispatch.
2. **Remove hydration pending from serializer and client** — clean up `clan_battle_hydration_pending` field and `X-Clan-Battle-Hydration-*` headers.
3. **Extend incremental player refresh** — add passive CB backfill for players with null `clan_battle_summary_updated_at`.
4. **Optional: run one-time backfill** — populate `PlayerExplorerSummary` CB fields for existing players who have cached data or meet the `pvp_battles >= 40` heuristic.
5. **Clean up dead code** — remove `queue_clan_battle_hydration()`, `clan_battle_player_hydration_needs_refresh()`, `is_clan_battle_data_refresh_pending()`, and associated constants.

---

## Configuration

| Variable                         | Default | Purpose                                                                            |
| -------------------------------- | ------- | ---------------------------------------------------------------------------------- |
| `CLAN_BATTLE_SUMMARY_STALE_DAYS` | `7`     | Days before a player's CB summary is considered stale and re-fetched on invocation |

Existing env vars remain valid:
| Variable | Default | Purpose |
|---|---|---|
| `CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT` | `8` | Max concurrent CB refresh tasks (reused for stale dispatch throttling in clan view) |
| `CLAN_BATTLE_ENJOYER_MIN_BATTLES` | `40` | Minimum CB battles for shield eligibility |
| `CLAN_BATTLE_ENJOYER_MIN_SEASONS` | `2` | Minimum CB seasons for shield eligibility |

---

## Risks & Mitigations

| Risk                                                                | Severity | Mitigation                                                                                     |
| ------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------- |
| Players with no prior hydration show no shield until invoked        | Low      | Organic backfill via incremental refresh + on-invocation. Optional one-time backfill command.  |
| Stale data visible for up to 7 days                                 | Low      | CB stats change infrequently. 7-day window is generous. Configurable via env.                  |
| Removing `clan_battle_hydration_pending` breaks client expectations | Low      | Client already handles the field being false — just remove the field and the conditional.      |
| Background task fails → no shield update                            | Low      | Same as today. Retry on next invocation (7-day window resets). Error doesn't affect read path. |

---

## Success Criteria

- [ ] Clan members view returns shield data immediately with zero background tasks for players that have been previously hydrated
- [ ] Player detail page returns shield data immediately from DB
- [ ] Stale/null players trigger a single background refresh on invocation
- [ ] `X-Clan-Battle-Hydration-*` headers and `clan_battle_hydration_pending` field removed
- [ ] Incremental player refresh passively backfills CB data for active players with null summaries
- [ ] All existing shield rendering (ClanMembers, PlayerDetail, Landing) continues to work unchanged
- [ ] No increase in WG API call volume compared to current hydration pattern
