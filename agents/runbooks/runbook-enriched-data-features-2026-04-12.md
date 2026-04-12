# Runbook: Enriched Player Data Feature Rollout

**Created:** 2026-04-12
**Status:** In progress

## Overview

With the enrichment pipeline populating `PlayerExplorerSummary` across the player base, we can build interactive charts and features that leverage the enriched data. This runbook tracks each feature from planning through deployment.

## Feature Queue (in implementation order)

### ~~Feature 1: Activity Trend Arrows~~ — SKIPPED
Skipped. The `activity_trend_direction` field compares first-half vs second-half battle counts in the activity window — a crude metric that doesn't track real evolution over time. Misleading without true time-series data.

### Feature 2: Player Score Distribution (histogram + "you are here" marker)
- **Status:** ✅ Implemented
- **Scope:** Added `player_score` to `PLAYER_DISTRIBUTION_CONFIGS` in `data.py` with a `source_model: 'explorer_summary'` key that queries `PlayerExplorerSummary` directly (player_score lives on the explorer summary, not on Player). Frontend: new `PlayerScoreDistributionSVG` wrapper, wired into Population tab on player detail page.
- **Backend files:** `server/warships/data.py` — new config entry + queryset source routing in `fetch_player_population_distribution`
- **Frontend files:** `client/app/lib/chartTheme.ts` (metricScore color), `client/app/components/PopulationDistributionSVG.tsx` (new metric type + decimal format), `client/app/components/PlayerScoreDistributionSVG.tsx` (new wrapper), `client/app/components/PlayerDetailInsightsTabs.tsx` + `PlayerDetail.tsx` (wiring)
- **Validation:** Build passes. Deploy frontend + backend, then visit any player's Population tab.

### Feature 3: Population Percentile Card on Player Detail
- **Status:** ⏳ Queued

### Feature 4: Kill Ratio vs Win Rate Scatter (population correlation)
- **Status:** ⏳ Queued

### Feature 5: Recent WR vs Lifetime WR Scatter
- **Status:** ⏳ Queued

## Design Principles

- Reuse existing infrastructure (`PLAYER_DISTRIBUTION_CONFIGS`, `PopulationDistributionSVG`, correlation builder pattern)
- All new distributions participate in the warmer cycle (55-min landing warmer + startup warmer)
- "You are here" player markers use the same highlight pattern as existing distributions
- No new browser-triggered WG API calls — all data from enriched Postgres data
