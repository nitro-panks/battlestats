# Runbook: Player KDR Alignment

_Last updated: 2026-03-18_

_Status: Phase 1 and Phase 3 implemented; Best-ranking KDR remains a measured follow-up_

## Goal

Align KDR usage across the product so that:

1. the player detail page shows actual KDR instead of the current weighted kill metric,
2. the Best-player ranking can consider KDR in a defensible way without double-counting or reintroducing low-tier distortion,
3. player lists on clan and player-centric ranking surfaces are ordered by player ranking, not by KDR or weighted KDR.

## Current-State Findings

### 1. The player detail page does not currently show actual KDR

The four-card stat strip in [client/app/components/PlayerDetail.tsx](../../client/app/components/PlayerDetail.tsx) renders a card labeled `Weighted KDR` and reads from `player.kill_ratio`.

That field is not literal kills divided by deaths.

Current server behavior:

1. [server/warships/serializers.py](../../server/warships/serializers.py) exposes `kill_ratio` from `PlayerExplorerSummary.kill_ratio` or recomputes it from `battles_json`.
2. [server/warships/data.py](../../server/warships/data.py) computes `_calculate_player_kill_ratio()` as a smoothed, tier-weighted kill-rate metric derived from ship rows.
3. The archived runbook [agents/runbooks/archive/runbook-player-kill-ratio.md](archive/runbook-player-kill-ratio.md) confirms that `kill_ratio` was intentionally defined as weighted kills-per-battle, not actual KDR.

Conclusion:

1. `kill_ratio` is a weighted ranking signal.
2. It should not be relabeled as actual KDR.
3. If the product wants actual KDR on player detail, it needs a separate field.

### 2. Actual KDR is not currently persisted at player level

The current player ingest in [server/warships/data.py](../../server/warships/data.py) persists:

1. `pvp_battles`
2. `pvp_wins`
3. `pvp_losses`
4. `pvp_survival_rate`

It does not currently persist:

1. total PvP frags,
2. survived-battles count,
3. deaths count,
4. actual KDR.

The ship rows in `battles_json` do include `frags`, but they do not include deaths. That means actual KDR cannot be reconstructed cleanly from `battles_json` alone.

Conclusion:

1. the clean implementation path is to extend player-level ingest to store the numerator and denominator for actual KDR,
2. swapping the player detail card should be an additive contract change, not a reinterpretation of `kill_ratio`.

### 3. Clan roster ordering is already ranking-first

The clan members endpoint in [server/warships/views.py](../../server/warships/views.py) orders members with `_player_score_ordering('last_battle_date')`, which means:

1. `explorer_summary__player_score DESC`
2. `last_battle_date DESC`
3. `name ASC`

There is already regression coverage in [server/warships/tests/test_views.py](../../server/warships/tests/test_views.py) asserting that clan members are ordered by `player_score` descending.

Conclusion:

1. the clan page is already aligned with the desired ranking behavior,
2. the task here is to preserve and harden that behavior, not redesign it.

### 4. Player Explorer is not KDR-ordered by default, but it is also not ranking-first

The Player Explorer in [client/app/components/PlayerExplorer.tsx](../../client/app/components/PlayerExplorer.tsx) currently defaults to:

1. `sort = 'pvp_battles'`
2. `direction = 'desc'`

Weighted KDR remains an available opt-in sort and column.

Conclusion:

1. the explorer is not incorrectly ordered by weighted KDR today,
2. if the desired product rule is `player-centric ranking views should default to player ranking`, then the explorer default should move to `player_score DESC`.

### 5. Best-player ranking already avoids weighted-KDR-first ordering

The live Best landing ranking in [server/warships/landing.py](../../server/warships/landing.py) currently ranks by a composite competitive score built from:

1. high-tier WR,
2. player score,
3. efficiency,
4. high-tier volume,
5. ranked signal,
6. clan-battle signal,
7. a competitive-share multiplier.

Weighted KDR is not a direct standalone term in the landing Best formula.

However, `player_score` already includes weighted KDR indirectly, because [server/warships/data.py](../../server/warships/data.py) still weights `kill_ratio` inside `_calculate_player_score()`.

Conclusion:

1. adding weighted KDR directly to Best would double-count it,
2. any KDR addition to Best should use actual competitive-tier KDR, not the current weighted kill metric.

## Recommended Product Decisions

## Decision 1: Split weighted kill metric from actual KDR

Do not repurpose `kill_ratio`.

Instead:

1. keep `kill_ratio` as the weighted tier-adjusted metric used by Player Explorer and `player_score`,
2. add a new additive player-detail field named `actual_kdr`,
3. change the player detail card label from `Weighted KDR` to `KDR`,
4. render the player detail card from `actual_kdr`, not from `kill_ratio`.

Why:

1. `kill_ratio` already has an established meaning in code, tests, and archived docs,
2. changing its meaning would create contract drift between detail, explorer, score calculation, and historical runbooks,
3. an additive field keeps the rollout reversible and minimizes breakage.

## Decision 2: Define actual KDR literally as kills divided by deaths

Recommended formula:

1. `pvp_deaths = max(pvp_battles - pvp_survived_battles, 0)`
2. `actual_kdr = pvp_frags / pvp_deaths` when `pvp_deaths > 0`

Recommended edge handling:

1. if `pvp_battles == 0`, return `null`,
2. if `pvp_deaths == 0`, also return `null` for now and render `—`.

Reasoning:

1. this keeps the metric mathematically honest,
2. it avoids silently substituting kills-per-battle or a capped pseudo-infinity,
3. zero-death cases can be revisited later with a dedicated display treatment if they matter in practice.

## Decision 3: Use actual KDR in Best only if it is high-tier scoped and lightly weighted

Do not add overall actual KDR directly to the live Best formula in the same tranche as the player-detail swap.

Recommended stance:

1. first implement actual KDR for player detail and data storage,
2. then evaluate a high-tier KDR variant for Best,
3. only promote it into the live formula if it improves ordering on real corpus checks.

Recommended candidate signal:

1. `high_tier_actual_kdr = tier_5_10_frags / tier_5_10_deaths`

Recommended guardrails:

1. require the same high-tier participation floor used by Best eligibility,
2. normalize with a saturating curve so outlier destroyer or seal-club samples do not dominate,
3. keep the weight small, in the `0.05-0.08` range,
4. reduce `player_score` weight by the same amount if KDR is added, so the formula does not double-count kill performance.

Why actual KDR can help:

1. it gives the Best model a direct frag-efficiency signal,
2. it can distinguish players with similar WR but very different carry profiles,
3. it is especially useful as a secondary separator among already-competitive players.

Why weighted KDR should not be added directly:

1. it is already embedded inside `player_score`,
2. it is intentionally tier-adjusted for a different purpose,
3. it is not the literal metric the user is asking about.

## Decision 4: Standardize ranking surfaces on `player_score`, not KDR

For ranking-oriented player lists:

1. clan roster should remain ordered by `player_score DESC`,
2. Player Explorer should default to `player_score DESC`,
3. weighted KDR should remain sortable, but only as an explicit user choice.

For non-ranking discovery surfaces:

1. keep recency-based or mode-based lists as they are,
2. do not force `player_score` onto surfaces whose purpose is `recent`, `random`, or `active-now` discovery.

This keeps the product honest about the difference between:

1. ranking,
2. discovery,
3. descriptive stats.

## Implementation Plan

## Phase 1: Add actual KDR to player data and player detail

### Backend

1. Extend `Player` in [server/warships/models.py](../../server/warships/models.py) with additive fields for the raw actual-KDR inputs and output.
2. Recommended fields:
   - `pvp_frags`
   - `pvp_survived_battles`
   - `pvp_deaths`
   - `actual_kdr`
3. Update player ingest in [server/warships/data.py](../../server/warships/data.py) to pull `statistics.pvp.frags` and `statistics.pvp.survived_battles` from the personal-data payload when available.
4. Compute `pvp_deaths` and `actual_kdr` during player refresh.
5. Expose `actual_kdr` on `PlayerSerializer` in [server/warships/serializers.py](../../server/warships/serializers.py).
6. Keep `kill_ratio` untouched for backward compatibility.

### Frontend

1. Extend the player detail type in [client/app/components/entityTypes.ts](../../client/app/components/entityTypes.ts) and [client/app/components/PlayerDetail.tsx](../../client/app/components/PlayerDetail.tsx) to include `actual_kdr`.
2. Change the fourth summary card label from `Weighted KDR` to `KDR`.
3. Change the displayed value from `player.kill_ratio` to `player.actual_kdr`.
4. Keep explorer copy and explorer column wording unchanged in this phase unless a separate copy pass is requested.

### Tests

1. Add serializer/API coverage that player detail includes `actual_kdr`.
2. Add ingest/unit coverage for:
   - normal deaths > 0 case,
   - zero-battle case,
   - zero-death case.
3. Update player-detail frontend tests to assert the card label/value swap.

## Phase 2: Evaluate KDR as a Best-ranking signal

### Recommended implementation shape

1. Add a helper in [server/warships/landing.py](../../server/warships/landing.py) that derives `high_tier_actual_kdr` from tier `5-10` ship rows when enough data exists.
2. Do not use overall actual KDR for Best.
3. Start by logging or test-driving the score offline against known examples before putting it into the live order.

### Recommended first experiment

Keep the current live Best formula intact and evaluate a hypothetical variant:

1. WR: `0.36`
2. player score: `0.17`
3. efficiency: `0.18`
4. high-tier volume: `0.10`
5. ranked: `0.06`
6. clan: `0.04`
7. high-tier actual KDR: `0.09`

If that is too aggressive, fall back to `0.05` KDR weight and take the delta only from `player_score`.

### Acceptance standard

Only ship KDR into Best if corpus checks show that it:

1. improves ordering among competitive high-tier players,
2. does not lift low-volume outliers above stronger all-around players,
3. does not recreate the earlier low-tier false-positive problem.

### Corpus findings from the 2026-03-18 landing-cohort refresh

The current bounded refresh covered the live landing cohorts for `best`, `sigma`, and `random`.

Observed rollout facts:

1. schema migration `0030_player_actual_kdr_fields` applied successfully,
2. a broad active-corpus refresh remains too large to force inline at once,
3. the bounded landing cohort refresh completed successfully for `120` unique players with `0` remaining `actual_kdr` gaps in that slice.

Observed Best-vs-KDR findings:

1. on the refreshed `Best` top-40 cohort, the Spearman correlation between live Best rank and overall `actual_kdr` rank was only `0.1328`,
2. only `3` names overlapped between one sampled live Best top 10 and the same cohort's overall-`actual_kdr` top 10,
3. the highest overall-`actual_kdr` names in the Best cohort were materially different from the current Best leaders.

Interpretation:

1. overall `actual_kdr` is not aligned closely enough with the live Best ordering to justify adding it directly,
2. it would likely over-reward specialist or survival-skewed outliers if used as a raw live term,
3. if KDR is revisited for Best, it should be a high-tier-scoped signal derived from competitive-tier rows, not the newly added overall player-detail metric.

Recommendation after corpus pass:

1. keep the shipped player-detail `actual_kdr` change,
2. keep Player Explorer defaulted to `player_score DESC`,
3. do not change the live Best formula in this tranche,
4. only revisit Best KDR after implementing and evaluating `high_tier_actual_kdr` separately.

## Phase 3: Make ranking order explicit on player-centric lists

### Clan page

1. Leave backend ordering in [server/warships/views.py](../../server/warships/views.py) as `player_score DESC`.
2. Keep the existing regression test that proves clan member ordering.
3. Add a client-side test if needed to verify the rendered order matches API order and is not locally re-sorted.

### Player Explorer

1. Change the default sort in [client/app/components/PlayerExplorer.tsx](../../client/app/components/PlayerExplorer.tsx) from `pvp_battles DESC` to `player_score DESC` for ranking-first player views.
2. Keep `kill_ratio` as an available opt-in sort.
3. Do not change backend support for `sort=kill_ratio`; just stop making it the implicit ranking story.

### Explicit non-goals

Do not change these surfaces in this runbook unless product intent changes:

1. landing `random` players,
2. landing `recent` players,
3. landing `active` or recency-driven lists,
4. clan charts whose purpose is not roster ranking.

## Validation Plan

## Automated

Backend:

1. player detail returns `actual_kdr`,
2. player detail still returns `kill_ratio` unchanged,
3. clan members remain ordered by `player_score DESC`,
4. players explorer default ordering tests are updated if the UI default changes,
5. any Best-ranking KDR experiment is covered with focused ordering fixtures.

Frontend:

1. player detail renders `KDR` and the new `actual_kdr` value,
2. Player Explorer default request uses `sort=player_score&direction=desc` if Phase 3 is implemented,
3. clan member render order remains consistent with API order.

## Manual

1. Compare one player with high weighted KDR but modest actual deaths profile and confirm the detail card now reflects literal KDR.
2. Verify clan roster order still reads like `best-known members first` rather than kill metric ordering.
3. If Player Explorer default sort is changed, confirm the first page reads like a ranking surface instead of a battle-count surface.
4. If Best KDR experimentation is enabled, spot-check known high-tier competitive players against prior Best output.

## Risks

1. Reusing `kill_ratio` for actual KDR would silently break explorer semantics and historical tests.
2. Adding overall KDR directly to Best would double-count kill performance through `player_score`.
3. Using deaths-derived KDR without storing raw numerator and denominator would make validation and future UI explanations harder.
4. Changing every player list to `player_score` would blur the difference between discovery and ranking surfaces.

## Recommended Outcome

Ship this as two separate decisions, not one blended rewrite:

1. first, add additive `actual_kdr` and swap the player detail card to it,
2. second, treat KDR-in-Best as a measured follow-up using high-tier actual KDR only,
3. third, preserve clan roster ranking and move Player Explorer defaults to `player_score` for ranking-first player views.
