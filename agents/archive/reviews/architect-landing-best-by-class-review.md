# Architect & DB Review: Landing Page Best by Class Filtering

**Reviewer:** Architect Agent
**Status:** Requires structural amendments before implementation.

## 1. Options Considered (Database Query Strategy)

To compute the "Best" players by ship class, we must query the `warships_player` table which stores class performance data inside the `type_json` JSONField.

- **Option A: Querying `type_json` directly in the warmer.** Scanning ~275K rows and sorting on an extracted JSON value (e.g., `CAST(type_json->0->>'battles' AS int) > 500`) without a GIN index on specific metrics will induce severe CPU/Memory spikes, exceeding the 2GB constraint of the managed database, even with `_elevated_work_mem()`.
- **Option B: Materialized View (Recommended).** Extract and flatten the `type_json` fields (Cruiser Battles, Cruiser Wins, Battleship Battles, etc.) into a new Materialized View, e.g., `mv_player_class_stats`, which is refreshed concurrently during the warmer cycle.

## 2. Why Chosen Option Wins

Option B (Materialized View) ensures the query runs efficiently against structured, typed columns with proper B-Tree indexes for `(class_battles, class_win_rate)`. It aligns with the existing project pattern (`mv_player_distribution_stats`) and prevents out-of-memory errors on the database.

## 3. Risks Introduced

- Expanding the warmer task to run 5 separate composite-score queries plus a Materialized View refresh might push the 55-minute cycle too long.
- **Mitigation:** The Materialized View should only index active, public players, greatly reducing its footprint before sorting.

## 4. Contract Considerations

- In the API Payload, the backend must return exact metric paths (e.g. mapping the `LandingPlayerSVG` axes `pvp_battles` and `pvp_ratio` to the specific class battles/ratio). The frontend shouldn't parse `type_json` client-side for these top 25; the backend should alias them as `class_battles` and `class_ratio` in the top-level returned dictionary.

## Required Spec Updates

- Add `mv_player_class_stats` (Materialized View) to the Backend & Database Optimization tranches.
- Specify that the backend will alias the class-specific metrics to a consistent key (e.g. `metric_battles`, `metric_ratio`) so the frontend SVG component doesn't need conditional mapping.
