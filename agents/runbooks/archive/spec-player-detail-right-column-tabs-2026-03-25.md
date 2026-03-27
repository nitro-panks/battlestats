# Spec: Player Detail Right-Column Tabs For Heavy Panel Mitigation

_Captured: 2026-03-25_

_Status: QA reviewed, tranche-1 implemented, and background tab-data warmup documented on 2026-03-25_

_Current shipped note: the visible tab set has since expanded with the follow-up `Badges` and `Clan Battles` lanes, and the current default active lane is `Profile` rather than the original `Population` proposal._

## QA Review Outcome

The runbook was reviewed against the current player-detail implementation and prior player-route performance specs before shipping the first tranche.

Accepted with two explicit tranche-1 clarifications:

1. the shipped interaction is tabs-first; mobile swipe or carousel behavior remains deferred
2. the first tranche uses single-active-panel mounting plus shared fetch caching, not a new parent-owned per-tab payload store

Post-implementation follow-up added one bounded warmup clarification:

3. once the player shell finishes loading, the client may warm inactive tab data in the background during idle time, but it must not eagerly mount inactive tab DOM

These clarifications keep the change small and reversible while still removing the initial right-column fan-out.

## Goal

Reduce player-route tail latency, DOM growth, and late layout churn by replacing the lower right-column stack on the player detail page with a single tabbed panel surface.

This spec starts at the current `Win Rate vs Survival` section and covers the heavy right-column panels beneath the player summary cards and metadata block.

## Doctrine Alignment

This design follows the active battlestats doctrine:

- prefer incremental evolution over a big-bang rewrite
- prefer reusing existing fetch paths, shared components, and validation patterns
- favor non-blocking background hydration over synchronous page-load fan-out
- keep rollback and validation steps explicit

This is a layout and fetch-orchestration change, not a new data model or broad visualization rewrite.

## Current State

The current right column renders this sequence after the summary block on `PlayerDetail`:

1. `Win Rate vs Survival`
2. `Battles Played Distribution` when `pvp_battles >= 150`
3. `Top Ships (Random Battles)`
4. `Ranked Games vs Win Rate` when the player has ranked history
5. `Ranked Seasons`
6. `Tier vs Type Profile`
7. `Performance by Ship Type`

Most of these sections are already wrapped in `DeferredSection`, but they still behave like a long scrolling stack:

1. several heavy panels can enter the viewport window close together
2. multiple D3 surfaces still mount on the same visit
3. each panel owns its own fetch lifecycle and mount cost
4. the route still grows large on heavier players and clan-heavy pages

Existing route findings already show that the player page problem is a tail problem rather than a median problem:

- large rendered DOM strongly correlates with bad pages
- late panel mounts are still implicated in LCP and CLS outliers
- the current direction in repo history is already toward deferral, idle loading, dedupe, and cache-first reads

## Product Decision

Replace the lower right-column stack with a single `Insights` surface that uses tabs on desktop and may expose swipe or carousel behavior on small screens, but is driven by the same single-active-panel state.

Desktop recommendation:

1. accessible tabs are the primary control
2. only one tab panel is mounted at a time
3. previously visited tabs may keep fetched data in memory, but their heavy DOM trees should not remain mounted

Mobile recommendation:

1. keep the same tab list for explicit navigation
2. optionally allow horizontal swipe between panels
3. do not implement a free-running multi-slide carousel that mounts adjacent heavy panels by default

The key performance requirement is single-active heavy content, not the visual metaphor.

## Proposed Tab Groups

Group the existing right-column panels into four tabs.

### 1. Population

Purpose:

- show where the player sits relative to the broader tracked population

Panels:

1. `Win Rate vs Survival`
2. `Battles Played Distribution` when eligible

Why first:

- it is the best default explanatory view after the summary cards
- the data is mostly player-agnostic and cache-friendly
- it is lighter than the ship-detail and ranked-detail lanes

### 2. Ships

Purpose:

- show the player’s recent random-battle ship mix and strongest visible ship lanes

Panels:

1. `Top Ships (Random Battles)`

Why isolated:

- this is one of the heavier chart surfaces
- it has its own filtering UI and a large SVG footprint
- it should not compete with ranked or correlation panels during initial route stabilization

### 3. Ranked

Purpose:

- show ranked participation and results without scattering ranked fetches across multiple independent sections

Panels:

1. `Ranked Games vs Win Rate`
2. `Ranked Seasons`

Behavior:

1. if the heatmap determines that no ranked history exists, keep the tab but show a compact empty state
2. the ranked tab should not trigger any polling or retries until it is the active tab

### 4. Profile

Purpose:

- show composition and playstyle structure

Panels:

1. `Tier vs Type Profile`
2. `Performance by Ship Type`

Why grouped:

- both panels describe ship mix and class usage
- both are below-the-fold today and should remain a lower-priority lane

## Explicit Non-Goals

This tranche should not:

1. move the header, summary cards, or summary metadata block into tabs
2. move the left-column clan, clan-member, clan-battle, efficiency-badge, or tier sections
3. redesign the chart art direction or rewrite D3 implementations
4. change public API payload shapes unless a later implementation tranche proves that necessary

## UX And Layout Requirements

### Default state

1. keep the current summary cards and summary metadata visible above the tabs
2. original tranche proposal: default the `Insights` surface to the `Population` tab
3. current shipped implementation defaults the visible tab surface to `Profile`
4. render the tab chrome immediately with a reserved content shell height so the page layout stabilizes before any heavy panel mounts

### Tab behavior

1. keyboard accessible tablist with clear active state
2. user tab choice persists while staying on the current player route
3. optional query-param support such as `?panel=ranked` is acceptable, but not required for the first implementation tranche

### Height strategy

The tabbed shell must reserve a stable minimum height per active tab family to reduce CLS.

Recommended initial minimums:

1. `Population`: about the current `WRDistribution` footprint plus optional distribution chart
2. `Ships`: about the current `RandomsSVG` footprint
3. `Ranked`: about the combined ranked heatmap plus ranked seasons table footprint
4. `Profile`: about the tier-type heatmap footprint plus type chart

The shell should grow only when the active tab genuinely requires more space, not because multiple deferred siblings are mounting one after another.

## Data Strategy

The layout change only pays off if the data strategy also stops hidden fan-out.

### Principle

Only fetch data for the active tab.

The current stack defers by viewport proximity. The redesigned surface should defer by explicit user intent.

### Lane A: shared population datasets

Applies to:

1. `GET /api/fetch/player_correlation/win_rate_survival/`
2. `GET /api/fetch/player_distribution/battles_played/`

Policy:

1. fetch only when the `Population` tab first becomes active
2. treat these as shared route-wide datasets, not per-panel one-offs
3. reuse `fetchSharedJson(...)` with a longer settled TTL than the current `1500 ms` route TTL
4. keep them hot across tab revisits and across nearby player navigations when practical

Reasoning:

- these endpoints are stable population references
- their data does not need revalidation every time the user flips tabs or opens another player shortly afterward
- the existing doctrine favors stale-but-fast reads over repeated refreshes

Recommended direction:

1. introduce a longer-lived panel TTL for shared population datasets, for example by using the existing panel-fetch lane instead of the route-fetch lane
2. keep the server contract cache-first and background-refresh only

### Lane B: player-scoped heavy detail datasets

Applies to:

1. `GET /api/fetch/randoms_data/<player_id>/?all=true`
2. `GET /api/fetch/player_correlation/tier_type/<player_id>/`
3. `GET /api/fetch/type_data/<player_id>/`

Policy:

1. do not request these on initial player-route paint
2. request them on first activation of the owning tab only
3. if the user revisits the tab during the same route session, reuse cached data and avoid a second fetch
4. preserve the current cache-first server behavior: if durable derived data exists, serve it even if stale, and refresh in the background only after source data changes

Implementation preference:

1. each tab owns a lightweight controller that records `not-requested`, `loading`, `loaded`, `error`
2. tab switches should not discard fetched payload state for the current player
3. inactive tabs should unmount their heavy SVG DOM even if their data remains cached in state

### Lane C: ranked datasets with pending hydration

Applies to:

1. `GET /api/fetch/player_correlation/ranked_wr_battles/<player_id>/`
2. `GET /api/fetch/ranked_data/<player_id>/`

Policy:

1. ranked fetches and any pending-refresh retries must start only when the `Ranked` tab becomes active
2. if the user leaves the `Ranked` tab, polling and retry timers should stop
3. if a last-known ranked payload exists, keep rendering it while a pending refresh message is shown
4. if the heatmap proves there is no ranked history, the tab should stay cheap and settle quickly into a no-data state

Reasoning:

- ranked data has historically been one of the more failure-prone and latency-sensitive lanes
- there is no reason to spend ranked budget on users who never open the ranked tab

### Lane D: bounded background warmup

Shipped follow-up behavior:

1. after the player shell finishes loading, the client schedules an idle-time background warmup for inactive tab datasets
2. the warmup covers data only; it does not mount hidden tab content or instantiate inactive D3 DOM trees
3. the warmup reuses `fetchSharedJson(...)` and the longer panel TTL so the first explicit tab activation can read from the settled client cache when possible
4. clan-specific warmup remains conditional on clan context and does not request `player_clan_battle_seasons` for clanless players

Current warmup coverage:

1. `randoms_data/<player_id>`
2. `player_correlation/ranked_wr_battles/<player_id>`
3. `ranked_data/<player_id>`
4. `player_correlation/tier_type/<player_id>`
5. `type_data/<player_id>`
6. `tier_data/<player_id>`
7. `player_clan_battle_seasons/<player_id>` only when the player has a clan

Guardrails:

1. warmup starts only after the route-level `isLoading` state clears
2. warmup uses the same bounded idle/timeout scheduling pattern already used for delayed clan-member loading on `PlayerDetail`
3. ranked retry behavior still stays local to the `Ranked` tab when the user opens it; warmup only seeds the first cached read

This keeps the solution aligned with the doctrine rule against hidden heavy mounts while still reducing perceived latency on first tab switch.

## Rendering Strategy

### New surface

Add a dedicated right-column controller component, for example `PlayerDetailInsightsTabs`, responsible for:

1. tab chrome
2. active-tab state
3. load-state bookkeeping per tab
4. stable placeholder heights
5. instrumentation for tab activation and panel render timing

### Mounting contract

1. mount only the active tab panel
2. do not wrap every child panel in independent near-fold `DeferredSection` blocks inside the tabbed shell
3. the tab surface itself becomes the defer boundary
4. if a tab contains two internal charts, they may still render sequentially inside the tab after the tab is active, but they should not be competing with three other top-level tabs for mount time

### Warmup contract

1. background warmup must never change the visible active tab
2. background warmup must never render duplicated headings or panels into the DOM
3. failures during warmup should be swallowed and left to the normal active-tab request path to surface locally if the user later opens that tab

### Empty and error states

1. each tab must have a compact empty state that does not collapse the shell height abruptly
2. error states should stay local to the tab and not affect sibling tabs
3. hidden players keep the existing top-level hidden-profile short-circuit and should never instantiate the tab surface

## API And Contract Guidance

The preferred implementation path is additive and client-led.

### Keep existing endpoints first

Initial implementation should continue using the current endpoints:

1. `win_rate_survival`
2. `player_distribution/battles_played`
3. `randoms_data`
4. `ranked_wr_battles`
5. `ranked_data`
6. `player_correlation/tier_type`
7. `type_data`

This keeps the change reversible and avoids payload drift during the UI restructuring tranche.

### Optional follow-up API improvement

If later profiling shows that the tabbed shell still spends too much time on per-panel HTTP overhead, a follow-up tranche may introduce grouped panel payloads such as:

1. a `player_insights/population/<player_id>/` response for the two population panels
2. a `player_insights/ranked/<player_id>/` response for ranked summary plus heatmap eligibility metadata
3. a `player_insights/profile/<player_id>/` response for tier-type and ship-type data

That should be treated as a separate contract review, not bundled into the first layout pass.

## Validation

The current shipped test bar for this runbook is:

1. `PlayerDetailInsightsTabs.test.tsx` verifies the default active lane, tab switching, ranked empty state, same-player tab persistence, and idle-scheduled tab-data warmup after `isLoading` clears
2. `PlayerDetail.test.tsx` verifies the player page still renders the clan chart and that the tab shell integration remains intact
3. `RankedSeasons.test.tsx` verifies ranked data still honors the ranked pending-refresh flow while using the panel TTL-backed shared fetch path
4. warmup coverage must prove that clanless players do not prefetch the clan-battle endpoint

Current implementation note:

1. the default active lane assertion now reflects the shipped `Profile` default rather than the earlier `Population` proposal

## Rollout Plan

### Tranche 1

1. add the tabbed shell
2. move the existing right-column heavy sections into the four tabs
3. fetch only the active tab
4. keep existing endpoints and visualizations
5. add route diagnostics for tab activation and tab-panel render timing

### Tranche 2

1. review whether `Population` should prefetch `Ships` on idle
2. review whether ranked retries should become visibility-aware and tab-scoped only
3. review whether panel TTLs should be widened for shared population data

### Tranche 3 if needed

1. only if HTTP overhead remains material, consider grouped API payloads
2. only after validating that the tabbed UI itself delivered the expected win

## Validation Plan

### Browser-level success criteria

On initial player-route load:

1. no requests should fire for inactive right-column tabs
2. initial rendered DOM should be smaller than the current stacked version on heavy pages
3. LCP and CLS tail should improve on the same heavy-player sample used in prior route audits

### Request-shape success criteria

For a player that lands on the default tab:

1. allow the player detail request
2. allow the default active-tab requests only
3. do not allow `randoms_data`, `ranked_data`, `ranked_wr_battles`, `tier_type`, or `type_data` unless their tab becomes active

### Interaction success criteria

When switching tabs:

1. first activation may fetch and show a reserved-height loading state
2. second activation in the same route session should reuse cached data and avoid a new request unless an explicit refresh policy says otherwise
3. leaving a tab should cancel or stop retry timers for that tab when safe to do so

## Risks And Mitigations

### Risk: reduced discoverability

Users may miss panels that are no longer visible in a long page.

Mitigation:

1. use explicit tab labels with concise descriptions
2. show compact badges such as `No ranked data` or `1 chart` only when useful
3. keep the default tab set to the broadest explanatory view

### Risk: tab revisits feel slower than scrolling

If tabs always remount cold, the UI may feel worse despite lower initial load.

Mitigation:

1. preserve fetched payload state per visited tab
2. unmount only the heavy DOM, not the cached tab data
3. consider one bounded idle prefetch after the initial tab settles

### Risk: ranked polling continues to waste work in the background

Mitigation:

1. make ranked retries active-tab-scoped
2. stop retry timers on tab exit
3. keep the last settled data visible instead of refetching aggressively

## Rollback

Rollback should be simple:

1. restore the existing stacked `DeferredSection` layout in `PlayerDetail`
2. retain any harmless fetch-dedupe improvements that are independently beneficial
3. remove tab-specific diagnostics if they no longer apply

Because this spec preserves existing endpoints and chart components, rollback is a layout-controller reversal rather than a contract migration.

## Recommended Acceptance Slice

The smallest safe slice is:

1. build the tab shell
2. move `Population`, `Ships`, `Ranked`, and `Profile` panels into it
3. gate each tab’s fetches behind activation
4. widen shared population-panel caching beyond the current route TTL
5. leave server contracts and D3 internals unchanged

That is the right bar for the first implementation tranche.
