# Runbook: Ranked Season Top Ship

## Purpose

Add a single new field to the player ranked-seasons table: the top ship a player used in that ranked season.

This runbook keeps the scope deliberately narrow. It does not expand the ranked table into a ship breakdown, win-rate view, or season drilldown.

## Problem Statement

The ranked table currently shows season-level performance only:

1. season,
2. highest league,
3. battles,
4. wins,
5. win rate.

That answers how a player performed, but not what ship they leaned on most in that season.

## Scope

### In Scope

1. Fetch ranked ship stats from the WG ranked shipstats endpoint.
2. Determine the most-played ship for each ranked season already shown in the table.
3. Store that ship name alongside each ranked season summary.
4. Add a `Top Ship` column to the ranked seasons table.
5. Keep the feature nullable and resilient when upstream shipstats are empty.

### Out of Scope

1. Multiple ships per season.
2. Ranked ship win-rate display.
3. Per-season row expansion.
4. New standalone endpoint for ranked ship details.

## Product Decision

Show one ship name only.

Reasoning:

1. It adds useful context without widening the table too much.
2. It avoids loading a second UI surface or detail state.
3. It stays aligned with the current ranked table being a compact summary view.

## Technical Plan

1. Reuse the existing ranked refresh path in `update_ranked_data`.
2. Call WG `seasons/shipstats/` with the same player and ranked season IDs already present in `rank_info`.
3. Aggregate ship rows to a single top ship per season by battles played.
4. Resolve the winning ship IDs through the existing ship metadata cache.
5. Persist the ship name onto each ranked season row as `top_ship_name`.
6. Backfill fresh cached ranked rows automatically if older cached data lacks the new field.

## Execution Notes

### Backend

Implemented changes:

1. Added ranked shipstats fetch helper in `server/warships/api/ships.py`.
2. Added ranked ship parsing helpers in `server/warships/data.py`.
3. Enriched ranked season summaries with `top_ship_name`.
4. Made ranked cache refresh rehydrate rows when older cached payloads are missing the new field.

### Frontend

Implemented changes:

1. Added a `Top Ship` column to the ranked seasons table.
2. Rendered a fallback em dash when no upstream top ship is available.

## Validation Plan

1. Unit test ranked ship parsing for multi-season inputs.
2. Unit test ranked refresh storing `top_ship_name`.
3. API view test confirming ranked rows serialize the new field.
4. Client production build to catch table typing or rendering regressions.

## Risks

1. WG `seasons/shipstats/` can legitimately return empty rows for a player or season.
2. Old cached ranked payloads may not contain `top_ship_name` until refreshed.
3. Shipstats response structure may vary by mode nesting, so parsing must tolerate both direct `battles` and nested ranked mode objects.

## Rollback

If the endpoint proves too inconsistent in production:

1. remove the `Top Ship` column from the client,
2. stop enriching `ranked_json` with `top_ship_name`,
3. keep the rest of ranked season aggregation unchanged.

## Definition of Done

This feature is done when:

1. ranked season rows include a nullable `top_ship_name`,
2. the player detail ranked table renders that value,
3. tests pass,
4. the change is committed and pushed.