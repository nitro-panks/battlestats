# Runbook: Clan Tier Distribution Recovery

_Last updated: 2026-04-02_

_Status: Active follow-up runbook_

## Purpose

Capture the current state of the clan tier distribution work after reviewing the recent frontend, backend, and runbook changes, and define the minimum next steps needed to make the tier chart visible and trustworthy again.

This runbook is based on code inspection plus live production endpoint checks on 2026-04-02.

## Executive Read

The aggregate clan tier data is coming in.

The current product direction is now clearer:

1. tier should not come back as the Z-axis or primary dimension in the main clan chart
2. the main clan chart should stay on KDR or another non-tier stat
3. the aggregate tier histogram should return as a separate surface only after the data lane is complete, warmed, and operationally trustworthy

That changes the priority order.

The main blocker is no longer only that the clan detail page stopped rendering the histogram. The main blocker is that the team wants to restore the histogram only after the data path is complete enough to avoid a half-ready UI rollout.

There is also a second layer of drift:

1. the original tier chart was an aggregate battles-by-tier chart
2. the follow-up 3D runbook was written around `avg_tier`
3. the current implementation has pivoted the 3D chart to `kdr` for the Z-axis while still returning `avg_tier` in the same payload

That means battlestats currently has working tier data, but the plan should now focus on hardening the data lane first and defer the histogram remount until the data is ready.

## Decision Update

This runbook is now governed by the following decision:

1. keep tier out of the main clan chart
2. keep the main chart on KDR or another non-tier stat
3. restore the aggregate tier histogram later as a secondary chart
4. prioritize data completeness, hydration behavior, warming coverage, and observability before remounting the histogram UI

This is intentionally slower than restoring the histogram immediately. Waiting days or weeks is acceptable if that produces a cleaner, more complete rollout.

## Verified Findings

### 1. Aggregate tier data is live on production

The backend aggregate endpoint is wired and returning non-empty data:

- endpoint: `/api/fetch/clan_tiers/<clan_id>`
- route file: `server/battlestats/urls.py`
- view: `server/warships/views.py`
- aggregation function: `server/warships/data.py`

Verified on production for warm NA clans:

1. clan `1000071346` returned tiers 1-11 with non-zero `pvp_battles`
2. clan `1000064482` returned tiers 1-11 with non-zero `pvp_battles`
3. clan `1000044123` returned tiers 1-11 with non-zero `pvp_battles`

Conclusion:

The aggregate tier distribution data path is not the current blocker.

### 2. The tier chart component and hook still exist

The old aggregate chart implementation is still present:

1. `client/app/components/ClanTierDistributionSVG.tsx`
2. `client/app/components/useClanTiersDistribution.ts`

That code still fetches `/api/fetch/clan_tiers/<clan_id>` and still knows how to render the bars.

Conclusion:

This is not a missing-component problem. It is a missing integration problem.

### 3. The clan detail page no longer renders the tier chart

Current `client/app/components/ClanDetail.tsx` mounts:

1. `ClanSVG` for 2D
2. `Clan3DSVG` for 3D
3. no `ClanTierDistributionSVG`

The old tier distribution section has been removed.

Conclusion:

Even with healthy aggregate tier data, users will not see the tier chart because it is not mounted.

### 4. The 3D work has drifted away from the tier-based design

The runbook `agents/runbooks/runbook-3d-clan-scatter-chart.md` is written around `avg_tier` as the Z-axis.

The current implementation has changed to `kdr` for the 3D experience:

1. `client/app/components/ClanDetail.tsx` uses KDR coverage to enable 3D
2. `client/app/components/Clan3DSVG.tsx` labels the Z-axis as KDR and normalizes `kdr`
3. `client/app/components/useClanMemberTiers.ts` returns both `avg_tier` and `kdr`
4. `server/warships/data.py` computes both `avg_tier` and `kdr`

Conclusion:

The product direction is currently ambiguous. The code is no longer implementing the runbook as written.

### 5. The member-tier data lane is only partially aligned with the runbook

The `clan_member_tiers` endpoint is live and returns data on production, but the follow-up implementation is incomplete relative to the runbook:

1. `avg_tier` is present in the payload
2. `kdr` is also present in the payload
3. missing `tiers_json` does not currently trigger background hydration in `compute_clan_member_avg_tiers()`
4. the daily warmer path still warms aggregate clan tier distributions, not explicitly the per-member `clan_member_tiers` cache

Conclusion:

The 3D member data lane exists, but it is not yet warmed or hydrated as deliberately as the original aggregate tier lane.

### 6. The aggregate histogram data lane is usable but not yet fully hardened for a delayed-quality rollout

The aggregate tier histogram path is ahead of the member-tier path, but there are still operational gaps if the team wants a high-confidence delayed rollout:

1. the aggregate lane returns partial data when some members are still missing `tiers_json`
2. there is not yet a clear operator-facing metric for histogram completeness by clan
3. the current work does not document a rollout threshold such as minimum hydrated-member coverage before the histogram is shown or trusted
4. there is no explicit runbook yet for validating histogram completeness after the warmers run

Conclusion:

The histogram lane works, but it is not yet documented and instrumented as a deliberately complete data product.

### 7. Tests and diagnostics are stale around the removed chart

The workspace still contains tier-chart expectations that no longer match the current UI direction:

1. `client/app/components/__tests__/ClanDetail.test.tsx` still mocks `ClanTierDistributionSVG`
2. `client/e2e/clan-loading-precedence.spec.ts` still tracks `clan_tiers` request ordering and tier-bar expectations
3. `client/test-results/clan-tier-diagnostic-results.json` records missing `[data-testid="clan-tier-distribution"]`

Conclusion:

The repo still carries test and diagnostic signals from the previous UI contract.

## What This Means

If the goal is specifically to bring the Tier Distribution histogram back later, more changes are needed first.

The needed changes are now primarily data-hardening and rollout-readiness fixes, followed by a smaller UI reintegration tranche.

There are two valid implementation phases:

### Phase A: Harden the data lane first

Do not remount the histogram yet.

First make the aggregate tier data lane operationally complete enough that the later UI restoration is low-risk.

### Phase B: Restore the aggregate histogram later

Use the existing working aggregate data lane and remount `ClanTierDistributionSVG` on the clan page.

This remains the correct UI move once the data lane has been hardened.

## Recommended Next Steps

### Tranche 1: Prioritize histogram data completeness and performance

1. Define what "complete enough" means for the histogram data lane.
2. Add a completeness standard such as hydrated-member coverage or explicit pending-state semantics per clan.
3. Verify that missing `tiers_json` hydration behaves predictably under the aggregate histogram path for cold or partially hydrated clans.
4. Decide whether partial aggregate histogram data should be shown with a pending banner, or suppressed until a minimum completeness threshold is reached.
5. Add operational logging or dashboard visibility for warmed vs partial histogram caches.
6. Document the rollout gate in this runbook so the histogram is not remounted before the data lane clears that bar.

This tranche is now the highest priority.

### Tranche 2: Keep the main clan chart non-tier-based

1. Treat the current KDR-based main-chart direction as intentional unless a later decision replaces KDR with another non-tier stat.
2. Do not route the main chart back through `avg_tier`.
3. Update stale tier-based 3D planning docs so they stop implying that tier belongs in the main chart.
4. Keep the member-tier payload available only as a supporting data lane unless a later product decision explicitly needs it.

This tranche is mostly documentation and contract clarity, not urgent UI work.

### Tranche 3: Restore the histogram after the data lane is ready

1. Re-mount `ClanTierDistributionSVG` in `client/app/components/ClanDetail.tsx` as a secondary chart beneath the main clan chart.
2. Keep the histogram clearly separate from the KDR-based main visualization.
3. Update `client/app/components/__tests__/ClanDetail.test.tsx` to reflect the restored histogram mount.
4. Update or rerun the relevant Playwright coverage so the histogram presence is asserted against the final UI contract.
5. Validate both warm-cache and cold-cache behavior before rollout.

This tranche should wait until the data lane from Tranche 1 is ready.

## Minimum Engineering Tranche I Would Do Next

Given the updated direction, the smallest safe next tranche is no longer UI restoration.

It is:

1. define and instrument histogram data completeness
2. harden hydration and warming expectations for aggregate tier data
3. update the stale planning docs so tier stays out of the main chart

Reason:

The product is explicitly willing to wait. That removes the pressure to restore a partially trusted histogram immediately and makes data readiness the correct priority.

## Suggested Validation After The Next Tranche

1. Force cold and partial clan cases and verify the aggregate histogram endpoint behaves predictably.
2. Confirm the warmer path improves histogram cache availability for known hot clans.
3. Verify the documented completeness threshold can be measured from real responses or logs.
4. Reconcile unit and Playwright expectations so they no longer imply that tier belongs in the main chart.

## File-Level Notes

The key files involved are:

1. `client/app/components/ClanDetail.tsx` — current integration blocker; tier chart no longer mounted
2. `client/app/components/ClanTierDistributionSVG.tsx` — still capable of rendering the aggregate chart
3. `client/app/components/useClanTiersDistribution.ts` — aggregate chart data hook still live
4. `client/app/components/Clan3DSVG.tsx` — current 3D implementation has drifted to KDR
5. `client/app/components/useClanMemberTiers.ts` — returns both `avg_tier` and `kdr`
6. `server/warships/views.py` — both aggregate and per-member endpoints are live
7. `server/warships/data.py` — aggregate tier lane is cache-backed; per-member lane is computed but less operationally complete
8. `agents/runbooks/runbook-3d-clan-scatter-chart.md` — stale relative to the current KDR-based frontend code and updated product direction

## Decision Recommendation

Recommendation:

Do not restore the histogram immediately.

Prioritize the aggregate histogram data lane first, keep the main chart non-tier-based, and restore the histogram only after the data lane is complete enough to deserve a UI surface.

The evidence today says:

1. tier data is already available
2. the main chart direction has moved away from tier
3. the old histogram code still exists and can be restored later
4. the current gap is less about missing code and more about rollout readiness and product clarity

That makes data hardening the smallest safe move, and histogram restoration a later follow-up.