# Feature Spec: Landing Player List Efficiency Sigma Icon

_Drafted: 2026-03-17_

## Goal

Add the Battlestats efficiency sigma icon to landing-page player lists when the landing payload already contains enough published efficiency-rank state to render it without additional browser fetches.

The desired product outcome is:

1. landing player lists can show the efficiency sigma where appropriate,
2. the landing page reuses the same published efficiency contract already used on player detail and clan rows,
3. the rollout stays bounded to landing player lists rather than reopening the underlying percentile model.

## Current State

### Existing landing player list surfaces

The landing page currently renders player-name lists in [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx):

1. featured/random landing players,
2. best landing players,
3. recent landing players.

Those row-like list surfaces already support several compact player markers:

1. ranked star,
2. PvE robot,
3. sleepy bed,
4. clan-battle shield,
5. hidden-account icon.

### Current efficiency state on other surfaces

The efficiency sigma currently behaves differently by surface:

1. player-detail header shows any published efficiency tier,
2. dense clan-roster row surfaces show only `E` to control visual noise,
3. landing player lists do not currently publish or render efficiency-rank state at all.

### Current backend landing payload shape

Landing rows built in [server/warships/landing.py](server/warships/landing.py) currently publish:

1. win-rate fields,
2. PvE flag,
3. sleepy flag,
4. ranked flag and highest league,
5. clan-battle flag and win rate.

They do not currently publish:

1. `efficiency_rank_tier`,
2. `has_efficiency_rank_icon`,
3. `efficiency_rank_percentile`,
4. `efficiency_rank_population_size`,
5. `efficiency_rank_updated_at`.

## Why This Needs A Separate Landing Spec

The core efficiency-rank percentile contract already exists.

What is missing here is not score math but surface rollout discipline:

1. landing rows are dense, compact list items,
2. landing payloads are cached and reused broadly,
3. the rollout should not introduce extra hydration or new fetch lanes,
4. the icon-density rule on landing should be chosen deliberately instead of inheriting player-detail header behavior by accident.

## Constraint Summary

From current repo doctrine and shipped behavior:

1. prefer additive payload changes,
2. reuse existing published efficiency fields rather than recomputing percentile logic locally,
3. avoid new browser-triggered fetches just for icons,
4. keep dense list surfaces visually bounded,
5. validate payload and UI changes together.

## Recommended Product Behavior

### Surface scope

Roll this out only to landing player lists:

1. random/featured landing players,
2. best landing players,
3. recent landing players.

Do not expand this tranche into explorer rows or other list surfaces.

### Visibility rule

Use the row-surface rule already established on clan rosters:

1. resolve the published efficiency tier from `efficiency_rank_tier` and `has_efficiency_rank_icon`,
2. render the sigma on landing player lists only when the resolved tier is `E`.

Reasoning:

1. landing player lists are dense row-like surfaces, not a dedicated player header,
2. the current clan-row precedent already limits row clutter by showing only `E`,
3. keeping landing aligned with clan-row density rules is less surprising than showing all tiers on landing while showing only `E` on clan rows.

### Tooltip behavior

Reuse the existing `EfficiencyRankIcon` tooltip contract so landing rows do not invent new copy.

That means landing should preserve:

1. tier label,
2. percentile wording,
3. tracked-player population wording.

## Backend Contract Recommendation

### Payload additions

Extend landing player rows additively with the same published efficiency fields already used elsewhere:

1. `efficiency_rank_percentile`
2. `efficiency_rank_tier`
3. `has_efficiency_rank_icon`
4. `efficiency_rank_population_size`
5. `efficiency_rank_updated_at`

These should be populated from the existing published efficiency helper, not by rebuilding percentile logic inside landing.

### Data source

Use the existing backend publication helper:

1. `_get_published_efficiency_rank_payload(player)`

Landing row construction should merge that helper output into each landing row during serialization, alongside the current ranked/PvE/clan-battle flags.

### Hidden-player behavior

Hidden players should not show the efficiency sigma on landing rows.

The published helper already suppresses icon publication for hidden players, so landing should preserve that behavior rather than adding a surface-specific override.

## Frontend Recommendation

### PlayerSearch landing row contract

Extend the local landing-row type in [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx) to accept the additive efficiency fields.

Then, in the landing player-name row:

1. resolve the effective efficiency tier using the shared `resolveEfficiencyRankTier(...)` helper,
2. render `EfficiencyRankIcon` only when the resolved tier is `E`,
3. keep the icon in the existing compact inline icon row with ranked/PvE/sleepy/clan-battle markers.

### No new fetches

Do not add:

1. a dedicated landing efficiency endpoint,
2. per-row player-detail fetches,
3. client polling for efficiency hydration on landing.

Landing should remain a pure consumer of the existing landing payload.

## Options Considered

### Option A: show all published tiers on landing rows

Pros:

1. matches player-detail header semantics,
2. makes more published efficiency information visible.

Cons:

1. increases icon clutter on a dense landing surface,
2. diverges from the current clan-row precedent,
3. risks reducing the salience of the highest-signal `E` marker.

Verdict:

Not recommended for the first landing tranche.

### Option B: show only `E` on landing rows

Pros:

1. consistent with clan-row density rules,
2. bounded visual impact,
3. simplest rollout for a compact surface.

Cons:

1. omits non-Expert published efficiency tiers from landing.

Verdict:

Recommended.

### Option C: add landing-specific simplified tooltip or glyph

Pros:

1. could optimize for even smaller row surfaces.

Cons:

1. creates another contract to maintain,
2. adds surface-specific drift without strong need,
3. weakens consistency with already-shipped efficiency UI.

Verdict:

Not recommended.

## Suggested Implementation Sequence

### Phase 1: Backend Payload

1. extend landing player row serialization additively with published efficiency fields,
2. update landing endpoint tests to verify the new fields are present and stable.

### Phase 2: Client Rendering

1. extend the landing player row type in `PlayerSearch.tsx`,
2. import and reuse `EfficiencyRankIcon` and `resolveEfficiencyRankTier`,
3. render the sigma only for resolved `E` rows.

### Phase 3: Validation

1. focused backend tests for landing and recent player payloads,
2. focused client tests for landing player-row icon rendering,
3. manual landing-page verification with at least one `E` player and one non-`E` efficiency player.

## Acceptance Criteria

1. Landing player payloads expose additive efficiency-rank fields from the published backend contract.
2. Landing player lists render the sigma icon for qualifying `E` rows.
3. Landing player lists do not render the sigma for non-`E` published efficiency tiers.
4. Hidden players do not render the sigma on landing rows.
5. No new browser request is added solely for landing efficiency icons.
6. The tooltip/copy remains the shared `EfficiencyRankIcon` contract.

## Validation Plan

Focused backend validation should cover:

1. landing players payload includes published efficiency fields,
2. recent landing players payload includes published efficiency fields,
3. hidden or unpublished rows remain suppressed.

Focused client validation should cover:

1. landing player rows render `E` sigma icons,
2. non-`E` rows do not render the sigma,
3. existing ranked/PvE/sleepy/clan-battle icon rendering remains intact.

## Non-Goals

1. do not change the underlying efficiency percentile model,
2. do not widen clan-row visibility beyond its current `E` rule,
3. do not add hydration polling or background warm-state UI on landing,
4. do not redesign the sigma icon.
