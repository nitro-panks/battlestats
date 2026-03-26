# Runbook: Client Test Hardening

_Last updated: 2026-03-26_

_Status: Active maintenance reference_

## Purpose

Capture the current client-side test baseline, the highest-risk uncovered areas, and the next recommended steps to make the Next.js frontend reliable enough for CI/CD and production deployment.

## Current Context

The client recently changed in several risk-heavy areas:

- route-based navigation for players and clans,
- header-search route/query synchronization,
- new route loader components for player and clan detail pages,
- player-detail layout reshuffling,
- player-detail insights-tab orchestration and idle warmup,
- shareable player and clan detail headers,
- responsive resizing for tier and ship-type charts,
- compacted `Efficiency Badges` presentation and sorting behavior,
- hidden-account icon treatment across discovery surfaces,
- clan chart redraw suppression for icon-only hydration updates,
- clan plot pending/retry behavior on routed clan pages,
- stricter reliance on browser-side fetch handling for detail routes.

One production-facing regression already surfaced during this tranche:

- `ClanRouteView` initially called `/api/clans/<id>/` and failed with `404`.
- the actual DRF route is `/api/clan/<id>/`.
- a regression test was added so this route mismatch is now covered.

## Current Test Baseline

The client now has two committed test lanes:

- Jest + React Testing Library for component and route-loader regressions.
- Playwright for browser-level route smoke tests.

### Test entry points

- `cd client && npm test -- --runInBand`
- `cd client && npm run test:ci`
- `cd client && npm run test:e2e:install`
- `cd client && npm run test:e2e:install:deps`
- `cd client && npm run test:e2e`

### Playwright metadata

The committed Playwright lane is currently configured as a browser-smoke layer for route-critical client behavior.

- config file: `client/playwright.config.ts`
- package: `@playwright/test`
- browser project: Chromium only
- base URL: `http://127.0.0.1:3100`
- web server command: `npm run dev -- --hostname 127.0.0.1 --port 3100`
- local reuse policy: reuse an existing Next dev server when available outside CI
- artifact output: `client/test-results/playwright/`
- failure artifacts: trace, video, and screenshot retention on failure

Operational implication:

- these specs validate browser behavior against the Next app with mocked `/api/...` traffic
- they do not require Django to be running for the default smoke lane
- they should be read as deterministic route-contract coverage, not full backend-integrated E2E tests

### Current covered files

- `client/app/components/__tests__/ClanSVG.test.tsx`
- `client/app/components/__tests__/ClanRouteView.test.tsx`
- `client/app/components/__tests__/HeaderSearch.test.tsx`
- `client/app/components/__tests__/PlayerClanBattleSeasons.test.tsx`
- `client/app/components/__tests__/PlayerRouteView.test.tsx`
- `client/app/components/__tests__/PlayerRouteViewWarmup.test.tsx`
- `client/app/components/__tests__/PlayerDetail.test.tsx`
- `client/app/components/__tests__/PlayerDetailInsightsTabs.test.tsx`
- `client/app/components/__tests__/PlayerEfficiencyBadges.test.tsx`
- `client/app/components/__tests__/RankedSeasons.test.tsx`
- `client/app/components/__tests__/TierSVG.test.tsx`
- `client/app/components/__tests__/clanChartActivity.test.ts`
- `client/app/lib/__tests__/entityRoutes.test.ts`
- `client/e2e/clan-route-clan-chart-pending.spec.ts`
- `client/e2e/player-route-warmup.spec.ts`
- `client/e2e/player-detail-tabs.spec.ts`
- `client/e2e/ranked-heatmap-performance.spec.ts`

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
4. `PlayerRouteViewWarmup`
   - proves inactive insights warmup does not start while the routed player payload is still loading,
   - proves the warmup requests begin only after the detail view mounts.
5. `PlayerDetail`
   - keeps the clan chart on the player page when clan context exists,
   - verifies the focused tab surface replaced the older always-visible secondary sections,
   - keeps the default visible insight lane aligned with the current tab shell.
6. `PlayerDetailInsightsTabs`
   - proves only one insight lane is active at a time,
   - proves idle warmup waits until the player shell is loaded,
   - proves clanless routes skip clan-battle warmup,
   - proves ranked empty-state and tab switching behavior.
7. `PlayerClanBattleSeasons`
   - covers empty state,
   - covers summary-card rendering,
   - covers callback summary updates.
8. `PlayerEfficiencyBadges`
   - renders empty state,
   - renders header totals,
   - renders compact class and tier summaries,
   - preserves compact ship metadata labels,
   - sorts by ship name.
9. `RankedSeasons`
   - keeps the ranked pending-refresh lane on the panel TTL-backed fetch path.
10. `TierSVG`

- requests tier data for the active player through the panel fetch TTL path.

11. `ClanSVG`

- retries short-lived clan-plot failures,
- preserves loading UI while `X-Clan-Plot-Pending` is set,
- only surfaces the chart error after retry exhaustion.

12. `clanChartActivity`

- ignores icon-only async member updates,
- changes signature when chart-relevant activity changes.

13. `entityRoutes`

- encodes player routes,
- slugifies clan routes,
- parses clan IDs from route segments.

14. `player-route-warmup.spec.ts`

- exercises the routed player detail page in a real Chromium browser,
- intercepts the `/api/...` surface in-browser,
- proves warmup waits for the primary route payload before issuing inactive-tab data requests.

15. `clan-route-clan-chart-pending.spec.ts`

- exercises the routed clan detail page in a real Chromium browser,
- intercepts the `/api/fetch/clan_data/...` surface in-browser,
- proves `X-Clan-Plot-Pending` keeps the chart in loading state until a non-pending payload arrives.

16. `player-detail-tabs.spec.ts`

- exercises the player-detail insights tabs in a real Chromium browser,
- mocks the route and panel `/api/...` traffic in-browser,
- proves the major tabs can be opened without surfacing chart or table failure states.

17. `ranked-heatmap-performance.spec.ts`

- exercises the ranked tab with a dense mocked compact heatmap payload,
- records bounded browser timing for request start, response completion, and SVG draw completion,
- serves as a diagnostic smoke for the ranked heatmap render path rather than a strict pass/fail performance benchmark.

18. `player-route-cold-performance-live.spec.ts`

- exercises 10 real player routes through the Next.js client against a live backend,
- isolates the routed player shell from secondary panel traffic,
- stores timestamped cold-route timing JSON for trend comparison under `logs/benchmarks/client/`.

19. `profile-chart-performance-live.spec.ts`

- exercises 10 real player profile tabs against a live backend,
- verifies the reworked profile tab uses exactly one `player_correlation/tier_type` request and no `type_data` or `tier_data` requests,
- stores timestamped chart timing JSON for trend comparison under `logs/benchmarks/client/`.

### Current validation result

Validated on `2026-03-25`:

- focused Jest detail-page and route-loader coverage commands passed during the 2026-03-25 tranche review.
- focused backend cache/scheduler regression command: passed.
- Playwright browser smoke lanes: added and runnable through `npm run test:e2e`.

### Playwright conventions worth preserving

- mock `/api/...` routes with `page.route(...)` inside each spec so failures stay local and deterministic
- use explicit fixture payloads and custom response headers to drive pending, retry, and warmup behavior
- keep the browser lane focused on a small number of route-critical contracts instead of trying to browser-test every D3 component
- treat `ranked-heatmap-performance.spec.ts` as a measurement aid; if timing thresholds become flaky across hosts, prefer logging and manual comparison over brittle hard caps
- store live benchmark output in both `client/test-results/playwright/benchmarks/` and `logs/benchmarks/client/` so individual runs and trend lines can be compared later

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
3. `ClanBattleSeasons.tsx`
   - fetches season rows with pending-refresh logic,
   - should be covered for success, empty, and error states.
4. `useClanMembers.ts`
   - polls and handles hydration-related state,
   - is prone to race and retry bugs.
5. search-to-route browser flow
   - still lacks a browser smoke that proves the full landing search interaction,
   - remains a likely place for regressions in rewrites or router state.
6. additional D3-heavy chart components
   - currently have effectively no automated coverage,
   - now have narrow coverage around clan-chart retry/loading and tier fetch routing,
   - should continue to be tested through extracted pure helpers before trying to snapshot full SVG output.

## Recommended Next Tranche

If this work is going into CI/CD soon, the next pass should prioritize reliability over visual breadth.

### Tier 1: route and browser safety

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

Expand Playwright coverage for:

- a player-route clanless warmup smoke test,
- one search-to-player navigation smoke covering the full browser path.

### Tier 2: fetch-driven table surfaces

Add tests for:

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

Use these commands in client CI:

```bash
cd client && npm run test:ci
cd client && npm run test:e2e
```

Keep it serialized with `--runInBand` for now. The suite is small and deterministic, and this avoids false negatives from shared mocks or environment reuse.

The Playwright lane is currently a browser smoke layer, not a full E2E suite against a live backend. It starts the Next dev server locally and intercepts the `/api/...` surface inside Chromium so route behavior and client-side scheduling can be validated without requiring Django for each browser run.

The current browser smoke set covers both routed player warmup timing and routed clan chart pending/retry behavior.

Use [client/README.md](client/README.md) as the operator-facing command reference and keep this runbook focused on coverage posture, risk, and next-step guidance.

On Linux developer hosts, prefer `npm run test:e2e:install:deps` for first-time setup if the browser runner reports missing shared libraries.

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
- `@playwright/test`

### Testing style used so far

- mock router behavior instead of mounting full Next routing,
- mock child detail components when the test target is the route loader,
- assert fetch contract correctness directly,
- avoid snapshot testing,
- prefer behavior assertions over implementation details.

For Playwright specifically:

- use real browser navigation against a local Next dev server,
- intercept `/api/...` calls in-browser instead of requiring a live backend for every smoke test,
- keep smoke tests narrow and route-critical rather than trying to browser-test every D3 panel.

## Completion Criteria For The Next Pass

- `HeaderSearch.tsx` is covered.
- `PlayerSearch.tsx` is covered for core route transitions.
- `ClanBattleSeasons.tsx` is covered for success, empty, and error paths.
- CI runs `npm run test:ci` successfully without local-only assumptions.
- no route-critical component remains completely untested.

## Summary

The client has moved from effectively no frontend tests to a meaningful regression layer around route safety, player-detail tab orchestration, and clan-chart pending behavior. That is enough to catch the known clan-route regression classes, but not enough to call the client production-hardened.

This runbook should stay additive and current. If a future tranche materially changes the protected client surface, update this file in the same change instead of leaving counts, commands, or covered files to drift.

The next useful work is not “more tests everywhere.” It is targeted coverage of:

1. route entry points,
2. search orchestration,
3. fetch-driven table components,
4. extracted pure logic from large detail and chart components.
