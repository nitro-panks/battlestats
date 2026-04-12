# Runbook: Enriched Player Data Feature Rollout

**Created:** 2026-04-12
**Status:** In progress

## Overview

With the enrichment pipeline populating `PlayerExplorerSummary` across the player base, we can build interactive charts and features that leverage the enriched data. This runbook tracks each feature from planning through deployment.

## Feature Queue (in implementation order)

### Feature 1: Activity Trend Arrows on Explorer Table
- **Status:** 🔧 In progress
- **Scope:** Frontend-only. Add inline ▲/▼/— trend indicator in the Player Explorer table for each row's `activity_trend_direction` field (already served by `build_player_summary`).
- **Files:** `client/app/components/PlayerExplorer.tsx`
- **Effort:** Very low
- **Validation:** Visual check on `/player/[name]` → Explorer tab — rows show colored trend arrows. No backend changes.

### Feature 2: Player Score Distribution (histogram + "you are here" marker)
- **Status:** ⏳ Queued
- **Scope:** Add `player_score` to `PLAYER_DISTRIBUTION_CONFIGS` in `data.py`. Expose via existing `/api/player-distribution/<metric>/` route. Frontend: reuse `PopulationDistributionSVG` with an optional player-position marker overlay.
- **Files:** `server/warships/data.py`, `client/app/components/PopulationDistributionSVG.tsx` (or new wrapper), routing/tab wiring
- **Effort:** Low

### Feature 3: Population Percentile Card on Player Detail
- **Status:** ⏳ Queued
- **Scope:** Compact percentile badges ("Top X% in WR", "Top X% in Score", etc.) on the player detail header. Compute from existing distribution bin data returned from the API.
- **Files:** New component + `PlayerDetail.tsx` integration
- **Effort:** Low-medium

### Feature 4: Kill Ratio vs Win Rate Scatter (population correlation)
- **Status:** ⏳ Queued
- **Scope:** New population correlation: `kill_ratio` (x) vs `pvp_ratio` (y). Backend: new correlation builder following the WR-vs-survival pattern. Frontend: new SVG scatter/heatmap component.
- **Files:** `server/warships/data.py` (new correlation), `server/warships/views.py` (new route), new frontend SVG component
- **Effort:** Medium

### Feature 5: Recent WR vs Lifetime WR Scatter
- **Status:** ⏳ Queued
- **Scope:** Scatter plot of `recent_win_rate` (29-day) vs `pvp_ratio` (lifetime). Diagonal reference line separates improving from declining players. Player dot highlighted.
- **Files:** Similar to Feature 4 — new correlation builder + new SVG component
- **Effort:** Medium

## Design Principles

- Reuse existing infrastructure (`PLAYER_DISTRIBUTION_CONFIGS`, `PopulationDistributionSVG`, correlation builder pattern) wherever possible
- All new distributions/correlations participate in the warmer cycle (55-min landing warmer + startup warmer) to avoid cold-cache penalties
- "You are here" player markers use the same highlight pattern as tier-type and ranked heatmaps
- No new browser-triggered WG API calls — all data comes from enriched Postgres data

## Versioning

Each feature is a `feat:` commit (minor version bump). Features are deployed individually. Version bump will happen as a batch after the full rollout is validated.
