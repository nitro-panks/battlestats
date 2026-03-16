# Runbook: Client Test Hardening

_Last updated: 2026-03-16_

## Purpose

Capture the current client-side test baseline, the highest-risk uncovered areas, and the next recommended steps to make the Next.js frontend reliable enough for CI/CD and production deployment.

## Current Context

The client recently changed in several risk-heavy areas:

- route-based navigation for players and clans,
- header-search route/query synchronization,
- new route loader components for player and clan detail pages,
- player-detail layout reshuffling,
- shareable player and clan detail headers,
- responsive resizing for tier and ship-type charts,
- compacted `Efficiency Badges` presentation and sorting behavior,
- hidden-account icon treatment across discovery surfaces,
- clan chart redraw suppression for icon-only hydration updates,
- stricter reliance on browser-side fetch handling for detail routes.

One production-facing regression already surfaced during this tranche:

- `ClanRouteView` initially called `/api/clans/<id>/` and failed with `404`.
- the actual DRF route is `/api/clan/<id>/`.
- a regression test was added so this route mismatch is now covered.

## Current Test Baseline

The client now has a minimal Jest + React Testing Library harness.

### Test entry points

- `cd client && npm test -- --runInBand`
- `cd client && npm run test:ci`

### Current covered files

- `client/app/components/__tests__/ClanRouteView.test.tsx`
- `client/app/components/__tests__/HeaderSearch.test.tsx`
- `client/app/components/__tests__/PlayerRouteView.test.tsx`
- `client/app/components/__tests__/PlayerEfficiencyBadges.test.tsx`
- `client/app/components/__tests__/clanChartActivity.test.ts`
- `client/app/lib/__tests__/entityRoutes.test.ts`

### Current covered behaviors

1. `ClanRouteView`
   - loads clan detail from the correct singular API endpoint,
   - rejects invalid clan slugs without fetching.
2. `HeaderSearch`
   - keeps the search box empty on routed player detail pages,
   - still reflects an active `q` query,
   - routes to the selected player on submit.
3. `PlayerRouteView`
   - loads player detail from the routed player API,
   - wires `onBack`, member navigation, and clan navigation correctly,
   - shows `Player not found.` on failed fetch.
4. `PlayerEfficiencyBadges`
   - renders empty state,
   - renders header totals,
   - renders compact class and tier summaries,
   - preserves compact ship metadata labels,
   - sorts by ship name.
5. `clanChartActivity`
   - ignores icon-only async member updates,
   - changes signature when chart-relevant activity changes.
6. `entityRoutes`
   - encodes player routes,
   - slugifies clan routes,
   - parses clan IDs from route segments.

### Current validation result

Validated on `2026-03-16`:

- `npm test -- --runInBand`: `6` suites passed, `15` tests passed.
- `npm run test:ci`: passed with coverage enabled.

## Known Gaps

Coverage is still intentionally narrow. The current suite protects the newest routing and badge logic, but the overall frontend is not yet broadly protected.

The biggest remaining risk areas are:

1. `HeaderSearch.tsx`
   - drives route entry,
   - syncs query state with query params and user input transitions,
   - can silently break navigation flows.
2. `PlayerSearch.tsx`
   - remains the main landing-page orchestration surface,
   - owns fetch and transition behavior across player and clan discovery,
   - has high branching complexity.
3. `PlayerClanBattleSeasons.tsx`
   - now has a viewport-sized scroll region,
   - performs fetch/error handling that should be protected.
4. `ClanBattleSeasons.tsx`
   - fetches season rows with pending-refresh logic,
   - should be covered for success, empty, and error states.
5. `useClanMembers.ts`
   - polls and handles hydration-related state,
   - is prone to race and retry bugs.
6. `PlayerDetail.tsx`
   - assembles many sections and conditional branches,
   - would benefit from smaller extracted helpers before deeper testing.
7. D3-heavy chart components
   - currently have effectively no automated coverage,
   - now have a first pure-helper regression around redraw signatures,
   - should continue to be tested through extracted pure helpers before trying to snapshot full SVG output.

## Recommended Next Tranche

If this work is going into CI/CD soon, the next pass should prioritize reliability over visual breadth.

### Tier 1: navigation and route safety

Add tests for:

- `HeaderSearch.tsx`
  - submit behavior,
  - route sync from pathname/query params,
  - suggestion navigation and highlighted selection behavior.
- `PlayerSearch.tsx`
  - player search success/failure,
  - clan selection pushes route,
  - member selection pushes route,
  - landing-state fallback rendering.

### Tier 2: fetch-driven table surfaces

Add tests for:

- `PlayerClanBattleSeasons.tsx`
  - loading,
  - empty state,
  - error state,
  - summary-card rendering,
  - fixed five-row viewport styling.
- `ClanBattleSeasons.tsx`
  - loading,
  - empty state,
  - error state,
  - pending refresh / retry behavior when applicable.

### Tier 3: extract testable logic from large view files

Before trying to deeply test `PlayerDetail.tsx` or the chart components, extract pure functions for:

- conditional section ordering,
- compact chart sizing decisions,
- summary label formatting,
- route and slug normalization,
- badge aggregation and sorting helpers.

This is the lowest-friction way to increase protection without writing brittle DOM tests around D3 output.

## CI Recommendations

### Immediate

Use this command in client CI:

```bash
cd client && npm run test:ci
```

Keep it serialized with `--runInBand` for now. The suite is small and deterministic, and this avoids false negatives from shared mocks or environment reuse.

### Short term

Once the next tranche lands, add a coverage gate for the specifically protected folders rather than the whole client at once.

Recommended initial focus:

- `app/lib`
- route loader components
- search/navigation components
- fetch-driven table components

Do not apply a whole-repo frontend coverage threshold yet. It will either fail immediately or incentivize low-value tests around chart wrappers.

## Operational Notes

### Files currently involved in the harness

- `client/package.json`
- `client/jest.config.js`
- `client/jest.setup.ts`

### Current testing dependencies

- `jest`
- `jest-environment-jsdom`
- `@testing-library/react`
- `@testing-library/jest-dom`

### Testing style used so far

- mock router behavior instead of mounting full Next routing,
- mock child detail components when the test target is the route loader,
- assert fetch contract correctness directly,
- avoid snapshot testing,
- prefer behavior assertions over implementation details.

## Completion Criteria For The Next Pass

- `HeaderSearch.tsx` is covered.
- `PlayerSearch.tsx` is covered for core route transitions.
- `PlayerClanBattleSeasons.tsx` and `ClanBattleSeasons.tsx` are covered for success, empty, and error paths.
- CI runs `npm run test:ci` successfully without local-only assumptions.
- no route-critical component remains completely untested.

## Summary

The client has moved from effectively no frontend tests to a small but meaningful regression layer around route safety and the newest player-detail badge UI. That is enough to catch the known clan-route regression class, but not enough to call the client production-hardened.

The next useful work is not “more tests everywhere.” It is targeted coverage of:

1. route entry points,
2. search orchestration,
3. fetch-driven table components,
4. extracted pure logic from large detail and chart components.
