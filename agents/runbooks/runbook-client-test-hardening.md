# Runbook: Client Test Hardening

_Last updated: 2026-04-02_

_Status: Active maintenance reference_

## Purpose

Capture the current client-side regression coverage, the highest-risk uncovered areas, and the focused validation commands that protect the Next.js frontend from route and UI regressions.

## Current Test Baseline

The client now uses a single lean Jest release gate.

The goal is end-of-cycle speed, not broad historical coverage. We keep only the tests that protect the current routed site experience and retire the rest from the active path.

### Test entry points

- `cd client && npm test`
- `cd client && npm run test:ci`

## Current Covered Files

- `client/app/lib/__tests__/entityRoutes.test.ts`
- `client/app/lib/__tests__/visitAnalytics.test.ts`
- `client/app/components/__tests__/PlayerSearch.test.tsx`
- `client/app/components/__tests__/PlayerRouteViewWarmup.test.tsx`
- `client/app/components/__tests__/PlayerDetail.test.tsx`
- `client/app/components/__tests__/PlayerDetailInsightsTabs.test.tsx`
- `client/app/components/__tests__/ClanRouteView.test.tsx`
- `client/app/components/__tests__/ClanDetail.test.tsx`

## Current Covered Behaviors

1. `entityRoutes`
   - covers player-route encoding, clan-route slugging, and clan-id parsing.
2. `visitAnalytics`
   - protects the first-party entity view emission path.
3. `PlayerSearch`
   - protects landing search behavior and the current best-clan fallback behavior.
4. `PlayerRouteViewWarmup`
   - delays inactive-tab warmup until the routed player payload has mounted.
5. `PlayerDetail` and `PlayerDetailInsightsTabs`
   - preserve the current tab shell,
   - keep one active lane at a time,
   - avoid clan-battle warmup on clanless players.
6. `ClanRouteView`
   - loads clan detail from the correct singular API endpoint,
   - rejects invalid clan slugs without fetching.
7. `ClanDetail`
   - protects the current clan detail rendering contract and clan-member integration.

## Known Gaps

The deliberately accepted gaps are:

1. D3-heavy visual components outside the routed player and clan shells.
2. broader landing-page presentation coverage beyond search/fallback behavior.
3. historical benchmark and browser-smoke coverage.

Those tests were retired on purpose to keep CI fast near the end of the delivery cycle.

## CI Recommendations

Use these commands in client CI:

```bash
cd client && npm run test:ci
cd client && npm run build
```

Keep Jest serialized with `--runInBand`. The active suite is intentionally small and favors determinism over parallelism.

## Summary

The client release gate is now a small routed-site smoke layer, not a general-purpose frontend test harness. Add a new frontend test only when it protects a release-critical contract that is currently missing from this curated set.
