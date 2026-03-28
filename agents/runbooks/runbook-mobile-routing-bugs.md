# Runbook: Mobile Routing Bugs Investigation

**Created**: 2026-03-28
**Status**: Investigated — fixes pending

## Summary

On iPhone (mobile Safari), tapping player names and links frequently routes to clan pages or fails to navigate entirely. Playwright E2E tests with mobile emulation (iPhone 17 Pro viewport, touch events enabled) reproduced multiple bugs.

## Findings

### Bug 1: Player detail — tapping clan name does NOT navigate (Critical)

**Reproduction**: Navigate to `/player/lil_boots`, tap the `[-N-] Naumachia` clan name link.

**Expected**: Navigate to `/clan/<clanId>-naumachia`
**Actual**: Nothing happens. URL stays at `/player/lil_boots`.

**Root cause**: The clan name button at `PlayerDetail.tsx:335-342` uses `onClick` without explicit touch event handling. On mobile touch devices, the `onClick` from the `button` element intermittently fails to fire after a tap. This is likely exacerbated by the `overflow-hidden` on the parent container (`PlayerDetail.tsx:322`) which can interfere with touch event propagation in iOS Safari.

**Playwright evidence**:
```
Tapping clan name button: "Open clan page for Naumachia"
Navigated to: https://battlestats.online/player/lil_boots   ← stayed on same page
```

**Code location**: `client/app/components/PlayerDetail.tsx:335-342`
```tsx
<button
    type="button"
    onClick={() => onSelectClan(player.clan_id, player.clan_name || "Clan")}
    className="mt-1 text-xl font-semibold ..."
    aria-label={`Open clan page for ${player.clan_name || "clan"}`}
>
```

### Bug 2: Landing page — D3 SVG clan chart circles visually ambiguous (Moderate)

**Reproduction**: Load landing page on mobile, tap any circle in the top scatter plot.

**Expected**: User intends to navigate to a player page.
**Actual**: All circles in the top chart are clan data — navigates to a clan page.

**Root cause**: The landing page renders two nearly identical scatter plots stacked vertically:
1. `LandingClanSVG` (top) — `PlayerSearch.tsx:462-467`, routes to `/clan/`
2. `LandingPlayerSVG` (bottom) — `PlayerSearch.tsx:523-527`, routes to `/player/`

Both charts use the same visual style (colored circles, WR on Y axis, battles on X axis). On a narrow mobile viewport where scrolling is required, users reasonably mistake the clan chart for the player chart. The section labels ("Active Clans" / "Active Players") are below the charts, not above them.

**Playwright evidence**:
```
First circle bounds: {"x":93.79,"y":453.75,"width":11.25,"height":11.25}
Circle tap navigated to: https://battlestats.online/clan/1000060069-friday-night-fights
```

### Bug 3: D3 SVG circles block each other on touch (Moderate)

**Reproduction**: Attempt to tap a densely-packed SVG circle on the landing player chart.

**Expected**: Navigation to the tapped player.
**Actual**: Playwright reports `<circle> intercepts pointer events` repeatedly, preventing the tap from reaching the intended circle.

**Root cause**: The landing charts use `svg.selectAll('circle')` to bind click handlers (`LandingPlayerSVG.tsx:226`, `LandingClanSVG.tsx:235`), which selects **all** circles in the SVG including axis tick marks. More critically, densely overlapping data circles (radius 5px, often <10px apart) cause the front circle to intercept touch events meant for the circle behind it. This is worse on touch devices where tap target area is larger than a mouse click.

**Playwright evidence**:
```
51 × waiting for element to be visible, enabled and stable
     - <circle> intercepts pointer events
```

### Bug 4: Player detail page not responsive on mobile (Layout)

**Reproduction**: Load `/player/lil_boots` on a 393px viewport.

**Expected**: Layout adapts to narrow screen.
**Actual**: The `grid-cols-[350px_1fr]` layout forces the first column to 350px, leaving only ~40px for the second column. Content is clipped by `overflow-hidden`.

**Playwright evidence**:
```
Player detail grid: 313px wide at x=40
```

The grid was measured at 313px (clipped from the requested 350px+1fr by the viewport), and the second column content (insights tabs, summary cards) is either hidden or inaccessible.

**Code location**: `client/app/components/PlayerDetail.tsx:330`
```tsx
<div className="grid grid-cols-[350px_1fr] gap-4">
```

No responsive breakpoint exists. Needs `grid-cols-1 md:grid-cols-[350px_1fr]` or similar.

### Non-Bug: HTML button taps work on landing page

Tapping player name buttons (`PlayerNameGrid`) and clan tag buttons (`ClanTagGrid`) on the landing page works correctly. These use standard `<button onClick={...}>` elements that receive touch-to-click synthesis properly.

```
Tapping player button: "Alphabeticol"
Navigated to: https://battlestats.online/player/Alphabeticol  ✓

Tapping clan button: "Show clan Friday Night Fights"
Navigated to: https://battlestats.online/clan/1000060069-friday-night-fights  ✓
```

### Non-Bug: No layout overlap between sections

Clan buttons and player buttons are separated by 453px vertically. No touch-target overlap.

## Proposed Fixes (Priority Order)

### P0: Fix player detail clan name tap (Bug 1)
- Convert the clan name `<button onClick>` to an `<a href>` link, or add explicit `onTouchEnd` handler
- Remove `overflow-hidden` from the player detail wrapper (`PlayerDetail.tsx:322`) or scope it more narrowly
- Alternatively, use Next.js `<Link>` component which handles touch navigation natively

### P1: Make player detail responsive (Bug 4)
- Change `grid-cols-[350px_1fr]` to `grid-cols-1 lg:grid-cols-[350px_1fr]`
- Stack columns vertically on mobile
- This also fixes the second column content being inaccessible

### P2: Differentiate landing charts visually (Bug 2)
- Move "Active Clans" / "Active Players" labels **above** their respective charts
- Use distinct visual styles (e.g., different marker shapes — squares for clans, circles for players)
- Add Y-axis labels ("Clan WR" vs "Player WR") that are visible on mobile

### P3: Fix D3 SVG touch handling (Bug 3)
- Change `svg.selectAll('circle')` to scope only to data circles (e.g., add a class `.data-circle` and select that)
- Consider increasing circle radius on mobile (touch targets should be ≥44px per Apple HIG, current circles are 10px diameter)
- Add `pointer-events: none` to non-data SVG elements
- Optionally add `pointerdown`/`pointerup` handlers alongside `click` for more reliable mobile interaction

## Test Evidence

Tests at: `client/e2e/mobile-routing-investigation.spec.ts`

Run with:
```bash
cd client
PLAYWRIGHT_EXTERNAL_BASE_URL=https://battlestats.online npx playwright test e2e/mobile-routing-investigation.spec.ts --reporter=list
```

| Test | Result | Finding |
|------|--------|---------|
| Player name button tap → /player/ | PASS | HTML buttons work on mobile |
| Clan tag button tap → /clan/ | PASS | HTML buttons work on mobile |
| SVG circle touch handling | PASS | Taps fire but route to clan (first chart) |
| Layout overlap investigation | PASS | 453px gap, no overlap. Player detail grid: 313px |
| Clan member circle tap → /player/ | PASS | ClanSVG touch works (less dense circles) |
| Clan name tap → /clan/ | **FAIL** | **Tap does not navigate on mobile** |

## Code Locations

- `client/app/components/PlayerDetail.tsx:322` — `overflow-hidden` wrapper
- `client/app/components/PlayerDetail.tsx:330` — Fixed grid layout
- `client/app/components/PlayerDetail.tsx:335-342` — Clan name button (Bug 1)
- `client/app/components/LandingPlayerSVG.tsx:226-241` — D3 circle click handlers
- `client/app/components/LandingClanSVG.tsx:235-250` — D3 circle click handlers
- `client/app/components/PlayerSearch.tsx:462-467` — Clan chart placement
- `client/app/components/PlayerSearch.tsx:523-527` — Player chart placement
- `client/app/components/ClanMembers.tsx:74-80` — Member name buttons
- `client/e2e/mobile-routing-investigation.spec.ts` — Playwright investigation tests
