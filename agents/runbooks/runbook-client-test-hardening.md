# Runbook: Client Test Hardening

_Last updated: 2026-04-01_

_Status: Active maintenance reference_

## Purpose

Capture the current client-side regression coverage, the highest-risk uncovered areas, and the focused validation commands that protect the Next.js frontend from route and UI regressions.

## Current Test Baseline

The client currently uses two test lanes:

- Jest + React Testing Library for component and route-loader regressions.
- Playwright for browser-level route smoke tests.

### Test entry points

- `cd client && npm test -- --runInBand`
- `cd client && npm run test:ci`
- `cd client && npm run test:e2e`

## Current Covered Files

- `client/app/components/__tests__/ClanSVG.test.tsx`
- `client/app/components/__tests__/ClanRouteView.test.tsx`
- `client/app/components/__tests__/Footer.test.tsx`
- `client/app/components/__tests__/HeaderSearch.test.tsx`
- `client/app/components/__tests__/LandingDropdowns.test.tsx`
- `client/app/components/__tests__/PlayerClanBattleSeasons.test.tsx`
- `client/app/components/__tests__/PlayerDetail.test.tsx`
- `client/app/components/__tests__/PlayerDetailInsightsTabs.test.tsx`
- `client/app/components/__tests__/PlayerEfficiencyBadges.test.tsx`
- `client/app/components/__tests__/PlayerRouteView.test.tsx`
- `client/app/components/__tests__/PlayerRouteViewWarmup.test.tsx`
- `client/app/components/__tests__/RankedSeasons.test.tsx`
- `client/app/components/__tests__/TierSVG.test.tsx`
- `client/app/components/__tests__/clanChartActivity.test.ts`
- `client/app/lib/__tests__/entityRoutes.test.ts`
- `client/e2e/clan-route-clan-chart-pending.spec.ts`
- `client/e2e/player-detail-tabs.spec.ts`
- `client/e2e/player-route-warmup.spec.ts`
- `client/e2e/ranked-heatmap-performance.spec.ts`

## Current Covered Behaviors

1. `ClanRouteView`
   - loads clan detail from the correct singular API endpoint,
   - rejects invalid clan slugs without fetching.
2. `HeaderSearch`
   - keeps the search box empty on routed player detail pages,
   - reflects active `q` query state,
   - routes to the selected player on submit.
3. `LandingDropdowns`
   - keeps inactive theme-menu options readable via theme-aware text color,
   - keeps inactive realm-menu options readable via theme-aware text color,
   - protects against hardcoded low-opacity gray values in landing header controls.
4. `PlayerRouteView`
   - loads player detail from the routed player API,
   - wires back/member/clan navigation correctly,
   - shows `Player not found.` on failed fetch.
5. `PlayerRouteViewWarmup`
   - delays inactive-tab warmup until the routed player payload has mounted.
6. `PlayerDetail` and `PlayerDetailInsightsTabs`
   - preserve the current tab shell,
   - keep one active lane at a time,
   - avoid clan-battle warmup on clanless players.
7. `PlayerClanBattleSeasons`
   - covers empty state, summary cards, and callback updates.
8. `PlayerEfficiencyBadges`
   - covers empty state, totals, compact summaries, and ship sorting.
9. `RankedSeasons`, `TierSVG`, `ClanSVG`, and `clanChartActivity`
   - protect panel fetch flow, clan pending/retry handling, and chart redraw gating.
10. `entityRoutes`
   - covers player-route encoding, clan-route slugging, and clan-id parsing.
11. Playwright smoke specs
   - cover routed player warmup timing,
   - clan chart pending/retry browser behavior,
   - player-detail tab interaction,
   - ranked heatmap render diagnostics.

## 2026-04-01 Dropdown Follow-Up

### Production symptom

The landing-page theme and realm dropdowns rendered inactive options with a hardcoded low-opacity gray. In practice, the non-selected menu item looked too dark, especially in dark theme, and appeared nearly unselectable.

### Implemented fix

- `client/app/components/ThemeToggle.tsx`
  - replaced the hardcoded inactive option color with `var(--text-secondary)`.
  - added a visible active-row background using `var(--accent-faint)`.
- `client/app/components/RealmSelector.tsx`
  - applied the same inactive/active treatment for visual consistency.
- `client/app/components/__tests__/LandingDropdowns.test.tsx`
  - added focused regression coverage for both menus.

### Validation

```bash
cd client && npm test -- --runInBand app/components/__tests__/LandingDropdowns.test.tsx
```

Result on 2026-04-01: `PASS` (`2` tests, `1` suite).

## Known Gaps

The highest-value uncovered client areas are still:

1. `PlayerSearch.tsx`
   - landing orchestration, transitions, and result handling remain high-branching.
2. `ClanBattleSeasons.tsx`
   - still needs deeper loading/error/pending coverage.
3. search-to-route browser flow
   - still lacks a full landing-search browser smoke.
4. additional D3-heavy chart components
   - remain lightly protected outside the most route-critical surfaces.

## CI Recommendations

Use these commands in client CI:

```bash
cd client && npm run test:ci
cd client && npm run test:e2e
```

Keep Jest serialized with `--runInBand` for now. The suite is still small, and this avoids low-value flake from shared mocks or environment reuse.

## Summary

The client has a meaningful regression layer around route safety, warmup behavior, pending/retry surfaces, and a small set of header/UI controls. Keep this runbook additive: when a new frontend regression test lands, update the covered-files list and the focused validation note in the same change.
