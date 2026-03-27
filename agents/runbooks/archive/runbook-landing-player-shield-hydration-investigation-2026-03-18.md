# Runbook: Landing Player Shield Hydration Investigation

_Last updated: 2026-03-18_

_Status: Active investigation and recommendation runbook_

## Purpose

Document the March 18, 2026 investigation into missing landing-page player shield icons.

In this runbook, "shield icon" means the clan-battle shield rendered in landing player rows, not the efficiency sigma icon.

The investigated user-facing symptom was:

1. a landing player row initially renders without the clan-battle shield,
2. clicking through to player detail causes the shield to appear later,
3. returning to landing shows the shield persist for that player.

## Scope

This investigation covered:

1. landing player row serialization in [server/warships/landing.py](server/warships/landing.py),
2. player-detail read and refresh behavior in [server/warships/views.py](server/warships/views.py),
3. player-detail clan-battle seasons fetch behavior in [client/app/components/PlayerClanBattleSeasons.tsx](client/app/components/PlayerClanBattleSeasons.tsx),
4. landing-row rendering in [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx).

This runbook does not cover the efficiency sigma icon except where needed to rule it out.

## Current Behavior

### Landing rows treat clan-battle cache miss as "not a clan-battle player"

Landing row serialization calls:

1. `get_player_clan_battle_summaries(player_ids, allow_fetch=False)` in [server/warships/landing.py](server/warships/landing.py),
2. `is_clan_battle_enjoyer(...)` against the returned summary,
3. `LandingClanBattleShield` in [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx) only when `is_clan_battle_player` is true.

With `allow_fetch=False`, a missing cache entry resolves to an empty season list in [server/warships/data.py](server/warships/data.py).

That means a cache miss is currently indistinguishable from true non-qualification on landing.

### Player detail populates the clan-battle cache through a different path

The player-detail page mounts [client/app/components/PlayerClanBattleSeasons.tsx](client/app/components/PlayerClanBattleSeasons.tsx), which requests:

1. `GET /api/fetch/player_clan_battle_seasons/<player_id>/`

That path eventually calls `fetch_player_clan_battle_seasons(...)`, which uses `_get_player_clan_battle_season_stats(...)` in [server/warships/data.py](server/warships/data.py).

Unlike landing, that helper does fetch and cache clan-battle seasons on cache miss.

Result:

1. landing first sees no cached clan-battle data and emits `is_clan_battle_player=false`,
2. player detail fetches and caches the seasons,
3. later landing reads the now-populated cache and emits the shield.

### Recent-player landing list is especially likely to flip after a click

The player detail read path updates `last_lookup` and calls `invalidate_landing_recent_player_cache()` in [server/warships/views.py](server/warships/views.py).

That creates a specific recent-list sequence:

1. user opens a player from landing,
2. the player becomes part of the recent list,
3. the recent-player cache is invalidated,
4. the detail page's clan-battle seasons fetch warms `clan_battles:player:<id>`,
5. returning to landing rebuilds recent rows with the warmed clan-battle cache,
6. the shield now appears and persists.

The random and best landing-player payloads are cached for one hour in [server/warships/landing.py](server/warships/landing.py), so they can remain stale even after the underlying clan-battle cache is warmed.

## Confirmed Live Reproduction

### Recent-player list reproduction

During this investigation, the following live players reproduced the issue:

1. `GreyViper`
2. `Shinn000`
3. `John_The_Ruthless`
4. `ChrisHansenMindFreak`

Observed pattern for `GreyViper`:

1. before detail fetch, `/api/landing/recent/` returned:
   - `is_clan_battle_player=false`
   - `clan_battle_win_rate=null`
2. fetching player detail and clan-battle seasons produced:
   - `seasons_played=28`
   - `total_battles=1856`
   - `overall_wr=55.6`
   - qualifies as a clan-battle player
3. after that fetch sequence, `/api/landing/recent/` returned:
   - `is_clan_battle_player=true`
   - `clan_battle_win_rate=55.6`

Equivalent live flips were observed for:

1. `Shinn000` from `false/null` to `true/59.8`
2. `John_The_Ruthless` from `false/null` to `true/79.4`
3. `ChrisHansenMindFreak` from `false/null` to `true/75.2`

### Best-player list backend reproduction

The underlying serializer issue also reproduces for best-player rows when bypassing the cached landing payload and rebuilding directly.

Example: `John_The_Ruthless`

1. clear `clan_battles:player:1020639850`,
2. rebuild best landing payload with `force_refresh=True`,
3. row shows `is_clan_battle_player=false` and `clan_battle_win_rate=null`,
4. fetch clan-battle seasons for that player,
5. rebuild best landing payload again with `force_refresh=True`,
6. row changes to `is_clan_battle_player=true` and `clan_battle_win_rate=79.4`.

This confirms the landing serializer itself is cache-dependent, not just the recent-list invalidation path.

## Root Cause

The root cause is a data-source mismatch between landing rows and player detail.

Landing rows currently rely on volatile per-player clan-battle cache entries:

1. cache key: `clan_battles:player:<player_id>`
2. read mode: `allow_fetch=False`
3. cache miss behavior: summarize empty list and publish `false/null`

Player detail uses a warmer, fetch-capable path:

1. player detail page loads without the shield dependency,
2. `PlayerClanBattleSeasons` fetches authoritative seasons,
3. that fetch populates the same cache key,
4. later landing rebuilds finally see enough local data to render the shield.

This is why the issue feels like "hydration": the browser interaction is warming a backend cache that landing had treated as authoritative absence.

## Why The Current Behavior Is Weak

### Problem 1: cache miss is being published as product truth

Landing is not distinguishing:

1. player definitely does not qualify for the shield,
2. Battlestats does not currently have local clan-battle data for that player.

That collapses "unknown" into "false".

### Problem 2: landing correctness depends on unrelated navigation

Opening player detail should not be required to make landing rows accurate.

The current behavior makes player detail act as an accidental cache warmer for landing.

### Problem 3: recent and active lists diverge operationally

Recent rows can self-correct after a click because player detail invalidates the recent-player cache.

Random and best rows can remain stale for up to the landing-player cache TTL because those payloads are not invalidated when clan-battle cache state changes.

## Recommendations

### Priority 1: stop using cache miss as the landing truth source

Recommended fix:

1. persist last-known clan-battle summary fields on a durable player-facing summary surface,
2. read those fields during landing serialization,
3. treat the volatile cache as a refresh source, not the only render source.

Recommended additive fields:

1. `clan_battle_total_battles`
2. `clan_battle_seasons_played`
3. `clan_battle_overall_win_rate`
4. `clan_battle_summary_updated_at`

Best fit:

1. add them to `PlayerExplorerSummary` if landing row denormalization stays centered there,
2. or add them directly to `Player` if they are intended for multiple surfaces beyond explorer-backed summaries.

Why this is the preferred fix:

1. landing can render the last known shield immediately without WG fetches,
2. player detail and landing can share the same local truth source,
3. correctness no longer depends on the `clan_battles:player:<id>` cache being warm at request time.

### Priority 2: if durable summary fields are not added immediately, queue hydration instead of silently publishing false

Interim mitigation:

1. detect visible landing players whose clan-battle cache is missing,
2. queue non-blocking hydration for those players,
3. avoid treating those rows as definitively non-clan-battle players.

Useful existing primitive:

1. `queue_clan_battle_hydration(...)` in [server/warships/data.py](server/warships/data.py).

Important constraint:

1. do not synchronously WG-fetch clan-battle seasons during landing payload construction.

This mitigation can remove the need for a detail-page click, but it still will not guarantee first-load completeness on landing.

### Priority 3: invalidate landing-player payload caches when clan-battle state changes

If landing continues to consume clan-battle-derived row state, then cache invalidation needs to follow the data.

Recommended invalidation behavior after clan-battle refresh or cache population:

1. always invalidate recent-player landing cache,
2. bump the landing-player namespace used for random and best landing payloads,
3. do this only when the rendered shield state changed materially if churn becomes a concern.

Without this, best and random rows can stay stale for up to one hour even after the clan-battle source data is warm.

### Priority 4: add backend regression coverage for the actual failure mode

Needed tests:

1. landing row on clan-battle cache miss does not permanently encode false once local data exists,
2. detail-path clan-battle fetch followed by landing rebuild changes row state for a qualifying player,
3. random and best landing caches are invalidated or refreshed when clan-battle state changes,
4. non-qualifying players remain shield-free after the same flow.

The current tests cover player-detail cached header behavior, but they do not cover landing-row correction after clan-battle data becomes available.

### Priority 5: optionally warm clan-battle summaries for landing-visible cohorts

If a durable summary fix is deferred, the next best operational step is warming clan-battle summaries for players likely to appear on landing:

1. recent players,
2. current best-player candidates,
3. current random-player sample if the product wants that list to be accurate on first load.

This should remain bounded and asynchronous.

It is not a substitute for a better source of truth.

## Recommended Delivery Shape

### Preferred tranche

1. add durable clan-battle summary fields to the player summary surface,
2. switch landing serialization to those fields,
3. preserve the existing detail seasons fetch as the authoritative reconciliation path,
4. invalidate landing caches when the durable summary changes.

### Interim tranche if a schema change is too large

1. queue clan-battle hydration for cache-miss landing players,
2. invalidate recent and landing-player caches when clan-battle data is populated,
3. document that first render may still be incomplete until warmup catches up.

## Non-Recommendations

Do not do these as the primary fix:

1. do not add browser polling on landing just for shield icons,
2. do not synchronously fetch WG clan-battle data while building landing payloads,
3. do not keep encoding cache absence as `is_clan_battle_player=false`,
4. do not fix only the recent-player list and leave best/random tied to stale one-hour payload caches.

## Validation Plan For The Fix

After implementation, validate all of the following:

1. a qualifying player can show the shield on landing without first opening player detail,
2. recent-player rows no longer depend on player-detail navigation to correct shield state,
3. best and random landing rows do not stay stale after clan-battle state changes,
4. non-qualifying players still do not show the shield,
5. landing requests remain non-blocking and do not perform synchronous WG clan-battle fetches.

## Investigation Summary

The landing shield issue is real and confirmed.

The primary defect is not in the client render code.

It is in the backend contract and source-of-truth choice:

1. landing publishes clan-battle row state from a cache-only read,
2. player detail warms that cache through a fetch-capable path,
3. the warmed cache then makes landing appear to "hydrate" only after a click.

The right fix is to give landing a durable last-known clan-battle summary source and stop treating cache miss as product truth.
