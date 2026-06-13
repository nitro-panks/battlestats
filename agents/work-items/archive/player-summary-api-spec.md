# Player Summary API Specification

**Author:** Architect + Engineer (Web Dev)
**Date:** 2026-03-09
**Status:** Draft for implementation planning
**Scope:** Derived player summary metrics used by player detail and future explorer surfaces

## 1. Objective

Define a stable derived-summary contract for player evaluation so the frontend can render recent activity and comparison metrics without re-deriving them independently from multiple endpoints.

## 2. Why This Is Needed

The current codebase stores the main ingredients for player evaluation, but many useful values are trapped inside JSON fields or spread across separate endpoints.

That is acceptable for isolated charts, but weak for:

1. player summary cards,
2. explorer sorting,
3. dataset-wide filtering,
4. consistent interpretation across surfaces.

## 3. Design Goals

1. Keep the contract explicit and flat.
2. Derive only metrics that are already justified by the current dataset.
3. Avoid composite scoring in the initial version.
4. Support both player-detail summaries and future explorer rows.

## 4. Recommended Endpoint

`GET /api/fetch/player_summary/<player_id>/`

Alternative implementation path:

1. Add these fields directly to the player detail response.
2. Still keep a clearly documented summary contract to prevent drift.

## 5. Proposed Response Contract

```json
{
  "player_id": 123456,
  "name": "ExamplePlayer",
  "is_hidden": false,
  "days_since_last_battle": 3,
  "last_battle_date": "2026-03-06",
  "account_age_days": 2240,
  "pvp_ratio": 54.2,
  "pvp_battles": 8123,
  "pvp_survival_rate": 39.1,
  "battles_last_29_days": 118,
  "wins_last_29_days": 64,
  "active_days_last_29_days": 12,
  "recent_win_rate": 0.542,
  "activity_trend_direction": "up",
  "ships_played_total": 64,
  "ship_type_spread": 4,
  "tier_spread": 6,
  "ranked_seasons_participated": 5,
  "latest_ranked_battles": 22,
  "highest_ranked_league_recent": "Silver"
}
```

## 6. Source Mapping

| Summary Field                  | Source                                                     |
| ------------------------------ | ---------------------------------------------------------- |
| `days_since_last_battle`       | `Player.days_since_last_battle`                            |
| `last_battle_date`             | `Player.last_battle_date`                                  |
| `account_age_days`             | derived from `Player.creation_date`                        |
| `pvp_ratio`                    | `Player.pvp_ratio`                                         |
| `pvp_battles`                  | `Player.pvp_battles`                                       |
| `pvp_survival_rate`            | `Player.pvp_survival_rate`                                 |
| `battles_last_29_days`         | sum of `activity_json.battles`                             |
| `wins_last_29_days`            | sum of `activity_json.wins`                                |
| `active_days_last_29_days`     | count of activity rows with battles > 0                    |
| `recent_win_rate`              | `wins_last_29_days / battles_last_29_days`                 |
| `activity_trend_direction`     | compare recent half-window to prior half-window            |
| `ships_played_total`           | count of `battles_json` rows with `pvp_battles > 0`        |
| `ship_type_spread`             | distinct ship types in `battles_json` with meaningful play |
| `tier_spread`                  | distinct tiers in `battles_json` with meaningful play      |
| `ranked_seasons_participated`  | count of `ranked_json` rows                                |
| `latest_ranked_battles`        | latest ranked season summary                               |
| `highest_ranked_league_recent` | latest or strongest recent ranked season summary           |

## 7. Computation Rules

### Activity Window

1. Use the current 29-day `activity_json` window as the source of truth.
2. Missing days should count as zero because the activity payload already backfills them.

### Trend Direction

Recommended initial rule:

1. Split the window into earlier and later halves.
2. Compare total battles in each half.
3. Return `up`, `flat`, or `down` using a modest threshold to avoid noise.

### Breadth Metrics

1. Count only ships with `pvp_battles > 0`.
2. Consider adding a meaningful-play threshold later if noise appears.

### Hidden Profiles

1. If the player is hidden, return only safe non-derived fields.
2. Return `null` for summary values that depend on cleared detailed JSON.
3. Do not fabricate zeros where the correct state is unavailable.

## 8. Implementation Recommendation

### Short-Term

1. Compute summary values in a dedicated service/helper.
2. Expose them via a small summary endpoint.
3. Reuse the endpoint in player detail.

### Medium-Term

1. Persist summary values to a dedicated model or denormalized fields.
2. Update them whenever player data refreshes.
3. Reuse the same summary source for explorer queries.

## 9. Guardrails

1. Do not include `last_lookup`.
2. Do not include `last_fetch`.
3. Do not expose clan battle participation until player-grain data exists.
4. Do not collapse the response into a single score.

## 10. Acceptance Criteria

1. The contract uses explicit, documented fields.
2. Player detail can render summary metrics without client-side re-derivation.
3. Explorer work can reuse the same metric definitions.
4. Hidden-profile handling remains correct and non-misleading.

## 11. Definition of Done

1. Metric formulas are documented and implemented once.
2. The frontend no longer needs to guess summary values from multiple sources.
3. The contract is ready for reuse across player detail and explorer surfaces.
