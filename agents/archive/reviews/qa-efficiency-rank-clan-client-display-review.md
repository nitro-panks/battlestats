# QA Review: Efficiency Rank Clan Client Display

_Reviewed: 2026-03-16_

## Scope

Review the runbook execution for the shared clan-roster client behavior:

1. clan detail fetches clan-member roster data and displays efficiency-rank icons for appropriate members,
2. player detail fetches clan-member roster data and displays efficiency-rank icons for appropriate members,
3. the shared roster UI exposes visible hydration progress while efficiency rank data is warming.

## Acceptance Traceability

### Acceptance Criterion 1

When a user loads the clan page, the client fetches the shared clan-members roster and displays efficiency-rank icons for each appropriate clan member.

Evidence:

1. `ClanDetail.tsx` uses `useClanMembers(clan.clan_id)`.
2. `ClanMembers.tsx` renders `EfficiencyRankIcon` from published row fields.
3. `app/components/__tests__/ClanDetail.test.tsx` verifies the shared hook is called with the clan id.
4. `app/components/__tests__/ClanMembers.test.tsx` verifies qualifying members render the icon.

Status: Pass

### Acceptance Criterion 2

When a user loads the player detail page, the client fetches the shared clan-members roster and displays efficiency-rank icons for each appropriate clan member.

Evidence:

1. `PlayerDetail.tsx` uses `useClanMembers(player.clan_id || null)`.
2. The same `ClanMembers.tsx` renderer is mounted in the player detail surface.
3. `app/components/__tests__/PlayerDetail.test.tsx` verifies the shared hook is called with the player clan id.
4. `app/components/__tests__/ClanMembers.test.tsx` verifies icon rendering for appropriate members in the shared renderer.

Status: Pass

### Acceptance Criterion 3

The client visibly indicates hydration is in progress while efficiency ranks are warming.

Evidence:

1. `ClanMembers.tsx` now renders a roster-level status when any row has `efficiency_hydration_pending`.
2. `app/components/__tests__/ClanMembers.test.tsx` verifies the warming message appears while pending rows exist and disappears when they do not.

Status: Pass

## Test Evidence

Focused client QA command:

```bash
cd client && npm test -- --runInBand app/components/__tests__/ClanMembers.test.tsx app/components/__tests__/PlayerDetail.test.tsx app/components/__tests__/ClanDetail.test.tsx
```

Result:

1. `PASS app/components/__tests__/ClanMembers.test.tsx`
2. `PASS app/components/__tests__/PlayerDetail.test.tsx`
3. `PASS app/components/__tests__/ClanDetail.test.tsx`
4. `12` tests passed, `0` failed

## Live Verification Evidence

Live verification was executed against the Dockerized stack after restarting the server and worker processes to pick up the snapshot-republish change.

Observed sequence:

1. a temporary stale RESIN roster slice returned `X-Efficiency-Hydration-Pending: 3` on the first roster fetch and `0` on a follow-up poll,
2. a temporary stale qualifying-icon fixture in clan `1000064093` returned `X-Efficiency-Hydration-Pending: 2` while `Shinn000` and `SavageCoastie` were warming,
3. repeated polls held those rows pending until the snapshot republish lane completed,
4. the final roster response restored published icons as `SavageCoastie -> II` and `Shinn000 -> III`.

## Findings

No critical or high issues found in the client execution lane.

## Residual Risks

1. Warm clans can legitimately show no hydration activity and no icons for many members, so manual testers still need a stale fixture to distinguish hydration behavior from qualification behavior.

## Release Recommendation

Recommendation: Pass for the client execution lane.

Confidence: High for shared route wiring, visible hydration behavior, and end-to-end live rehydration of published icon fields.
