# Feature Spec: Landing Player Best Ranking Recalibration

_Drafted: 2026-03-17_

## Implementation Update: 2026-04-04

The landing page `Best` surface now ships multiple backend sub-sorts behind the existing `/api/landing/players/?mode=best&sort=...` contract.

Current shipped player sub-sorts are:

1. `overall`
2. `ranked`
3. `efficiency`
4. `wr`
5. `cb`

The `ranked` sub-sort has been updated to use medal-history ordering instead of the earlier weighted recent-performance heuristic.

Current ranked ordering contract:

1. more `Gold` finishes first,
2. ties break by aggregate ranked win rate across ranked history,
3. then more `Silver` finishes,
4. then more `Bronze` finishes,
5. freshness, recent ranked volume, and player score only break later ties.

Implementation notes:

1. medal counts are derived from `Player.ranked_json`,
2. the ranking remains backend-only and does not change the landing row payload shape,
3. landing cache warmers and best-player bulk entity warming now union all shipped `best` sub-sort cohorts so non-`overall` winners stay warm.

Validation completed for this update:

1. focused API regression tests for ranked medal ordering passed in `server/warships/tests/test_views.py`,
2. frontend explanatory copy in `client/app/components/PlayerSearch.tsx` was updated to match the shipped contract.

Follow-up implementation note:

1. landing player cache invalidation now bumps the player cache namespace and treats the player dirty key as authoritative during payload reads,
2. player published fallback keys are now namespaced too, which prevents stale non-default `limit` payloads from surviving a deploy or a family invalidation,
3. focused landing cache regressions in `server/warships/tests/test_landing.py` now cover dirty rebuild behavior for player landing payloads.

## Implementation Update: 2026-04-05

The `Best` player sub-sort surfaces are now being moved off request-time recomputation and onto a DB-backed materialization path.

Current implementation status:

1. a new `LandingPlayerBestSnapshot` model stores the top-25 payload for each `realm + sort`,
2. landing Best-player requests now read the stored snapshot and slice it for smaller `limit` values,
3. if a snapshot is missing, the backend materializes it once and persists it before serving the request,
4. a dedicated management command and daily Celery Beat schedule now refresh those snapshots once per day per realm.

Operational intent:

1. all shipped Best-player sub-sorts (`overall`, `ranked`, `efficiency`, `wr`, `cb`) share the same architecture,
2. landing cache warmers republish Redis from the stored snapshot instead of recomputing the full leaderboard every warm cycle,
3. this keeps the public `/api/landing/players/?mode=best&sort=...` contract unchanged while removing the expensive Ranked cold path from the request path.

## Goal

Replace the landing page `Best` player filter with a more competitive, corpus-aware ranking that heavily discounts tiers 1-4 and ranks players using a blend of high-tier PvP performance, efficiency, achievements-derived signals, experience, and competitive activity.

The intended product outcome is:

1. low-tier bot-farm win rates no longer dominate the `Best` list,
2. the list better reflects strong tier 5-10 PvP players rather than raw overall WR,
3. the first pass stays within existing Battlestats data contracts and landing endpoint architecture.

## Problem Statement

### Current behavior

Current `best` mode in [server/warships/landing.py](server/warships/landing.py) is still fundamentally win-rate sorted:

1. candidate players are selected by overall `pvp_ratio`,
2. tier 5-10 WR is used only as a fallback replacement when a player already has more than 2500 high-tier battles,
3. players with little or no tier 5-10 play can still rank at the top if their overall WR is inflated by tiers 1-4.

That produces a list that reads as `highest visible WR` rather than `best competitive players`.

### Why this is materially wrong

User feedback is correct: tiers 1-4 are bot-heavy and should be heavily discounted relative to tiers 5-10 human play.

The current ranking still allows players with almost no competitive-tier sample to sit above strong tier 5-10 performers.

## Corpus Findings

The current data supports a stronger `Best` ranking without inventing a new telemetry system.

### Representative examples from the live corpus

Current `best` payload examples:

1. `Toby_70`: 91.81% PvP WR, 2831 PvP battles, `0` tier 5-10 PvP battles, `player_score=2.8`, no published efficiency rank.
2. `VL6NJH_E`: 91.66% PvP WR, 3801 PvP battles, `3` tier 5-10 PvP battles, `player_score=2.82`, no published efficiency rank.
3. `Slowhand57`: 86.8% PvP WR, 28925 PvP battles, `106` tier 5-10 PvP battles, 50.0% high-tier WR, `player_score=2.94`, no published efficiency rank.
4. `Geargiong`: 80.62% PvP WR, 3648 PvP battles, `3591` tier 5-10 PvP battles, 80.62% high-tier WR, `player_score=6.68`, published efficiency percentile `0.93153`.

These examples show that raw WR is surfacing low-tier specialists above materially stronger competitive players.

### Current top-40 `Best` cohort

Using the current live `best` payload:

1. `30/40` players have fewer than `1000` tier 5-10 PvP battles,
2. the median tier 5-10 share is `0.0`,
3. `17/40` have no published efficiency percentile,
4. the surface is therefore dominated by players whose rank is not strongly supported by competitive-tier or published-efficiency evidence.

### Broader active-player corpus coverage

Across visible active players with more than `2500` PvP battles:

1. `64.0%` already have a published efficiency percentile,
2. `87.7%` already have ranked participation data,
3. `47.1%` already have at least one Expert badge row,
4. `player_score >= 7` is rare (`1.7%`), which makes `player_score` a useful candidate-pool discriminator,
5. `battles_last_29_days` is currently not populated reliably enough to be a hard requirement for this first pass.

This means the first pass can safely rely on:

1. tier 5-10 WR and tier-share,
2. `player_score`,
3. published efficiency percentile when available,
4. ranked and clan-battle signals,

while using recent-activity data only as a weak tie-breaker or deferring it entirely.

## Product Definition

The landing page `Best` filter should mean:

`best competitive players currently visible on Battlestats`.

It should no longer mean:

`highest overall PvP win rate regardless of tier distribution`.

## Scope

In scope:

1. recalibrate only landing player `best` mode,
2. keep the existing `/api/landing/players/` endpoint and `mode=best` contract,
3. compute a new backend `best` ranking score using existing data,
4. preserve current landing row response shape unless a small additive debug field is explicitly approved.

Out of scope:

1. changing `random` or `sigma` ranking,
2. changing player detail ranking or explorer sorting,
3. inventing a new user-facing badge or label,
4. backfilling new telemetry before the first pass ships.

## Recommended Ranking Model

## Step 1: Broaden and improve the candidate pool

Do not seed `best` candidates from raw PvP WR.

Instead, seed candidates from the active visible player corpus using stronger prefilters:

1. `is_hidden=False`
2. `days_since_last_battle <= 180`
3. `pvp_battles > 2500`
4. `last_battle_date is not null`
5. order the candidate query by `explorer_summary__player_score DESC`, then `pvp_ratio DESC`, then `name`
6. increase the candidate pool beyond the current `400` so the reranker is not trapped inside a WR-distorted subset

Recommended starting constant:

1. `LANDING_PLAYER_BEST_CANDIDATE_LIMIT = 1200`

Reasoning:

1. `player_score` already discounts low-tier samples,
2. the corpus shows `player_score >= 7` is selective enough to produce a strong pool,
3. reranking cannot recover from a candidate pool that is already polluted by low-tier WR inflation.

## Step 2: Add a hard competitive-sample gate

Before ranking, exclude players who have effectively no tier 5-10 PvP foundation.

Recommended first-pass gate:

1. require `high_tier_pvp_battles >= 500`

Recommended fallback rule if this unexpectedly starves the list in production:

1. lower the gate to `250`,
2. do not remove the gate entirely.

Reasoning:

1. a hard gate is required because weighting alone still lets 90%+ low-tier WR accounts float upward,
2. `500` tier 5-10 battles is strict enough to remove obvious bot-tier distortion,
3. it still keeps the surface focused on established competitive play.

## Step 3: Compute a competitive best score

Rank remaining candidates by a new backend-only `best_competitive_score` on a `0.0-1.0` normalized scale.

### Proposed score components

1. `0.40` competitive WR score
2. `0.22` `player_score`
3. `0.18` efficiency and achievements score
4. `0.10` competitive battle-volume score
5. `0.06` ranked score
6. `0.04` clan-battle score

This keeps win rate primary, but no longer lets it act alone.

### Component definitions

#### 1. Competitive WR score (`40%`)

Use tier 5-10 PvP WR as the main strength signal.

Recommended definition:

1. primary input: `high_tier_pvp_ratio`
2. normalize to `0.0-1.0` using the same WR normalization family already used by `player_score`
3. if a player has `500+` high-tier battles, do not blend low-tier WR back in

Rationale:

1. this is the direct fix for tiers 1-4 distortion,
2. tier 5-10 WR is the clearest first-pass competitive signal already available in landing data.

#### 2. Player score (`22%`)

Use `explorer_summary__player_score / 10.0`.

Rationale:

1. this score already blends WR, kill ratio, survival, activity, battle volume, and a competitive-tier factor,
2. it is already heavily low-tier discounted,
3. it gives the reranker a stable all-around quality signal.

#### 3. Efficiency and achievements score (`18%`)

Use published efficiency percentile when available.

Recommended definition:

1. preferred input: `explorer_summary__efficiency_rank_percentile`
2. fallback input when percentile is missing: `explorer_summary__shrunken_efficiency_strength`
3. if neither exists, use a neutral fallback like `0.35` rather than `0.0`

Rationale:

1. efficiency percentile is the strongest existing public achievements-derived signal,
2. the shrunken badge-strength fallback prevents missing-publication players from being treated as zero-skill,
3. a neutral fallback avoids over-penalizing players simply because publication coverage is not yet universal.

#### 4. Competitive battle-volume score (`10%`)

Reward proven tier 5-10 sample size without letting pure grinders dominate.

Recommended definition:

1. normalize `high_tier_pvp_battles` with a saturating log curve,
2. reach near-max contribution around `5000` high-tier battles.

Rationale:

1. a 70% WR over 6000 competitive games should outrank the same WR over 520 games,
2. saturation prevents battle volume from overpowering skill.

#### 5. Ranked score (`6%`)

Use ranked participation as a secondary competitive proof point.

Recommended inputs:

1. `latest_ranked_battles`
2. `highest_ranked_league_recent`

Recommended behavior:

1. award a small bonus for meaningful recent ranked participation,
2. scale that bonus upward for Silver, Gold, and higher leagues,
3. keep this contribution intentionally smaller than WR, score, and efficiency.

#### 6. Clan-battle score (`4%`)

Use clan battle participation and results as another small competitive proof point.

Recommended inputs:

1. `clan_battle_summary.total_battles`
2. `clan_battle_summary.win_rate`

Recommended behavior:

1. give a modest bonus for meaningful clan-battle participation,
2. allow stronger clan-battle WR to improve ordering within otherwise similar players,
3. avoid turning `Best` into a clan-battle leaderboard.

## Step 4: Apply an explicit low-tier penalty multiplier

Even after the hard gate, players with only a thin high-tier slice should still be discounted relative to players whose history is mostly tier 5-10.

Recommended multiplier:

1. compute `competitive_share = high_tier_pvp_battles / pvp_battles`
2. convert that to a multiplier from `0.55` to `1.0`
3. use a steep curve so low competitive share is still meaningfully penalized

Recommended first-pass shape:

1. `competitive_share <= 0.20` maps near `0.55`
2. `competitive_share >= 0.80` maps to `1.0`

Final ranking:

1. `final_best_score = best_competitive_score * competitive_share_multiplier`

Reasoning:

1. the hard gate removes the worst offenders,
2. the multiplier still differentiates between players who dabble in high tier and players who live there,
3. this directly addresses the user requirement that tiers 1-4 be heavily, heavily discounted.

## Data Availability Notes

The corpus supports this first pass, but not every field should be treated equally.

Recommended usage by reliability:

1. strong inputs now: `high_tier_pvp_ratio`, `high_tier_pvp_battles`, `player_score`, `efficiency_rank_percentile`, ranked summary, clan-battle summary
2. optional fallback now: `shrunken_efficiency_strength`, `expert_count`
3. not a hard dependency yet: `battles_last_29_days`, `active_days_last_29_days`

Because `battles_last_29_days` is not currently populated reliably in the active-player corpus, recent-activity fields should not be a hard gate or high-weight score component in the first pass.

## API Contract Recommendation

Keep the endpoint shape unchanged:

1. `GET /api/landing/players/?mode=best&limit=40`
2. preserve the existing landing row payload

Implementation should compute the new ranking internally and then return the same row schema already used by the client.

Do not add a user-visible score field in this tranche.

If debugging is needed during rollout, add temporary logging or tests rather than shipping `best_competitive_score` to the client.

## Candidate Implementation Notes

Recommended backend changes in [server/warships/landing.py](server/warships/landing.py):

1. replace the raw-WR candidate query in `_build_best_landing_players`
2. annotate or fetch the extra explorer-summary fields needed for the composite score
3. compute `high_tier_pvp_battles` and `high_tier_pvp_ratio` as part of row serialization or candidate scoring
4. compute ranked and clan-battle sub-scores in memory for the bounded candidate pool
5. sort by `final_best_score DESC`, then `high_tier_pvp_ratio DESC`, then `player_score DESC`, then `name ASC`

## Validation Requirements

Add focused backend tests covering:

1. a low-tier-only 90% WR player no longer outranks a strong tier 5-10 competitive player,
2. players with insufficient tier 5-10 sample are excluded from `best`,
3. published efficiency improves ranking among otherwise similar players,
4. ranked and clan-battle signals act as secondary tie-breakers rather than dominating the list,
5. the endpoint still returns deterministic top-40 results,
6. the existing `random` and `sigma` behaviors remain unchanged.

Recommended regression fixture pattern:

1. create one player shaped like `Toby_70` with elite overall WR but zero high-tier battles,
2. create one player with lower raw WR but strong high-tier WR, meaningful ranked/clan-battle history, and published efficiency,
3. assert the competitive player ranks above the low-tier WR farmer.

## Rollout Guidance

This should ship as a quiet backend recalibration, not as a new UI concept.

Recommended rollout steps:

1. implement the composite ranking behind existing `mode=best`,
2. warm the landing cache after deploy,
3. manually inspect the top 40 returned by `mode=best`,
4. spot-check known false positives such as `Toby_70` and similar accounts,
5. confirm that the resulting list reads like strong competitive players rather than bot-tier specialists.

## Success Criteria

This pass is successful if:

1. the top `Best` rows are no longer dominated by zero-high-tier or near-zero-high-tier accounts,
2. strong tier 5-10 players with solid efficiency, ranked, or clan-battle signals move upward,
3. the landing list still feels stable, fast, and deterministic,
4. the result is visibly more credible to experienced players reviewing the landing page.
