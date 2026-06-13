# QA Review: Efficiency Rank Icon Implementation Runbook

_Reviewed: 2026-03-16_

## Scope Reviewed

- [agents/work-items/efficiency-rank-icon-spec.md](/home/august/code/archive/battlestats/agents/work-items/efficiency-rank-icon-spec.md)
- [agents/reviews/qa-efficiency-rank-icon-spec-review.md](/home/august/code/archive/battlestats/agents/reviews/qa-efficiency-rank-icon-spec-review.md)
- [agents/runbooks/runbook-efficiency-rank-icon-implementation.md](/home/august/code/archive/battlestats/agents/runbooks/runbook-efficiency-rank-icon-implementation.md)
- [server/warships/models.py](/home/august/code/archive/battlestats/server/warships/models.py)
- [server/warships/data.py](/home/august/code/archive/battlestats/server/warships/data.py)
- [server/warships/serializers.py](/home/august/code/archive/battlestats/server/warships/serializers.py)
- [server/warships/views.py](/home/august/code/archive/battlestats/server/warships/views.py)
- [client/app/components/PlayerDetail.tsx](/home/august/code/archive/battlestats/client/app/components/PlayerDetail.tsx)

## QA Verdict

Conditional approval.

The runbook is correctly scoped and aligned with the planning spec, but execution still depends on implementing the missing summary fields, denominator helpers, percentile snapshot command, payload contract, and header icon.

## What QA Confirmed

1. The runbook keeps the first tranche bounded to the player detail header.
2. The runbook uses `battles_json` for denominator math and does not regress to `randoms_json`.
3. The runbook preserves the spec's key statistical rules: rarity weights, opportunity normalization, shrinkage, stale snapshot suppression, and unmapped-row suppression.
4. The runbook now includes an explicit threshold review gate, deterministic tie handling guidance, and migration/rollback notes.

## Required QA Checks During Execution

1. Add all planned `PlayerExplorerSummary` fields through a migration before any payload or UI work depends on them.
2. Unit-test denominator construction, unmapped badge suppression, tie handling, and stale snapshot suppression.
3. Verify the snapshot command emits an analysis report with eligible population size, qualifying share, and percentile-band diagnostics.
4. Verify player detail only exposes fresh published values.
5. Verify tooltip copy says `tracked players` and does not imply a WG-awarded player badge.

## Residual Risks

1. The tracked Battlestats population is crawler-shaped rather than representative of the full WoWS population, so the UI copy must stay conservative.
2. Badge rows reflect peak qualifying ship performances rather than average consistency, so the first real-data threshold review remains a release gate.
3. Metadata coverage problems can still suppress a large share of players if unmapped badge rows are more common than expected.

## QA Position

Approved as an execution plan, conditional on the implementation satisfying the required checks above and on the first real-data distribution review confirming the threshold is still sensible.
