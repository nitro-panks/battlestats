# Runbook: Ranked Player Heatmap Payload Optimization 2026-03-25

_Implemented: 2026-03-25_

## Goal

Reduce ranked-player heatmap load cost without changing the rendered chart semantics.

The target surface is the player-detail `Ranked Games vs Win Rate` heatmap served from `GET /api/fetch/player_correlation/ranked_wr_battles/<player_id>/`.

## Review Summary

The chart does not need a fully expanded rectangle description for every occupied bin.

Before this change, the payload repeated all of these values for every occupied heatmap cell:

1. `x_min`
2. `x_max`
3. `y_min`
4. `y_max`
5. `count`

That shape is expensive because the x-axis and y-axis bucket geometry are mostly shared across the entire chart.

On local tracked data during review, the warmed ranked heatmap payload measured approximately:

1. `53,895` tracked players
2. `1,491` occupied tiles
3. `37` trend points
4. about `103,886` compact JSON bytes

After the indexed payload change on the same local dataset, the warmed payload measured approximately:

1. `53,895` tracked players
2. `1,491` occupied tiles
3. `37` trend points
4. about `59,058` compact JSON bytes

That is a reduction of roughly `43%` in serialized JSON size without changing the chart granularity.

The dominant waste was repeated bin-boundary data, not the trend line or player marker.

## What The Chart Actually Needs

To draw the same chart, the client only needs:

1. shared x-axis bin edges once
2. shared y-axis min/max/bin-width once
3. occupied tile indexes and counts
4. trend-bin indexes and y values
5. the player point

Everything else can be derived on the client from those shared bin definitions.

## Shipped Change

The ranked heatmap payload now uses a compact indexed format:

1. `x_edges` is sent once for the full x-axis bin geometry
2. each tile is sent as:
   - `x_index`
   - `y_index`
   - `count`
3. each trend point is sent as:
   - `x_index`
   - `y`
   - `count`
4. the client reconstructs tile bounds and trend x positions locally

The chart remains visually and analytically equivalent:

1. same x-axis buckets
2. same y-axis buckets
3. same occupied-cell counts
4. same trend y values
5. same player marker

## Why This Was Chosen

This follows the battlestats doctrine:

1. incremental evolution over a chart rewrite
2. additive local reconstruction instead of a new data product
3. bounded change to one endpoint and one client component
4. no extra upstream calls or browser fan-out

It is cheaper than:

1. adding response compression-specific plumbing just for one endpoint
2. reworking the chart into a different visual encoding
3. widening cache TTLs without first reducing the payload itself

## Alternatives Considered

### 1. Coarsen the heatmap bins

Rejected for now.

This would reduce tile count, but it would also change the chart meaning and undo the deliberate granularity work documented in `agents/runbooks/archive/runbook-ranked-wr-battles-heatmap-granularity.md`.

### 2. Drop the trend line

Rejected.

The trend line is a small fraction of the payload and provides useful interpretation. It was not the main cost driver.

### 3. Gzip-only thinking

Not sufficient as the primary answer.

Transport compression helps, but it still leaves unnecessary server serialization work, client parse work, and cache footprint. The payload should be structurally smaller first.

### 4. Pre-render the chart as an image

Rejected.

That would trade away interactive semantics and make player-point overlay logic less flexible.

## Files

1. `server/warships/data.py`
2. `server/warships/serializers.py`
3. `server/warships/views.py`
4. `server/warships/tests/test_views.py`
5. `client/app/components/RankedWRBattlesHeatmapSVG.tsx`
6. `client/app/components/__tests__/RankedWRBattlesHeatmapSVG.test.tsx`

## Validation

Focused validation for this tranche:

1. backend view test confirms the ranked endpoint returns indexed tiles and indexed trend points
2. client helper test confirms the compact payload reconstructs the same tile bounds and trend x positions as the older expanded payload implied
3. focused Playwright browser probe confirms the ranked tab can request and draw a realistic compact heatmap payload in a bounded time window

Focused browser measurement captured on `2026-03-25` with Playwright using a mocked compact payload shaped like the current ranked endpoint:

1. payload size: about `59,096` JSON bytes
2. tile count: `1,491`
3. trend count: `37`
4. click to ranked-heatmap request start: about `358 ms`
5. ranked-heatmap request round-trip: about `6 ms`
6. click to heatmap draw completion: about `496 ms`
7. response completion to heatmap draw completion: about `132 ms`

The Playwright probe lives at `client/e2e/ranked-heatmap-performance.spec.ts`.

Recommended measurement check after rollout:

1. warm the ranked heatmap population cache
2. serialize one ranked heatmap payload from local tracked data
3. compare JSON byte length before vs after the compact format

## Follow-Up Options

If more reduction is needed later, the next safe options are:

1. quantize `player_point.y` and trend `y` to fewer decimals if precision review says it is visually irrelevant
2. remove optional labels that are not rendered in the chart body
3. consider whether the ranked heatmap should stay on the current idle warmup lane or become click-only on slower hosts

Do not coarsen bucket granularity unless product explicitly accepts a less precise chart.
