# QA Review: Efficiency Rank Frontend Rollout Runbook

_Reviewed: 2026-03-16_

## Scope Reviewed

1. [agents/runbooks/archive/runbook-efficiency-rank-frontend-rollout.md](/home/august/code/archive/battlestats/agents/runbooks/archive/runbook-efficiency-rank-frontend-rollout.md)
2. [agents/runbooks/runbook-efficiency-rank-icon-hardening.md](/home/august/code/archive/battlestats/agents/runbooks/runbook-efficiency-rank-icon-hardening.md)
3. [server/warships/models.py](/home/august/code/archive/battlestats/server/warships/models.py)
4. [server/warships/serializers.py](/home/august/code/archive/battlestats/server/warships/serializers.py)
5. [server/warships/data.py](/home/august/code/archive/battlestats/server/warships/data.py)
6. [client/app/components/PlayerDetail.tsx](/home/august/code/archive/battlestats/client/app/components/PlayerDetail.tsx)
7. [client/app/components/PlayerEfficiencyBadges.tsx](/home/august/code/archive/battlestats/client/app/components/PlayerEfficiencyBadges.tsx)
8. [client/app/components/**tests**/PlayerDetail.test.tsx](/home/august/code/archive/battlestats/client/app/components/__tests__/PlayerDetail.test.tsx)

## QA Verdict

Approved and verified for the player-detail frontend tranche.

The runbook was executed successfully. The player-detail header icon now reads as a Battlestats-specific rank marker instead of another generic badge chip, and the focused client regression suite passed after implementation.

## What QA Confirmed

1. The backend contract needed by player detail already exists.
2. The runbook preserves the hardening rule that the client consumes the published tier contract rather than rebuilding percentile logic locally.
3. The runbook keeps tooltip language conservative and explicitly differentiates the Battlestats rank from WG ship badges.
4. The runbook is honest that live coverage is still sparse and treats that as an operational constraint rather than a frontend blocker.
5. The runbook correctly limits the tranche to player-detail polish and validation.

## Required QA Checks During Execution

1. Verified by test: the icon is hidden when the published rank fields are absent.
2. Verified by test: the accessible label includes both the tier name and percentile wording.
3. Verified by test: the legacy boolean fallback still produces a `III` compatibility path.
4. Manual follow-up still recommended: mobile header wrapping with many concurrent status markers.
5. Verified by code review: the visual treatment is now distinct from the per-ship `Efficiency Badges` section through the `BST` marker and wrapper styling.

## Execution Evidence

Implementation landed in:

1. [client/app/components/PlayerDetail.tsx](/home/august/code/archive/battlestats/client/app/components/PlayerDetail.tsx)
2. [client/app/components/**tests**/PlayerDetail.test.tsx](/home/august/code/archive/battlestats/client/app/components/__tests__/PlayerDetail.test.tsx)

Focused validation run:

1. `cd client && npm test -- --runInBand app/components/__tests__/PlayerDetail.test.tsx`
2. Result: `PASS`, `4` tests passed.

## Residual Risks

1. Sparse live icon counts can make visual QA harder unless both positive and negative player cases are sampled deliberately.
2. The current compatibility fallback can hide contract drift if it remains in place too long.
3. If row-surface rollout is attempted without a separate payload review, frontend work can silently pull backend scope back into the tranche.

## QA Position

Player-detail frontend tranche complete.

No additional backend or data-layer work was required for this tranche. Separate planning should still be used for explorer-row, clan-row, or landing-surface rollout.
