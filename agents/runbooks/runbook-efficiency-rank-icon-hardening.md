# Runbook: Tiered Efficiency Rank Icon Hardening

_Drafted: 2026-03-16_

_Status: Implemented historical runbook_

This file remains useful as the rationale for the published tiered efficiency-rank contract and its hardening constraints. The tiered icon system, serializer payload caching, and partial-publication safeguards described here have since been implemented.

## Purpose

Replace the current top-third boolean icon with a ranked-style tiered icon system for eligible players, while carrying forward the durability and accuracy fixes identified in the prior hardening review.

The current implementation already computes a publishable percentile from stored efficiency inputs. The missing piece is the publication contract: instead of one boolean threshold, publish a tier band that behaves more like a ranked ladder.

## Assumption On Tier Names

The request listed the levels as `E`, `I`, `II`, `II`.

For implementation, treat that as:

1. `E` = `Expert`
2. `I` = `Grade I`
3. `II` = `Grade II`
4. `III` = `Grade III`

Reasoning:

1. the repo already uses the four-step WG badge ladder `Expert / Grade I / Grade II / Grade III`,
2. the current summary model already stores `expert_count`, `grade_i_count`, `grade_ii_count`, and `grade_iii_count`,
3. a repeated `II` would not yield a usable four-band publication scheme.

## Target Product Behavior

### Publication rule

For a publishable player, expose:

1. `efficiency_rank_percentile`
2. `efficiency_rank_tier`
3. `efficiency_rank_population_size`
4. `efficiency_rank_updated_at`

Keep `has_efficiency_rank_icon` during transition as a compatibility field derived from `efficiency_rank_tier is not null`.

### Visibility rule

Do not assign a visible tier to the entire eligible field.

Publish a tier only for players at or above the `50th percentile` of the eligible tracked population.

Reasoning:

1. the current signal is still based on peak badge rows, not a full all-skill performance model,
2. showing a badge for the entire ladder would overstate weak or noisy profiles,
3. a top-half publication floor is more conservative while still expanding beyond the current top-third boolean.

### Ranked-style color ladder

Use a four-band visual system that reads like a ladder rather than a yes/no award:

1. `Grade III`: bronze or copper icon
2. `Grade II`: silver or steel icon
3. `Grade I`: gold or amber icon
4. `Expert`: crimson icon with a stronger highlight treatment

Notes:

1. color tokens can be finalized in client work,
2. the important contract is that color intensity and prestige rise with tier,
3. `Expert` should look meaningfully rarer than `Grade I`, not just slightly redder.

## Rank Math

### Keep the existing score foundation

Retain the current per-player score pipeline already implemented in [server/warships/data.py](/home/august/code/archive/battlestats/server/warships/data.py):

1. eligible ship denominator from `battles_json`
2. rarity-weighted badge points `8 / 4 / 2 / 1`
3. opportunity normalization by eligible ship count
4. empirical-Bayes shrinkage toward the field mean
5. deterministic percentile assignment with average-rank tie handling

That existing foundation is good enough for tiering. The change should happen at the publication layer, not by replacing the scoring model.

### Definitions

For player $i$:

$$
R_i = 8E_i + 4G1_i + 2G2_i + G3_i
$$

Where:

1. $E_i$ = Expert badge rows counted into score math
2. $G1_i$ = Grade I rows
3. $G2_i$ = Grade II rows
4. $G3_i$ = Grade III rows

Let $S_i$ be the eligible ship count from `battles_json`.

Then the normalized badge strength is:

$$
N_i = \frac{R_i}{8S_i}
$$

Let $\bar{N}$ be the mean normalized strength across the eligible published field and let $k = 12$ remain the shrinkage constant already defined in code.

The shrunken strength remains:

$$
w_i = \frac{S_i}{S_i + k}
$$

$$
H_i = w_iN_i + (1 - w_i)\bar{N}
$$

Sort players by $H_i$ descending and `player_id` ascending for deterministic tie breaks. If a player's average rank in the sorted field is $r_i$ among population size $P$, keep the existing percentile formula:

$$
p_i = \frac{P - r_i}{P - 1}
$$

With the existing single-player fallback of `1.0` when $P \le 1$.

### Tier band function

Define the published tier function $T(p_i)$ as:

1. `null` when $p_i < 0.50$
2. `III` when $0.50 \le p_i < 0.75$
3. `II` when $0.75 \le p_i < 0.90$
4. `I` when $0.90 \le p_i < 0.97$
5. `E` when $p_i \ge 0.97$

This produces the intended ranked-style shape:

1. `Grade III` is the entry tier for players who are above field median,
2. `Grade II` is distinctly stronger but still reachable,
3. `Grade I` is narrow enough to feel prestigious,
4. `Expert` is reserved for the extreme upper tail.

### Why these cut points

1. `0.50` keeps the system from labeling the entire field while still moving beyond the current top-third boolean.
2. `0.75` creates a clean second band using an intuitive upper-quartile break.
3. `0.90` and `0.97` intentionally compress the upper ladder so the best bands stay rare, which is closer to how ranked-style prestige systems feel.
4. the bands are percentile-based, not raw-score-based, so they stay stable as the tracked population changes.

## Publication Gates

The tier should only publish if the player already passes the current eligibility and freshness gates:

1. not hidden
2. `pvp_battles >= 200`
3. denominator present from `battles_json`
4. at least `5` eligible ships
5. badge rows present
6. unmapped badge share at or below `10%`
7. snapshot freshness still valid relative to badge and battle inputs

If any gate fails, publish:

1. `efficiency_rank_percentile = null`
2. `efficiency_rank_tier = null`
3. `has_efficiency_rank_icon = false`
4. `efficiency_rank_population_size = null`
5. `efficiency_rank_updated_at = null`

## Data Contract Changes

### Summary model

Extend [server/warships/models.py](/home/august/code/archive/battlestats/server/warships/models.py) so `PlayerExplorerSummary` stores:

1. `efficiency_rank_tier = CharField(max_length=4, null=True, blank=True)`

Keep existing fields:

1. `efficiency_rank_percentile`
2. `has_efficiency_rank_icon`
3. `efficiency_rank_population_size`
4. `efficiency_rank_updated_at`

Compatibility rule:

1. `has_efficiency_rank_icon` becomes a derived persistence convenience for current callers,
2. after client migration, it can remain for backward compatibility or be retired in a later cleanup.

### API shape

Expose `efficiency_rank_tier` in the player payload.

Optional follow-up field if the client wants less mapping logic:

1. `efficiency_rank_label` with values `Expert`, `Grade I`, `Grade II`, `Grade III`

Do not persist color names in the backend. Color is presentation, tier is contract.

## Hardening Work That Must Carry Forward

The previous runbook findings remain valid and should be treated as prerequisites for the tier rollout, because a more complex publication contract makes partial or interrupted publication even riskier.

### Priority 1: Make snapshot publication atomic

Current risk in [server/warships/data.py](/home/august/code/archive/battlestats/server/warships/data.py): the recompute path clears published fields before bulk-writing new rows.

Required change:

1. compute candidate scores and tiers in memory first,
2. open `transaction.atomic()` for the write phase,
3. perform the reset and bulk update inside the same transaction,
4. if the run fails before commit, the previous published tier snapshot remains intact.

### Priority 2: Prevent partial-population publication from `--limit`

Current risk in [server/warships/management/commands/backfill_player_efficiency_ranks.py](/home/august/code/archive/battlestats/server/warships/management/commands/backfill_player_efficiency_ranks.py): a limited run can still publish live fields.

Required change:

1. make `--limit` analysis-only by default,
2. or require an explicit `--publish-partial` override,
3. or reject `--limit` when publication is requested.

Tier publication must never be computed from a knowingly truncated field unless the operator opts into that behavior deliberately.

### Priority 3: Cache the published payload in the serializer

Current risk in [server/warships/serializers.py](/home/august/code/archive/battlestats/server/warships/serializers.py): the serializer rebuilds the same published payload once per field.

Required change:

1. memoize the payload once per serialized object,
2. add `efficiency_rank_tier` to that same cached payload,
3. keep output semantics unchanged apart from the new tier field.

## Implementation Order

1. add atomic publication to the snapshot writer,
2. guard `--limit` from publishing partial snapshots,
3. add `efficiency_rank_tier` to the summary model and migration,
4. replace boolean threshold assignment in recompute logic with tier assignment from percentile bands,
5. keep `has_efficiency_rank_icon = (efficiency_rank_tier is not null)` during transition,
6. expose `efficiency_rank_tier` in the serializer and API,
7. update the player detail header to render tier-specific color treatment,
8. cache the serializer payload to avoid repeated rebuilds,
9. update the analysis report to emit counts by tier and suppression reason.

## Validation

### Backend tests

1. percentile-to-tier boundary tests at exactly `0.50`, `0.75`, `0.90`, and `0.97`
2. tie-group tests where a tie straddles a tier boundary and all tied rows receive the same percentile and tier
3. stale snapshot tests proving percentile and tier both suppress together
4. unmapped-badge gate tests proving percentile and tier both suppress together
5. transaction rollback test proving prior published tier fields survive a failed publish
6. command test proving `--limit` does not publish live tier data by default

### API tests

1. fresh player detail payload includes percentile, tier, population size, and update timestamp
2. stale payload returns `null` for percentile and tier and `false` for boolean icon
3. compatibility field `has_efficiency_rank_icon` stays aligned with `efficiency_rank_tier`

### Client checks

1. `Grade III` renders the entry color treatment
2. `Grade II` renders a stronger treatment than `Grade III`
3. `Grade I` renders above `Grade II`
4. `Expert` is visually distinct and clearly the rarest top tier
5. no icon renders when `efficiency_rank_tier` is `null`

## Exit Criteria

1. the published contract expresses a tiered ranked-style ladder instead of a single top-third boolean
2. the tier math is deterministic, percentile-based, and documented in code comments or tests
3. interrupted publishes cannot erase the previous live snapshot
4. partial runs cannot silently publish misleading tier assignments
5. the player detail payload exposes a stable tier field the client can style without reverse-engineering percentiles
