# Runbook: Heavy Client Component Audit

_Captured: 2026-03-20_

_Status: historical audit; first policy-extraction slice executed on 2026-03-20_

## Purpose

Record which client components are carrying the most code and complexity, and identify where simplification and optimization work is most likely to pay off.

## Method

- Ranked component files under `client/app/components` by line count, excluding tests.
- Read the largest route/container files first.
- Read the largest chart-heavy D3 files second.
- Classified findings by simplification value and runtime optimization value.

## Heaviest Component List

1. `client/app/components/PlayerDetail.tsx` - `834` lines
2. `client/app/components/RandomsSVG.tsx` - `722` lines
3. `client/app/components/PopulationDistributionSVG.tsx` - `656` lines
4. `client/app/components/PlayerSearch.tsx` - `655` lines
5. `client/app/components/ClanSVG.tsx` - `463` lines
6. `client/app/components/TraceDashboard.tsx` - `462` lines
7. `client/app/components/TierTypeHeatmapSVG.tsx` - `404` lines
8. `client/app/components/WRDistributionDesign2SVG.tsx` - `400` lines
9. `client/app/components/LandingActivityAttritionSVG.tsx` - `376` lines
10. `client/app/components/PlayerEfficiencyBadges.tsx` - `329` lines
11. `client/app/components/ClanActivityHistogram.tsx` - `325` lines
12. `client/app/components/RankedSeasons.tsx` - `299` lines
13. `client/app/components/PlayerExplorer.tsx` - `290` lines

## Findings

### `PlayerDetail.tsx`

Why it is heavy:

- It is both the route shell and the section composition layer for the player page.
- It owns dynamic imports, local effects, header-state derivation, share behavior, clan-member idle loading, and the full two-column layout.
- It declares a large number of deferred and non-deferred sections inline, so the file has become the registry for the entire route.

Simplification findings:

- Split the file by responsibility, not by arbitrary size. The most natural seams are `PlayerHeader`, `PlayerClanRail`, `PlayerSummaryGrid`, and `PlayerAnalyticsSections`.
- Move header-only helpers and badge rules into a dedicated player-header module so the route file stops carrying icon policy and verdict copy.
- Move clan-member idle activation and clan-battle summary synchronization into small hooks. Those are behavior units, not layout concerns.
- Convert the repeated `DeferredSection` declarations into a data-driven section list or grouped subcomponents. The current inline pattern is readable in isolation but too verbose at this size.

Optimization findings:

- The file remains the top rerender fan-out point for the player route. Any state change in the parent can rebuild the JSX for all sections, even if the expensive children are dynamically imported.
- `ClanSVG` still mounts above the fold and receives `membersData`, so clan-member state updates can cascade through the left rail early.
- A future optimization tranche should reduce parent-level state ownership so that share-state or section-specific state does not force the whole page shell to reconcile.

Priority:

- Highest simplification priority in the client.
- High performance priority because it sits on the hot player route.

### `PlayerSearch.tsx`

Why it is heavy:

- It combines landing-page content, route navigation, player-detail handoff, recent-entity loading, landing list refresh, URL query handling, custom event handling, and clan hydration polling.
- It effectively acts as both a landing dashboard and a search controller.

Simplification findings:

- Split route orchestration from landing presentation. A `LandingSurface` container and a `usePlayerSearchRoute` hook would remove a large amount of effect and state noise from the view layer.
- Extract the landing clan/player refresh logic into dedicated hooks instead of keeping interval management inside the top-level component.
- Extract the clan-hydration polling behavior into its own hook. It is operational behavior and obscures the main render path.
- The landing button groups and formula tooltips are good candidates for small presentational components because they are repeated structure rather than route logic.

Optimization findings:

- Two separate `setInterval` effects refresh landing clans and landing players independently every minute. That is functional, but it keeps polling policy coupled to a large render component.
- The whole landing surface rerenders as route-level state changes between landing and detail mode.
- `PlayerExplorer` is dynamically imported but guarded behind `SHOW_PLAYER_EXPLORER = false`; keeping dormant feature wiring inside the main landing container adds maintenance cost without current user value.

Priority:

- Highest container cleanup priority alongside `PlayerDetail`.
- Medium-to-high runtime priority because it owns the landing surface.

### `RandomsSVG.tsx`

Why it is heavy:

- It contains two full chart designs, data fetching, filter state, freshness display, filter controls, and imperative D3 rendering in one file.
- The file is effectively a mini feature area rather than a component.

Simplification findings:

- Split the data-and-filter controller from the D3 renderer.
- Separate `design1` and `design2` into different renderer modules. Carrying two large imperative chart implementations in one file makes the default path harder to maintain.
- If `design2` is no longer an active product choice, retire it instead of preserving both renderers indefinitely.
- Extract shared helpers for color rules, labels, and tooltip rendering so the chart logic is easier to inspect.

Optimization findings:

- The component fully clears and redraws the SVG whenever `chartData` or `design` changes.
- Top-20 data keeps redraw cost bounded, so the bigger issue is not raw D3 cost alone but the coupling of fetch, filter, and redraw policy in one component.
- The initial fetch resets selected tiers and types after loading, which means data arrival also reconfigures local UI state in the same component pass.

Priority:

- Highest chart refactor priority.
- Medium runtime priority; bounded data size limits damage, but maintainability is poor.

### `PopulationDistributionSVG.tsx`

Why it is heavy:

- It is a generic chart abstraction that carries scale logic, percentile math, gradient construction, overlay logic, axis formatting, legend rendering, fetch lifecycle, and error handling in a single file.
- It serves multiple wrappers, so its complexity is structural rather than route-specific.

Simplification findings:

- Split distribution math from SVG rendering. Percentile calculation, axis formatting, and domain building are reusable utilities, not component concerns.
- Move fetch logic into a `usePopulationDistribution` hook so the chart module is a pure renderer.
- Keep the wrapper components thin, but reduce how much branching the generic renderer owns internally.

Optimization findings:

- The component fetches its own payload on mount instead of consuming a shared higher-level distribution resource.
- That is acceptable at current scale, but it becomes noisier as more wrapper charts are added to the same route.
- The component also fully redraws the SVG on each dependency change, which is fine for moderate data volumes but increases the cost of keeping all chart behavior inside one generic file.

Priority:

- High simplification priority.
- Medium runtime priority.

### `ClanSVG.tsx`

Why it is heavy:

- It mixes fetch policy, member/activity enrichment, activity-bar rendering, scatterplot rendering, tooltip behavior, highlight logic, and point filtering.
- The imperative draw function performs the fetch itself.

Simplification findings:

- Move `/api/fetch/clan_data/...` retrieval out of `drawClanPlot` and into a hook or controller layer.
- Split the activity-bar logic from the scatterplot logic. They are related views, but they are distinct responsibilities.
- Extract tooltip/summary-card rendering helpers into a separate renderer module.

Optimization findings:

- The component redraws when `chartMemberActivitySignature` changes, and that redraw path includes the fetch call because fetching is embedded in the draw function.
- Shared JSON caching softens the cost, but the structure still couples redraws to network retrieval attempts.
- Because this chart is above the fold on the player route, any avoidable redraw or refetch policy here matters more than a similarly sized secondary chart.

Priority:

- High optimization priority on the player route.
- Medium simplification priority.

### `TraceDashboard.tsx`

Why it is heavy:

- It contains payload normalization, formatting rules, fetch lifecycle, and all page sections in one file.
- It reads more like a full page implementation than a reusable component.

Simplification findings:

- Break it into `TraceDashboardHeader`, `TraceRunsPanel`, `TraceDiagnosticsPanel`, and `TraceLearningPanel`.
- Move payload normalization into a dedicated adapter module. The page component should not own schema cleanup details inline.
- Keep list renderers like `CountList` and `LearningNoteList`, but move them out so the file stops mixing page orchestration with local helper declarations.

Optimization findings:

- Runtime pressure is modest because this page performs a single fetch and renders a management surface, not a hot landing/detail flow.
- This is primarily a maintainability cleanup target, not a front-of-queue performance target.

Priority:

- Medium simplification priority.
- Low runtime priority.

### `TierTypeHeatmapSVG.tsx`

Why it is heavy:

- It bundles normalization, summary-card rendering, scale setup, grid rendering, overlay rendering, and fetch lifecycle.
- It has the same pattern as several other D3 files: fetch plus renderer plus interaction model together.

Simplification findings:

- Extract shared D3 chart helpers for message states, axis drawing, and summary overlays.
- Move payload normalization and fetch concerns out of the draw module.
- Keep the chart-specific view logic, but stop making the component responsible for every data and rendering concern.

Optimization findings:

- The chart redraw model is acceptable for its data size.
- The larger issue is duplication of the same architectural pattern already seen in `RandomsSVG`, `ClanSVG`, and `PopulationDistributionSVG`.
- If a chart cleanup tranche happens, this file should be addressed by the same pattern rather than independently.

Priority:

- Medium simplification priority.
- Low-to-medium runtime priority.

### `PlayerExplorer.tsx`

Why it is heavy relative to value:

- It is smaller than the top group, but it still combines filter state, debounce timing, fetch lifecycle, table rendering, paging, and error/loading states in one file.
- The feature is currently behind `SHOW_PLAYER_EXPLORER = false` in `PlayerSearch.tsx`.

Simplification findings:

- Extract a `usePlayerExplorerQuery` hook for query/filter/page state and remote loading.
- Split the control bar from the table and pager.
- If the feature remains disabled, the code should not keep influencing the complexity of `PlayerSearch`.

Optimization findings:

- The current debounce and abort behavior is reasonable.
- Because the feature is off, this is not a user-facing performance problem today.
- This should be cleaned up only if the explorer is going to be turned back on or expanded.

Priority:

- Low current priority.

### `RankedSeasons.tsx`

Why it was reviewed:

- It is not one of the largest files overall, but it is one of the larger player-route section components and participates in deferred route work.

Simplification findings:

- The sorting and retry logic can be pushed down into a small data hook.
- The table header sort controls are clear enough, but they add to the weight of the file when combined with loading-state overlays and data management.

Optimization findings:

- The biggest smell is not render cost; it is the local retry loop inside the component.
- Retry policy should generally live in a fetch helper or data hook so every section does not reinvent it.

Priority:

- Low-to-medium priority.

## Cross-Cutting Themes

### 1. Route containers are carrying too much policy

`PlayerDetail.tsx` and `PlayerSearch.tsx` are not just large; they own too many distinct behaviors. They should be the first cleanup tranche because every other section depends on them.

### 2. D3 files repeat the same architectural smell

The main D3 components repeatedly combine:

- fetch lifecycle
- payload normalization
- renderer setup
- tooltip and legend composition
- redraw policy

The code would be easier to reason about if data acquisition and chart drawing were separated consistently.

### 3. Above-the-fold chart policy still matters more than raw chart size

`ClanSVG` matters more than some larger files because it sits above the fold on the player route and is wired to member-state updates. Hot-route placement should influence priority more than line count alone.

### 4. Dormant feature paths should not stay wired into hot containers

`PlayerExplorer` is the clearest example. A disabled feature should not continue to inflate the main landing controller if it is not currently part of the product surface.

## Recommended Next Tranche

If this audit is turned into implementation work later, the cleanest first tranche is:

1. Split `PlayerDetail.tsx` into route shell plus sub-sections.
2. Split `PlayerSearch.tsx` into landing/search controller hooks plus presentational sections.
3. Move fetch policy out of `ClanSVG.tsx` and `RandomsSVG.tsx`.
4. Extract shared chart data hooks/utilities for `PopulationDistributionSVG.tsx` and `TierTypeHeatmapSVG.tsx`.

That order attacks the highest-traffic containers first, then removes the most obvious chart-level architectural coupling.

## Defer For Now

- `TraceDashboard.tsx` is worth simplifying, but it is not on the critical path for main site latency.
- `PlayerExplorer.tsx` should stay deferred unless the feature is re-enabled.
- Lower-ranked D3 components should be handled as part of a shared chart refactor pattern, not as isolated one-off cleanups.
