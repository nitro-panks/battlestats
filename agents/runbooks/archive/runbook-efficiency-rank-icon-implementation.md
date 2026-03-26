# Runbook: Efficiency Rank Icon Implementation

_Drafted: 2026-03-16_

_Status: Implemented historical runbook_

This file captures the original rollout plan for the first efficiency-rank tranche. The backend snapshot command, player-detail payload contract, and header icon work described here have since landed, with later follow-up expanding the same rank contract onto additional player surfaces.

## Goal

Implement the first tranche of the player-level Battlestats efficiency-rank icon.

This tranche must complete four outcomes together:

1. compute per-player efficiency-rank input metrics from stored `efficiency_json` and `battles_json`,
2. compute a field-wide percentile snapshot with explicit eligibility and suppression rules,
3. expose the percentile contract on the player detail payload,
4. render the icon on the player detail header only.

The tranche also needs an analysis/reporting step so the initial percentile threshold can be checked against the real tracked distribution.

## Scope

In scope:

- backend summary fields on `PlayerExplorerSummary`
- backend helpers for efficiency-rank input derivation and percentile publication
- resumable or repeatable management command for snapshot recomputation
- threshold-analysis output from that command
- player detail API exposure
- player detail header icon and tooltip
- focused backend and client regression coverage

Out of scope:

- clan rows
- player explorer rows
- landing surfaces
- alternate icon artwork beyond a compact placeholder treatment

## Preconditions

Before editing code:

1. re-read the current spec and QA review because the planning files may have been externally edited,
2. confirm the player detail route already sources denormalized metrics from `PlayerExplorerSummary`,
3. confirm the current efficiency badge lane still stores `ship_tier` on mapped rows and uses `battles_json` as the full ship-pool source.

## Implementation Steps

### 1. Extend the denormalized summary schema

Add efficiency-rank fields to `PlayerExplorerSummary` for:

- `eligible_ship_count`
- `efficiency_badge_rows_total`
- `badge_rows_unmapped`
- `expert_count`
- `grade_i_count`
- `grade_ii_count`
- `grade_iii_count`
- `raw_badge_points`
- `normalized_badge_strength`
- `shrunken_efficiency_strength`
- `efficiency_rank_percentile`
- `has_efficiency_rank_icon`
- `efficiency_rank_population_size`
- `efficiency_rank_updated_at`

Add a migration for those fields.

### 2. Add backend derivation helpers

Implement helpers in `server/warships/data.py` for:

- denominator construction from `battles_json`
- mapped badge counting from `efficiency_json`
- unmapped badge tracking
- raw rarity-weighted point calculation with `Expert=8`, `Grade I=4`, `Grade II=2`, `Grade III=1`
- opportunity normalization against `eligible_ship_count * 8`
- eligibility evaluation:
  - `is_hidden = False`
  - `pvp_battles >= 200`
  - `eligible_ship_count >= 5`
  - `efficiency_json` present and non-empty
- publication suppression when unmapped badge share exceeds `10%`

Keep percentile computation separate from the per-player summary builder so normal player refreshes do not attempt a full population scan.

### 3. Populate local rank inputs during summary refresh

Update `build_player_summary()` and `refresh_player_explorer_summary()` so ordinary summary refreshes persist the efficiency-rank input fields listed above.

Rules:

- hidden players should retain `null` publication fields and `false` icon state,
- ordinary summary refresh should not overwrite a previously computed percentile snapshot unless the new command explicitly recomputes it,
- summary staleness checks should refresh when the local rank-input fields drift from current player JSON.

### 4. Add a field-wide percentile snapshot command

Create a management command to recompute the full efficiency-rank snapshot.

Required behavior:

- refresh or create `PlayerExplorerSummary` rows for players in scope before percentile publication,
- compute field mean normalized strength from eligible players,
- compute shrunken strength with `k = 12`,
- compute percentile with descending order, average-rank tie handling, and inclusive threshold comparison,
  - use the average of the 1-based tied ranks,
  - convert rank to percentile with a deterministic top-is-1.0 scale,
- set `has_efficiency_rank_icon = True` when percentile is at or above the configured threshold,
- null out percentile publication fields for hidden, ineligible, stale, or unmapped-gated players,
- store `efficiency_rank_population_size` and `efficiency_rank_updated_at` on published rows,
- emit an analysis summary that reports:
  - eligible population size,
  - qualifying icon count and share,
  - percentile cut line used,
  - p50, p67, p75, p90 of shrunken strength,
  - suppressed counts by reason where practical.

Preferred command shape:

- repeatable and safe to rerun,
- optional `--limit` for local smoke tests,
- optional `--threshold` override for analysis,
- optional `--report-file` JSON output,
- optional `--skip-refresh` when summary inputs are already current.

The command does not need a durable checkpoint unless implementation naturally benefits from one.

Threshold review gate:

- the command output must be reviewed after the first real-data run before the default threshold is treated as locked,
- capture the observed qualifying share at `0.67`,
- if the qualifying share is materially broader or narrower than expected, update the threshold before relying on the icon for UI interpretation.

### 5. Publish the contract to player detail

Expose on the player detail payload:

- `efficiency_rank_percentile`
- `has_efficiency_rank_icon`
- `efficiency_rank_population_size`
- `efficiency_rank_updated_at`

Only expose published values when the snapshot is fresh enough for UI use. If the snapshot is stale, return `null` percentile and `false` icon.

### 6. Add the first UI surface

Render the icon in the player detail header alongside the existing compact status icons.

UI rules:

- only show when `has_efficiency_rank_icon` is true,
- keep the icon visually distinct from WG ship badge chips,
- tooltip copy must say this is a Battlestats tracked-player rank, not a WG-awarded badge,
- include the percentile in the tooltip when available.

### 7. Add focused tests

Backend tests must cover:

- denominator math uses `battles_json`, not `randoms_json`
- unmapped badge rows are counted and can suppress publication
- hidden players get null percentile and no icon
- empty or missing badge rows are ineligible
- percentile ordering is descending and deterministic under ties
- inclusive threshold behavior at the cut line
- player detail response exposes the new fields
- stale snapshot suppression hides the published contract

Client tests must cover:

- header icon renders when the API payload sets the flag,
- tooltip copy distinguishes Battlestats rank from WG badge semantics,
- icon is absent when the flag is false.

## Execution Steps

1. Implement the backend summary fields, helpers, and command.
2. Implement the player detail payload changes.
3. Implement the player detail header icon.
4. Run focused Django tests covering summary math, views, and management command behavior.
5. Run focused client tests covering the player detail header icon.
6. Apply the migration in the runtime environment.
7. Execute the efficiency-rank snapshot command against real tracked data.
8. Review the analysis output and confirm whether the default `67th percentile` threshold is still sensible.

## Validation Gate

Do not consider the tranche complete until all of the following are true:

1. player detail payload contains the new efficiency-rank fields,
2. the player detail header renders the icon only for qualifying players,
3. the snapshot command completes successfully on the current tracked dataset,
4. the analysis output reports the real qualifying share at the chosen threshold,
5. focused backend and client tests pass.

## Rollback Notes

If the percentile snapshot logic produces obviously unstable or inflated results:

1. keep the stored input fields,
2. suppress `has_efficiency_rank_icon` publication,
3. leave the UI icon hidden until the threshold or shrinkage parameters are recalibrated.

Migration notes:

1. new nullable summary fields should default to `null` or `false` as appropriate so the migration remains backwards compatible for existing rows,
2. if the migration succeeds but the snapshot command has not run yet, the UI must continue to behave as if no icon is available,
3. if rollback is required, revert the code path first so player detail stops reading the new contract before removing the fields.

Operational notes:

1. log or report suppressed-player counts by reason so unmapped-metadata issues are visible,
2. preserve the analysis report from the first production-like run for threshold review,
3. treat large shifts in eligible population size or qualifying share as a re-review trigger.
