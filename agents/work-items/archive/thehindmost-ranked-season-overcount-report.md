# TheHindmost Ranked Season Overcount Report

## Summary

Player `TheHindmost` was showing impossible ranked season summaries on the player detail page. Examples observed on 2026-03-15:

- `S2`: `154` wins in `22` battles
- `S3`: `84` wins in `22` battles

The defect was in backend ranked aggregation, not in the UI table.

## Reproduction

Live container inspection showed the stored ranked rows already contained the bad totals.

Stored `ranked_json` excerpts:

- `S2`: `total_battles = 22`, `total_wins = 154`, `win_rate = 7.0`
- `S3`: `total_battles = 22`, `total_wins = 84`, `win_rate = 3.8182`

The raw upstream `rank_info` payload for older seasons contains sprint rows like:

- `victories > 0`
- `battles = 0`

For `TheHindmost`, season `1002` included historical sprint entries such as:

- sprint `1`: `victories = 33`, `battles = 0`
- sprint `2`: `victories = 35`, `battles = 0`
- sprint `3`: `victories = 45`, `battles = 0`
- sprint `4`: `victories = 12`, `battles = 22`
- sprint `5`: `victories = 29`, `battles = 0`

The previous aggregator summed all `victories` values regardless of whether the same row reported any battles.

## Root Cause

`server/warships/data.py` in `_aggregate_ranked_seasons()` trusted raw WG sprint-level `victories` counts even when `battles` was zero.

Older WG ranked seasons appear to preserve historical progression metadata for archived sprints while zeroing out their battle counts. Summing those rows inflated `total_wins` and produced impossible season win rates greater than `100%`.

## Fix

The aggregator now ignores sprint or league entries where:

- `battles <= 0`
- `victories > 0`

Those rows still contribute league and rank metadata, but they no longer affect season battle or win totals.

## Validation

- Added regression coverage in `server/warships/tests/test_data.py`
- The new test proves an old-season payload with zero-battle victory rows now aggregates to `12` wins in `22` battles for `S2`, while preserving `Gold` as the highest achieved league

## Blast Radius

Live cache scan on 2026-03-15 found that this is not a single-player anomaly.

- Players with non-empty ranked history: `32,041`
- Ranked season rows scanned: `226,903`
- Players with at least one impossible ranked row: `3,186`
- Impossible ranked rows detected: `6,437`

Affected-player rate is about `9.9%` of players with ranked data. Affected-row rate is about `2.8%` of stored ranked season rows.

The issue is concentrated entirely in early ranked seasons:

- season `1003` (`S3`): `2,771` bad rows
- season `1002` (`S2`): `2,677` bad rows
- season `1001` (`S1`): `989` bad rows

No impossible rows were detected outside seasons `1001` to `1003` in the live cache scan.

## Accuracy Plan

### Phase 1: Contain New Corruption

1. Keep the aggregator fix in place so newly refreshed ranked rows cannot reintroduce `wins > battles` for archived early-season WG payloads.
2. Keep the regression test that covers zero-battle historical victories.
3. Add a lightweight post-refresh invariant check in future ranked maintenance work so obviously impossible rows are flagged immediately in logs or metrics.

### Phase 2: Repair Existing Stored Rows

1. Run a one-off repair pass against players whose stored `ranked_json` contains impossible rows.
2. Scope the repair to players matching either of these conditions:
   - any season row with `total_wins > total_battles` when `total_battles > 0`
   - any season row with `win_rate > 1.0`
3. Recompute those players by calling `update_ranked_data(player_id)` so the corrected aggregator rewrites the cached ranked history.
4. Prefer a targeted repair queue or one-off management command over a full force-refresh of every ranked player; the blast radius is large enough to matter but narrow enough to avoid a full corpus rebuild.

### Phase 3: Verify the Repair

1. Re-run the same impossible-row scan after the repair pass.
2. Success criterion: `0` rows with `wins > battles` and `0` rows with `win_rate > 1.0`.
3. Spot-check representative repaired players from each affected season, not just TheHindmost.
4. Confirm the public `/api/fetch/ranked_data/<player_id>/` endpoint returns corrected values for sampled players after repair.

### Phase 4: Keep It Correct

1. Add an audit query or management command that can be run after ranked backfills and incrementals.
2. Add the impossible-row audit to ranked maintenance runbooks so future backfills verify data quality, not just job completion.
3. Treat any future impossible ranked row as a blocking data-integrity regression because the invariant is simple and deterministic.

## Follow-Up

After deploying the code change, refresh affected player caches so stored `ranked_json` rows are recomputed from the corrected aggregator.
