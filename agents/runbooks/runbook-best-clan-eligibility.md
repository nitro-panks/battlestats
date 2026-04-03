# Runbook: Best Clan Eligibility Criteria

**Created**: 2026-03-28
**Status**: Implemented 2026-03-28, updated 2026-04-03

## Purpose

Define which clans qualify as "Best" for the bulk entity cache loader's clan-member cohort and for the landing page Best clans surface. The goal is to select 25 clans whose members are worth pre-loading into Redis, ensuring those clans represent active, high-quality organizations — not hollowed-out shells of formerly great clans.

## Current State

The shared backend helper `score_best_clans()` in `data.py` is now the source of truth for both the bulk cache loader cohort and the landing page Best clans payload. The landing best-clans query in `landing.py` preserves that backend order. As of 2026-04-03, the landing page client no longer re-filters or re-sorts Best clans after fetch; it only falls back to recent clans when the backend returns an empty Best payload.

## Proposed Eligibility Criteria

### Hard Filters (must pass all)

| Criterion               | Field                                                    | Threshold    | Rationale                                              |
| ----------------------- | -------------------------------------------------------- | ------------ | ------------------------------------------------------ |
| Minimum members         | `Clan.members_count`                                     | > 10         | Excludes micro-clans that aren't representative        |
| Minimum tracked members | `Player.objects.filter(clan_id=...).count()` (annotated) | ≥ 5          | Must have enough players in our DB to be worth caching |
| Minimum activity ratio  | `Clan.cached_active_member_count / Clan.members_count`   | ≥ 0.40 (40%) | Filters out clans where most members have gone dormant |
| Minimum total battles   | `Clan.cached_total_battles`                              | ≥ 50,000     | Ensures statistical significance for win rate          |

### Scoring Formula

Clans passing the hard filters are ranked by a composite score. Each component is normalized to [0, 1] across the candidate pool before weighting.

```
clan_score = (
    0.30 × norm(clan_wr)
    + 0.25 × norm(activity_ratio)
    + 0.20 × norm(avg_member_score)
    + 0.15 × norm(recency_weighted_cb_score)
    + 0.10 × norm(log(total_battles))
)
```

#### Component Definitions

**1. Clan Win Rate (30%)**

- Field: `Clan.cached_clan_wr`
- Higher is better. The primary signal of clan quality.

**2. Activity Ratio (25%)**

- Formula: `cached_active_member_count / members_count`
- Higher is better. Distinguishes living clans from dormant ones.

**3. Average Member Score (20%)**

- Formula: mean `player_score` across tracked members (players with `clan_id` FK pointing to this clan), via `PlayerExplorerSummary.player_score`
- Higher is better. Ensures the clan has individually skilled players, not just a good collective record from a past era.

**4. Recency-Weighted Clan Battle Score (15%)**

- Source: `PlayerExplorerSummary` fields on tracked members:
  - `clan_battle_total_battles` (int, nullable)
  - `clan_battle_overall_win_rate` (float, nullable)
  - `clan_battle_summary_updated_at` (datetime, nullable) — last time CB data was refreshed for this member
- Per-member CB contribution:
  ```
  member_cb = clan_battle_total_battles × clan_battle_overall_win_rate × recency_factor
  ```
- Members with null CB data contribute 0.
- `recency_factor` uses `clan_battle_summary_updated_at` to approximate recency:
  ```
  years_since_update = (now - clan_battle_summary_updated_at).days / 365.25
  recency_factor = 1.0 / (1.0 + years_since_update)
  ```

  - Updated this year: weight ≈ 1.0
  - Updated 1 year ago: weight = 0.5
  - Updated 2 years ago: weight ≈ 0.33
  - Never updated (null): weight = 0
- Clan-level CB score = mean of all tracked members' `member_cb` values.
- This component rewards clans that actively participate in the game's primary competitive mode _recently_, not just historically.

**5. Battle Volume (10%)**

- Field: `log(Clan.cached_total_battles)`
- Log-scaled to prevent mega-clans from dominating purely on volume. Serves as a tiebreaker and confidence signal.

### Selection

1. Apply hard filters
2. Compute composite score for each surviving clan
3. Sort descending by score
4. Take top 25

## Anti-Patterns This Prevents

| Bad candidate                                  | Why it fails                                         |
| ---------------------------------------------- | ---------------------------------------------------- |
| Tiny 5-member clan with 70% WR                 | Fails members_count > 10                             |
| 40-member clan, 35 inactive                    | Fails activity ratio < 0.40                          |
| Large clan, mediocre players, old CB glory     | Low avg_member_score + low recency_weighted_cb_score |
| Brand-new clan, 15 active members, 200 battles | Fails minimum total_battles                          |
| Former top clan, all members quit              | Fails activity ratio                                 |

## Data Dependencies

| Field                                                  | Source                             | Updated by             |
| ------------------------------------------------------ | ---------------------------------- | ---------------------- |
| `Clan.cached_clan_wr`                                  | Denormalized from member snapshots | `update_clan_data()`   |
| `Clan.cached_total_battles`                            | Denormalized                       | `update_clan_data()`   |
| `Clan.cached_active_member_count`                      | Denormalized                       | `update_clan_data()`   |
| `Clan.members_count`                                   | WG API clan detail                 | Clan crawl             |
| `PlayerExplorerSummary.player_score`                   | Computed from PvP stats            | `update_player_data()` |
| `PlayerExplorerSummary.clan_battle_total_battles`      | Parsed from CB seasons             | Explorer summary build |
| `PlayerExplorerSummary.clan_battle_overall_win_rate`   | Parsed from CB seasons             | Explorer summary build |
| `PlayerExplorerSummary.clan_battle_summary_updated_at` | Set on CB summary refresh          | Explorer summary build |

## Landing Page Contract

The landing page Best clans surface now treats the backend payload as authoritative.

- `client/app/components/PlayerSearch.tsx` shows the Best clans in the exact order returned by `/api/landing/clans/?mode=best`.
- The client does not re-apply battle/activity thresholds or re-sort by win rate.
- The only client fallback is: if the Best payload is empty, show recent clans with the existing warmup notice.
- The tooltip text in `PlayerSearch.tsx` should continue to describe the backend composite score rather than any client-side approximation logic.

## Implementation Notes

- The scoring function (`score_best_clans()`) lives in `data.py` and is shared by both the bulk cache loader and the landing best-clans query.
- Hard filters are applied via ORM queryset annotations. The composite scoring is done in Python because the CB recency weighting requires per-member aggregation that is complex in pure ORM.
- Normalization: use min-max scaling across the candidate pool after hard filters. Handle edge cases where all values are identical (set normalized value to 0.5).
- The composite score does not need to be persisted — it's computed on each bulk load run (every 12h) and each landing cache refresh (every 55 min).
- Log the top-25 clan IDs and scores on each run for observability.
- The landing client should remain a thin renderer for Best clans. If the ranking logic changes again, update the backend helper and tooltip copy instead of adding client-side ranking rules.

## Code Locations (to modify)

- `server/warships/data.py` — New shared helper: `score_best_clans(limit)`, update `bulk_load_player_cache()` clan selection
- `server/warships/landing.py` — Update `_build_best_landing_clans()` to call `score_best_clans()`
- `client/app/components/PlayerSearch.tsx` — Render backend Best payload order directly and keep only the empty-payload fallback to recent clans
- `agents/runbooks/runbook-bulk-entity-cache-loader.md` — Cross-reference this runbook for eligibility criteria

## Validation

- `client/app/components/__tests__/PlayerSearch.test.tsx` covers that Best clan mode preserves backend ordering and does not apply an extra client-side filter pass.
