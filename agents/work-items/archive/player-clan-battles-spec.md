# Player Profile Clan Battles — Feature Specification

**Author:** Project Manager Agent  
**Date:** 2026-03-15  
**Status:** Draft for Cross-Functional Review  
**Scope:** Player detail experience in the Next.js client plus a targeted player-scoped clan-battle endpoint in the Django backend  
**Primary Surface:** Player detail left column, directly below the clan member list

---

## 1. Objective

**Core question this feature answers:** _"What has this player actually done in clan battles, and how active or successful were they across seasons?"_

The player page currently shows clan context, ranked performance, randoms, and several population-position charts, but it does not expose the player's clan battle history. Clan battle data exists in the system today, but it is surfaced only as a **clan aggregate** on clan detail pages, not as a **player-specific** history on player detail pages.

This feature adds a player-scoped clan battle section so a user can quickly understand:

1. Whether the player has meaningful clan battle participation.
2. Which clan battle seasons they played.
3. How much they played in those seasons.
4. How successful they were by season.
5. How clan battle activity complements the existing ranked and random-battle views.

---

## 2. Available Data Analysis

### What Exists Today

The backend already has a usable clan battle data lane:

1. WG clan battle season metadata is fetched via `clans/season/`.
2. WG per-player clan battle season stats are fetched via `clans/seasonstats/`.
3. The app caches per-player clan battle season rows in `_get_player_clan_battle_season_stats(account_id)`.
4. The current clan detail feature aggregates those player rows into clan-wide season summaries through `refresh_clan_battle_seasons_cache(clan_id)`.

Relevant sources:

1. `server/warships/api/clans.py`
2. `server/warships/data.py`
3. `server/warships/views.py`
4. `server/warships/serializers.py`
5. `client/app/components/ClanBattleSeasons.tsx`

### What the Current Clan UI Shows

The existing clan-battle table is a **current-roster aggregate by season** with these fields:

1. `season_id`
2. `season_name`
3. `season_label`
4. `start_date`
5. `end_date`
6. `ship_tier_min`
7. `ship_tier_max`
8. `participants`
9. `roster_battles`
10. `roster_wins`
11. `roster_losses`
12. `roster_win_rate`

This is useful for the clan page, but it is not appropriate to mount directly on player detail because it answers a different question.

### What Is Missing for Player Detail

The player page needs a **player-scoped season summary**, not a clan-scoped roster rollup.

The good news is the backend already has the raw player-season fetch path. The missing pieces are:

1. A player-facing API endpoint or payload lane for clan battle seasons.
2. A serializer for player clan battle season rows.
3. A player detail UI section to render the data.

---

## 3. PM Recommendation

Implement this as a **new player-scoped clan battle season section** on player detail, backed by the existing cached WG player-season fetch path.

### Recommended MVP

1. Add a player-specific endpoint that returns clan battle seasons for a single player.
2. Mount the new section in the **left column**, directly below `ClanMembers`.
3. Render a compact summary row plus a season table.
4. Defer more ambitious charting until the basic player contract is proven stable.

### Why This Path

1. The backend already has the right upstream fetch path, so this is not a greenfield data integration.
2. The player detail page already has a clear clan-context column where this belongs naturally.
3. A season table is the lowest-risk way to expose exact values without inventing derived interpretations too early.
4. Once the player contract exists, richer visualizations can layer on top cleanly.

---

## 4. Placement Specification

### Exact UI Position

Place the new content in `PlayerDetail` left column:

1. Below the clan plot (`ClanSVG`).
2. Below the stacked clan roster (`ClanMembers`).
3. Above the bottom-page Back button.

### Left-Column Order After Change

Recommended order:

1. Clan header / clan name link
2. Clan plot
3. Clan members list
4. **Player Clan Battles**

This keeps all clan-context surfaces together in one vertical lane and avoids mixing this content into the right-column personal performance stack, where it would compete visually with ranked, randoms, and distribution charts.

### Section Heading

Recommended title: `Clan Battle Seasons`

Recommended helper copy:

`Player-specific clan battle participation by season. Shows when this player appeared in clan battles, how much they played, and how they performed.`

---

## 5. Product Scope

### In Scope

1. Add a player-scoped clan battle section to player detail.
2. Expose player season rows through a new API lane.
3. Render season-level participation and outcome data.
4. Keep the section visually aligned with the current player detail design language.
5. Support loading, empty, and error states.

### Out of Scope

1. Replacing or redesigning the existing clan detail clan-battle table.
2. Per-match clan battle history.
3. Cross-clan historical attribution if the player changed clans between seasons.
4. Deep battle composition analytics, ship breakdowns, or role analysis for clan battles.
5. New WG upstream dependencies beyond the already used `clans/seasonstats/` lane.

---

## 6. Data Contract Recommendation

### Proposed Endpoint

`GET /api/fetch/player_clan_battle_seasons/<player_id>/`

### Recommended Response Shape

Each row should represent one season for one player.

```json
[
  {
    "season_id": 32,
    "season_name": "Northern Waters",
    "season_label": "S32",
    "start_date": "2025-11-01",
    "end_date": "2025-12-15",
    "ship_tier_min": 10,
    "ship_tier_max": 10,
    "battles": 48,
    "wins": 27,
    "losses": 21,
    "win_rate": 56.3
  }
]
```

### Recommended Fields

1. `season_id`
2. `season_name`
3. `season_label`
4. `start_date`
5. `end_date`
6. `ship_tier_min`
7. `ship_tier_max`
8. `battles`
9. `wins`
10. `losses`
11. `win_rate`

### Data Source

Use the existing cached player lane in `warships.data._get_player_clan_battle_season_stats(account_id)` and decorate those rows with existing season metadata from `_get_clan_battle_seasons_metadata()`.

### Data Contract Guardrails

1. Return an empty list if the player has no clan battle history.
2. Do not block the page indefinitely if the WG-backed cache lane is cold.
3. Preserve the current background-refresh pattern used by clan battle summaries where practical.
4. Keep the response season-oriented and flat; do not over-normalize MVP payloads.

---

## 7. Visualization Options

### Option A: Season Summary Table

**Recommendation:** ship this first.

Columns:

1. Season
2. Date Start
3. Ships
4. Battles
5. Wins
6. Losses
7. WR

Why it works:

1. It matches the current clan battle and ranked season mental model.
2. It exposes exact values without ambiguity.
3. It is resilient for sparse or long-tail data.
4. It fits the narrow left column if styled compactly.

### Option B: Compact Battles vs Win Rate Scatterplot

Each season becomes one point:

1. X-axis = battles
2. Y-axis = win rate
3. Color = season recency or tier bracket
4. Tooltip = season label plus exact values

Why it works:

1. It gives a fast visual read on high-volume vs high-win-rate seasons.
2. It complements the exact table instead of replacing it.

Risk:

The left column is narrower, so this should be secondary, not the only representation.

### Option C: Participation Timeline

Render seasons as time-ordered bars or chips showing:

1. season label
2. date window
3. battle volume intensity

Why it works:

1. It answers when the player was active in clan battles.
2. It is visually clean for left-column placement.

Risk:

It communicates recency and participation better than performance, so it should not replace a values table.

### Option D: Two-Layer MVP

Best medium-term composition:

1. summary cards at top
2. season table below
3. optional mini scatterplot later

This is the strongest long-term pattern if the team wants both scanability and precision.

---

## 8. UX Recommendation

### Recommended First Release

Render the section as:

1. A small header with helper copy.
2. A compact summary strip:
   - seasons played
   - total clan battle battles
   - overall clan battle win rate
3. A season table below.

### Why This Is the Best Fit

1. The section will live in the left column, where vertical scanability matters more than decorative charting.
2. The user likely wants to answer `does this player actually play clan battles?` first.
3. The summary strip gives that answer immediately.
4. The table provides exact detail without forcing hover interactions.

### Visual Language

1. Reuse the restrained blue/gray section rhythm already present on player detail.
2. Keep WR colored similarly to existing WR encodings.
3. Avoid overly dense chart chrome in the left column.
4. Use the same loading-panel pattern already used elsewhere on the player page.

---

## 9. Backend Specification

### Required Backend Work

1. Add a player-scoped data helper, for example:
   `fetch_player_clan_battle_seasons(player_id)`
2. Join cached player season rows with season metadata.
3. Sort rows by actual season dates, not raw `season_id` alone.
4. Add a serializer dedicated to player season rows.
5. Add a new view and URL for the player endpoint.

### Sorting Requirement

Use season metadata dates as the primary sort key.

Reason:

The repo already documents that clan-battle season ordering must not rely on raw `season_id` because WG mixes legacy and newer season numbering.

### Caching

Recommended approach:

1. Reuse the existing per-player cached WG fetch path.
2. Avoid duplicating another persistent cache layer unless profiling shows the need.
3. Permit the endpoint to return `[]` on a cold miss only if the UI also has a pending state strategy; otherwise prefer a synchronous cache fill similar to the per-player ranked lane.

---

## 10. Frontend Specification

### New Component

Recommended new component:

`client/app/components/PlayerClanBattleSeasons.tsx`

Responsibilities:

1. Fetch player clan battle season rows.
2. Render loading state.
3. Render empty state.
4. Render summary strip.
5. Render season table.

### Mount Point

Mount inside `PlayerDetail` left column, immediately after `ClanMembers`.

Recommended wrapper pattern:

1. `DeferredSection`
2. section heading with concise helper copy
3. component body

### Empty State

Recommended copy:

`No clan battle season data available for this player.`

### Error State

Recommended copy:

`Unable to load clan battle seasons right now.`

Keep the message neutral and non-sensitive.

---

## 11. Acceptance Criteria

### Product and UX

1. The player detail page shows a `Clan Battle Seasons` section in the left column beneath the clan list when the player has a clan context.
2. The section communicates whether the player has participated in clan battles.
3. The section shows season-by-season volume and outcome, not clan-wide roster aggregates.

### Data and Behavior

1. The section uses a player-scoped endpoint, not the clan aggregate endpoint.
2. Rows include season metadata and player-specific battle outcomes.
3. Empty results render a clear neutral state.
4. Failures render a concise fallback without breaking the page.

### Layout and Integration

1. The new section appears below `ClanMembers` in the left column.
2. The right-column chart stack remains unchanged.
3. The layout does not collapse or overflow awkwardly on typical desktop widths.

### Technical Quality

1. No TypeScript or editor errors are introduced in touched client files.
2. No serializer or view errors are introduced in touched backend files.
3. Clan battle season ordering respects metadata dates.
4. Existing clan detail clan-battle behavior remains unchanged.

---

## 12. Risks and Mitigations

| Risk                                                          | Severity | Mitigation                                                                               |
| ------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------- |
| Team accidentally reuses clan aggregate data on player detail | High     | Require a player-scoped endpoint and dedicated serializer                                |
| Left column becomes too dense or visually noisy               | Medium   | Start with summary strip + compact table, defer heavier charting                         |
| Cold cache WG fetches add latency                             | Medium   | Reuse cached player fetch path and keep the UI resilient to delayed data                 |
| Season sort order looks wrong because of WG legacy IDs        | Medium   | Sort by metadata dates, not raw season ids                                               |
| Users expect historical clan attribution across clan changes  | Low      | Keep MVP scoped to player season performance only; avoid unsupported clan-history claims |

---

## 13. Open Questions

1. Should the MVP load synchronously on first player visit, or should it use a pending/poll pattern similar to clan battle summaries?
2. Should the summary strip include an overall win rate across all clan battle seasons or only season rows?
3. Do we want to expose the clan identity tied to each season later, or is that explicitly out of scope for now?
4. Should the first release stop at the table, or ship with a mini scatterplot as well?

---

## 14. Recommended Implementation Sequence

1. Add backend helper and serializer for player clan battle season rows.
2. Add player endpoint and tests.
3. Build `PlayerClanBattleSeasons.tsx` with loading, empty, and error states.
4. Mount it in the `PlayerDetail` left column beneath `ClanMembers`.
5. Validate layout on desktop and narrow widths.
6. Decide after MVP whether to add a compact chart layer.
