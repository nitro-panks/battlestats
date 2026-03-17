# QA Review: Streamlined Player PvE Icon Heuristic Spec

_Reviewed: 2026-03-17_

## Scope Reviewed

- [agents/work-items/player-pve-icon-streamlined-heuristic-spec.md](agents/work-items/player-pve-icon-streamlined-heuristic-spec.md)
- [agents/runbooks/archive/runbook-clan-pve-marker.md](agents/runbooks/archive/runbook-clan-pve-marker.md)
- [server/warships/data.py](server/warships/data.py)
- [server/warships/views.py](server/warships/views.py)
- [server/warships/landing.py](server/warships/landing.py)
- [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx)

## QA Verdict

Approved for implementation.

The spec fixes the main quality problem in the current lane: the icon has two different live rules, and one of them still has an absolute-count override that over-labels some players. The proposed replacement is narrower, consistent across surfaces, and testable using already stored totals.

## What QA Confirmed

1. The spec replaces two conflicting live rules with one shared backend heuristic.
2. The recommended rule correctly classifies the five named example players.
3. The proposed logic uses only `total_battles` and `pvp_battles`, so implementation does not require new upstream data or new browser fetches.
4. The spec explicitly removes the `>= 4000 PvE battles` override as a standalone pass condition.
5. The spec keeps the change bounded to heuristic unification and payload consistency, not a broader playstyle taxonomy.

## QA Focus Areas For Implementation

1. Boundary behavior around `total_battles > 500`.
2. Boundary behavior around `pve_battles >= 1500`.
3. Boundary behavior around `pve_share_total >= 0.30`.
4. Consistency between clan members, landing players, recent players, and player detail.
5. Regression risk from player detail switching from local derivation to backend payload.

## Required QA Checks

1. A player with large absolute PvE volume but less than 30% PvE share does not get the icon.
2. A player with strong PvE share but fewer than 1500 derived PvE battles does not get the icon.
3. A player exactly at 30% PvE share and exactly 1500 PvE battles is classified deterministically.
4. Player detail consumes the same derived boolean as other surfaces.
5. No surface still relies on the archived `>= 4000` exception.

## Residual Risks

1. The `30%` threshold is a product heuristic, not an upstream semantic, so some near-boundary players may still feel debatable.
2. `total_battles - pvp_battles` still treats all non-PvP battles as PvE-like, which is acceptable for this icon but should not be misread as a complete mode breakdown.
3. If player detail does not switch to the shared payload, the surface inconsistency will persist even if the helper changes.

## QA Recommendations

1. Treat the five named example players as release fixtures for manual verification.
2. Add one backend test for each threshold edge instead of relying only on broad endpoint snapshots.
3. Update any stale knowledge or runbook references that still describe the old majority-PvE or `>= 4000` logic as current behavior.

## Exit Criteria

1. One shared PvE helper drives all current surfaces.
2. Player detail no longer re-derives its own PvE icon rule.
3. Focused backend tests cover threshold boundaries and endpoint payloads.
4. Focused client tests confirm player detail honors the shared backend flag.

## Final QA Position

Approved for the planned heuristic-unification tranche.
