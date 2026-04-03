# Runbook: EU Heatmap Rollout and Payload Flattening

**Created**: 2026-04-02
**Status**: Implemented
**Depends on**: `spec-multi-realm-eu-support.md`, `runbook-cache-audit.md`, `archive/runbook-ranked-player-heatmap-payload-optimization-2026-03-25.md`

## Goal

Start serving EU population heatmaps from the data already loaded into the database, then make those payloads as compact and render-friendly as the best existing NA heatmap paths.

This is not a request to invent new heatmap surfaces. It is a request to make the current population heatmap family work well for EU with the current corpus and to flatten the payloads where they are still carrying avoidable structure.

## Implementation Outcome

The first EU heatmap tranche is now shipped for the existing player heatmap family.

What changed:

1. `tier_type` now uses a compact indexed payload instead of repeating `ship_type` and `ship_tier` on every populated tile
2. the backend keeps the same realm-aware `tier_type` endpoint and the same `X-Tier-Type-Pending` behavior for players whose `battles_json` has not been hydrated yet
3. the client reconstructs tier-type axes and tiles through a small helper, mirroring the existing ranked heatmap payload helper pattern
4. existing player overlays remain explicit via `player_cells`, so the player-specific tables and warm-pending logic stay intact
5. `warm_player_correlations(realm=...)` now warms the full player heatmap family: `tier_type`, `win_rate_survival`, and `ranked_wr_battles`
6. shared population heatmaps now persist durable published cache copies alongside the TTL-bound primary cache so warm results survive primary-key churn more gracefully

The compact payload now looks like:

```json
{
  "metric": "tier_type",
  "x_labels": ["Destroyer", "Cruiser", "Battleship", "Aircraft Carrier", "Submarine"],
  "y_values": [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
  "tiles": [{"x_index": 0, "y_index": 1, "count": 18432}],
  "trend": [{"x_index": 0, "avg_tier": 8.37, "count": 90211}],
  "player_cells": [...]
}
```

No EU-specific endpoint or EU-specific React surface was added. EU rides the same realm-aware correlation path as NA.

## Current Readiness

As of 2026-04-02, the active database reports:

1. `466,774` EU players
2. `58,223` EU clans
3. `466,774 / 466,774` EU players fresh within 7 days
4. `62 / 435,782` visible active EU players with `efficiency_json`

That is enough to start building EU population heatmaps from stored data.

The important nuance is that EU is now strong on core population coverage but still weak on enrichment. The first EU heatmap tranche should therefore prefer charts that rely on already-populated fields rather than waiting for efficiency or achievement backfills.

## What Counts As "EU Heatmaps"

For this tranche, "EU heatmaps" means the existing population correlation family, scoped to `realm='eu'`:

1. `tier_type` via `fetch_player_tier_type_correlation(...)`
2. `win_rate_survival` via `fetch_player_wr_survival_correlation(...)`
3. `ranked_wr_battles` via `fetch_player_ranked_wr_battles_correlation(...)`

These already have realm-aware server paths and warmers.

This tranche does **not** introduce a new clan heatmap product. Clan landing charts remain on the existing landing payload path. If a future clan heatmap is desired, it should be planned as a separate runbook after the EU player heatmap family is stable.

## Current Architecture

### Backend builders already in place

The current server already supports realm-scoped analytics caches:

1. player distributions use `players:distribution:v2:{metric}`
2. player correlations use `players:correlation:v2:{metric}`
3. ranked WR vs battles uses `players:correlation:v2:ranked_wr_battles:v6`

Relevant builders and warmers:

1. `server/warships/data.py` `fetch_player_population_distribution(metric, realm=...)`
2. `server/warships/data.py` `warm_player_distributions(realm=...)`
3. `server/warships/data.py` `fetch_player_tier_type_correlation(player_id, realm=...)`
4. `server/warships/data.py` `fetch_player_wr_survival_correlation(realm=...)`
5. `server/warships/data.py` `fetch_player_ranked_wr_battles_correlation(player_id, realm=...)`
6. `server/warships/data.py` `warm_player_correlations(realm=...)`
7. `server/warships/tasks.py` `warm_landing_page_content_task(..., realm=...)`
8. `server/warships/management/commands/startup_warm_all_caches.py`

### Current compactness status

Not all heatmap payloads are equally compact today.

1. `ranked_wr_battles` is already in the preferred indexed format: shared axis geometry plus tile indexes and counts.
2. `win_rate_survival` is already mostly flat: shared domains plus `x_index`, `y_index`, and `count`.
3. `tier_type` is the least compact path: tiles still repeat `ship_type` strings and `ship_tier` values instead of sending a normalized axis domain once and index-based cells.

That means the first flattening target should be the tier-vs-type population payload, not the ranked heatmap path that was already optimized.

## Decision

Proceed with an EU heatmap rollout now, using the existing data at hand.

The implementation should follow four rules:

1. reuse the current realm-aware endpoints and cache families rather than adding EU-only APIs
2. compute population heatmaps entirely from stored data, not browser-triggered WG calls
3. pre-warm EU caches before exposing the surfaces as "ready"
4. flatten payloads where repeated axis metadata or repeated labels still create unnecessary JSON weight

## Work Plan

### Phase 1: Measure EU tracked populations and cold-build cost

Before changing payloads, record what the current EU builders actually produce.

For each correlation family, capture:

1. tracked population
2. tile count
3. trend count
4. serialized payload size
5. cold-build time
6. warm-cache response time

Validation commands should use the existing realm-aware builders and the active DB, for example:

```bash
cd /home/august/code/archive/battlestats/server && \
/home/august/code/archive/battlestats/.venv/bin/python manage.py shell -c "from warships.data import warm_player_correlations; import json; print(json.dumps(warm_player_correlations(realm='eu'), indent=2, sort_keys=True))"
```

And direct endpoint probes should include `?realm=eu` against the player-correlation endpoints to measure real response shapes.

This phase produces the baseline needed to decide whether EU is already fast enough on warm-cache reads and which payload still needs flattening most.

### Phase 2: Flatten the tier-vs-type population payload

Status: complete

This is the highest-value structural change.

Today `tier_type` tiles repeat:

1. `ship_type`
2. `ship_tier`
3. `count`

The EU rollout should change this to the same style already proven on ranked and survival heatmaps:

1. send the ship-type axis once, for example `x_labels`
2. send the tier domain once, for example `y_values` or `y_min` plus `y_max`
3. send population tiles as indexes plus count
4. send trend rows as indexes plus value
5. keep `player_cells` either compact as well or explicitly documented as player-overlay-only payload

Implemented shape:

```json
{
  "metric": "tier_type",
  "x_labels": ["Destroyer", "Cruiser", "Battleship", "Aircraft Carrier", "Submarine"],
  "y_values": [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
  "tiles": [{"x_index": 0, "y_index": 2, "count": 18432}],
  "trend": [{"x_index": 0, "avg_tier": 8.37, "count": 90211}],
  "player_cells": [...]
}
```

This keeps the chart semantics the same while removing repeated labels from every occupied tile.

### Phase 3: Add durable fallback behavior where EU cold misses are still too expensive

Status: not required in this tranche

The ranked heatmap path already keeps a published fallback copy.

If EU cold-build timings for `win_rate_survival` or `tier_type` are still high enough to hurt first-paint rendering, add the same stale-while-revalidate pattern used by ranked:

1. primary TTL-bound cache key
2. published fallback key with no timeout
3. request-time serve from published key if the primary key is cold
4. background republish rather than synchronous empty states where practical

This was intentionally left unchanged in the first implementation slice. The existing pending-header behavior remains in place for per-player tier-type overlays, and no new published fallback layer was added yet.

### Phase 4: Warm EU heatmaps explicitly as part of the realm warm path

Status: existing realm-aware warm path retained

The code already supports realm-scoped warming through `warm_player_correlations(realm=...)` and the landing warmer task.

The rollout work should make EU warming operationally explicit:

1. confirm the deploy/startup warm path warms correlations for `realm='eu'`
2. confirm the scheduled landing warmer keeps EU heatmaps hot after rollout
3. document a one-shot operator command for warming EU after a crawl spike or deploy

Preferred operator command:

```bash
cd /home/august/code/archive/battlestats/server && \
/home/august/code/archive/battlestats/.venv/bin/python manage.py startup_warm_all_caches --realm eu
```

If startup warming is too broad for repeated operator use, add a narrower management command later. Do not add that command in the first tranche unless the current warm path proves too slow or too coarse.

### Phase 5: Keep the client rendering path simple

Status: complete

The client should continue to render EU heatmaps through the same components that NA uses.

That means:

1. no EU-specific React components
2. no EU-specific endpoint family
3. no new browser polling loops
4. only small client helpers to reconstruct compact tile bounds where payload flattening requires it

The ranked heatmap helper in `client/app/components/rankedHeatmapPayload.ts` is the model to reuse.

If `tier_type` becomes indexed, add a similarly small helper instead of burying reconstruction logic inside the SVG component.