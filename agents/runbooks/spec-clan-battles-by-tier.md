# Feature Specification: Clan Battles by Tier

## Objective
Introduce a horizontal or vertical bar chart underneath the primary plot on the Clan Detail page. This chart aggregates the `pvp_battles` played at every Ship Tier (1-11) for all active members in the clan, providing a holistic snapshot of where the clan spends most of its time.

## Operational Constraints & Principles
1. **Performance First**: Querying `jsonb` array elements (`tiers_json`) across a clan's roster synchronously is expensive and bypasses indexes. We must rely on an asynchronous background job and 24-hour Redis caching. Directional accuracy is prioritized over real-time precision.
2. **Theme Consistency**: The new SVG component should share stroke, fill, and text colors with the existing D3 implementation suite (`client/app/lib/chartTheme.ts`, `TierSVG.tsx`).
3. **Graceful Loading**: If the cache misses, the frontend should show a skeleton loader and retry lazily without blocking other charts using the `X-Clan-Tiers-Pending: true` header schema, per doctrine.

## Architecture & Implementation Tranches

### Tranche 1: Backend Aggregation & Routing (Python/Django)
1. **Data Aggregation Logic (`server/warships/data.py`)**:
   - Create `update_clan_tier_distribution(clan_id: int, realm: str) -> list`.
   - Query all `Player` records efficiently: `Player.objects.filter(clan_id=clan_id, is_hidden=False).values_list('tiers_json', flat=True)`.
   - In Python, iterate over all `tiers_json` payloads (which contain `[{ship_tier: 10, pvp_battles: 154}, ...]`) and reduce them into a single dictionary summing `pvp_battles` for tiers 1-11.
   - Cache the computed output as `[{"tier": 1, "battles": 200}, {"tier": 2, "battles": ...}]` into Redis (`cache.set`) using a key like `clan:tiers:v1:{realm}:{clan_id}` with a 24-hour TTL (`86400`).

2. **Celery Task Integration (`server/warships/tasks.py`)**:
   - Register `@app.task(**TASK_OPTS) def update_clan_tier_distribution_task(request, clan_id, realm)`. 
   - Integrate this as a deferred task triggered by cache misses.

3. **API View (`server/warships/views.py`)**:
   - Create `clan_tier_distribution(request, clan_id: str)`.
   - Execute a fast cache `get(key)`. 
   - If missing/stale, dispatch `update_clan_tier_distribution_task` and return `[]` combined with HTTP Header `X-Clan-Tiers-Pending: true`.
   - Add the route to `battlestats/urls.py` (e.g. `/api/fetch/clan_tiers/<clan_id>/`).

### Tranche 2: Frontend Data Access & D3 Component
1. **Component Creation (`client/app/components/ClanTierDistributionSVG.tsx`)**:
   - Model the component on standard conventions: accept `data`, `svgHeight`, `theme`, and use `d3.scaleBand()` for the 11 explicit tiers. 
   - Use `d3.scaleLinear()` for the Y-axis targeting the max battle count.
   - Draw standard `d3` `<rect>` bars, styling them gently with the current active motif (`theme.colors.axisLine` for borders, slight transparency for fills).

2. **React Data Hook Integration**:
   - Define `useClanTiersDistribution(clanId)` leveraging `sharedJsonFetch`.
   - Instruct the fetching layer to handle the `X-Clan-Tiers-Pending` flag by engaging the existing poll-retry mechanism inside `sharedJsonFetch` or an explicit timeout loop.
   - Mount a Skeleton UI during the initial fetch phase to avoid content judder. Include a loading message: `"Aggregating clan tier distributions..."`.
   
3. **Mount in UI (`client/app/clan/[clanSlug]/page.tsx`)**:
   - Insert the new component clearly below the primary `ClanSVG` or `ActivitySVG` slot but above individual member tables, wrapped in an `ErrorBoundary` that degrades to `"Tier data unavailable"`.

## Mock Agent Reviews

**[Architect] - Endorsed with Minor Adjustments**
> *Review Notes*: Using standard Python dict reduction over 50 rows of `.values_list('tiers_json', flat=True)` is O(1) negligible computation. The standard DB managed instance will not spike from this. Utilizing the standard Header-Pending pattern natively unifies it with existing architecture. No need for Postgres `MATERIALIZED VIEW` since this is clan-scoped (max 50 members), unlike the 275,000 global player dataset constraint from our previous runbook.

**[DB Agent] - Passed Quality Gate**
> *Review Notes*: Correctly identified the `jsonb` unnesting limitation. Doing the sum map inside the Celery Python memory space offloads the DB engine perfectly.

**[UX Designer] - Advisory**
> *Review Notes*: Please ensure `Tier 1` to `Tier 11` display properly as Roman Numerals (I, II ... X, XI) on the X-axis for aesthetics. The tooltips should say "X Total Battles" to make the scope explicit.

