# Ranked Enjoyer icon: current-season criteria — spec

- **Status**: approved 2026-07-15 (design reviewed in session; both heuristic defaults accepted)
- **Owner surface**: player header tray (`PlayerDetail.tsx`), clan roster rows (`ClanMembers.tsx` via `clan_members` payload)
- **Supersedes**: career-total criteria — `is_ranked_player(ranked_rows, minimum_ranked_battles=100)` and all-time `get_highest_ranked_league_name` in the icon path

## Behavior change

The Ranked Enjoyer star (`RankedPlayerIcon`) now marks **current-season participation**, not career volume:

1. **Qualification**: the player has any ranked battles (`total_battles > 0`) recorded for the *current* ranked season in their `ranked_json`.
2. **Color**: the star is tinted by the player's **highest league reached in the current season** (`highest_league_name` of that season's row), via the existing `rankedLeague.ts` palette.
3. **Tooltip**: "ranked this season (Gold|Silver|Bronze)" — the icon must say what it now means.

## Current-season heuristic (approved)

- **"Latest season persists"**: the current season is the newest season that has started; it remains current through the off-season gap until the next season starts. The icon never goes dark fleet-wide between seasons.
- Resolution: `get_current_ranked_season_id()` = max `season_id` in the durable reference whose `start_date` is null or ≤ today (a season listed with a future start date is not yet current).
- **Unknown current season** (empty reference, first boot before any ranked fetch): icon hidden; no fallback to career semantics.
- **No minimum battle floor**: any current-season battles > 0 qualify.

## Durable reference data: `RankedSeason`

Redis is `allkeys-lru` — even `timeout=None` keys can evict — so season dates get a DB home:

- New model `RankedSeason`: `season_id` (PK), `name`, `label`, `start_date` (nullable), `end_date` (nullable), `updated_at` (auto).
- `_get_ranked_seasons_metadata()` keeps its 24h Redis fresh key, and on every fresh WG `seasons/info/` fetch **upserts** all rows into `RankedSeason`.
- Fallback order on a request: Redis fresh key → WG fetch (+ upsert) → **DB read** (WG failed/cold) → `{}`.
- No new Beat task: the fetch already runs on every `update_ranked_data` call.

### Self-healing rollover

WG lists a new season in `seasons/info/` on its own schedule, and our metadata key is 24h-cached. If `update_ranked_data` sees a `season_id` in a player's `rank_info` that is **newer than the max known season**, it deletes the Redis metadata key and refetches once before aggregating — bounding rollover lag to WG's own listing latency. The refetch is **skipped once that season is already in the durable `RankedSeason` table** (published OR activity-imputed, below) — otherwise every ranked refresh during a publish-lag gap re-hits `seasons/info/` uselessly (it can't return a season WG hasn't listed); real dates then land via the normal 24h cache expiry.

### Activity-imputed rollover (WG publish-lag fallback, 2026-07-23)

`seasons/info/` can lag the season it dates by **a week or more**: per-player `rank_info` starts returning the new season's battles at open, but `seasons/info/` still tops out at the prior season, so the self-heal refetch above yields nothing new and `get_current_ranked_season_id()` stays pinned to the prior (now-ended) season — last-season players keep the star, new-season players don't get it. Observed at the S29→S30 rollover: S29 ended 2026-07-15, ~20% of recently-refreshed players had real S30 battles, yet `seasons/info/` still listed only ≤S29.

`_impute_ranked_season_from_activity(result, season_meta)` (called from `update_ranked_data` after aggregation) bridges the gap: when a refreshed player has `total_battles > 0` in the season `max_known + 1` and WG hasn't dated it, it writes an imputed `RankedSeason` row with `start_date` = first-observation date (today, or the earliest already stored). The resolver rolls over immediately. It also **stamps that date onto the aggregated `ranked_json` row** (which was built before imputation with a null `start_date`), so the ranked season table shows the imputed date instead of "Start date unavailable" (v4.3.8). The stamp uses the earliest stored observation, not just this player's; the DB write only fires when it creates or moves the stored start earlier (steady-state is stamp-only). Guards: **only `max_known + 1`** (a phantom far-future id can't leap the fleet forward); **only when the prior season has ended** (`end_date < today` or null — overlap-edge pre-season battles can't kill a still-live season's stars); **never bootstraps from an empty reference**; **idempotent** (skips the write once `start_date ≤ today` is already set, so the date never drifts forward). Self-corrects: once WG publishes the season, `_upsert_ranked_seasons_reference`'s unconditional `update_or_create` overwrites the imputed placeholder date/name with WG's real dates, and imputation stops (`next_season in season_meta`).

## Wiring (single source of truth = server)

| Site | Before | After |
|---|---|---|
| `data.py` helpers | `is_ranked_player` (career >100) | + `is_current_season_ranked_player(ranked_rows, current_season_id)`, `get_current_season_ranked_league(ranked_rows, current_season_id)`, `get_current_ranked_season_id()` |
| `views.py` `clan_members` (~1682/1691) | career flag + all-time league | current-season flag + current-season league (one `get_current_ranked_season_id()` call per request, not per member) |
| Player serializer | `highest_ranked_league` = all-time | `highest_ranked_league` = current season; **new** `is_ranked_player` boolean field |
| `PlayerDetail.tsx` | client-derived `rankedBattleCount > 100`; `getHighestRankedLeagueName` fallback over all rows | payload-driven: `is_ranked_player` + `highest_ranked_league`; client derivation removed |
| `rankedLeague.ts` | tooltip "ranked enjoyer (League)" | "ranked this season (League)" |

Legacy `is_ranked_player`/`get_highest_ranked_league_name` remain for non-icon consumers (`RankedSeasons` tab renders per-season data independently; explorer `highest_ranked_league_recent` untouched).

## Freshness caveat (accepted)

A player whose `ranked_json` predates the current season shows no icon until their ranked data refreshes (profile view, floor ranked sweep). Data-driven by design; no speculative WG calls added. This is also the transient at an activity-imputed rollover: the instant the new season is imputed, every prior-season-only player (and anyone whose `ranked_json` predates it) loses the star until their next ranked refresh — correct, and strictly better than showing wrong prior-season stars through WG's publish lag.

## Tests

- Season resolution: started / future-dated / off-season persistence / empty reference.
- Metadata upsert + DB fallback when WG fetch fails.
- Self-heal: unknown season id busts the cache and refetches.
- Activity-imputed rollover: imputes `max_known + 1` from observed play + flips resolution; stops once WG publishes (self-correction); rejects a phantom far-future id; holds while the prior season still runs; idempotent (no forward drift); empty-reference no bootstrap; end-to-end through `update_ranked_data`.
- `clan_members` payload: `is_ranked_player` + `highest_ranked_league` reflect current season only.
- Player serializer: new boolean + scoped league.
- Frontend: `PlayerDetail` renders/omits the star from payload flags alone.
