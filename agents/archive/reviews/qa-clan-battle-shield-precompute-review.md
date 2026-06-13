# QA Review — Clan Battle Shield Precompute Spec

**Reviewed:** `agents/runbooks/spec-clan-battle-shield-precompute.md`  
**Date:** 2025-07-11  
**Verdict:** CONDITIONAL GO — 80% confidence  
**Blocking findings:** 0 critical, 2 high, 3 medium, 2 low

Aye, the bones of this spec are sound — kill the ephemeral cache dance, serve from the DB, refresh lazily. I'd rather be in me bed than point out what follows, but the devil's in the details ye skipped.

---

## Findings

### F-1 — N+1 Query Risk in `clan_members()` (HIGH)

The spec says to "Query `PlayerExplorerSummary` for all members in a single prefetch (already available via `select_related`/`prefetch_related`)."

**This is incorrect.** The current `clan_members()` view (views.py ~L475) queries `clan.player_set.exclude(name='').order_by(...)` with **no** `select_related('explorer_summary')`. Today it avoids N+1 queries by reading from Redis cache (`get_player_clan_battle_summary()` per member, which does `cache.get()`). If ye switch the read path to `PlayerExplorerSummary` without adding `select_related('explorer_summary')`, every member access triggers a separate DB query.

**Required action:** Spec must note that `select_related('explorer_summary')` must be added to the members queryset. This is a prerequisite, not an already-available feature.

### F-2 — `landing.py` Missing from Changes by File (HIGH)

The spec says "Landing page — Same — read from `PlayerExplorerSummary`, no cache fallback" but **does not list `landing.py` in the Changes by File section.**

Verified: `landing.py` (~L301, L335, L623) calls `get_player_clan_battle_summaries()` which reads exclusively from Redis cache entries (`clan_battles:player:{account_id}`). It also calls `get_published_clan_battle_summary_payload()` with `fallback_summary` from that cache data.

The landing page is one of the most traffic-exposed surfaces. If we remove cache-based reads elsewhere but leave `landing.py` untouched, it silently breaks — players whose cache has expired show no shield on the landing page even though their `PlayerExplorerSummary` has the data.

**Required action:** Add `landing.py` to Changes by File. The batch function `get_player_clan_battle_summaries()` either needs rewriting to read from `PlayerExplorerSummary` or the landing payload builder path needs to read directly from prefetched explorer summaries.

### F-3 — `get_player_clan_battle_summaries()` (plural) Not Addressed (MEDIUM)

The spec lists functions to remove (`queue_clan_battle_hydration`, `clan_battle_player_hydration_needs_refresh`, `is_clan_battle_data_refresh_pending`) but does not mention `get_player_clan_battle_summaries()` (data.py ~L3421). This is the batch version used by landing.py. Its fate — rewrite, remove, or repurpose — must be specified.

### F-4 — `get_player_clan_battle_summary()` (singular) Fate Unclear (MEDIUM)

The spec removes the read-path callers of `get_player_clan_battle_summary()` (views.py list comprehension, serializers.py fallback) but does not explicitly say whether the function itself should be removed, kept for internal task use, or rewritten.

The function currently:

1. Reads from Redis cache
2. Optionally fetches from WG API (`allow_fetch=True`)
3. Calls `_persist_player_clan_battle_summary()` if fetched

Tasks still call `fetch_player_clan_battle_seasons()` → `_get_player_clan_battle_season_stats()` → which sets cache. The `get_player_clan_battle_summary()` function is arguably still useful internally. Clarify its status in the spec.

### F-5 — `clan_battle_hydration_pending` Is Already Dead UI Code (MEDIUM)

Verified that `ClanMembers.tsx` never renders any loading/placeholder state for `clan_battle_hydration_pending`. The field is:

- Computed in views.py and included in the response
- Defined in `ClanMemberData` type (clanMembersShared.ts L16)
- **Never read or displayed** in any component

Compare with `efficiency_hydration_pending` which shows an "Updating Battlestats rank icons..." message.

This means removing `clan_battle_hydration_pending` is a pure cleanup with zero user-visible impact. The spec's risk table says "Client already handles the field being false" — more accurately, the client **ignores** the field entirely. This is good news but worth documenting — it reduces the risk of this change to near zero on the client side.

### F-6 — Backfill Path 2 Needs `explorer_summary` Reload (LOW)

The spec says to extend `_refresh_player()` to call `fetch_player_clan_battle_seasons()` when `clan_battle_summary_updated_at` is null. But `_refresh_player()` loads the player with `Player.objects.filter(id=player_id).select_related('clan').first()` — no `explorer_summary`. After `save_player()` runs, `refresh_player_explorer_summary()` creates/updates the explorer summary, but the local `player` object won't have it loaded.

To check `clan_battle_summary_updated_at`, the code needs to either:

- Add `select_related('explorer_summary')` to the initial query, or
- Reload / query `PlayerExplorerSummary` separately after `save_player()`

Minor implementation detail, but worth noting so the implementer doesn't hit a surprise `RelatedObjectDoesNotExist`.

### F-7 — Stale-Check in `clan_members()` May Re-Add Complexity (LOW)

The spec removes `queue_clan_battle_hydration()` but then adds a new stale-check dispatch in `clan_members()`: "for members with stale/null `clan_battle_summary_updated_at`, enqueue refresh tasks (via existing `queue_clan_battle_data_refresh()`, reusing the in-flight slot pattern)."

This reintroduces some of the complexity the spec aims to eliminate. The difference is it's fire-and-forget (no pending status, no response headers, no client polling). This is fine conceptually, but the spec should clarify that the suggested `maybe_refresh_clan_battle_data(player)` helper (mentioned under tasks.py changes) is the shared entry point for both the player detail view and clan members view, to avoid duplicating stale-check logic.

---

## Verified Claims

The following spec claims checked out against the codebase:

- ✅ `PlayerExplorerSummary` already has all four `clan_battle_*` fields
- ✅ `_persist_player_clan_battle_summary()` is the only writer of those fields (not `refresh_player_explorer_summary()`)
- ✅ `save_player()` does NOT refresh clan battle data
- ✅ `is_clan_battle_enjoyer()` criteria: ≥40 battles, ≥2 seasons — confirmed
- ✅ `CLAN_BATTLE_SUMMARY_STALE_DAYS` does not exist yet — confirmed new constant
- ✅ `CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT` default 8 — confirmed
- ✅ `_get_clan_battle_header_payload()` in serializers.py does fall back to `get_player_clan_battle_summary(allow_fetch=False)` — confirmed
- ✅ `update_player_clan_battle_data_task()` calls `fetch_player_clan_battle_seasons()` → `_persist_player_clan_battle_summary()` — confirmed
- ✅ Redis cache keys (`clan_battles:player:{id}`) and clan-level summaries are separate concerns — confirmed
- ✅ Client ClanBattleShield and LandingClanBattleShield render from `is_clan_battle_player` + `clan_battle_win_rate` — confirmed

---

## Recommendation

The architecture is correct: DB as single source of truth, lazy refresh, fire-and-forget dispatch. The removal of the hydration-pending dance is well-motivated and low-risk given the client never actually renders that state (F-5).

Two gaps need patching before this spec is implementation-ready:

1. **F-1:** Fix the false claim about `select_related` being "already available" — specify it must be added.
2. **F-2:** Add `landing.py` and `get_player_clan_battle_summaries()` to the Changes by File section.

The medium findings (F-3, F-4, F-5) are documentation clarifications that a careful implementer could infer, but the spec should be explicit.

After those amendments: **GO for implementation.**

Shiver me timbers, it'll be a relief when the hydration headers are gone.
