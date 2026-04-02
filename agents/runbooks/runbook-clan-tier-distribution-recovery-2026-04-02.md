# Runbook: Clan Tier Distribution — Data Readiness and Histogram Restoration

_Last updated: 2026-04-02_

_Status: **Deferred** — blocked on Asia data migration completion_

## Purpose

Track the clan tier histogram restoration. The histogram was removed from the clan detail page on 2026-04-02 because it was hanging for most clans due to incomplete tier data. The aggregate tier data lane is functional but only ~37% populated. This runbook defines what "ready" looks like and the steps to get there.

## Current Product Direction

1. The **main clan chart stays KDR-based** (3D scatter: Battles x WR x KDR)
2. The **aggregate tier histogram returns later** as a separate secondary chart beneath the main scatter plot
3. Histogram restoration is **deferred until after the Asia (SEA) data migration** is complete — no timeline pressure

## What Shipped (2026-04-02)

| Change                                                  | Status            |
| ------------------------------------------------------- | ----------------- |
| Removed `ClanTierDistributionSVG` from ClanDetail       | Deployed (v1.5.1) |
| 3D scatter chart with KDR Z-axis                        | Deployed (v1.5.2) |
| `clan_member_tiers` endpoint returns `avg_tier` + `kdr` | Deployed (v1.5.2) |
| ColorBrewer Set2 axis styling, drag fix                 | Deployed (v1.5.3) |
| Daily tier distribution warmer (EU 02:30, NA 08:30 UTC) | Running           |
| 97.8% of clans have 3D available (KDR coverage)         | Verified          |

## Data Coverage Snapshot (2026-04-02)

| Metric                                | Value                   |
| ------------------------------------- | ----------------------- |
| Total non-hidden players              | 715,763                 |
| Players with `battles_json`           | 1,027 (0.1%)            |
| Players with `tiers_json`             | 267,154 (37.3%)         |
| Clans with >=50% tier coverage        | 34,752 / 96,034 (36.2%) |
| Clans with KDR coverage (3D eligible) | 93,924 / 96,034 (97.8%) |

**Why the gap**: `tiers_json` is computed from `battles_json`, which only gets populated when a player is individually viewed or hit by the daily clan crawl. The daily tier warmer reads existing `tiers_json` but does not trigger `battles_json` hydration for players that lack it.

## What "Ready" Means

The histogram should be restored when:

1. **>=80% of clans with >=10 members** have non-empty aggregate tier data (the `/api/fetch/clan_tiers/:id` endpoint returns at least one tier with `pvp_battles > 0`)
2. **The daily warmer** has completed at least one full pass for each realm
3. **Partial data semantics** are clear: decide whether to show partial histograms with a "Data still loading" indicator, or suppress until a per-clan completeness threshold is met

## Remaining Work

### Phase 1: Data lane hardening (do first, after Asia migration)

1. **Trigger hydration for missing `tiers_json`**: When `compute_clan_member_avg_tiers()` or `update_clan_tier_distribution()` encounters a player with null `tiers_json`, dispatch `update_tiers_data_task` (or `update_battle_data_task` if `battles_json` is also null). Rate-limit to avoid flooding the hydration queue.
2. **Measure completeness**: Add a management command or Celery task that reports tier coverage stats (% players with tiers_json, % clans with sufficient aggregate data) so progress can be tracked after each warmer run.
3. **Warmer observability**: Log warmer completion stats — how many clans warmed, how many had partial data, how many had zero data.

### Phase 2: Histogram restoration (after Phase 1 meets readiness threshold)

1. **Re-mount `ClanTierDistributionSVG`** in `ClanDetail.tsx` as a secondary chart beneath the main scatter plot. Place it in a `DeferredSection` to avoid competing with the primary chart fetch.
2. **Pending state**: If the aggregate endpoint returns partial data (fewer tiers with battles than expected), show a subtle "Tier data still loading" indicator rather than suppressing the chart entirely.
3. **Update tests**: Remove stale tier assertions from `ClanDetail.test.tsx`, update Playwright specs.

### Phase 3: Cleanup

1. Archive `runbook-3d-clan-scatter-chart.md` to `agents/runbooks/archive/` — it is fully superseded.
2. Clean up `client/test-results/clan-tier-diagnostic-results.json` and any stale test fixtures.
3. Reconcile `clan-loading-precedence.spec.ts` — remove `clan_tiers` request ordering assertions.

## Key Files

| File                                                | Role                                                                                                    |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `server/warships/data.py`                           | `update_clan_tier_distribution()` — aggregate lane; `compute_clan_member_avg_tiers()` — per-member lane |
| `server/warships/views.py`                          | `clan_tier_distribution` and `clan_member_tiers` endpoints                                              |
| `server/warships/tasks.py`                          | `warm_all_clan_tier_distributions_task` — daily warmer                                                  |
| `server/warships/signals.py`                        | Schedules daily tier warmer per realm                                                                   |
| `client/app/components/ClanTierDistributionSVG.tsx` | Aggregate histogram component (exists, not mounted)                                                     |
| `client/app/components/useClanTiersDistribution.ts` | Aggregate data hook (exists, not used)                                                                  |
| `client/app/components/ClanDetail.tsx`              | Integration point for histogram restoration                                                             |

## Decision Log

| Date       | Decision                                                        |
| ---------- | --------------------------------------------------------------- |
| 2026-04-02 | Removed tier histogram from production — hanging for most clans |
| 2026-04-02 | Shipped 3D scatter with KDR Z-axis instead of tier              |
| 2026-04-02 | Deferred histogram restoration until after Asia data migration  |
| 2026-04-02 | Tier data collection continues in background via daily warmers  |
