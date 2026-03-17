# Feature Spec: Player Detail Efficiency Header E-Only Alignment

_Drafted: 2026-03-17_

## Goal

Revert the player-detail header efficiency icon behavior so the header only shows the Battlestats sigma when the player is in the top visible tier and would therefore show the same icon on the other player-list surfaces.

The intended product outcome is:

1. the player-detail header no longer shows efficiency icons for tiers that are hidden on clan and landing rows,
2. the player-detail header no longer implies a stronger cross-surface visibility contract than the other player lists actually use,
3. the client returns to one visible efficiency-icon rule across player-facing list and header surfaces: show the sigma only for `E`.

## Current State

### Current row-surface behavior

The other player-list surfaces already use an `E`-only visibility rule:

1. [client/app/components/ClanMembers.tsx](client/app/components/ClanMembers.tsx) resolves the published efficiency tier and renders the sigma only when that resolved tier is `E`,
2. [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx) uses the same `E`-only rule for landing player rows,
3. hidden players remain suppressed through the shared published-efficiency contract.

### Current player-detail header behavior

The player-detail header in [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx) is currently broader than the row surfaces:

1. it resolves and renders any published efficiency tier (`III`, `II`, `I`, `E`),
2. when no published tier exists, it can still render a header sigma by falling back to the best stored WG badge row from `efficiency_json`,
3. this means the header can show a sigma for players who would not show one in clan rows or landing rows.

### Current inconsistency

The visible rule is currently split across surfaces:

1. player-detail header: all published tiers plus stored-badge fallback,
2. clan rows: `E` only,
3. landing rows: `E` only.

That inconsistency makes the header feel like a separate product contract rather than a higher-detail view of the same visible efficiency marker.

## Requested Product Change

Update the client behavior so the player-detail header reverts to only showing the efficiency icon when the player is in the top visible tier and therefore has the same visible icon in the other player lists.

Operationally, that means:

1. the header should render the sigma only when the resolved published tier is `E`,
2. the header should not render the sigma for published `III`, `II`, or `I`,
3. the header should not render the sigma from stored-badge fallback rows when no published `E` tier exists,
4. hidden-player suppression remains unchanged.

## Why This Needs A Separate Spec

This is a deliberate contract reversal, not a small cosmetic tweak.

The repo recently widened the player-detail header behavior to all published tiers plus stored-badge fallback. Reverting that behavior should be documented explicitly so implementation, QA, and later sessions do not treat the narrower `E`-only rule as an accidental regression.

## Constraint Summary

From current repo doctrine and shipped behavior:

1. prefer the smallest safe vertical slice,
2. preserve existing backend publication when the requested change is only in visible client behavior,
3. keep dense and summary surfaces aligned when the user explicitly asks for one visible rule,
4. update focused tests and durable docs in the same tranche,
5. avoid reopening the underlying efficiency percentile model.

## Recommended Product Behavior

### Visibility rule

Use one visible rule across player-facing header and list surfaces:

1. resolve the published efficiency tier through the shared resolver,
2. render the sigma only when the resolved tier is `E`,
3. otherwise render no header efficiency icon.

### Stored badge fallback

Do not use stored WG badge fallback rows to create a visible header sigma in this reverted client behavior.

Reasoning:

1. the user asked for the player-detail header to only show the icon when the same player also has the icon in the other player lists,
2. the other player lists do not use stored-badge fallback for visible non-`E` header-only affordances,
3. keeping fallback-only header sigma behavior would preserve the current inconsistency.

### Tooltip behavior

When the header does render an `E` sigma, it should continue to use the existing shared `EfficiencyRankIcon` tooltip contract.

No new copy is needed for this revert.

## Scope Boundary

In scope:

1. player-detail header efficiency-icon visibility in [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx),
2. player-detail client tests in [client/app/components/**tests**/PlayerDetail.test.tsx](client/app/components/__tests__/PlayerDetail.test.tsx),
3. durable docs that currently describe the broader all-tier or stored-badge-fallback header behavior,
4. any spec, runbook, or QA artifacts needed to record the revert.

Out of scope:

1. clan-row efficiency behavior,
2. landing-row efficiency behavior,
3. backend efficiency payload publication,
4. percentile thresholds or rank-publication logic,
5. redesigning the sigma icon.

## Backend Position

No backend contract change is required for this revert.

The backend can continue to publish:

1. `efficiency_rank_tier`,
2. `has_efficiency_rank_icon`,
3. `efficiency_rank_percentile`,
4. `efficiency_rank_population_size`,
5. any stored badge rows already present in `efficiency_json`.

This tranche changes only which of those already-published signals the player-detail header treats as visible enough to render.

## Frontend Recommendation

### PlayerDetail behavior

Update [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx) so the header:

1. computes the resolved published tier as it does today,
2. renders the sigma only when that resolved tier is `E`,
3. removes the current stored-badge fallback path from visible header rendering,
4. does not show non-`E` published tiers in the header.

### Shared component usage

Keep using [client/app/components/EfficiencyRankIcon.tsx](client/app/components/EfficiencyRankIcon.tsx) for the actual glyph and tooltip when an `E` icon is shown.

This revert should not fork the icon component.

## Options Considered

### Option A: keep all published tiers on player detail only

Pros:

1. exposes more efficiency information on the dedicated detail page,
2. preserves the current broader header treatment.

Cons:

1. conflicts with the user request,
2. keeps a visible mismatch with clan and landing player lists,
3. allows the header to imply a shared marker that is not actually shared.

Verdict:

Not recommended.

### Option B: revert player-detail header to `E` only

Pros:

1. matches the visible rule on the other player lists,
2. is the smallest client-only correction,
3. removes header-only fallback visibility that currently breaks cross-surface consistency.

Cons:

1. reduces visible efficiency information on the detail page,
2. leaves non-`E` efficiency data present in payloads but not visibly surfaced in the header.

Verdict:

Recommended.

### Option C: expand the other player lists to all tiers instead

Pros:

1. would align surfaces by widening visibility rather than narrowing it.

Cons:

1. directly conflicts with the request to revert the client,
2. increases row clutter on dense list surfaces,
3. reopens a prior product decision that the current landing and clan behavior already narrowed.

Verdict:

Out of scope for this request.

## Suggested Implementation Sequence

### Phase 1: PlayerDetail Revert

1. remove the stored-badge fallback path from visible header rendering,
2. restore `E`-only header visibility,
3. keep hidden-account suppression unchanged.

### Phase 2: Focused Regression Coverage

1. update player-detail tests so non-`E` published tiers no longer expect a visible header sigma,
2. update tests so stored-badge fallback rows no longer expect a visible header sigma,
3. retain `E`-tier header coverage.

### Phase 3: Documentation Cleanup

1. update durable docs that currently say the player-detail header shows all published tiers,
2. record this revert in the appropriate spec/runbook/review artifacts if the repo follows the existing spec-to-execution workflow for this tranche.

## Acceptance Criteria

1. The player-detail header shows the efficiency sigma only when the resolved published tier is `E`.
2. The player-detail header does not show the sigma for published `III`, `II`, or `I` tiers.
3. The player-detail header does not show the sigma solely from stored badge fallback rows.
4. Clan and landing player lists remain unchanged and continue to show `E` only.
5. Focused player-detail tests prove the reverted client behavior.

## Validation Plan

Focused client validation should cover:

1. published `E` tier still renders the header sigma,
2. published non-`E` tiers do not render the header sigma,
3. stored-badge-only fallback rows do not render the header sigma,
4. hidden players still suppress the icon.

Suggested target:

1. [client/app/components/**tests**/PlayerDetail.test.tsx](client/app/components/__tests__/PlayerDetail.test.tsx)

## Non-Goals

1. do not change the efficiency percentile model,
2. do not remove additive efficiency fields from the backend payload,
3. do not widen row-surface efficiency visibility,
4. do not redesign the sigma icon.
