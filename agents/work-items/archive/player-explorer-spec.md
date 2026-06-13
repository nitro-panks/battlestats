# Player Explorer Specification

**Author:** Project Manager Agent
**Date:** 2026-03-09
**Status:** Draft for cross-functional review
**Scope:** Dataset-wide player discovery and comparison
**Primary Surface:** New explorer view linked from landing and reusable from search flows

## 1. Objective

**Core question this feature answers:** _"Where does this player sit among the players Battlestats already knows about?"_

The current product supports player lookup by name and exposes a player detail page, but it does not let users compare players across the known dataset with explicit filters, sorting, or cohort context.

This feature adds a first-class Player Explorer that lets a user:

1. search known players,
2. sort by explicit evaluation metrics,
3. filter by activity and profile characteristics,
4. jump from a comparative row into player detail.

## 2. PM Recommendation

Ship the explorer before any composite player score.

Reasoning:

1. It answers a real lookup problem with lower interpretation risk.
2. It forces the backend to expose explicit sortable metrics instead of hiding logic in UI code.
3. It gives product and QA a better environment for validating which metrics are actually useful.
4. It creates the foundation for later percentiles, cohorts, and derived archetypes.

## 3. MVP Scope

### In Scope

1. Server-backed player list endpoint with filtering, sorting, and pagination.
2. Explorer table view in the Next.js client.
3. Free-text name search within the known dataset.
4. Sorting by explicit metrics.
5. Click-through from explorer row to player detail.
6. Clear hidden-profile handling.

### Out of Scope

1. Composite player score.
2. Percentiles.
3. Saved cohorts or comparisons.
4. Clan battle participation scoring.
5. Predictive labels or archetype automation.

## 4. Initial Metric Columns

The first explorer should expose only metrics already available or cleanly derivable from current data.

| Column                   | Source                              | Purpose                    |
| ------------------------ | ----------------------------------- | -------------------------- |
| Player                   | `Player.name`                       | identity and click-through |
| Hidden                   | `Player.is_hidden`                  | availability context       |
| Days Since Last Battle   | `days_since_last_battle`            | recency                    |
| PvP WR                   | `pvp_ratio`                         | aggregate effectiveness    |
| PvP Battles              | `pvp_battles`                       | experience / scale         |
| Account Age Days         | derived from `creation_date`        | longevity                  |
| Battles Last 29 Days     | derived summary                     | recent activity volume     |
| Active Days Last 29 Days | derived summary                     | cadence                    |
| Ships Played Total       | derived summary from `battles_json` | breadth                    |
| Ranked Seasons           | derived summary from `ranked_json`  | competitive participation  |

## 5. Filtering and Sorting

### Required Filters

1. Name contains
2. Hidden vs visible
3. Activity bucket
4. Minimum PvP battles
5. Ranked participation present / absent

### Suggested Activity Buckets

1. Active in last 7 days
2. Active in last 30 days
3. Active in last 90 days
4. Dormant 90+ days

### Required Sorts

1. Days since last battle
2. Battles last 29 days
3. Active days last 29 days
4. PvP WR
5. PvP battles
6. Account age days
7. Ships played total

## 6. UX Specification

### Layout

1. Search and filter controls above the table.
2. Sticky table header for sorting.
3. Visible empty state for no matches.
4. Simple pagination or incremental load.

### Row Behavior

1. Clicking a visible player row opens player detail.
2. Hidden-profile rows remain visible but clearly labeled.
3. Sorting must be deterministic and stable.

### Copy Principles

1. Use plain labels, not invented jargon.
2. Keep metrics decomposed and inspectable.
3. Avoid implying leaderboard authority beyond the known dataset.

## 7. Backend Contract

### Recommended Endpoint

`GET /api/players/explorer/`

### Query Parameters

1. `q`
2. `sort`
3. `direction`
4. `hidden`
5. `activity_bucket`
6. `min_pvp_battles`
7. `ranked`
8. `page`
9. `page_size`

### Response Shape

```json
{
  "count": 1240,
  "page": 1,
  "page_size": 50,
  "results": [
    {
      "name": "ExamplePlayer",
      "player_id": 123456,
      "is_hidden": false,
      "days_since_last_battle": 3,
      "pvp_ratio": 54.2,
      "pvp_battles": 8123,
      "account_age_days": 2240,
      "battles_last_29_days": 118,
      "active_days_last_29_days": 12,
      "ships_played_total": 64,
      "ranked_seasons_participated": 5
    }
  ]
}
```

## 8. Engineering Notes

1. Do not derive explorer metrics ad hoc in the frontend.
2. Prefer a denormalized summary layer over repeated JSON parsing in request-time views.
3. Make sort keys explicit and whitelisted.
4. Exclude `last_lookup` and `last_fetch` from explorer metrics.

## 9. Acceptance Criteria

1. Users can locate players across the known dataset with search and filters.
2. Users can sort by explicit activity and performance metrics.
3. Hidden profiles are clearly labeled and not misrepresented as bad performers.
4. Explorer rows link cleanly into existing player detail.
5. The feature does not depend on a composite player score.

## 10. Risks

| Risk                                                     | Severity | Mitigation                                                 |
| -------------------------------------------------------- | -------- | ---------------------------------------------------------- |
| Users interpret explorer ordering as a universal ranking | Medium   | Use explicit metric labels and avoid "top players" framing |
| Request-time JSON parsing is too slow                    | High     | Use summary fields or a derived summary model              |
| Hidden-profile rows confuse users                        | Medium   | Make hidden state explicit in both filters and rows        |

## 11. Definition of Done

1. Backend exposes a stable explorer contract.
2. Frontend supports search, sorting, and filtering.
3. Metrics shown are explicit and explainable.
4. No composite score is required for MVP.
