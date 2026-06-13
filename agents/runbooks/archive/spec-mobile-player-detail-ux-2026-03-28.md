# Spec: Mobile Player Detail UX

**Created**: 2026-03-28
**Status**: Spec — implementation pending

## Goal

Make the player detail page usable on standard mobile viewports (393px iPhone, ~360px Android). The current desktop layout overflows or clips on narrow screens. This spec covers the first tranche of mobile fixes for the right rail (player info) content.

## Scope

Four changes, scoped to the insights tab charts on the player detail page:

### 1. Column reorder on mobile (DONE)

Player info (right rail) renders above clan info (left rail) on mobile via `order-1`/`order-2` Tailwind utilities. Committed in `36b66c5`.

### 2. Tier vs Type heatmap — hug left edge

**Component**: `TierTypeHeatmapSVG.tsx`
**Current state**: Default width 680px, resolves to `Math.min(680, Math.max(clientWidth, 320))`. Margins: `{ top: 62, right: 18, bottom: 42, left: 42 }`. On a ~350px effective container, the chart renders at 350px but uses 42px left + 18px right margins = 60px of dead space, leaving only 290px for the data grid (5 ship types x 11 tiers).

**Problem**: The heatmap is centered in its container with symmetric padding. On mobile, every pixel matters. The chart needs to hug the left edge of its container to maximize the data area.

**Fix**:
- Detect compact mode when `resolvedSvgWidth < 480`
- In compact mode, reduce left margin from 42px to 28px, right margin from 18px to 6px
- This recovers ~26px of chart width for data cells
- Reduce top margin from 62px to 48px (the summary card area is smaller on mobile)
- Reduce axis font size from 10px to 9px in compact mode

**Location**: `TierTypeHeatmapSVG.tsx` — `drawChart()` function, margin block near line 170

### 3. Population tab charts — scale to fit viewport

**Components**: `WRDistributionDesign2SVG.tsx`, `PopulationDistributionSVG.tsx`

**Current state**: Both charts use a hardcoded `svgWidth = 600` prop default. They do NOT read `containerElement.clientWidth`. On a 393px viewport with 24px page padding, the effective container is ~345px. The charts overflow by ~255px.

**Fix — WRDistributionDesign2SVG**:
- Read `containerElement.clientWidth` and use `Math.min(svgWidth, containerElement.clientWidth)` as the resolved width
- Detect compact mode at `< 480px` resolved width
- Compact margins: reduce left from 44px to 32px, right from 18px to 8px, bottom from 34px to 28px
- Compact font size: 9px instead of 10px for axis labels
- Add resize listener to redraw on orientation change
- Default height 248px is fine; no height change needed

**Fix — PopulationDistributionSVG**:
- Same container-width clamping: `Math.min(svgWidth, containerElement.clientWidth)`
- Compact margins at `< 480px`: reduce left from 42px to 30px, right from 14px to 6px
- Compact font size: 9px
- Add resize listener
- Default height 184px is fine on mobile

**Locations**:
- `WRDistributionDesign2SVG.tsx` — `drawChart()` near line 186, component near line 374
- `PopulationDistributionSVG.tsx` — `drawChart()` near line 329, component near line 612

### 4. Ships chart (RandomsSVG) — scale X axis for mobile

**Component**: `RandomsSVG.tsx`

**Current state**: Design 1 enforces `Math.max(containerWidth, 680)` — minimum 680px. This means the SVG is always at least 690px wide (680 + 10px extension). On mobile, horizontal scroll is required. Left margin is 83px (68 + 15px shift constant). Right margin is 96px. Together, margins consume 179px of a 680px minimum = 26%.

**Fix**:
- Remove the 680px minimum floor; allow the chart to render at container width
- Detect compact mode at `containerWidth < 580px`
- Compact margins: reduce left from 83px to 52px, right from 96px to 14px
- In compact mode, truncate Y-axis ship names to ~8 characters with ellipsis to save left margin space
- Reduce X-axis tick count from 5 to 3 on compact
- Reduce font sizes from 10px to 9px
- The right-side WR% labels that use the 96px right margin: in compact mode, overlay them on the bars instead (white text on bar fill) or omit them
- Add resize listener (already exists at line 571)

**Location**: `RandomsSVG.tsx` — `drawBattlePlotDesign1()` near line 75

## UX Recommendation — Chart Placement

The current Profile tab order is:
1. Tier vs Type heatmap (widest chart, 680px default)
2. Performance by Ship Type (TypeSVG, responsive, 210px tall)
3. Performance by Tier (TierSVG, responsive, 300px tall)

On mobile, TypeSVG and TierSVG already handle compact mode well (< 420px breakpoint with compressed margins). The heatmap is the widest and least mobile-friendly.

**Recommendation**: Keep the current order. The heatmap is the most information-dense chart and serves as the primary profile overview — it should remain first. With the compact margin fix, it will fit within the viewport. TypeSVG and TierSVG already work at mobile widths.

### 5. Clan page chart — scale to fit mobile

**Component**: `ClanSVG.tsx`, called from `ClanDetail.tsx`

**Current state**: `ClanDetail.tsx:100` passes `svgWidth={900} svgHeight={440}`. The container div at `ClanSVG.tsx:619` enforces `style={{ minHeight: svgHeight, minWidth: svgWidth }}` — hardcoding a 900px minimum width. Margins are `{ top: 64, right: 16, bottom: 32, left: 38 }` (96px total vertical, 54px total horizontal). On a 393px viewport with 24px page padding (`p-6`), the effective container is ~345px — the chart overflows by **555px**.

**Problem**: The clan scatter plot (WR vs battles for each member) is completely unusable on mobile. The `minWidth: svgWidth` inline style forces horizontal scroll.

**Fix — ClanSVG.tsx**:
- Remove `minWidth` from the container `style` — replace with `minWidth: Math.min(svgWidth, 280)` so the container can shrink
- Read `containerElement.clientWidth` and resolve width as `Math.min(svgWidth, Math.max(containerElement.clientWidth, 280))`
- Detect compact mode at `resolvedWidth < 480`
- Compact margins: `{ top: 48, right: 10, bottom: 28, left: 30 }` (from `{ top: 64, right: 16, bottom: 32, left: 38 }`)
- Compact axis font: 9px (from 10px)
- Compact circle radius: keep at current size (circles are already touch-target-sized at this chart density)
- Add resize listener to redraw on orientation change

**Fix — ClanDetail.tsx**:
- Remove hardcoded `svgWidth={900}` — let the component resolve from container width
- Keep `svgHeight={440}` or reduce to `svgHeight={360}` on mobile (fewer vertical pixels needed when chart is narrower)

**Location**: `ClanSVG.tsx:135,619`, `ClanDetail.tsx:100`

**Note**: ClanSVG is also used on the player detail page at `PlayerDetail.tsx:352` with default props (`svgWidth=320, svgHeight=280`). The player detail usage already fits mobile. Only the clan detail page call needs the width fix.

## Out of Scope

- Increasing D3 touch target sizes (tracked in mobile routing runbook P3)
- Clan info rail mobile layout
- Tab navigation UX on mobile (horizontal scroll of tab buttons)
- RandomsSVG Design 2 (bubble chart) — only Design 1 is in scope

## Test Plan

- Playwright mobile emulation (393px viewport) on each tab:
  - Profile: heatmap fits without horizontal overflow
  - Population: both charts fit without horizontal overflow
  - Ships: chart fits without horizontal overflow, ship names readable
  - Clan page: scatter plot fits without horizontal overflow
- Visual regression: desktop layout unchanged (charts render at full width on both player and clan pages)
- No new horizontal scrollbar on player detail page or clan detail page at 393px
