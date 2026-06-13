# Clan List Shield Badge Durability Spec

**Date:** 2026-03-26  
**Status:** Accepted  
**Scope:** Make clan-list shield badges paint immediately from durable data, without frequent request-path refreshes or client polling.

## QA Notes

QA review against the live code found three important constraints:

1. `ClanMembers.tsx` already ignores any shield-pending field, so removing request-path shield hydration does not require client work.
2. `warm_landing_best_entity_caches()` already refreshes clan-battle summaries for hot players, so the missing producer-lane coverage was primarily the null-only logic in `incremental_player_refresh`.
3. The clan roster route was still dispatching stale shield refreshes after the response payload was built, which added churn without improving first paint.

Implementation scope for this spec is therefore:

- remove request-path shield dispatch from `clan_members()`,
- move incremental refresh from null-only to stale-or-null,
- introduce a dedicated slow badge freshness knob,
- keep the client and landing read surfaces DB-backed and unchanged.

## Problem Statement

Clan lists currently render two visually similar badge classes with very different data paths:

1. The robot PvE badge is effectively instant.
2. The clan-battle shield badge is observably slower or missing on first paint.

This difference is not caused by icon rendering. It is caused by data availability.

## Why The Robot Paints Instantly

On the clan roster response, `is_pve_player` is computed directly from `Player.total_battles` and `Player.pvp_battles` in `clan_members()`.

Current path:

1. `clan_members()` loads member `Player` rows.
2. The view computes `is_pve_player = is_pve_player(member.total_battles, member.pvp_battles)`.
3. `ClanMembers.tsx` renders the robot immediately if that boolean is true.

This path is synchronous, local, and uses fields that are already present on the main player row.

## Why The Shield Is Slower

The shield does not come from the main `Player` row. It comes from the durable clan-battle summary fields on `PlayerExplorerSummary`:

- `clan_battle_seasons_participated`
- `clan_battle_total_battles`
- `clan_battle_overall_win_rate`
- `clan_battle_summary_updated_at`

Current clan roster path:

1. `clan_members()` reads `member.explorer_summary`.
2. It derives `is_clan_battle_player` and `clan_battle_win_rate` from those explorer-summary fields.
3. Only after the response payload is built, the view loops stale members and calls `maybe_refresh_clan_battle_data(member)`.
4. That queues background refresh work, but it does not improve the response already sent.
5. `useClanMembers()` only polls for ranked and efficiency hydration. It does not poll for shield hydration.

Result:

- If `PlayerExplorerSummary` is already populated, the shield is instant.
- If it is null or stale, the first clan-list response still renders no shield.
- The refresh happens behind the scenes and the current client usually does not re-fetch specifically for that shield change.

## Current-State Conclusion

The slow shield is not a frontend paint issue and not primarily a Redis issue.

It is a data-freshness policy issue:

- PvE badge state is derived from hot local columns.
- Shield state depends on a secondary summary table that is not guaranteed to be fresh before the clan list request arrives.
- The request path currently triggers shield refresh too late to help first paint.

## Design Goal

Clan-list badges should be served from durable data that is usually already present before the route is rendered.

For the shield badge specifically:

- Do not rely on request-path hydration for first paint.
- Do not add client polling just to chase a slow-moving badge.
- Refresh on a low-frequency cadence because clan-battle badge state changes rarely.

## Product Assumption

Shield badges are low-churn indicators.

For most players, shield state changes only when:

1. they start or stop meeting the minimum clan-battle thresholds,
2. they accumulate enough additional clan-battle games to materially shift win rate, or
3. a new clan-battle season contributes to the aggregate.

That means the system should prefer:

- durable precomputed state,
- slow scheduled refresh,
- targeted opportunistic backfill,

instead of:

- per-request refresh,
- short TTL churn,
- client-side waiting.

## Proposed Remedy

### 1. Treat Shield Data As A Durable Badge Snapshot

Keep `PlayerExplorerSummary` as the single read source for clan-list shields.

No new client fetches are needed. No shield-specific pending state is needed.

### 2. Remove Request-Path Shield Refresh From `clan_members()`

The `clan_members()` endpoint should stop calling `maybe_refresh_clan_battle_data(member)` during the request.

Reason:

- It does not help the current response.
- It creates the impression of a reactive system without actually making the shield immediate.
- It adds background churn to a hot public route.

The clan roster route should be a pure read path for shield data.

### 3. Move Shield Refresh Into Low-Frequency Producer Lanes

Refresh shield summaries before roster requests need them.

Primary producers:

1. `incremental_player_refresh._refresh_player()`
   - Expand from `null-only` clan-battle backfill to `stale-or-null` backfill.
   - Use a long cadence because badges rarely change.

2. clan/player warm paths
   - When warming hot entity caches or recent/high-traffic players, refresh stale-or-null clan-battle summaries in the same durable lane.
   - Do not require this from the clan-members endpoint itself.

3. optional scheduled badge refresher
   - Add a lightweight management command or Celery task that refreshes clan-battle summaries for recently looked-up players or current clan-roster members on a slow cadence.

### 4. Introduce A Slower Shield Freshness Policy

Add a dedicated freshness window for clan-list shield summaries.

Proposed env var:

`CLAN_BATTLE_BADGE_REFRESH_DAYS=14`

Behavior:

- `null` summary: refresh needed.
- summary older than 14 days: refresh needed.
- summary newer than 14 days: use as-is.

This is intentionally slower than high-churn surfaces because the badge is a low-frequency summary.

### 5. Keep Landing / Player-Search Reading The Same Durable Fields

Landing/player-search shields already read from `explorer_summary`-backed payloads.

Keep that model, but ensure producer lanes also cover players that appear in landing caches so landing badges stay hot without waiting for request-time repair.

### 6. Keep Cache Invalidation On Actual Payload Change

When `_persist_player_clan_battle_summary()` writes changed shield data, continue invalidating landing player caches.

This preserves eventual consistency for landing/player-search without introducing short shield TTLs.

## Explicit Non-Goals

1. Do not add a shield hydration header to `clan_members()`.
2. Do not add client polling for shield changes.
3. Do not reintroduce Redis cache as the primary read source for shield badges.
4. Do not refresh clan-battle summaries on every clan list request.

## Proposed Changes By File

### `server/warships/views.py`

In `clan_members()`:

1. Keep `select_related('explorer_summary')`.
2. Keep synchronous derivation of `is_clan_battle_player` and `clan_battle_win_rate` from `explorer_summary`.
3. Remove the stale-member loop that calls `maybe_refresh_clan_battle_data(member)`.

### `server/warships/data.py`

1. Add a dedicated badge freshness helper, either:
   - `clan_battle_badge_summary_is_stale(player)`, or
   - reuse `clan_battle_summary_is_stale(player)` but back it with a slower badge-specific threshold.
2. Keep `_persist_player_clan_battle_summary()` as the durable writer.
3. Keep `get_published_clan_battle_summary_payload()` as the DB-backed read helper.
4. Update naming/comments to clarify this is durable badge state, not request-path hydration state.

### `server/warships/management/commands/incremental_player_refresh.py`

Change `_refresh_player()` so clan-battle summary backfill is triggered when the badge summary is stale or null, not just when `clan_battle_summary_updated_at is None`.

### `server/warships/tasks.py`

Optionally add a slow badge refresher task, for example:

- `refresh_clan_battle_badge_summaries_task`

Inputs:

- batch of recently looked-up WG player ids, or
- active clan-member players, or
- hot landing-player set.

Cadence:

- daily or twice daily is sufficient.

### `server/warships/data.py` or `server/warships/landing.py`

Ensure existing hot-entity / landing warm paths include stale-or-null shield summary refresh for players likely to appear in public lists.

### `client/app/components/useClanMembers.ts`

No shield-specific change required.

The hook should remain focused on ranked and efficiency hydration only.

## Acceptance Criteria

1. Clan-list robot and shield badges both render from data already available in the first successful clan-members response.
2. `clan_members()` no longer queues shield refresh work during response generation.
3. Players with stale-or-null shield summaries are refreshed by slow producer lanes, not hot read paths.
4. Landing/player-search shield badges remain DB-backed and continue updating after durable summary writes invalidate the published landing caches.
5. Shield refresh cadence is explicitly slower than ranked/efficiency hydration and configurable by env.

## Tests

### Server tests

1. `clan_members()` returns shield fields from `explorer_summary` with no request-path dispatch.
2. `clan_members()` does not call `maybe_refresh_clan_battle_data()` or equivalent dispatch helper.
3. incremental player refresh dispatches/fetches clan-battle summaries when badge data is stale-or-null.
4. incremental player refresh skips clan-battle summary fetch when badge data is fresh.
5. landing payload builders still expose `is_clan_battle_player` and `clan_battle_win_rate` from explorer summaries.

### Browser / integration tests

1. Clan roster renders shield icons on first load when explorer-summary fixture data is prepopulated.
2. No client polling is introduced for shield-only changes.
3. Landing/player-search badge snapshots continue to repaint after a durable summary change and landing cache republish.

## Rollout Plan

1. Implement server-side freshness-policy change and remove request-path shield dispatch from `clan_members()`.
2. Extend incremental refresh from `null-only` to `stale-or-null` for shield summaries.
3. Add optional slow badge refresher task if organic incremental coverage is insufficient.
4. Validate with focused Django tests and one Playwright or fixture-based clan roster smoke.

## Rationale

The robot badge is fast because it is derived from data already present on the hot row.

The shield badge will feel equally fast only when its summary is treated the same way operationally:

- durable,
- already computed,
- rarely refreshed,
- never repaired on the critical read path.
