# RankedSVG — PRD-Lite Specification

**Author:** Project Manager Agent  
**Date:** 2026-03-04  
**Status:** Draft  
**Component:** `RankedSVG.tsx` (frontend) + `/api/fetch/ranked_data/<player_id>/` (backend)

---

## 1. Objective

**Core question the chart answers:** _"How has this player performed in Ranked Battles across seasons, and are they getting better?"_

Competitive WoWS players invest heavily in Ranked. Today, BattleStats shows zero ranked data — it's a blind spot. RankedSVG fills that gap by showing:

1. **Which seasons** a player participated in (breadth of commitment)
2. **Highest league attained** per season (Bronze → Silver → Gold) — the primary success metric
3. **Win rate and volume** per season — effort vs. efficiency
4. **Trend over time** — is the player climbing, plateauing, or declining?

This is the ranked equivalent of the ActivitySVG + RandomsSVG combo: a single chart that tells you how serious and how successful a player is in competitive play.

---

## 2. Visualization Design

### Chart Type: Stacked Season Bar Chart with League Color Encoding

**Layout (500px wide, ~320px tall):**

```
                        Ranked Battles by Season
  Gold   ┃                                           ██
  Silver ┃                              ██  ██  ██  ██
  Bronze ┃  ██  ██  ██  ██  ██  ██  ██  ██  ██  ██  ██
         ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
           S17 S18 S19 S20 S21 S22 S23 S24 S25 S26 S27
```

### Axes

| Axis       | Encoding                                                                                                                                                                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **X-axis** | Season labels (e.g., "S24", "S25", "S26", "S27"). One bar group per season. Categorical, ordered chronologically. Show the **last 10 seasons** the player participated in (not all 27 — most old seasons have no data for active players). |
| **Y-axis** | League attainment level: **1 = Bronze**, **2 = Silver**, **3 = Gold**. Discrete scale with labeled ticks. This is NOT a battles count axis — league is the primary outcome metric.                                                         |

### Bar Encoding

Each season renders as a **single stacked column** whose height represents the highest league reached:

| League | Fill Color         | Height                 |
| ------ | ------------------ | ---------------------- |
| Bronze | `#CD7F32` (bronze) | y=1                    |
| Silver | `#C0C0C0` (silver) | y=2 (stacks on bronze) |
| Gold   | `#FFD700` (gold)   | y=3 (stacks on silver) |

A player who reached Silver shows a bar that spans Bronze+Silver. A player who only played Bronze shows a single-height bronze bar. This creates an instantly-readable "How high did they climb?" visual.

### Bar Width & Interior Detail

Each bar is **36px wide**. Inside the bar (bottom section, always visible):

- **Win rate** rendered as a small text label inside the bar body: e.g., `52%`
- **Battle count** rendered below the bar on the x-axis area: e.g., `100b`

The bar's **opacity** varies with win rate:

- ≥ 56% → full opacity (strong performance)
- 50–55% → 80% opacity
- < 50% → 60% opacity (struggle)

This gives an immediate visual read on _quality_ of the season alongside _outcome_.

### Hover Interaction

On hover over a season bar, render a detail group (top-right of SVG, matching existing pattern) showing:

```
Season 27 — Jan 2026
━━━━━━━━━━━━━━━━━━━━
Highest League: Silver
100 battles · 52 wins (52.0%)
Sprints played: 4
Best sprint: Sprint 2 — Silver, Rank 1
```

This gives the full sprint-level detail without cluttering the chart.

### Empty / No-Data State

- **Player has no ranked data at all**: Show text "No Ranked Battles data available."
- **Player has ranked data but only very old seasons**: Still show whatever exists (the "last 10 participated" filter handles this).

---

## 3. Data Shape

### API Endpoint

```
GET /api/fetch/ranked_data/<player_id>/
```

### Response JSON

```jsonc
[
  {
    "season_id": 1027,
    "season_name": "Season 27",
    "season_label": "S27", // short label for x-axis
    "start_date": "2026-01-15", // for hover detail
    "end_date": "2026-03-11", // for hover detail
    "highest_league": 3, // 3=Bronze, 2=Silver, 1=Gold
    "highest_league_name": "Bronze",
    "total_battles": 100,
    "total_wins": 52,
    "win_rate": 0.52,
    "sprints_played": 4,
    "best_sprint": {
      "sprint_number": 2,
      "league": 3,
      "league_name": "Bronze",
      "best_rank": 2,
      "battles": 28,
      "wins": 15,
    },
    "sprints": [
      // full sprint detail for future use
      {
        "sprint_number": 0,
        "league": 3,
        "league_name": "Bronze",
        "rank": 3,
        "best_rank": 2,
        "battles": 33,
        "wins": 20,
      },
      // ... additional sprints
    ],
  },
  // ... additional seasons, ordered by season_id ascending
]
```

### Data Source

**Primary:** `/wows/seasons/accountinfo/` → `rank_info` field.  
**Secondary:** `/wows/seasons/info/` → season metadata (names, dates). This can be cached globally since seasons don't change per-player.

### Backend Processing Logic

1. **Fetch season metadata** (cache globally, refresh daily): season_id → name, start_at, close_at.
2. **Fetch player's `rank_info`** from WG API for the target account_id.
3. **For each season** present in `rank_info`:
   - Iterate all sprints within the season.
   - For each sprint, iterate all leagues.
   - Aggregate: total battles, total wins across all sprints/leagues.
   - Determine `highest_league`: the minimum league number seen (1=Gold is highest).
   - Identify `best_sprint`: the sprint where the player reached the highest league + lowest rank.
4. **Filter** to only seasons where `total_battles > 0`.
5. **Sort** by `season_id` ascending.
6. **Return** the last 10 seasons (or all if fewer than 10).

### Caching Strategy

Follow the existing pattern: store as a JSON field on the Player model (`ranked_json`) with a `ranked_updated_at` timestamp. Refresh if stale (> 1 hour, since ranked seasons are active and stats update frequently during a season).

---

## 4. Acceptance Criteria

### Backend

| #   | Criterion                                                                                                                                                                                 | Testable?                              |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| B1  | `GET /api/fetch/ranked_data/<player_id>/` returns 200 with a JSON array                                                                                                                   | Yes — integration test                 |
| B2  | Each element has all required fields: `season_id`, `season_name`, `season_label`, `highest_league`, `total_battles`, `total_wins`, `win_rate`, `sprints_played`, `best_sprint`, `sprints` | Yes — serializer validation            |
| B3  | `highest_league` correctly reflects the numerically lowest league number across all sprints in that season (1=Gold > 2=Silver > 3=Bronze)                                                 | Yes — unit test with known API fixture |
| B4  | `win_rate` = `total_wins / total_battles`, rounded to 2 decimal places                                                                                                                    | Yes — unit test                        |
| B5  | Seasons with 0 battles are excluded                                                                                                                                                       | Yes — unit test                        |
| B6  | Response is ordered by `season_id` ascending                                                                                                                                              | Yes — unit test                        |
| B7  | Endpoint returns `[]` for a player with no ranked history                                                                                                                                 | Yes — integration test                 |
| B8  | Response is cached (second call within TTL does not re-fetch from WG API)                                                                                                                 | Yes — mock test                        |

### Frontend

| #   | Criterion                                                                      | Testable?                 |
| --- | ------------------------------------------------------------------------------ | ------------------------- |
| F1  | `RankedSVG` renders inside the player detail page below `TypeSVG`              | Yes — visual              |
| F2  | SVG width is 500px                                                             | Yes — DOM inspection      |
| F3  | Bars are colored Bronze/Silver/Gold based on `highest_league`                  | Yes — visual              |
| F4  | X-axis shows season labels chronologically                                     | Yes — visual              |
| F5  | Hover displays season detail overlay with battles, wins, win rate, best sprint | Yes — interaction test    |
| F6  | "No Ranked Battles data available." shown when API returns `[]`                | Yes — visual              |
| F7  | Component handles API errors gracefully (shows error message, no crash)        | Yes — manual or mock test |
| F8  | Component follows the `useRef` + D3 pattern used by `ActivitySVG`              | Yes — code review         |

---

## 5. Non-Goals (v1)

| Excluded                             | Rationale                                                                                                                                   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Per-sprint drill-down view**       | The hover tooltip provides sprint summary. A full expandable sprint-by-sprint chart is a v2 enhancement.                                    |
| **Rank-within-league visualization** | Showing rank 1–10 within each league adds complexity for marginal insight. League attainment is what matters.                               |
| **Ship-level ranked stats**          | The API's `seasons` field provides ship-type-agnostic ranked stats. Per-ship ranked data requires a different API call and is out of scope. |
| **Comparison between players**       | Multi-player overlay is a separate feature.                                                                                                 |
| **Historical ranked rating / Elo**   | WG API does not expose a ranked rating score.                                                                                               |
| **Real-time live season tracking**   | We show current season data as cached, not live-updating.                                                                                   |
| **Sprint-level bars**                | Showing each sprint as its own bar would make the chart too wide and noisy. Season is the right granularity.                                |

---

## 6. Risks

| Risk                                                              | Severity | Mitigation                                                                                                                                                                                                                                               |
| ----------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **WG API rate limits**                                            | Medium   | Season metadata should be cached globally (one call for all players). Player rank_info is one call per player — same pattern as existing endpoints.                                                                                                      |
| **`rank_info` format inconsistencies across old vs. new seasons** | High     | The API uses nested dict keys (season → sprint → league) with varying presence. Backend must handle missing keys defensively. Write unit tests against fixture data for both old (S1001) and new (S1027) seasons.                                        |
| **Players with no ranked data**                                   | Low      | API returns empty `rank_info`. Backend returns `[]`, frontend shows empty state. Already handled by design.                                                                                                                                              |
| **League numbering confusion**                                    | Medium   | In the API, league "1" is Gold (best) but visually Gold should be the tallest bar. The backend should provide both the numeric league and a human-readable name. Frontend uses the number for height, name for display. Document this inversion clearly. |
| **Sprint aggregation correctness**                                | Medium   | A player can appear in multiple leagues within the same sprint (e.g., qualify from Bronze to Silver mid-sprint). The `rank_info` structure nests league under sprint. We must iterate all league entries per sprint and aggregate correctly.             |
| **New DB migration for `ranked_json` field**                      | Low      | Standard Django migration. Follow the pattern of `battles_json`, `tiers_json`. Small risk of migration conflicts if other branches are active.                                                                                                           |
| **SVG rendering performance with many seasons**                   | Low      | Capped at 10 seasons. D3 handles this trivially.                                                                                                                                                                                                         |

---

## 7. Implementation Sequence

### Phase 1: Backend (estimated: 1 session)

| Step | Task                                                                                                                                                                                                                                 | Files                      |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------- |
| 1.1  | Add `ranked_json` (JSONField, nullable) and `ranked_updated_at` (DateTimeField, nullable) to `Player` model. Generate and apply migration.                                                                                           | `models.py`, `migrations/` |
| 1.2  | Create `fetch_ranked_seasons_metadata()` in `data.py` — calls `/wows/seasons/info/`, caches result in a module-level dict or a simple Django cache entry (TTL 24h). Returns `dict[season_id → {name, label, start_date, end_date}]`. | `data.py`                  |
| 1.3  | Create `_fetch_ranked_account_info(account_id)` in `api/players.py` — calls `/wows/seasons/accountinfo/` with `account_id`. Returns raw `rank_info` dict.                                                                            | `api/players.py`           |
| 1.4  | Create `fetch_ranked_data(player_id)` in `data.py` — orchestrates 1.2 + 1.3, aggregates per-season, caches to `ranked_json`. Implements the processing logic from §3.                                                                | `data.py`                  |
| 1.5  | Create `RankedDataSerializer` in `serializers.py`.                                                                                                                                                                                   | `serializers.py`           |
| 1.6  | Create `ranked_data` view function in `views.py`.                                                                                                                                                                                    | `views.py`                 |
| 1.7  | Add URL route `api/fetch/ranked_data/<str:player_id>/` in `urls.py`.                                                                                                                                                                 | `urls.py`                  |
| 1.8  | Write unit tests for aggregation logic (fixture-based).                                                                                                                                                                              | `tests/`                   |

### Phase 2: Frontend (estimated: 1 session)

| Step | Task                                                                                                                                              | Files                      |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| 2.1  | Create `RankedSVG.tsx` following `ActivitySVG` pattern: `useRef` container, `useState`/`useEffect` for fetch, D3 rendering in second `useEffect`. | `components/RankedSVG.tsx` |
| 2.2  | Implement bar chart: categorical x-axis (season labels), discrete y-axis (league levels 1–3), stacked bars with league colors.                    | `components/RankedSVG.tsx` |
| 2.3  | Implement hover detail group.                                                                                                                     | `components/RankedSVG.tsx` |
| 2.4  | Implement empty/error states.                                                                                                                     | `components/RankedSVG.tsx` |
| 2.5  | Add `<RankedSVG playerId={player.player_id} />` to `PlayerDetail.tsx` below `TypeSVG`.                                                            | `PlayerDetail.tsx`         |
| 2.6  | Visual QA — verify with a known player (walkish1) that the chart renders correctly against known ranked data.                                     | Manual                     |

### Phase 3: Polish (estimated: 0.5 session)

| Step | Task                                                                              |
| ---- | --------------------------------------------------------------------------------- |
| 3.1  | Add win-rate opacity encoding to bars.                                            |
| 3.2  | Add battle count labels below bars.                                               |
| 3.3  | Test edge cases: player with 1 season, player with all Gold, player with no data. |
| 3.4  | Verify caching behavior (second load is fast, stale data triggers refresh).       |

---

## Appendix A: League Color Palette

| League          | Color                       | Hex       |
| --------------- | --------------------------- | --------- |
| Bronze          | Bronze metallic             | `#CD7F32` |
| Silver          | Silver metallic             | `#C0C0C0` |
| Gold            | Gold metallic               | `#FFD700` |
| Hover highlight | Lavender (existing pattern) | `#bcbddc` |

## Appendix B: API Call Reference

```
# Season metadata (global, cached)
GET https://api.worldofwarships.com/wows/seasons/info/
  ?application_id=<APP_ID>
  &fields=season_id,season_name,start_at,close_at

# Player ranked data
GET https://api.worldofwarships.com/wows/seasons/accountinfo/
  ?application_id=<APP_ID>
  &account_id=<ACCOUNT_ID>
  &fields=rank_info
```

## Appendix C: Relationship to Existing Components

```
PlayerDetail.tsx
├── RandomsSVG    — top ships by battles (horizontal bars)
├── ActivitySVG   — daily battles over 28 days (vertical bars, time axis)
├── TierSVG       — battles by tier (horizontal bars)
├── TypeSVG       — battles by ship type (horizontal bars)
└── RankedSVG     — ranked season progression (vertical bars, categorical axis) ← NEW
```
