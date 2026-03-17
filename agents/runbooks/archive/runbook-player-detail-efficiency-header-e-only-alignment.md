# Runbook: Player Detail Efficiency Header E-Only Alignment

_Drafted: 2026-03-17_

## Status

Executed on 2026-03-17.

## Purpose

Revert the player-detail header efficiency marker so it only renders the Battlestats sigma for published Expert-tier players, matching the visible rule already used by the other player-list surfaces.

## Source Artifacts

- [agents/work-items/player-detail-efficiency-header-e-only-alignment-spec.md](agents/work-items/player-detail-efficiency-header-e-only-alignment-spec.md)
- [agents/reviews/qa-player-detail-efficiency-header-e-only-alignment-review.md](agents/reviews/qa-player-detail-efficiency-header-e-only-alignment-review.md)

## Current Behavior Summary

Before this revert, the player-detail header had widened beyond the row surfaces:

1. it showed any published efficiency tier,
2. it could also show a fallback sigma from stored WG badge rows,
3. clan and landing player lists still showed `E` only.

That left player detail visually out of alignment with the other player-list surfaces.

## Intended Change

1. Restore `E`-only visible efficiency behavior in the player-detail header.
2. Remove stored-badge fallback as a visible header-rendering path.
3. Keep the backend efficiency payload unchanged.
4. Update focused player-detail tests and durable docs.

## Scope Boundary

In scope:

1. [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx)
2. [client/app/components/EfficiencyRankIcon.tsx](client/app/components/EfficiencyRankIcon.tsx)
3. [client/app/components/**tests**/PlayerDetail.test.tsx](client/app/components/__tests__/PlayerDetail.test.tsx)
4. [client/README.md](client/README.md)
5. [README.md](README.md)

Out of scope:

1. backend efficiency publication,
2. clan-row efficiency rendering,
3. landing-row efficiency rendering,
4. percentile-model changes.

## Agent Responsibilities

### Project Manager

1. Keep the tranche limited to the requested client revert.
2. Prevent backend or row-surface behavior changes from slipping in.

### Architect

1. Preserve the additive backend contract.
2. Re-align only the visible header rule.

### Engineer-Web-Dev

1. Remove non-`E` and stored-badge fallback rendering from player detail.
2. Update the focused player-detail tests.
3. Correct the durable docs.

### QA

1. Verify `E` still renders.
2. Verify non-`E` and fallback-only cases do not render.
3. Verify hidden-player suppression remains intact.

## Implementation Sequence

### Phase 1: PlayerDetail Revert

1. Remove the current stored-badge fallback header logic.
2. Restrict visible header rendering to resolved `E` only.
3. Leave the rest of the header icon tray unchanged.

### Phase 2: Regression Coverage

1. Update tests for published non-`E` tiers.
2. Update tests for legacy fallback-only cases.
3. Preserve the published `E` test.

### Phase 3: Documentation Cleanup

1. Update the repo README.
2. Update the client README.
3. Ensure docs no longer claim the player-detail header visibly shows all tiers or stored-badge fallback.

## Validation Command

Focused client command:

```bash
cd client && npm test -- --runInBand app/components/__tests__/PlayerDetail.test.tsx
```

## Rollback Plan

If the revert needs to be undone:

1. restore the broader header visibility logic in [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx),
2. restore the widened player-detail test expectations,
3. restore the broader header docs.

## Execution Evidence

Implementation landed in:

1. [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx)
2. [client/app/components/EfficiencyRankIcon.tsx](client/app/components/EfficiencyRankIcon.tsx)
3. [client/app/components/**tests**/PlayerDetail.test.tsx](client/app/components/__tests__/PlayerDetail.test.tsx)
4. [client/README.md](client/README.md)
5. [README.md](README.md)

Focused validation passed:

```bash
cd client && npm test -- --runInBand app/components/__tests__/PlayerDetail.test.tsx
```

## Completion Checklist

- [x] Player-detail header reverted to `E` only
- [x] Stored-badge fallback removed from visible header rendering
- [x] Focused player-detail tests updated
- [x] Durable docs updated
- [x] Focused validation passed

## Operating Notes

1. This tranche changes visible client behavior only; the backend still publishes broader efficiency fields.
2. The user-facing rule is now consistent across player detail, clan rows, and landing player rows.
