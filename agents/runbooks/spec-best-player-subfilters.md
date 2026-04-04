# Spec: Best Player Sub-Sorts (Overall, Ranked, Efficiency, WR, CB)

Created: 2026-04-04
Status: Implemented in 1.6.9

Supersedes the narrower Sigma-only planning in `agents/work-items/landing-player-sigma-filter-spec.md`. That earlier spec remains useful background, but this document is the intended source of truth for the broader Best-player surface.

## Goal

Convert the landing-page Active Players control from a flat mode switch into the same backend-owned pattern already used by Best clans.

Target UX:

- main button order: Best, Random, Recent
- Best is the default player mode on page load
- Best exposes a secondary sub-sort row in this order: Overall, Ranked, Efficiency, WR, CB
- all ranking and filtering stay in the backend
- the players shown by any Best sub-sort stay hot in cache so opening a row is usually a cache hit

This is not a cosmetic client re-order. Each sub-sort owns its own top-player cohort.

## Implementation Status

Shipped on 2026-04-04 in patch release `1.6.9`.

Implemented behavior:

- Active Players now defaults to `Best`
- player mode order is `Best | Random | Recent`
- Best-only sub-sort row is `Overall | Ranked | Efficiency | WR | CB`
- the client requests `mode=best&sort=...` and does not re-rank rows locally
- `mode=sigma` remains accepted as a backend alias to `mode=best&sort=efficiency`
- Best-player cache entries are separated per sub-sort
- landing warmers prebuild all shipped Best-player variants
- best-entity warming and bulk player cache loading now use the union of Best-player cohorts rather than only the overall Best list
- landing TTLs for this surface now follow the project 6-hour norm

Validation completed during implementation:

- `server`: `python -m pytest warships/tests/test_landing.py -k "landing_players_endpoint_uses_cached_payload_for_random_mode or normalize_landing_player_best_sort or best_player_cache_keys_are_sort_specific or warm_landing_page_content or warm_landing_best_entity_caches or landing_best_players or landing_players_best" -x --tb=short`
- `server`: `python -m pytest warships/tests/test_views.py -k "landing_best_players or landing_players_best or warm_best_player" -x --tb=short`
- `client`: `npm test -- --runInBand app/components/__tests__/PlayerSearch.test.tsx`

Known validation note:

- a broader `warships/tests/test_landing.py` run still encounters an unrelated pre-existing clan Best CB ordering failure outside this player feature slice; the player-focused landing and view tests added for this feature pass

## Why This Reuses The Clan Pattern

The clan tranche already established the correct architecture for this kind of feature:

1. the client owns only toggle state and fetches `mode=best&sort=...`
2. the backend normalizes the sort and returns the authoritative order
3. each sub-sort gets its own cache entry and published fallback
4. warmers pre-build the visible lists instead of relying on ad hoc first-user misses

The player surface should follow the same shape rather than keeping `sigma` as a special top-level mode beside Best.

## Current State

### Player landing modes today

The Active Players surface currently behaves like this:

- top-level player modes in the UI: Random, Best, Sigma, Recent
- backend-recognized player modes in `landing.py`: `random`, `best`, `sigma`, `popular`
- `best` uses a composite competitive score
- `sigma` is a separate backend mode ordered by published efficiency percentile
- `recent` is fetched from `/api/landing/recent/`

That means the UI shape and backend contract are split across two ideas:

1. Best is one standalone ranked surface
2. Sigma is another standalone ranked surface

The clan work showed the better pattern: Best is the umbrella mode, and the ranking question lives in a backend `sort` parameter.

### Reuse points from the clan implementation

The player feature should deliberately mirror these existing clan-side patterns:

- `normalize_landing_clan_best_sort()` style sort normalization in `server/warships/landing.py`
- distinct cache keys per Best sub-sort
- frontend sub-sort row rendered only while Best is active
- reset sub-sort to `overall` when switching away from Best or changing realms
- landing warmers that precompute all visible Best variants
- tests that prove backend ordering, not client-local re-sorting

## Desired UX

### Main button order

The Active Players mode buttons should be reordered to:

`Best | Random | Recent`

Best should be the default on first load, matching the clan surface.

### Sub-sort row

When Best is active, show a secondary row of understated text links in this order:

`Overall | Ranked | Efficiency | WR | CB`

Behavior:

1. `Overall` is the default sub-sort
2. switching to Random or Recent hides the row and resets the selected player Best sort to `overall`
3. switching realms also resets the player Best sort to `overall`
4. the client renders rows in backend order only
5. layout height should stay stable when the sub-sort row is hidden or shown, matching the clan implementation

## Backend Contract

### Endpoint shape

Extend the existing player landing endpoint to support a Best sort parameter:

- `/api/landing/players/?mode=best&sort=overall&limit=25`
- `/api/landing/players/?mode=best&sort=ranked&limit=25`
- `/api/landing/players/?mode=best&sort=efficiency&limit=25`
- `/api/landing/players/?mode=best&sort=wr&limit=25`
- `/api/landing/players/?mode=best&sort=cb&limit=25`

Recommended helper additions in `server/warships/landing.py`:

- `LANDING_PLAYER_BEST_SORTS = ('overall', 'ranked', 'efficiency', 'wr', 'cb')`
- `normalize_landing_player_best_sort(sort)`
- sort-aware player Best cache key builders similar to the clan Best helpers

### Backward compatibility

To avoid breaking existing callers immediately:

1. keep accepting `mode=sigma` temporarily
2. normalize it internally to the same payload as `mode=best&sort=efficiency`
3. update the landing UI to stop requesting `mode=sigma`

This preserves additive API evolution while still moving the product surface to the new shape.

## Shared Eligibility Rules

All Best player sub-sorts should remain backend-owned and start from explicit eligibility rules, not raw all-player ordering.

Shared baseline filters:

1. player name present
2. `is_hidden = false`
3. `last_battle_date` present
4. recently active: `days_since_last_battle <= 180`
5. realm-scoped queries only

Sub-sort-specific sample floors are allowed and expected.

## Recommended Sub-Sort Definitions

### Overall

Purpose:

`Which players are the strongest all-around active accounts on Battlestats?`

Use the existing composite score path as the primary contract. This is the current `best` logic, renamed conceptually to `best:overall`.

Recommended implementation:

1. keep `_calculate_landing_best_score()` as the ranking core
2. keep the current minimum floors that protect against low-tier or tiny-sample specialists
3. treat the current `best` response as the `overall` sub-sort going forward

### Ranked

Purpose:

`Which active players are strongest in the current ranked-centric signal set?`

Recommended ranking basis:

1. primary signal: existing `_normalize_best_ranked_score(latest_ranked_battles, highest_ranked_league_recent)`
2. secondary signal: `explorer_summary__player_score`
3. tertiary signal: high-tier PvP win rate
4. final tiebreaker: player name ascending

Recommended hard filters:

1. baseline Best-player filters
2. at least one recent ranked participation signal: `latest_ranked_battles > 0` or non-null recent highest league

Recommended ordering formula:

```text
ranked_sort_score =
  0.70 * ranked_score
  + 0.20 * normalized_player_score
  + 0.10 * normalized_high_tier_wr
```

This keeps Ranked meaningfully distinct from Overall instead of just surfacing Overall players who happen to have a ranked badge.

### Efficiency

Purpose:

`Which active players rank highest by the published Battlestats efficiency contract?`

This is the existing Sigma feature moved under Best.

Recommended ranking basis:

1. `explorer_summary__efficiency_rank_percentile` descending
2. `explorer_summary__player_score` descending
3. `pvp_ratio` descending
4. player name ascending

Recommended hard filters:

1. baseline Best-player filters
2. `explorer_summary__efficiency_rank_percentile IS NOT NULL`
3. keep the current sigma sample floor unless implementation review intentionally raises it

Notes:

1. the row UI can continue showing the inline efficiency icon only for `E` rows
2. the sub-sort is about ranking by efficiency, not changing the icon policy

### WR

Purpose:

`Which active players have the strongest proven PvP win-rate record at competitive tiers?`

Recommended ranking basis:

1. high-tier PvP win rate from `_calculate_tier_filtered_pvp_record(..., minimum_tier=5)`
2. minimum high-tier battle floor identical to current Best-player eligibility
3. secondary tiebreakers: player score, efficiency percentile, name

Recommended ordering:

```text
wr_sort_order =
  high_tier_pvp_ratio DESC,
  high_tier_pvp_battles DESC,
  player_score DESC,
  efficiency_rank_percentile DESC,
  name ASC
```

This preserves a pure WR answer instead of burying WR under the Overall composite.

### CB

Purpose:

`Which active players have the strongest recent clan-battle profile?`

Recommended ranking basis:

Use existing player explorer summary fields rather than issuing new landing-time upstream calls:

1. `clan_battle_total_battles`
2. `clan_battle_seasons_participated`
3. `clan_battle_overall_win_rate`
4. `clan_battle_summary_updated_at`

Recommended hard filters:

1. baseline Best-player filters
2. player qualifies as a CB player by the existing `is_clan_battle_enjoyer(...)` helper or an equivalent explicit floor

Recommended ranking formula:

```text
cb_sort_score =
  0.55 * normalized_cb_wr
  + 0.25 * normalized_cb_volume
  + 0.20 * normalized_cb_season_depth
```

Where:

1. `normalized_cb_wr` is derived from `clan_battle_overall_win_rate`
2. `normalized_cb_volume` saturates on a reasonable battle floor instead of rewarding lifetime volume without bound
3. `normalized_cb_season_depth` rewards multi-season participation over single-season spikes

This should answer a player-level CB strength question without conflating it with clan-level CB ranking.

## Caching And Warming Requirements

The user requirement is not just faster list fetches. It is that the players shown by these lists should already be warm when clicked.

### Landing payload caches

Each player Best sub-sort needs independent payload caches and published fallbacks, mirroring the clan Best pattern.

Recommended cache families:

1. `best:overall`
2. `best:ranked`
3. `best:efficiency`
4. `best:wr`
5. `best:cb`

Each should have:

1. active cache key
2. metadata key
3. published cache key
4. published metadata key

### Landing warmers

`warm_landing_page_content()` should warm all visible player surfaces, including every Best sub-sort:

1. `players_best_overall`
2. `players_best_ranked`
3. `players_best_efficiency`
4. `players_best_wr`
5. `players_best_cb`
6. `players_random`
7. `recent_players`

The previous standalone `players_sigma` warm becomes part of `players_best_efficiency`.

### Hot player entity warming

The existing `warm_landing_best_entity_caches()` path currently assumes a single Best-player cohort. This spec requires it to warm the union of player IDs returned by all Best sub-sorts, not only Overall.

Required behavior:

1. collect player IDs for `overall`, `ranked`, `efficiency`, `wr`, and `cb`
2. de-duplicate them
3. warm player detail payloads for that union
4. keep clan-side best warming unchanged

Implementation note:

The warmer should not depend on serialized landing rows that have already stripped `player_id`. It should either:

1. gather IDs before row serialization, or
2. use dedicated ID-returning ranking helpers for warming

### Bulk cache loader

`bulk_load_player_cache()` should also be updated so the always-hot player cohort includes the union of top players across the shipped Best sub-sorts, not just top `player_score` plus best-clan members.

Recommended cohort change:

1. replace the current single `top players by player_score` cohort with the union of the shipped Best-player sub-sort cohorts
2. preserve pinned players and recently-viewed players as additive cohorts
3. keep best-clan-member warming as a separate additive cohort

This prevents `ranked`, `efficiency`, `wr`, or `cb` leaders from being cold simply because they were not also top `player_score` accounts.

## Frontend Contract

The client should copy the clan-side interaction model.

Recommended changes in `client/app/components/PlayerSearch.tsx`:

1. change default player mode to `best`
2. reorder main player buttons to Best, Random, Recent
3. replace the top-level Sigma button with a Best-only sub-sort link
4. add local `playerBestSort` state with default `overall`
5. request `/api/landing/players/?mode=best&sort=<selected>` while Best is active
6. keep using `/api/landing/recent/` for Recent
7. do not compute or re-rank player lists in the client

The existing clan header layout should be the direct visual reference for the player surface.

## API And Test Coverage

Implementation should be considered incomplete without focused backend and frontend regression coverage.

### Backend tests

Add or update tests for:

1. `normalize_landing_player_best_sort()` accepted and rejected values
2. `mode=sigma` compatibility alias behavior
3. per-sort ordering for Overall, Ranked, Efficiency, WR, and CB
4. per-sort eligibility filters
5. per-sort cache key separation
6. landing warmers warming all new player Best variants
7. best-entity warmer using the union of all Best-player cohorts

### Frontend tests

Add or update tests for:

1. main player button order: Best, Random, Recent
2. Best default on initial load
3. player sub-sort order: Overall, Ranked, Efficiency, WR, CB
4. Sigma no longer shown as a top-level player mode
5. fetches include `sort` when Best is active
6. rendered player order matches backend payload order exactly

## Rollout Notes

This is intentionally a backend-first contract change with thin frontend wiring.

Recommended implementation order:

1. add backend sort normalization and sort-aware cache keys
2. map existing Best to `overall` and existing Sigma to `efficiency`
3. add Ranked, WR, and CB ranking helpers
4. extend warmers and bulk cache loading to the union of Best-player cohorts
5. switch the client UI to Best-first with Best-only sub-sorts
6. keep `mode=sigma` as a compatibility alias until the new UI is shipped and verified

## Out Of Scope

This spec does not propose:

1. changes to player detail page ranking UI
2. new browser-side WG API fetches
3. redesign of the landing player row component
4. changes to clan Best behavior beyond using it as the interaction pattern to mirror
5. immediate removal of dormant backend-only `popular` support
