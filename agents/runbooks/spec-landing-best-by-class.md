# Spec: Landing Page "Best by Class" Filtering

_Last updated: 2026-04-01_

_Status: Proposed / Planning_

## Purpose
Introduce a sub-filtering mechanism on the landing page's player chart. When a user selects the "Best" mode, a secondary row of buttons will appear allowing them to filter the "Best" list by specific ship classes: Battleships, Cruisers, Destroyers, Submarines, and Aircraft Carriers.

## UX & Interaction Scope

1. **Sub-Navigation Row:**
   - Appears immediately under the primary mode selector (Best, Random, Sigma, Popular) *only* when "Best" is active.
   - Disappears immediately if another primary mode is selected.
   - Options: `Overall` (default), `Battleships`, `Cruisers`, `Destroyers`, `Carriers`, `Submarines`.
2. **Chart Updates:**
   - Clicking a class button switches the chart's data payload to the top 25 players for that specific class.
   - The chart axes (`pvp_battles` vs `pvp_ratio`) and `LandingPlayerSVG` logic remain exactly the same; only the injected JSON payload changes.
   - State transition should leverage the existing stale-while-revalidate / cache-first patterns (no full-page spinners).

## Backend & Database Optimization

Computing "Best" players across ~275K rows based on per-class performance is computationally expensive and cannot be performed synchronously on page load. It must adhere to the Battlestats caching doctrine.

### 1. Data Pre-computation (The Warmer Task)
The existing landing page warmer (`server/warships/tasks.py` -> `warm_landing_page_content`) must be extended to pre-calculate these lists every 55 minutes.
- **Aggregation:** 
  - Instead of computing one `landing_best_players` payload, the warmer will run a composite score query for *each* ship class. 
  - Criteria logic: Minimum class battles threshold (e.g., > 500 battles in class) to filter noise, then ranked by class-specific win rate and damage metrics.
- **Database Load:**
  - This requires scanning `warships_player` or the ship-level rollups.
  - MUST use `_elevated_work_mem()` context manager for the sorting phases to stay within the 2MB -> 8MB PostgreSQL constraint.
  - *Recommendation:* If `warships_player` does not have flat cached columns for `pvp_battleship_battles` / `pvp_battleship_wins`, we may need a lightweight Materialized View representing "Player Class Aggregates" to prevent hammering the database during the warmer cycle.

### 2. Cache & Publishing
- Publish 6 distinct keys to Redis:
  - `landing:players:best:overall` (existing)
  - `landing:players:best:battleship`
  - `landing:players:best:cruiser`
  - `landing:players:best:destroyer`
  - `landing:players:best:carrier`
  - `landing:players:best:submarine`
- Employ the standard Durable Fallback mechanism in the database in case the Redis TTL expires before the warmer completes its next cycle.

### 3. API Contract Update
- Existing Endpoint: `GET /api/landing_players/?mode=best`
- New Contract: Accept an optional `class` query parameter.
  - Example: `GET /api/landing_players/?mode=best&class=battleship`
  - If `class` is omitted or invalid, default to `overall` to preserve backward compatibility with existing frontends during rollout.

## Implementation Tranches

### Tranche 1: Backend Data Preparation (Non-user facing)
- Add class-specific win rate & battle count aggregations to the scoring logic within `server/warships/landing.py` or `data.py`.
- Update `warm_landing_page_content` Celery task to populate the 5 new cache keys + durable fallbacks.
- Expose the `class` parameter on the API endpoint.
- **Validation:** Hit the API directly (`/api/landing_players/?mode=best&class=cruiser`) and ensure response is < 100ms (served from cache).

### Tranche 2: Frontend & UI
- Update landing page state (`LandingDropdowns.tsx` or similar controller) to track `selectedClass`.
- Render the sub-navigation conditionally (`if mode === 'best'`).
- Pipe the `selectedClass` state into the `sharedJsonFetch` call that retrieves the landing page data.
- Add focused React Testing Library checks for the dual-row interaction.
- **Validation:** Click "Best", click "Cruisers", verify chart updates. Switch to "Sigma", verify the sub-row unmounts. Switch back to "Best", verify it remounts with "Overall" (or the previous selection) highlighted. 

## Safety & Limits
- **Wargaming API Load:** 0. This relies entirely on existing data scraped via the roster update loops.
- **Database Connection Load:** Negligible. All reads will hit Redis via the lazy-refresh endpoint. Write operations are isolated to the single background Celery worker executing `warm_landing_page_content`.
- **UX Load:** SVG does not need to remount. `D3` will seamlessly transition `.data(plotData)` with the new slice of 25 nodes.