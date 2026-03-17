# Feature Spec: Efficiency Rank Gating Recalibration

_Drafted: 2026-03-17_

## Goal

Recalibrate the Battlestats efficiency-rank publication gate so the published sigma tiers are fair relative to the badge data currently stored in the system.

The immediate product problem is not that the published `E` threshold is mathematically wrong. The problem is that the current candidate pool is far smaller than the stored badge-bearing population because the denominator lane depends on `battles_json` coverage that is absent for most players who already have `efficiency_json` badge rows.

The desired outcome is:

1. the published sigma tiers continue to mean field-relative strength,
2. the eligible field is built from data coverage Battlestats actually has today,
3. the comparative rank remains honest about breadth and does not collapse to a tiny operationally-biased subset.

## Current Model

The current rank model in [server/warships/data.py](server/warships/data.py) requires all of the following before a player enters the percentile field:

1. player is visible,
2. `pvp_battles >= 200`,
3. `eligible_ship_count >= 5`,
4. `efficiency_badge_rows_total > 0`,
5. `normalized_badge_strength is not None`,
6. unmapped badge-row share is at most `10%`.

The current tier publication thresholds are:

1. `III >= 50th percentile`,
2. `II >= 75th percentile`,
3. `I >= 90th percentile`,
4. `E >= 97th percentile`.

The current `eligible_ship_count` is derived from `battles_json`, not from `efficiency_json`.

Specifically, a ship counts as eligible only when the corresponding `battles_json` row has:

1. `ship_tier >= 5`,
2. `pvp_battles >= 5`.

## Current Data Findings

The following measurements were taken against the local March 17, 2026 dataset.

### Raw stored badge coverage

1. visible players: `265,689`
2. visible players with non-empty `efficiency_json`: `81,037`
3. visible players with at least one raw WG Expert badge row: `33,658`

### Current published comparative rank coverage

1. visible players eligible for the current comparative field: `403`
2. visible players with any published tier: `202`
3. visible players with published `E`: `13`

### Suppression breakdown under the current model

1. `too_few_eligible_ships`: `164,617`
2. `low_pvp_battles`: `100,444`
3. `no_badge_rows`: `225`
4. eligible: `403`

### Suppression inside the raw Expert-badge population

1. visible raw Expert-badge players: `33,658`
2. raw Expert-badge players eligible for the current comparative field: `319`
3. raw Expert-badge players with any published tier: `195`
4. raw Expert-badge players with published `E`: `13`

Suppression reasons inside the raw Expert subset:

1. `too_few_eligible_ships`: `32,435`
2. `low_pvp_battles`: `834`
3. `no_badge_rows`: `70`

## Key Diagnosis

The current `E` threshold is not the main fairness problem.

Within the current candidate pool, `13 / 403` is about `3.2%`, which is aligned with the intended `97th percentile` Expert threshold.

The fairness problem is that the candidate pool is operationally tiny because `eligible_ship_count` depends on `battles_json`, and that denominator is missing for most badge-bearing players.

Measured directly:

1. among visible players with non-empty `efficiency_json`, `80,519` are missing `battles_json`,
2. only `518` players with non-empty badge rows have `battles_json` present but still fail to produce any eligible Tier V+ ship with at least 5 PvP battles.

This means the current comparative field is primarily gating on missing denominator coverage, not on comparative quality.

## What Does Not Help

Lowering `EFFICIENCY_RANK_MIN_ELIGIBLE_SHIPS` alone does not materially improve coverage under the current denominator source.

Simulated current-model candidate pools:

1. `min eligible ships = 5`: population `403`
2. `min eligible ships = 3`: population `404`
3. `min eligible ships = 1`: population `405`

Conclusion:

The threshold value is not the bottleneck. The denominator source is.

## Recommended Direction

Rebuild the comparative-field gate around mapped Tier V+ efficiency badge rows, not around `battles_json` coverage.

### Revised principle

For the published sigma rank, breadth should still matter, but breadth should be measured from the badge-bearing ship pool Battlestats already stores reliably today.

That means:

1. keep the current percentile tiers,
2. keep the current `pvp_battles >= 200` floor,
3. keep the current `10%` unmapped-share protection,
4. replace the `battles_json`-derived `eligible_ship_count` gate with a badge-row-derived mapped-ship count gate.

## Recommended Gating Policy

### Candidate eligibility

Recommended candidate gate:

1. player is visible,
2. `pvp_battles >= 200`,
3. mapped Tier V+ badge-row count from `efficiency_json >= 5`,
4. total valid badge rows > 0,
5. unmapped share <= `10%`.

### Strength normalization

Recommended normalization:

1. keep the current badge point weights,
2. normalize by mapped Tier V+ badge-row count instead of `battles_json`-derived `eligible_ship_count`,
3. keep shrinkage, but apply it to the mapped badge-row count rather than the current denominator count.

This preserves the meaning of a player-level comparative summary built from stored WG badge evidence while removing the current dependence on missing ship-denominator hydration.

## Why `>= 5` mapped badge rows is the recommended starting point

Using the simulated badge-row denominator model:

1. `min mapped badge rows = 1`: population `69,348`
2. `min mapped badge rows = 3`: population `59,791`
3. `min mapped badge rows = 5`: population `53,580`

Tier counts under the same percentile thresholds:

### At `>= 1` mapped badge row

1. `III`: `17,351`
2. `II`: `10,409`
3. `I`: `4,855`
4. `E`: `2,081`

### At `>= 3` mapped badge rows

1. `III`: `14,882`
2. `II`: `9,034`
3. `I`: `4,173`
4. `E`: `1,806`

### At `>= 5` mapped badge rows

1. `III`: `13,394`
2. `II`: `8,020`
3. `I`: `3,760`
4. `E`: `1,616`

Recommendation:

Start with `>= 5` mapped badge rows.

Reasoning:

1. it preserves the repo’s original intent that comparative publication should reflect more than a single hot ship,
2. it already expands the candidate field from `403` to `53,580`,
3. it remains broad enough to feel like a profile rather than a single-ship award,
4. it uses data Battlestats already stores on `efficiency_json` instead of waiting on missing `battles_json` coverage.

## Fairness Position

### What is fair today

1. The percentile thresholds themselves are reasonable.
2. The `97th percentile` Expert rule is not inherently too strict.
3. The desire to avoid awarding a published comparative tier for one lucky badge row is valid.

### What is not fair today

1. The current field is too dependent on missing `battles_json` coverage.
2. Players with substantial stored badge evidence are excluded for operational-data reasons rather than comparative-quality reasons.
3. The published sigma rank currently reads as if it summarizes the badge population, but in practice it summarizes a tiny denominator-covered subset.

## Scope Boundary

In scope:

1. recalibrating the comparative-rank candidate gate,
2. changing the denominator source used for `eligible_ship_count` or its replacement,
3. recomputing normalized and shrunken strength from the revised denominator,
4. backfilling the published summary field after the model change,
5. updating docs and QA expectations for the revised field size.

Out of scope:

1. redesigning the sigma icon,
2. changing raw WG badge ingestion,
3. changing the player-detail fallback that already renders sigma from stored badge rows,
4. inventing a second comparative-rank taxonomy.

## Proposed Implementation Sequence

### Phase 1: Denominator Refactor

1. add a mapped Tier V+ efficiency badge row count derived from `efficiency_json`,
2. stop using `battles_json` as the primary publication gate for comparative efficiency rank,
3. refactor `normalized_badge_strength` to use mapped badge-row count.

### Phase 2: Summary Persistence

1. refresh `PlayerExplorerSummary` for the revised inputs,
2. recompute the comparative snapshot across the full visible field,
3. record the new field size and tier counts.

### Phase 3: Validation

1. verify the new candidate pool size is materially larger than the current `403`,
2. verify published tier counts remain consistent with the percentile thresholds,
3. verify raw Expert-badge holders are no longer excluded primarily because `battles_json` is absent,
4. spot-check players who currently have badge rows but no published sigma tier.

## Acceptance Criteria

1. Lowering the ship-count threshold alone is no longer the only available lever.
2. The comparative-field candidate pool is derived from badge-backed data coverage rather than mostly from `battles_json` presence.
3. The published field size expands materially beyond the current `403` visible candidates.
4. The published `E` tier continues to represent the top `3%` of the revised candidate pool.
5. The revised model preserves a breadth gate and does not collapse to single-ship recognition.

## Validation Plan

Required analysis after implementation:

1. total visible candidate pool size,
2. tier counts for `III`, `II`, `I`, `E`,
3. counts of raw Expert-badge players with any published tier,
4. counts of players suppressed by each remaining gate,
5. comparison versus the current-model baselines captured in this spec.

Required test coverage:

1. unit tests around the revised denominator builder,
2. snapshot tests for rank recomputation tier bands,
3. regression coverage for unmapped-share gating,
4. targeted API tests that confirm non-stale published tiers surface as expected.

## Final Recommendation

Do not spend implementation time tuning `EFFICIENCY_RANK_MIN_ELIGIBLE_SHIPS` from `5` to `3` or `1` under the current model.

That change does not address the real bottleneck.

Instead:

1. keep the percentile thresholds,
2. keep the current broad-profile intent,
3. move the denominator and breadth gate onto mapped Tier V+ badge rows from `efficiency_json`,
4. start with a `>= 5` mapped badge-row gate,
5. remeasure the field before considering any further percentile or shrinkage changes.
