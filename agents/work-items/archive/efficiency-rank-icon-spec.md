# Feature Spec: Player Efficiency Rank Icon

_Drafted: 2026-03-16_

## Goal

Add a compact player-level UI icon that marks players whose stored Efficiency Badge profile ranks near the top of the observed Battlestats field.

The icon is not a replacement for WG ship-level Efficiency Badges. It is a Battlestats summary marker that answers a different question:

- is this player's badge profile unusually strong compared with other tracked players?

For early UI exploration, the placeholder glyph can be the requested `&#x2140;` character. Final visual treatment can change later, but the statistical contract should be settled first.

## Why This Needs A Separate Rank Model

The repo already stores WG ship-level badge rows in `Player.efficiency_json`.

Those rows are:

- per ship,
- ordinal rather than continuous,
- based on a player's best qualifying single-battle result,
- sparse for many players,
- influenced by both skill and the number of ships a player has touched.

That means a naive ranking like `4 * Expert + 3 * Grade I + 2 * Grade II + 1 * Grade III` is directionally useful, but not strong enough on its own for a cross-player icon because it has two major biases:

1. breadth bias: players with many eligible ships get more opportunities to accumulate raw points,
2. tiny-sample bias: a player with only one or two exceptional badge rows can look stronger than a player with a broad high-end profile.

The icon needs a field-relative score that handles both.

## Existing Inputs In Repo

Current stored data relevant to this feature:

- `Player.efficiency_json`
  - per-ship badge rows with `top_grade_class`, `top_grade_label`, `ship_name`, `ship_tier`, `ship_type`
- `Player.battles_json`
  - stored per-ship battle rows; this is the reliable local ship-pool source for denominator math
- `Player.randoms_json`
  - top-random-ships presentation slice; useful for joins and UI context, but not a safe full denominator source
- `Player.pvp_battles`
- `Player.is_hidden`
- `PlayerExplorerSummary.player_score`

Important current semantics:

- WG badge classes already have a known ordinal mapping:
  - `1 = Expert`
  - `2 = Grade I`
  - `3 = Grade II`
  - `4 = Grade III`
- WG badge thresholds are percentile-based by ship and condition, not absolute performance buckets.
- `efficiency_json` is a peak-signal lane, not an average-skill lane.

Important repo constraint:

- `randoms_json` is not the player's full random ship pool,
- the rank model must not use `randoms_json` as the denominator source.

## Product Shape

### Primary UI behavior

Add an efficiency-rank icon to the player detail header first.

Clan rows, explorer rows, and landing surfaces are follow-up work after the percentile contract is denormalized onto an appropriate summary surface.

Suggested tooltip copy for v1:

- `Top Battlestats efficiency rank based on stored WG badge profile.`

The tooltip should also expose the actual percentile or band, for example:

- `Efficiency rank: 81st percentile among eligible tracked players.`

### Initial display rule

Do not show the icon for every ranked player. Show it only when the player clears a percentile threshold within the eligible field.

Working starting point:

- show icon for players at or above the `67th percentile`

Reasoning:

- it matches the user's initial `top 33%` idea,
- it is easy to explain,
- it leaves room to tighten later if the population clusters too high.

Important rule:

- store a numeric percentile, not just a boolean icon flag,
- the icon threshold should be configurable after observing the actual distribution.

## Statistical Options Considered

### Option A: Raw linear badge sum

Example:

- `Expert = 4`
- `Grade I = 3`
- `Grade II = 2`
- `Grade III = 1`

Pros:

- simple,
- explainable,
- easy to debug.

Cons:

- rewards players with more ships much more than players with narrower but stronger quality,
- unstable for low-sample players,
- does not distinguish rarity strongly enough.

Verdict:

- useful as a diagnostic baseline, not the final ranking score.

### Option B: Average badge strength only

Example:

- compute average points per badge-bearing ship.

Pros:

- reduces breadth bias.

Cons:

- massively over-rewards tiny samples,
- ignores how much of the player's ship pool actually earned strong badges.

Verdict:

- not sufficient for the icon.

### Option C: Raw percentile rank of linear sum

Example:

- rank players by raw weighted sum and use percentile.

Pros:

- still simple,
- produces a field-relative number.

Cons:

- breadth bias remains inside the raw score,
- percentile alone does not fix the underlying metric.

Verdict:

- better than a fixed threshold, still not good enough.

### Option D: Opportunity-normalized, rarity-weighted, empirical-Bayes score

This is the recommended approach.

Pros:

- respects badge rarity,
- normalizes for player opportunity set,
- shrinks tiny samples toward the field mean,
- produces a stable percentile rank for UI use.

Cons:

- more complex to explain than a raw sum,
- requires one population pass to calibrate field mean and thresholds.

Verdict:

- best fit for a player-vs-field icon.

## Recommended Rank Model

### 1. Define the eligible field

Include only players who satisfy all of the following:

- `is_hidden = False`
- `pvp_battles >= 200`
- `efficiency_json` is present
- `battles_json` is present
- at least `5` eligible random-battle ships in the denominator set

Rationale:

- hidden players should not participate in a public comparative icon,
- very low-battle players do not provide a stable enough opportunity set,
- percentile rank needs a minimally comparable pool.

### 2. Define eligible ships for each player

Use a player-level denominator set based on `battles_json` rows where:

- `ship_tier >= 5`
- `pvp_battles >= 5`

Reasoning:

- Tier V+ matches the WG badge eligibility lane,
- a small battle floor reduces denominator inflation from single-touch ships,
- `battles_json` is the stored full ship-pool lane, while `randoms_json` is only a top-ships slice.

Recommended derived value:

- `eligible_ship_count`

Fallback rule:

- if `battles_json` is missing, malformed, or stale, do not publish a percentile for that player,
- do not silently substitute `randoms_json` as the denominator.

### 3. Convert badge classes into rarity-weighted points

Recommended v1 weights:

- `Expert = 8`
- `Grade I = 4`
- `Grade II = 2`
- `Grade III = 1`

Why not just `4/3/2/1`:

- badge rarity is highly nonlinear,
- `Expert` is much rarer than `Grade I`, which is much rarer than `Grade III`,
- doubling weights gives a better first-order approximation of rarity without making the model too opaque.

Recommended derived value:

- `raw_badge_points = 8 * expert_count + 4 * grade_i_count + 2 * grade_ii_count + 1 * grade_iii_count`

Metadata inclusion rule:

- include a badge row in score math only when `ship_tier` is present and `ship_tier >= 5`,
- rows lacking tier metadata should be tracked as `badge_rows_unmapped`,
- unmapped badge rows may still appear in raw badge UI, but they must not affect percentile score math.

Publication gate for incomplete badge metadata:

- publish percentile and icon only when either:
  - `badge_rows_unmapped = 0`, or
  - `badge_rows_unmapped / total_badge_rows <= 0.10`
- if unmapped badge share exceeds `10%`, emit `null` percentile and `false` icon until ship metadata coverage is repaired.

### 4. Opportunity-normalize the score

Compute:

- `max_possible_points = eligible_ship_count * 8`
- `normalized_badge_strength = raw_badge_points / max_possible_points`

Interpretation:

- `0.0` means no badge signal across the eligible ship set,
- `1.0` would mean every eligible ship is `Expert`, which is effectively unreachable.

This step controls raw breadth bias.

### 5. Shrink low-sample players toward the field mean

Use empirical-Bayes style shrinkage:

- `shrunken_strength = (eligible_ship_count / (eligible_ship_count + k)) * normalized_badge_strength + (k / (eligible_ship_count + k)) * field_mean_strength`

Recommended starting constant:

- `k = 12`

Interpretation:

- players with only a few eligible ships are pulled toward the field mean,
- players with broader ship coverage are allowed to separate more clearly.

### 6. Rank players by percentile

For the eligible field, compute:

- `efficiency_rank_percentile`

Recommended semantics:

- higher is better,
- `0.81` means the player outranks roughly `81%` of eligible tracked players on this metric.

Required deterministic rules:

- ineligible players receive `null` percentile and `false` icon,
- empty `efficiency_json` is not eligible,
- percentile uses only eligible players with non-null shrunken score,
- ties use average-rank handling,
- icon threshold comparison is inclusive.
- players failing the unmapped-badge publication gate receive `null` percentile and `false` icon even if their partial score would otherwise qualify.

### 7. Icon threshold

Recommended initial rule:

- show icon when `efficiency_rank_percentile >= 0.67`

Recommended calibration rule after first real distribution review:

- if more than roughly half of visible active players cluster tightly above the cut line, tighten to `0.75`,
- if too few players qualify and the icon becomes noise-level rare, loosen slightly but do not go below `0.60`.

Publication rule:

- percentile and icon should be hidden when the population summary snapshot is stale or absent,
- do not publish an old field percentile against a newly refreshed player badge record without explicitly accepting that lag in the contract.

## Recommended Derived Fields

Whether computed on read or denormalized later, the spec should target these outputs:

- `eligible_ship_count`
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

Recommended publication fields for v1:

- `efficiency_rank_percentile`
- `has_efficiency_rank_icon`
- `efficiency_rank_population_size`
- `efficiency_rank_updated_at`

## Interpretation Rules

The icon should communicate:

- this player has a strong efficiency badge profile relative to the tracked field.

The icon should not imply:

- this player is top-tier overall in every mode,
- this player is necessarily broad rather than specialized,
- this is a WG-awarded player-level badge,
- this is an average-win-rate or average-skill rank.

Recommended concise explanatory copy:

- `This icon summarizes Battlestats' field-relative rank from stored WG Efficiency Badges across eligible Tier V+ random ships.`

## Data And Refresh Recommendation

For the first implementation tranche, do not calculate this rank ad hoc in the browser.

Recommended backend shape:

- compute the rank from stored `efficiency_json` plus stored `battles_json`,
- expose percentile and icon flag in the player payload or a shared summary payload,
- refresh the field distribution in a backfill or scheduled summary lane rather than recalculating the whole population on every page load.

Freshness rule:

- the percentile contract should come from a population summary snapshot with its own `updated_at`,
- the player UI should suppress the icon when that population snapshot is stale,
- a player's badge refresh can precede percentile recomputation, but the UI should not pretend the percentile is current until the population job reruns.

Recommended later persistence options:

- add fields to `PlayerExplorerSummary`, or
- add a dedicated denormalized player-rank summary model if more percentile-style icons accumulate.

First-tranche contract recommendation:

- publish the icon on player detail only,
- defer clan-member and explorer-row exposure until percentile fields are denormalized onto `PlayerExplorerSummary` or an equivalent summary lane.

## UI Recommendation

### Placement

Recommended first surface:

- player detail header only

Deferred surfaces:

- clan member list rows,
- player explorer rows,
- landing summaries.

### Visual rule

The icon should be visually distinct from WG ship badge chips.

Reasoning:

- ship badges already mean a specific WG ship-level accomplishment,
- this new icon is a Battlestats comparative summary marker.

### Tooltip content

Minimum tooltip content:

- icon meaning,
- percentile,
- conservative wording about badge-based ranking.

Example:

- `⅀ Efficiency rank icon`
- `81st percentile among eligible tracked players`
- `Based on stored WG Efficiency Badges across eligible Tier V+ random ships`

## Validation Plan

### Distribution review

Before implementation locks the icon threshold, inspect:

- field mean,
- median,
- upper quartile,
- percentage of zero-badge or near-zero players,
- number of players above the proposed `67th percentile` threshold.

This is required because the observed field may be zero-inflated or crawler-biased.

Additional required review:

- fraction of players excluded due to missing `battles_json`,
- fraction of badge rows excluded as `badge_rows_unmapped`,
- proportion of players with fresh badges but stale or missing population percentile snapshot.

### Sanity cases

The spec should be validated against at least these player shapes:

1. a broad veteran with many Tier V+ ships and many badge rows,
2. a narrow specialist with only a few strong badge rows,
3. a high-volume player with weak badge coverage,
4. a low-volume player who should fail eligibility,
5. a hidden player who should never receive the public icon.
6. a player with badge rows but incomplete ship metadata who should remain visible but partially excluded from rank math.

### Test requirements for later implementation

- badge-point mapping is stable,
- denominator ship filtering is stable,
- shrinkage reduces tiny-sample inflation,
- percentile ordering is deterministic,
- icon threshold is applied after percentile computation,
- hidden and ineligible players do not receive the icon.

## Risks

1. Crawl bias: the tracked field is not the full game population, so percentile language must say `tracked players` or equivalent.
2. Peak-signal distortion: badge rows reflect best qualifying battles, so the icon can overstate average consistency if copy is too bold.
3. Threshold drift: a `top 33%` cut may be too loose or too tight depending on the observed distribution.
4. Opportunity-set mismatch: if `battles_json` is missing or incomplete, normalized scores can become unstable.
5. Metadata mismatch: badge rows without tier metadata can silently distort numerator math if their handling is implicit.
6. Snapshot lag: player-level badge refreshes can outrun the population percentile snapshot unless freshness is explicit.

## Recommendation Summary

Use a rarity-weighted badge score, normalize it by each player's eligible Tier V+ random ship pool, shrink it toward the field mean for low-sample players, and then rank by percentile across the eligible tracked field.

This yields a player-level icon that is:

- more statistically honest than a raw badge sum,
- more stable than a plain average,
- still explainable enough for product and QA.

## Acceptance Criteria

1. The spec clearly separates WG ship badges from the new Battlestats player-level icon.
2. The recommended model addresses both breadth bias and tiny-sample bias.
3. The spec defines a field-relative percentile, not just a raw score.
4. The icon threshold is configurable and not hard-coded as permanent truth.
5. The copy and tooltip guidance stay conservative about what the icon means.
6. The spec identifies the backend inputs already available in the repo.
7. The denominator source is implementable from actual stored repo data and not a truncated presentation slice.
8. The first release surface and percentile publication rules are explicit and testable.
