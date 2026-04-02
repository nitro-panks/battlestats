# Spec: Landing Page "Best by Class" Filtering

_Last updated: 2026-04-01_

_Status: Approved / Ready for Implementation_

## Purpose

Introduce a sub-filtering mechanism on the landing page's player chart. When a user selects the "Best" mode, a secondary row of buttons will appear allowing them to filter the "Best" list by specific ship classes: Battleships, Cruisers, Destroyers, Submarines, and Aircraft Carriers.

## UX & Interaction Scope

1. **Sub-Navigation Row:**
   - Appears immediately under the primary mode selector (Best, Random, Sigma, Popular) _only_ when "Best" is active.
   - Disappears immediately if another primary mode is selected.
   - If the user switches away from the "Best" mode and back to it later, the sub-filter explicitly resets to `Overall` to prevent disjointed persistent state issues.
   - Options: `Overall` (default), `Battleships`, `Cruisers`, `Destroyers`, `Carriers`, `Submarines`.
2. **Chart Updates:**
   - Clicking a class filter button retrieves the respective top 25 players calculated by our formula for that specific ship class.
   - The selected 25 players are then splayed out on the existing `LandingPlayerSVG` chart.
   - The chart axes will dynamically map to the selected class's statistics (e.g. Battleship Win Rate & Battleship Battles limit) rather than overall account statistics to provide an accurate visual representation of the class skill.
   - State transition leverages the `X-[Dataset]-Pending` header. If the Redis cache misses (e.g. `X-Landing-Pending: true` is returned), do not unmount the chart frame. Instead, show an explicit loading skeleton overlay WITH a clear message (e.g., `"Calculating top [Class] players... check back shortly"`) while gracefully holding the active chart. If the request 500s or hard-fails, display the standard `"Unable to load [Class] stats"` error message with a retry affordance.

## Backend & Database Optimization

Computing "Best" players across ~275K rows based on per-class performance is computationally expensive and cannot be performed synchronously on page load. It must adhere to the Battlestats caching doctrine.

### 1. Data Pre-computation (The Warmer Task)

The existing landing page warmer (`server/warships/tasks.py` -> `warm_landing_page_content`) must be extended to pre-calculate these lists every 55 minutes.

- **Aggregation:**
  - Instead of computing one `landing_best_players` payload, the warmer will run a composite score query for _each_ ship class.
  - Criteria logic: Minimum class battles threshold (e.g., > 500 battles in class) to filter noise, then ranked by class-specific win rate and damage metrics.
- **Database Load & Materialized View (Mandatory):**
  - Extract and flatten the unstructured `type_json` (e.g. Battleship Battles, Cruiser Wins) fields into a new Materialized View (`mv_player_class_stats`).
  - This prevents out-of-memory errors on the DB when attempting to cast and sort JSON across 275K rows natively.
  - The MV should only index active, public players to reduce its footprint, utilizing B-Tree indices for concurrent refreshes during `warm_landing_page_content`.
  - MUST use `_elevated_work_mem()` context manager for the sorting phases.

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
- **Metric Aliasing:** When a class payload like `cruiser` is hit, the backend will serialize and return the specific class' battles and win-rate explicitly mapped to generic root-level payload keys `metric_battles` and `metric_ratio`. This guarantees `LandingPlayerSVG` does not need hardcoded class-parsing mappings.

## Implementation Tranches

### Tranche 1: Backend Data Preparation (Non-user facing)

- Add Django migration to create `mv_player_class_stats` flattening the `type_json` blobs.
- Add class-specific win rate & battle count aggregations using `mv_player_class_stats` within `server/warships/landing.py` or `data.py`.
- Update `warm_landing_page_content` Celery task to populate the 5 new cache keys + durable fallbacks.
- Expose the `class` parameter on the API endpoint, alias returning `metric_battles` & `metric_ratio`.
- **Validation:** Hit the API directly (`/api/landing_players/?mode=best&class=cruiser`) and ensure response is < 100ms (served from cache).

### Tranche 2: Frontend & UI

- Update landing page state (`LandingDropdowns.tsx` or similar controller) to track `selectedClass`.
- Render the sub-navigation conditionally (`if mode === 'best'`).
- Pipe the `selectedClass` state into the `sharedJsonFetch` payload call.
- Add UI state behavior to explicitly handle missing specific-class cache `X-Landing-Pending: true` by rendering the `"Calculating top [Class] players... check back shortly"` loading message. Provide an error-fallback UI (`"Unable to load [Class] stats."`) for network failures.
- **Tests (Mandatory):** Generate Playwright integration test at `e2e/landing-best-by-class.spec.ts` matching the UI behaviors and asserting the exact loading/error texts.
- **Validation:** Open live UI. Click "Best", click "Cruisers", verify chart updates without unmounting. Switch to "Sigma" to hide sub-nav. Switch to "Best", verify it resets to "Overall".

## Safety & Limits

- **Wargaming API Load:** 0. This relies entirely on existing data scraped via the roster update loops.
- **Database Connection Load:** Negligible. All reads will hit Redis via the lazy-refresh endpoint. Write operations are isolated to the single background Celery worker executing `warm_landing_page_content`.
- **UX Load:** SVG does not need to remount. `D3` will seamlessly transition `.data(plotData)` with the new slice of 25 nodes.
