# Runbook: EU Profile Chart Population

**Created**: 2026-04-02
**Status**: Planned
**Depends on**: `spec-multi-realm-eu-support.md`, `archive/runbook-eu-heatmap-rollout-2026-04-02.md`, `runbook-cache-audit.md`

## Goal

Capture what still needs to happen for the EU player profile tab to show fully populated data for:

1. Tier vs Type Profile
2. Performance by Ship Type
3. Performance by Tier

This runbook is intentionally about readiness and data flow, not about inventing a new chart family. The current client already has the needed surfaces. The remaining work is primarily upstream hydration coverage and operational warming for EU.

## Current State

The three profile-tab charts already share one realm-aware backend source:

1. the client requests `/api/fetch/player_correlation/tier_type/<player_id>/?realm=eu`
2. the server returns the EU population overlay plus per-player `player_cells`
3. the client derives both summary charts from those `player_cells`

Important consequence:

1. there is no separate EU-specific chart implementation missing on the frontend
2. there is no separate EU-specific `type_json` or `tiers_json` endpoint needed to render these three profile charts on the Insights tab
3. if the tier-type correlation payload has populated `player_cells`, all three profile charts can render meaningful player data

## What Is Already Working

The profile chart path is already wired end to end.

### Client behavior

`PlayerDetailInsightsTabs` loads the realm-aware tier-type correlation endpoint when the profile tab becomes active.

If the payload comes back with `X-Tier-Type-Pending: true` and empty `player_cells`, the tab stays in its bounded warmup loop rather than rendering a false final empty state.

That means the client behavior is already correct for EU. Empty or sparse charts on EU are usually a data-readiness issue, not a React rendering issue.

### Server behavior

`fetch_player_tier_type_correlation(...)` does two jobs:

1. read the shared population heatmap payload for the requested realm
2. build per-player `player_cells` from that player's `battles_json`

If `battles_json` is missing, the server queues `update_battle_data_task` and returns the population payload with empty `player_cells`.

That means the server is also already behaving as designed for EU. The missing ingredient is not an endpoint implementation gap. It is the amount and freshness of EU battle hydration.

## The Actual Data Dependency

For these three charts, the critical dependency is `Player.battles_json` for the EU player.

The dependency chain is:

1. `battles_json` is populated from per-ship battle data
2. `player_cells` are built from `battles_json`
3. Tier vs Type Profile renders from the shared population payload plus `player_cells`
4. Performance by Ship Type is derived from `player_cells`
5. Performance by Tier is derived from `player_cells`

This means:

1. if `battles_json` is missing, all three profile charts are effectively blocked
2. if `battles_json` is present but sparse, all three charts render but may look thin or uninteresting
3. `type_json` and `tiers_json` are useful derived caches elsewhere, but they are not the primary gating dependency for these specific profile-tab charts

## Why EU Still Looks Sparse

There are two separate kinds of completeness, and they should not be confused.

### Per-player completeness

This asks: does the specific EU player have enough hydrated battle rows to produce `player_cells`?

If no, the UI shows the warmup state and the backend marks the response pending.

### Population completeness

This asks: does the EU realm have enough hydrated players with usable `battles_json` to make the shared Tier vs Type background heatmap meaningful?

As of the latest manual warm run on 2026-04-02:

1. `eu.win_rate_survival.tracked_population = 348631`
2. `eu.ranked_wr_battles.tracked_population = 66`
3. `eu.tier_type.tracked_population = 4`

Interpretation:

1. the EU realm already has broad data for WR vs Survival
2. the ranked population heatmap is still very early on EU
3. the tier-type population overlay is the main weak point for profile-tab chart quality on EU, even though the code path itself is live

So when someone says the EU profile charts are not "fully populated," that can mean one of two different failures:

1. this player has no hydrated battle rows yet
2. the player is hydrated, but the shared EU tier-type population overlay is still based on too few qualified players

## What Needs To Happen

The next tranche should focus on data readiness, not frontend redesign.

### Step 1: Increase EU player battle hydration coverage

This is the highest-priority requirement.

Until more EU players have non-null `battles_json`, the shared EU tier-type population overlay remains too small to be representative, and some individual players will continue to land in pending warmup.

Desired operational outcome:

1. increase the count of visible EU players with populated `battles_json`
2. ensure newly discovered or recently visited EU players are prioritized for battle hydration
3. verify the hydration lane is not lagging behind player/clan discovery volume

### Step 2: Audit the EU battle hydration queue and scheduler behavior

The current code queues `update_battle_data_task` on demand, but the EU corpus has grown enough that on-demand hydration alone may leave too many player profile tabs cold.

Next checks:

1. confirm the relevant Celery workers are draining `update_battle_data_task` consistently in production
2. confirm EU recently visited players are not being starved behind broader refresh work
3. identify whether a dedicated EU backfill tranche for battle rows is needed

If queue drain is healthy but coverage remains low, add a narrower operational backfill for EU battle rows instead of changing the client.

### Step 3: Keep EU tier-type population caches warm after hydration expands

The cache side is now in acceptable shape:

1. `warm_player_correlations(realm=...)` warms `tier_type`, `win_rate_survival`, and `ranked_wr_battles`
2. shared population heatmaps keep published fallback copies

But warmer correctness does not create data by itself. Once EU battle hydration improves, the correlation warmers need to be rerun so the shared tier-type overlay reflects the newly hydrated corpus.

Operational rule:

1. after any significant EU hydration/backfill tranche, rerun the EU correlation warmer before evaluating chart quality

### Step 4: Validate with live EU players, not just aggregate counts

Tracked population alone is not enough. We need to verify live player routes in the EU realm.

Validation should include at least three buckets:

1. EU player with fully populated `battles_json` and broad ship history
2. EU player that is newly discovered and expected to warm from pending state
3. EU player with sparse but real history, to confirm the client distinguishes "thin data" from "broken data"

### Step 5: Only consider payload or UX changes if hydration proves healthy

If EU battle hydration becomes healthy and the charts still feel incomplete, only then consider product changes such as:

1. explicit UI copy that distinguishes player-specific emptiness from realm-population thinness
2. a softer visual treatment when `tracked_population` is below a reasonable threshold
3. an operator-facing metric or dashboard for EU tier-type tracked population growth

Do not start with these changes. The first question is still whether the data lane is sufficiently hydrated.

## Non-Goals

This runbook does not call for:

1. a new EU-only endpoint family
2. a new EU-only React component
3. browser-triggered WG API calls for profile-chart rendering
4. replacing the current tier-type endpoint with direct reads from `type_json` or `tiers_json`

Those would be detours unless the current data path proves fundamentally incorrect, which it does not.

## Readiness Audit Snapshot

Initial audit run on 2026-04-02 against the active EU corpus returned:

1. `total_players = 471291`
2. `visible_players = 448540`
3. `visible_with_battles_json = 6`
4. `visible_with_tier_type_rows = 6`
5. `warm_player_correlations(realm='eu') = {'tier_type': {'tracked_population': 6}, 'win_rate_survival': {'tracked_population': 348631}, 'ranked_wr_battles': {'tracked_population': 66}}`

Interpretation:

1. the EU tier-type profile-chart bottleneck is decisively battle-row hydration, not cache warmth
2. the shared EU tier-type overlay is still effectively empty for product purposes
3. a bounded backfill lane is justified immediately

## Recommended Next Steps

### Tranche A: Measure EU battle hydration readiness

Capture the current EU population relevant to the profile charts:

1. total EU players
2. visible EU players
3. visible EU players with non-null `battles_json`
4. visible EU players whose `battles_json` yields at least one tier-type cell
5. tracked population from `warm_player_correlations(realm='eu')`

This establishes whether the bottleneck is queue coverage, filtering thresholds, or both.

This repo now has a dedicated command for repeating that audit:

```bash
cd /home/august/code/archive/battlestats/server && \
set -a && source .env && source .env.secrets && set +a && \
/home/august/code/archive/battlestats/.venv/bin/python manage.py audit_profile_chart_readiness --realm eu --warm-correlations
```

### Tranche B: Run or add a focused EU battle-row backfill

If the readiness audit confirms low `battles_json` coverage, run a focused EU battle hydration sweep or add a bounded management command/task for it.

Requirements:

1. keep it bounded and resumable
2. avoid unbounded queue fan-out
3. prefer players that are recently visited, high-visibility, or otherwise likely to hit profile routes soon

This repo now has a bounded command for that backfill tranche:

```bash
cd /home/august/code/archive/battlestats/server && \
set -a && source .env && source .env.secrets && set +a && \
/home/august/code/archive/battlestats/.venv/bin/python manage.py backfill_battle_data --realm eu --limit 250 --dispatch queue
```

### Tranche C: Re-warm EU profile-chart correlations

After hydration improves:

1. rerun `warm_player_correlations(realm='eu')`
2. record the new `tracked_population` values
3. compare live EU profile routes before and after

### Tranche D: Decide whether UX follow-up is still needed

If the EU tier-type overlay remains weak even after battle hydration becomes substantial, add a follow-up runbook for product-level handling of low-population overlays.

That should be a separate decision, not folded into the data-lane work.

## Validation Commands

### Warm EU correlations

```bash
cd /home/august/code/archive/battlestats/server && \
set -a && source .env && source .env.secrets && set +a && \
/home/august/code/archive/battlestats/.venv/bin/python manage.py shell -c "from warships.data import warm_player_correlations; import json; print(json.dumps(warm_player_correlations(realm='eu'), indent=2, sort_keys=True))"
```

### Check realm health

```bash
cd /home/august/code/archive/battlestats/server && \
set -a && source .env && source .env.secrets && set +a && \
/home/august/code/archive/battlestats/.venv/bin/python manage.py check_realm_health --realm eu
```

### Probe a live EU player tier-type payload

```bash
curl -sS "https://battlestats.online/api/fetch/player_correlation/tier_type/<player_id>/?realm=eu"
```

Inspect:

1. whether `player_cells` is empty or populated
2. whether the response includes `X-Tier-Type-Pending: true`
3. whether the returned `tracked_population` is still too low to support a meaningful overlay

## Success Criteria

This runbook can be marked implemented when all of the following are true:

1. a meaningful share of visible EU players have populated `battles_json`
2. newly visited EU players do not commonly get stuck in repeated profile-tab pending states
3. `warm_player_correlations(realm='eu')` reports a non-trivial `tier_type.tracked_population`
4. live EU profile routes show populated Tier vs Type, Performance by Ship Type, and Performance by Tier charts for representative EU players

## Notes For Follow-Up Implementation

If implementation work starts from this runbook, the most likely files to revisit are:

1. `server/warships/data.py`
2. `server/warships/tasks.py`
3. `server/warships/signals.py`
4. `server/warships/management/commands/check_realm_health.py`
5. `client/app/components/PlayerDetailInsightsTabs.tsx`

The expected first change, however, is operational or scheduler-focused, not a new client feature.
