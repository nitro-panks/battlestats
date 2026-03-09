# Runbook: Player Activity and Evaluation

## Purpose

Provide a decision framework for using the current Battlestats dataset to answer broader player-evaluation questions, not just "has this player played recently?"

This revision updates the runbook against the live codebase as of 2026-03-09. It expands the scope from narrow recent activity measurement to a more complete player-evaluation model spanning:

1. recent activity shape,
2. player lookup across the known dataset,
3. multi-factor player evaluation,
4. the stories the product can credibly tell now,
5. the engineering path required to make those stories queryable and explainable.

## Revision Status

- Revision date: 2026-03-09
- Status: current-state planning runbook only
- Ranked status: live on player detail via ranked seasons table
- Randoms status: live on player detail via filterable top-ships chart
- Activity-series status: backend endpoint and chart component exist, but recent activity is not yet a first-class player-detail section in the current UI
- Clan battle status: live at clan-roster season summary level only, not player-grain participation history

## Questions This Runbook Must Answer

1. What is the overall activity of the player base shaped like?
2. How can a user locate a player's performance across the dataset?
3. How should Battlestats evaluate players using performance, engagement, activeness, longevity, ship breadth, and competitive participation?
4. Which of those ingredients are truly available now?
5. What product stories are defensible with current data, and what engineering is required to support them well?

## Executive Recommendation

Do not jump directly to a single opaque "player score."

The current codebase already supports a strong descriptive model if the product stays explicit about dimensions:

1. **Activeness:** how recently and how often the player has played.
2. **Performance:** how well the player performs in aggregate and recent windows.
3. **Engagement shape:** where their play appears to be concentrated across randoms and ranked.
4. **Breadth:** how many ships, tiers, and ship types they meaningfully use.
5. **Longevity:** how long the account has existed.

This should be built in phases:

1. Strengthen the descriptive player model.
2. Add dataset-wide comparison surfaces.
3. Add composite evaluation only after the inputs are queryable, stable, and explainable.

## Current Product and Data Reality

### What Exists in the Product Today

The current application already provides these user-facing surfaces:

| Surface                     | Current Support                                     | Notes                                                               |
| --------------------------- | --------------------------------------------------- | ------------------------------------------------------------------- |
| Player search               | live                                                | Name search with suggestions and hidden-profile labeling            |
| Active players landing list | live                                                | Ordered by `last_battle_date`, useful for discovery, not evaluation |
| Recently viewed list        | live                                                | Viewer behavior only, not gameplay behavior                         |
| Player detail stat cards    | live                                                | PvP battles, PvP WR, survival, recency                              |
| Randoms top-ships chart     | live                                                | Filterable across all ships via `randoms_data?all=true`             |
| Performance by tier         | live                                                | Derived from `battles_json`                                         |
| Performance by ship type    | live                                                | Derived from `battles_json`                                         |
| Ranked seasons table        | live                                                | Historical competitive participation and outcomes                   |
| Activity endpoint           | live                                                | 29-day activity rows from snapshots                                 |
| Activity chart component    | implemented, not currently mounted in player detail | Can support recent activity storytelling with minimal contract risk |
| Clan battle seasons         | live at clan level                                  | Not valid as player-level activity history                          |

### What the Backend Stores for a Player Today

| Data                                                                      | Purpose                             | Reliability for Evaluation                               |
| ------------------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------- |
| `last_battle_date`                                                        | gameplay recency                    | high                                                     |
| `days_since_last_battle`                                                  | derived recency display             | high                                                     |
| `creation_date`                                                           | account longevity                   | high                                                     |
| `pvp_battles`, `pvp_wins`, `pvp_losses`, `pvp_ratio`, `pvp_survival_rate` | aggregate performance context       | high                                                     |
| `activity_json`                                                           | 29-day daily recent activity series | high for recent activity                                 |
| `battles_json`                                                            | per-ship base dataset               | high for breadth/composition derivations                 |
| `randoms_json`                                                            | top random-battle ship view         | high for top-ship storytelling, partial for full breadth |
| `type_json`                                                               | ship class aggregation              | high                                                     |
| `tiers_json`                                                              | ship tier aggregation               | high                                                     |
| `ranked_json`                                                             | ranked season summaries             | high for competitive participation summaries             |
| `last_lookup`                                                             | recent profile views                | not a gameplay metric                                    |
| `last_fetch`                                                              | backend refresh timing              | operational only                                         |

## Core Decision Standard

1. Use gameplay-derived signals before operational timestamps.
2. Separate dataset-wide discovery capability from single-player detail capability.
3. Treat recent activity, lifetime performance, and competitive participation as separate axes until the product explicitly defines weighting.
4. Use only player-grain data for player-level claims.
5. Keep any future composite scoring fully decomposable into understandable ingredients.

## Answering the User Questions

### 1. "What is the overall activity of the player base shaped like?"

### Short Answer

Partially answerable with the current dataset, but not yet well supported in the current product.

### What We Can Support Now

The codebase already contains enough player-level fields to describe the player base with offline analysis or a new backend aggregate surface:

1. Recency distribution using `days_since_last_battle`.
2. Lifetime participation context using `pvp_battles`.
3. Account-age context using `creation_date`.
4. Recent activity intensity using derived sums from `activity_json`.
5. Competitive participation presence using `ranked_json`.

### What Is Missing in the Product

There is no existing dataset-wide API or UI that aggregates players into distributions, percentiles, or cohorts. The current landing page exposes lists and search, not analytics.

### Best Illustration

Use small, explicit population views rather than one mega-dashboard.

Recommended first analytics visuals:

1. **Histogram:** `days_since_last_battle`
2. **Histogram:** `battles_last_29_days`
3. **Scatter plot:** `days_since_last_battle` vs `pvp_ratio`
4. **Scatter plot:** `battles_last_29_days` vs `pvp_ratio`
5. **Bar chart:** player counts by `highest_ranked_league_recent` or ranked participation bucket

These visuals answer "what is the shape of the population?" more honestly than a single blended score.

### 2. "How can I look across the dataset to locate a player's performance?"

### Short Answer

Today the product supports lookup by name, but not true comparative placement.

### What Exists Now

1. Search suggestions by player name.
2. Active players landing list ordered by recency.
3. Recently viewed players list.
4. Clan-level navigation for member discovery.

### What Does Not Exist Yet

1. Ranking or percentile placement by WR, activity, longevity, or breadth.
2. Filterable player explorer across the dataset.
3. Cohort comparisons such as "among players with 5k+ battles" or "among players active in the last 30 days."
4. A dedicated dataset query endpoint for player comparison.

### Recommendation

Add a first-class **Player Explorer** surface before attempting a composite player score.

The explorer should support:

1. search by name,
2. sorting by explicit numeric metrics,
3. filters for activity, ranked participation, account age, and hidden status,
4. a detail jump from any row into player detail.

This is the cleanest path to answering "where does this player sit in the dataset?"

### 3. "How can I evaluate players by a combination of performance, engagement, activeness, longevity, number of ships, etc.?"

### Short Answer

This is supportable as a descriptive framework now, but only partially supportable as a queryable product feature without additional engineering.

### Recommended Evaluation Dimensions

| Dimension                 | Meaning                                                   | Current Inputs                                                                   | Confidence  |
| ------------------------- | --------------------------------------------------------- | -------------------------------------------------------------------------------- | ----------- |
| Activeness                | how recently and steadily the player has played           | `last_battle_date`, `days_since_last_battle`, `activity_json`                    | high        |
| Performance               | how effectively the player wins and survives              | `pvp_ratio`, `pvp_survival_rate`, `pvp_wins`, `pvp_losses`, recent activity wins | high        |
| Engagement                | how deeply the player participates across available modes | `activity_json`, `ranked_json`, `randoms_json`, `type_json`, `tiers_json`        | medium-high |
| Longevity                 | how established the account is                            | `creation_date`                                                                  | high        |
| Breadth                   | how wide the player's ship pool and class/tier spread is  | `battles_json`, `type_json`, `tiers_json`                                        | medium-high |
| Competitive intensity     | how strongly the player participates in ranked            | `ranked_json`                                                                    | high        |
| Clan competitive activity | player participation in clan battles                      | not available at player grain                                                    | unsupported |

## Current-State Data Inventory by Decision Area

### Reliable Now

| Signal                   | Use                              | Notes                                        |
| ------------------------ | -------------------------------- | -------------------------------------------- |
| `days_since_last_battle` | recency                          | strongest simple activity signal             |
| `last_battle_date`       | recency                          | useful raw date for labels and bucketization |
| `activity_json`          | recent volume, cadence, momentum | current best recent-play time series         |
| `pvp_ratio`              | aggregate effectiveness          | stable, intuitive performance metric         |
| `pvp_survival_rate`      | supporting performance lens      | secondary to WR                              |
| `creation_date`          | account age                      | useful for veteran vs newer account context  |
| `ranked_json`            | competitive participation        | strongest current non-randoms mode signal    |
| `type_json`              | class concentration              | useful for playstyle and breadth             |
| `tiers_json`             | tier concentration               | useful for experience band context           |

### Derivable Now But Not Yet First-Class

These should be derived either in a player summary layer or precomputed analytics fields.

| Derived Metric                | Source                                                | Why It Matters              |
| ----------------------------- | ----------------------------------------------------- | --------------------------- |
| `battles_last_29_days`        | sum of `activity_json.battles`                        | recent activity volume      |
| `wins_last_29_days`           | sum of `activity_json.wins`                           | recent output context       |
| `active_days_last_29_days`    | count of `activity_json` rows with battles > 0        | cadence                     |
| `recent_win_rate`             | `wins_last_29_days / battles_last_29_days`            | recent performance          |
| `activity_trend_direction`    | recent segment vs prior segment of `activity_json`    | momentum                    |
| `ships_played_total`          | count rows in `battles_json` with `pvp_battles > 0`   | breadth                     |
| `top_ship_concentration`      | share of battles in top 1-3 ships from `battles_json` | specialist vs spread player |
| `ship_type_spread`            | distinct ship types with meaningful play              | engagement breadth          |
| `tier_spread`                 | distinct tiers with meaningful play                   | experience spread           |
| `ranked_seasons_participated` | count of ranked seasons in `ranked_json`              | competitive engagement      |
| `latest_ranked_battles`       | latest season summary                                 | current ranked intensity    |
| `account_age_days`            | today minus `creation_date`                           | longevity                   |

### Not Suitable for Player Evaluation

| Signal                       | Why Exclude                                   |
| ---------------------------- | --------------------------------------------- |
| `last_lookup`                | measures viewer behavior, not player behavior |
| `last_fetch`                 | operational freshness only                    |
| clan battle roster summaries | not player-level participation history        |
| raw hidden-profile absence   | missingness should not become a quality proxy |

## What Stories the Data Can Tell Now

The runbook should anchor future implementation around clear stories, not only metric lists.

### Story Set A: Activity Shape

1. **Recently active regular**
   High active-day count, moderate recency, steady recent battles.
2. **Burst player**
   High recent total but low active-day count, suggesting play concentrated into a few sessions.
3. **Dormant veteran**
   Old account, high lifetime battles, poor recent recency.
4. **Reactivated account**
   Long account age with renewed recent activity after a quiet period.

### Story Set B: Performance Shape

1. **High-volume steady winner**
   Strong lifetime WR plus healthy recent activity volume.
2. **Low-volume high-WR specialist**
   Good results but concentrated into fewer ships or fewer recent sessions.
3. **Recent hot streak vs stable career average**
   Recent WR materially above aggregate WR.
4. **High activity, middling results**
   Useful for identifying grinders without overstating skill.

### Story Set C: Engagement and Playstyle

1. **Broad fleet generalist**
   Many ships, multiple tiers, multiple ship types.
2. **Narrow specialist**
   Most play concentrated in a few ships or one class.
3. **Ranked competitor**
   Ranked seasons present with meaningful battle counts and league progression.
4. **Casual randoms-only player**
   Strong randoms profile with little or no ranked participation.

### Story Set D: Longitudinal Player Context

1. **Established veteran still active**
   Older account and strong recent activity.
2. **Newer account ramping quickly**
   Lower age, fast accumulation, recent activity density.
3. **Long-lived account with narrow current engagement**
   Good for explaining why longevity and activeness must remain separate dimensions.

## Recommended Product Framing

### Do Not Ship First

1. A single undisclosed weighted score.
2. A radar chart with six underexplained dimensions.
3. Player percentile claims without a stable dataset denominator.

### Ship First

1. An explicit **Player Activity Summary** on player detail.
2. A **Player Explorer** for sorting and filtering known players.
3. Dataset overview analytics that show distributions and cohorts.
4. A decomposed evaluation model with clearly labeled ingredients.

## Recommended UI and Illustration Plan

### Phase 1: Strengthen Player Detail

Use current contracts to make the player page answer a fuller evaluation question.

Recommended additions:

1. Mount the recent activity chart as a first-class section using the existing activity endpoint.
2. Add a compact summary row for:
   - battles in last 29 days,
   - active days in last 29 days,
   - recent WR,
   - ranked seasons participated,
   - ships played total.
3. Keep ranked separate as competitive context, not a hidden component of a universal score.
4. Use randoms, tier, and type charts to explain breadth and concentration.

### Phase 2: Add Dataset-Wide Comparison Surface

Create a Player Explorer table or grid with:

1. search,
2. sorting,
3. filters,
4. explicit metric columns,
5. row-to-detail navigation.

Best first columns:

1. player name,
2. days since last battle,
3. battles last 29 days,
4. active days last 29 days,
5. PvP WR,
6. total PvP battles,
7. account age,
8. ships played total,
9. latest ranked league or ranked participation flag.

### Phase 3: Add Population Overview

Best first visuals:

1. activity recency histogram,
2. recent-volume histogram,
3. recency vs WR scatter,
4. breadth vs WR scatter,
5. ranked-participation segment chart.

These views answer "what is the overall player base shaped like?" in a way that is observable and auditable.

## Engineering Plan

### Phase 0: Keep Current Contracts Honest

1. Treat `activity_json` as the recent activity backbone.
2. Treat `ranked_json` as separate competitive history.
3. Treat `battles_json` as the source for breadth derivations.
4. Keep clan battle data out of player-level evaluation until a player-grain model exists.

### Phase 1: Add Derived Player Summary Metrics

Add a derived summary layer for player evaluation.

Recommended first derived fields or computed API payload values:

1. `battles_last_29_days`
2. `wins_last_29_days`
3. `active_days_last_29_days`
4. `recent_win_rate`
5. `activity_trend_direction`
6. `ships_played_total`
7. `ship_type_spread`
8. `tier_spread`
9. `ranked_seasons_participated`
10. `latest_ranked_battles`
11. `account_age_days`

### Phase 2: Make Dataset Queries Cheap and Explicit

The current data model is good for player detail, but not ideal for dataset-wide ranking because several useful signals live inside JSON blobs.

Recommended engineering direction:

1. Add a denormalized player-summary model or persisted summary fields.
2. Recompute those summaries when player data refreshes.
3. Expose a dedicated explorer endpoint with sorting and filtering.
4. Avoid computing dataset-wide rankings directly from JSON fields on request.

### Phase 3: Build Comparison and Storytelling Surfaces

1. Player Explorer
2. Population Overview
3. Optional player archetype badges or narrative summaries

## Known Constraints and Gaps

### Constraint: Activity Window Is Recent, Not Lifetime

`activity_json` is a rolling recent window, not a complete longitudinal history. It supports recency, cadence, and recent momentum. It does not support robust multi-month behavioral modeling by itself.

### Constraint: Ranked Is Seasonal Summary Data

`ranked_json` is excellent for competitive participation summaries but is not currently a daily time series.

### Constraint: Clan Battles Are Not Player-Grain

The current clan battle implementation aggregates at clan roster season level. It should not be reinterpreted as a player's own participation or attendance history.

### Constraint: Hidden Profiles Must Stay Explicit

Hidden profiles clear cached detailed views. Missing data here is expected and should never be transformed into a negative player judgment.

## Recommended Metric Families

### Ship Now

| Metric                        | Why It Is Ready                                    |
| ----------------------------- | -------------------------------------------------- |
| `days_since_last_battle`      | direct, intuitive, already stored                  |
| `battles_last_29_days`        | strong recent activity measure from current series |
| `active_days_last_29_days`    | captures cadence better than total alone           |
| `recent_win_rate`             | simple recent-performance companion metric         |
| `pvp_ratio`                   | stable lifetime effectiveness metric               |
| `account_age_days`            | useful longevity context                           |
| `ranked_seasons_participated` | current competitive engagement indicator           |
| `ships_played_total`          | breadth measure derivable from current ship rows   |

### Ship Later With Care

| Metric                                     | Why It Needs More Work                                      |
| ------------------------------------------ | ----------------------------------------------------------- |
| composite player score                     | needs transparent weighting and validation                  |
| percentile rank                            | needs stable dataset denominator and refresh semantics      |
| mode-mix score                             | current data does not have complete daily cross-mode parity |
| recent-vs-career overperformance indicator | possible, but requires careful interpretation copy          |

### Defer

| Metric Area                           | Why Defer                                        |
| ------------------------------------- | ------------------------------------------------ |
| player clan battle engagement         | no player-grain data                             |
| predictive churn or health score      | not enough validated longitudinal infrastructure |
| skill rating beyond descriptive stats | invites overclaiming without a stronger model    |

## Cross-Agent Guidance

### Project Manager

Prioritize explainability over cleverness. The first useful win is a clearer descriptive player model plus dataset explorer, not a prestige score.

### Architect

Push evaluation summaries into a dedicated derived layer. Do not build a comparison product that depends on ad hoc JSON parsing during every dataset query.

### Engineer (Web Dev)

Reuse the existing activity endpoint and chart for player-detail recent activity. Add dataset-wide comparison only after the backend exposes explicit sortable metrics.

### UX

Keep dimensions separate and named. Users should understand whether they are looking at recency, performance, breadth, or competitive participation.

### Designer

Favor tables, histograms, and scatter plots for dataset comparison. Avoid decorative composite visuals that hide the logic.

### QA

Validate not only correctness, but interpretation. Check hidden profiles, sparse activity, ranked-empty players, and players with narrow ship pools.

### Safety

Do not imply value judgments from missing or partial data. Keep operational freshness timestamps out of player evaluation copy.

## Execution Checklist

- [ ] Confirm the product wants a descriptive framework before a composite score.
- [ ] Add recent activity as a first-class player-detail section.
- [ ] Define and expose derived summary metrics for player evaluation.
- [ ] Design a Player Explorer for dataset-wide search, sorting, and filtering.
- [ ] Keep clan battle data excluded from player-level scoring.
- [ ] Add clear hidden-profile handling for all evaluation surfaces.
- [ ] Revisit weighting only after the derived metrics are live and queryable.

## Update Triggers

Revisit this runbook when any of the following happens:

1. The player detail page ships the recent activity section.
2. A dataset-wide player explorer endpoint is introduced.
3. Derived player-summary fields are added to the model or API.
4. Player-level clan battle participation becomes available.
5. The product chooses to ship a composite score or percentile system.

## Definition of Done for This Runbook Revision

This runbook revision is complete when:

1. It reflects the live codebase instead of a hypothetical future state.
2. It distinguishes single-player detail capability from dataset-wide analytics capability.
3. It identifies which metrics are ready, derivable, unsuitable, and deferred.
4. It proposes concrete stories, visuals, and engineering phases.
5. It avoids overclaiming what Battlestats can currently measure.
