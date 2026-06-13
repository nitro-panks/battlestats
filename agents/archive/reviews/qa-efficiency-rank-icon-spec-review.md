# QA Review: Efficiency Rank Icon Spec

_Reviewed: 2026-03-16_

## Scope Reviewed

- [agents/work-items/efficiency-rank-icon-spec.md](agents/work-items/efficiency-rank-icon-spec.md)
- [agents/work-items/efficiency-badges-player-story-spec.md](agents/work-items/efficiency-badges-player-story-spec.md)
- [agents/reviews/qa-player-efficiency-badges-runbook-review.md](agents/reviews/qa-player-efficiency-badges-runbook-review.md)
- [agents/knowledge/wows-encyclopedia-surface.md](agents/knowledge/wows-encyclopedia-surface.md)
- [server/warships/data.py](server/warships/data.py)
- [server/warships/models.py](server/warships/models.py)

## QA Verdict

Approved as a planning artifact for a first implementation tranche.

The revised spec now keeps the icon statistically grounded, clearly separates it from WG ship-level badges, and defines a backend contract that is narrow enough to implement and verify.

## What QA Confirmed

1. The spec no longer treats `randoms_json` as a full opportunity denominator and correctly points denominator math at `battles_json`.
2. The spec keeps the first tranche bounded to the player detail header instead of over-scoping into clan and explorer rows immediately.
3. The spec makes percentile semantics explicit enough for deterministic backend tests.
4. The spec defines a freshness contract between player badge refreshes and the population percentile snapshot.
5. The spec now includes a publication gate for unmapped badge rows so incomplete metadata suppresses the icon rather than silently depressing player rank.

## QA Focus Areas For Implementation

1. Correct denominator construction from stored `battles_json`.
2. Stable rarity-weight mapping and shrinkage behavior.
3. Deterministic percentile ordering, tie handling, and inclusive threshold application.
4. Strict suppression when the population snapshot is stale or unmapped badge share exceeds the publication gate.
5. Conservative UI copy that says `tracked players` and does not imply a WG-awarded player badge.

## Required QA Checks

### Data-shape checks

- denominator math uses `battles_json`, not `randoms_json`
- `eligible_ship_count` counts only Tier V+ ships with the chosen minimum battle floor
- unmapped badge rows are tracked separately as `badge_rows_unmapped`

### Rank-contract checks

- hidden players produce `null` percentile and no icon
- ineligible players produce `null` percentile and no icon
- players with unmapped badge share above the configured threshold produce `null` percentile and no icon
- percentile ordering is deterministic under ties
- icon threshold is applied after percentile calculation, not before

### Freshness checks

- stale or absent population snapshots suppress the icon
- newly refreshed player badge data does not imply a fresh percentile until the population rank job reruns

### UI checks

- tooltip copy distinguishes the Battlestats icon from WG ship badges
- tooltip copy uses `tracked players` or equivalent language
- the first tranche stays on the player detail header only

## Residual Risks

1. The tracked Battlestats population is crawler-shaped, not the full WoWS population, so percentile language must remain conservative.
2. Badge rows reflect peak qualifying performances, not average consistency, so strong icon holders may still have uneven overall ship results.
3. If future row-level surfaces reuse this signal without a denormalized summary lane, contract drift or overfetch risk will return.

## QA Recommendations

1. Treat the first distribution review as a release gate before locking the `67th percentile` threshold.
2. Capture the share of players suppressed by the unmapped-badge gate during early backfills so the metadata coverage problem is visible.
3. Re-review the contract before expanding the icon into clan rows or explorer rows.

## Exit Criteria

1. The denominator source is implemented from actual stored repo data.
2. Percentile and icon publication follow explicit eligibility, tie, freshness, and unmapped-data rules.
3. The UI copy remains conservative and distinct from WG badge semantics.
4. The first release surface stays limited to player detail until denormalized row-level support exists.

## Final QA Position

Approved for the planned first tranche.

The remaining work is implementation detail and threshold calibration, not planning completeness.
