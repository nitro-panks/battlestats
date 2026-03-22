# Spec: Heavy Client Component Simplification Audit

_Captured: 2026-03-20_

_Status: historical audit; narrowed first slice executed on 2026-03-20_

## Goal

Identify the heaviest client components by file size and responsibility, then define where simplification and performance work should be concentrated next.

This spec is intentionally documentation-first. It does not authorize code changes by itself.

## Problem Statement

The client now has several large React and D3 components that mix multiple responsibilities in a single file:

- route orchestration
- fetch policy
- derived state
- rendering policy
- interactive controls
- imperative D3 drawing

Large file size alone does not prove runtime slowness, but in this codebase the largest files also correlate with the surfaces that already matter most to perceived latency:

- player route detail composition
- landing/search orchestration
- chart-heavy D3 sections

The risk is twofold:

- maintenance risk: changes become harder to reason about because one file owns too many concerns
- performance risk: a parent rerender, effect restart, or redraw touches too much work at once

## Inputs Used

Component ranking was generated from `client/app/components` by non-test line count.

Top component files observed:

1. `PlayerDetail.tsx` - `834` lines
2. `RandomsSVG.tsx` - `722` lines
3. `PopulationDistributionSVG.tsx` - `656` lines
4. `PlayerSearch.tsx` - `655` lines
5. `ClanSVG.tsx` - `463` lines
6. `TraceDashboard.tsx` - `462` lines
7. `TierTypeHeatmapSVG.tsx` - `404` lines
8. `WRDistributionDesign2SVG.tsx` - `400` lines
9. `LandingActivityAttritionSVG.tsx` - `376` lines
10. `PlayerEfficiencyBadges.tsx` - `329` lines
11. `ClanActivityHistogram.tsx` - `325` lines
12. `RankedSeasons.tsx` - `299` lines
13. `PlayerExplorer.tsx` - `290` lines

Test files were excluded from the audit ranking.

## Audit Heuristics

Each heavy component is evaluated on four axes:

### 1. Responsibility concentration

Questions:

- Does the file combine controller logic and presentational rendering?
- Does it own fetch lifecycle, UI state, and rendering primitives together?
- Does it contain multiple distinct sections that could be isolated?

### 2. Rerender blast radius

Questions:

- When local state changes, how much of the tree redraws?
- Does a small interaction force a large parent rerender?
- Are multiple expensive children declared inline under the same controller?

### 3. Fetch and redraw policy

Questions:

- Is data fetched inside the component that also performs heavy rendering?
- Can repeated effect runs trigger repeated fetches or full SVG rebuilds?
- Is async polling or retry logic embedded in a large UI file?

### 4. Reuse and abstraction quality

Questions:

- Is there duplicated chart math or tooltip/legend logic?
- Is a generic abstraction carrying too many conditional branches?
- Are there alternate designs or dormant code paths that still carry maintenance cost?

## Audit Scope

The primary review set for this tranche is:

- `client/app/components/PlayerDetail.tsx`
- `client/app/components/PlayerSearch.tsx`
- `client/app/components/RandomsSVG.tsx`
- `client/app/components/PopulationDistributionSVG.tsx`
- `client/app/components/ClanSVG.tsx`
- `client/app/components/TraceDashboard.tsx`
- `client/app/components/TierTypeHeatmapSVG.tsx`
- `client/app/components/PlayerExplorer.tsx`
- `client/app/components/RankedSeasons.tsx`

These were selected because they are either among the largest files or they combine heavy UI work with route-level orchestration.

## Non-Goals

This audit does not:

- rewrite the components
- change visual design
- replace D3 with another chart library
- measure bundle size or browser traces in this document
- make backend changes

## Desired Output

The companion runbook should produce:

- the ranked heavy-component list
- per-component simplification findings
- per-component optimization findings
- a priority order for any future implementation tranche
- explicit notes on what should not be touched yet

## Acceptance Criteria

This audit is complete when:

- the heaviest client components are listed with size context
- the main simplification opportunities are documented per reviewed file
- the main optimization opportunities are documented per reviewed file
- a clear next tranche is proposed without implementing it
