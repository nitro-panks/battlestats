# Runbook: Mobile Player Detail Chart Scaling

**Created**: 2026-03-28
**Updated**: 2026-03-29
**Status**: Complete — all 7 changes implemented, tested, deployed
**Spec**: `spec-mobile-player-detail-ux-2026-03-28.md`

## Context

The player detail page charts were designed for desktop viewports (680px+ content area). On a 393px mobile viewport with ~24px page padding, the effective container width is ~345px. Four chart components overflow this width.

## Current Chart Dimensions vs Mobile Budget

| Component | Default Width | Min Width | Mobile Container | Overflow |
|-----------|--------------|-----------|-----------------|----------|
| TierTypeHeatmapSVG | 680px | 320px | ~345px | Margins waste 60px; fits but cramped |
| WRDistributionDesign2SVG | 600px | none (hardcoded) | ~345px | **255px overflow** |
| PopulationDistributionSVG | 600px | none (hardcoded) | ~345px | **255px overflow** |
| RandomsSVG Design 1 | 680px min | 680px (floor) | ~345px | **335px overflow** |
| ClanSVG (clan page) | 900px | 900px (minWidth style) | ~345px | **555px overflow** |
| RankedWRBattlesHeatmapSVG | 600px | none (hardcoded) | ~345px | **255px clipped** (overflow-hidden) |
| TierTypeHeatmapSVG summary card | 364px (columns) | n/a | ~345px | **~19px overflow** |

## Components Already Mobile-Ready

| Component | Compact Breakpoint | Notes |
|-----------|-------------------|-------|
| TierSVG | < 420px | Reduces margins, font sizes |
| TypeSVG | < 420px | Reduces margins, font sizes |
| TierTypeHeatmapSVG | Partial — clamps to container width, but margins don't compress | Needs compact mode |

## Implementation Checklist

### Change 1: TierTypeHeatmapSVG compact margins — DONE
- [x] Compact mode at `svgWidth < 480`: margins `{ top: 48, right: 6, bottom: 42, left: 28 }`, axis font 9px
- [x] Axis tick padding reduced in compact mode

### Change 2: WRDistributionDesign2SVG container-aware width — DONE
- [x] Resolves width from `containerElement.clientWidth`, clamped to `[280, svgWidth]`
- [x] Compact mode at `< 480px`: margins `{ top: 38, right: 8, bottom: 28, left: 32 }`, axis font 9px
- [x] Reduced tick counts in compact (5 instead of 8/7)
- [x] Window resize listener with cached payload for efficient redraw

### Change 3: PopulationDistributionSVG container-aware width — DONE
- [x] Resolves width from `containerElement.clientWidth`, clamped to `[280, svgWidth]`
- [x] Compact mode at `< 480px`: margins `{ top: 22, right: 6, bottom: 28, left: 30 }`, axis font 9px
- [x] Window resize listener with cached payload

### Change 4: RandomsSVG Design 1 remove width floor — DONE
- [x] Removed 680px floor, now `Math.max(containerWidth || 0, 280)`
- [x] Compact mode at `containerWidth < 580px`: margins `{ top: 28, right: 14, bottom: 48, left: 52 }`
- [x] Compact Y-axis: ship names truncated to 8 chars with ellipsis
- [x] Compact X-axis: 3 ticks instead of 5
- [x] WR% labels positioned relative to bar end — scale naturally with chart width

### Change 5: ClanSVG container-aware width on clan page — DONE
- [x] Removed `minWidth: svgWidth` from container div (key fix — was forcing 900px)
- [x] Resolves width from `containerElement.clientWidth`, clamped to `[280, svgWidth]`
- [x] Compact mode at `< 480px`: margins `{ top: 48, right: 10, bottom: 28, left: 30 }`, axis font 9px
- [x] Resize listener with proper cleanup (cancels animation frame from previous draw)
- [x] ClanDetail.tsx: kept `svgWidth={900}` as ceiling; container width shrinks it on mobile
- [x] PlayerDetail ClanSVG usage (default 320px) unaffected

### Additional fix: PlayerDetail clan name Link
- [x] Removed redundant `onClick` from clan name `<Link>` — was racing with Link's native navigation on mobile

## Validation

```bash
cd client
PLAYWRIGHT_EXTERNAL_BASE_URL=https://battlestats.online npx playwright test e2e/mobile-routing-investigation.spec.ts --reporter=list
```

Playwright tests added at `client/e2e/mobile-chart-overflow.spec.ts`:
- Profile tab: body scroll width + all SVG widths <= 398px — **PASS on live (pre-deploy)**
- Population tab: SVG widths <= 398px — **FAIL on live (600px), expected to pass post-deploy**
- Ships tab: SVG widths <= 398px — **FAIL on live (690px), expected to pass post-deploy**
- Clan page: body scroll width <= 398px — **FAIL on live (940px), expected to pass post-deploy**

## Remaining Issues (Post-deploy UX Review)

### Change 6: TierTypeHeatmapSVG summary card proportional columns + resize — DONE
- [x] `renderSummaryCard` now accepts `chartWidth` parameter
- [x] Summary columns scaled proportionally: `[0, w*0.27, w*0.52, w*0.82]` where `w = chartWidth - 40` (compact) or 400 (default)
- [x] Added window resize listener with cached payload and `cancelAnimationFrame` cleanup

### Change 7: RankedWRBattlesHeatmapSVG container-aware width — DONE
- [x] Resolves width from `containerElement.clientWidth`, clamped to `[280, svgWidth]`
- [x] Compact mode at `< 480px`: margins `{ top: 38, right: 8, bottom: 36, left: 38 }`, axis font 9px
- [x] Summary text x-offset scaled proportionally: `Math.round(width * 0.4)` instead of hardcoded `x=210`
- [x] X-axis ticks filtered to every-other in compact mode; Y-axis ticks reduced to 4
- [x] Y-axis label offset adjusted in compact mode (`-26` vs `-38`)
- [x] Window resize listener with cached payload and animation frame cleanup

### Playwright test addition
- [x] Added ranked tab test to `mobile-chart-overflow.spec.ts`
- Ranked tab: body scroll width + all SVG widths <= 398px — **FAIL on live (600px), expected to pass post-deploy**

## Risk

- Compact margins may clip axis labels on certain data ranges (e.g., very large battle counts with 5+ digits). Mitigate with tick formatting (`1.2k` instead of `1,200`).
- Ship name truncation may reduce readability. Keep tooltip on hover/tap showing full name.
- The WR labels on RandomsSVG bars (right margin) are a key UX element. If hidden in compact mode, ensure the bar color or tooltip still communicates WR.

## Code Locations

- `client/app/components/TierTypeHeatmapSVG.tsx:170` — margins
- `client/app/components/WRDistributionDesign2SVG.tsx:186,374` — margins, component
- `client/app/components/PopulationDistributionSVG.tsx:329,612` — margins, component
- `client/app/components/RandomsSVG.tsx:75-95` — Design 1 dimensions
- `client/app/components/ClanSVG.tsx:135,619` — margins, container minWidth
- `client/app/components/ClanDetail.tsx:100` — hardcoded svgWidth={900}
- `client/app/components/RankedWRBattlesHeatmapSVG.tsx:83,230,237,247-248,298` — margins, summary text, defaults, container
- `client/app/components/TierTypeHeatmapSVG.tsx:89,257` — summary card columns, summary group position
- `client/app/components/PlayerDetailInsightsTabs.tsx:92-99,392-397` — tab min-heights, ranked chart call
