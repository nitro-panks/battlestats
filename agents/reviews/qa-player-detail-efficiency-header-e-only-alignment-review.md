# QA Review: Player Detail Efficiency Header E-Only Alignment Spec

_Reviewed: 2026-03-17_

## Scope Reviewed

- [agents/work-items/player-detail-efficiency-header-e-only-alignment-spec.md](agents/work-items/player-detail-efficiency-header-e-only-alignment-spec.md)
- [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx)
- [client/app/components/ClanMembers.tsx](client/app/components/ClanMembers.tsx)
- [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx)
- [client/app/components/**tests**/PlayerDetail.test.tsx](client/app/components/__tests__/PlayerDetail.test.tsx)

## QA Verdict

Approved for implementation.

The spec is tight and correctly scoped to a client-visible rule change. It restores one shared visible efficiency-icon rule across player header and player-list surfaces without forcing any backend contract rollback.

## What QA Confirmed

1. The current mismatch is real: player detail is broader than clan and landing player lists.
2. Reverting the header to `E` only satisfies the user request without reopening the broader efficiency-ranking model.
3. The change can stay client-only because the backend payload can remain additive even if the header stops surfacing all published tiers.
4. Removing stored-badge fallback from visible header rendering is necessary to fully restore cross-surface consistency.
5. The existing shared `EfficiencyRankIcon` component can still be reused unchanged for visible `E` rows.

## QA Focus Areas For Implementation

1. Non-`E` published tiers must stop rendering in player detail.
2. Stored-badge-only fallback rows must stop rendering in player detail.
3. Expert published tiers must continue to render normally.
4. Hidden-player suppression must remain intact.
5. Durable docs must stop claiming the header shows all tiers or stored-badge fallback visibly.

## Required QA Checks

1. A published `E` player still renders the header sigma.
2. A published `II` or `III` player no longer renders the header sigma.
3. A legacy fallback row with `has_efficiency_rank_icon=true` but no explicit `E` tier does not render the header sigma.
4. A player with only stored WG badge rows in `efficiency_json` does not render the header sigma.
5. Clan and landing player-list behavior remains unchanged.

## Residual Risks

1. Payloads will still carry broader efficiency data than the player-detail header now surfaces; that is expected, but docs need to make the distinction clear.
2. If tests only remove prior expectations without preserving `E` coverage, the revert could accidentally suppress the icon entirely.
3. If the detail header still references any fallback-only description wiring, dead client code could linger after the revert.

## QA Recommendations

1. Keep the implementation confined to [client/app/components/PlayerDetail.tsx](client/app/components/PlayerDetail.tsx), [client/app/components/EfficiencyRankIcon.tsx](client/app/components/EfficiencyRankIcon.tsx), and the focused player-detail/docs files.
2. Preserve the current row-surface behavior exactly as-is to avoid accidental scope creep.
3. Run the focused PlayerDetail Jest target after updating the expectations.

## Exit Criteria

1. Player detail renders the efficiency sigma only for resolved `E` rows.
2. Player detail no longer renders fallback-only or non-`E` efficiency icons.
3. Durable docs describe the reverted visible rule accurately.
4. Focused client validation passes.

## Final QA Position

Approved for the planned player-detail header revert.
