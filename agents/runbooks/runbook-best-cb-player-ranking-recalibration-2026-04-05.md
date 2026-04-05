# Runbook: Best CB Player Ranking Recalibration

_Created: 2026-04-05_
_QA: 2026-04-05_

## Summary

The Best CB player ranking on the landing page has two distinct issues:

1. **Chart/tooltip data mismatch** — The scatter chart and tooltip display overall PvP stats (`pvp_battles`, `pvp_ratio`) while ranking by hidden CB-specific stats. A player at 8K overall PvP games / 72% overall WR appears at #1 because their CB stats (2,136 battles / 80.5% WR) are never shown. This makes the ranking appear broken.

2. **Ranking formula imbalance** — The Wilson Lower Bound (WLB) formula converges to raw WR above ~500 battles, so volume (games played) has negligible impact on ranking among top CB players. The user wants sustained high WR over thousands of games to be rewarded more.

## Bug: Chart and Tooltip Show Wrong Data in CB Mode

### Root cause

The landing page Best CB mode **sorts** by CB-specific stats via `cb_sort_score` (`landing.py:1964`), but:

- `_finalize_best_player_payload()` (`landing.py:1591-1592`) **strips** `clan_battle_total_battles` and `clan_battle_seasons_participated` from the API response before it reaches the frontend
- `clan_battle_win_rate` survives to the API response but the chart ignores it
- `LandingPlayerSVG.tsx` has **no mode-aware logic** — it always plots `pvp_battles` (x-axis, line 219) and `pvp_ratio` (y-axis, line 220) regardless of which Best sub-mode is active
- The tooltip (`LandingPlayerSVG.tsx:169-178`) always shows overall wins, overall battles, and overall WR

### Result

- Snoozing_Mako plots at 8K games / 72% WR on the chart (overall PvP)
- But ranks #1 based on 2,136 CB battles / 80.5% CB WR (hidden from the user)
- The chart positions directly contradict the ranking order

### Fix scope (ship before or alongside ranking recalibration)

**Backend** (`server/warships/landing.py`):
- Stop stripping `clan_battle_total_battles` and `clan_battle_seasons_participated` in `_finalize_best_player_payload()` (lines 1591-1592) — these fields are needed for the chart

**Frontend** (`client/app/components/LandingPlayerSVG.tsx`):
- Accept a `sort` or `mode` prop to distinguish CB mode from overall mode
- In CB mode: use `clan_battle_total_battles` for x-axis, `clan_battle_win_rate` for y-axis
- Update tooltip to show CB Battles, CB Win Rate, CB Seasons in CB mode
- Update axis labels: "CB Battles" and "CB Win Rate" instead of "PvP Battles" and "Player WR"

**Frontend** (`client/app/components/entityTypes.ts`):
- `LandingPlayer` interface already has `is_clan_battle_player` (line 30) and `clan_battle_win_rate` (line 31) but is missing `clan_battle_total_battles` and `clan_battle_seasons_participated` — add these fields

## Current Ranking Formula

**Location:** `server/warships/landing.py:362-378`

```python
cb_sort_score = (0.92 * WLB(wr, battles)) + (0.08 * min(seasons / 10, 1.0))
```

### Components

| Component | Weight | Description |
|---|---|---|
| Wilson Lower Bound | 92% | Statistical lower bound of true WR given observed WR and sample size |
| Season Depth | 8% | `min(seasons_participated / 10, 1.0)` — caps at 10 seasons |

### Wilson Lower Bound formula

**Location:** `server/warships/landing.py:345-359`

```python
z = 1.2815  # 80th percentile one-sided confidence
proportion = wr / 100.0
center = proportion + (z^2 / (2 * battles))
margin = z * sqrt(((proportion * (1 - proportion)) + (z^2 / (4 * battles))) / battles)
lower_bound = (center - margin) / (1 + z^2 / battles)
```

The WLB naturally penalizes small sample sizes — with few battles, the confidence interval is wide and the lower bound drops. But at the battle counts seen in top CB players (2,000-6,000+), the interval is already very tight and the WLB converges to the raw WR. Volume differences above ~500 battles have negligible impact on the score.

### Constants

**Location:** `server/warships/landing.py:118-121`

```python
LANDING_PLAYER_CB_SORT_WILSON_Z = 1.2815515655446004  # z-score for 80th percentile one-sided
LANDING_PLAYER_CB_SORT_SEASON_DEPTH_WEIGHT = 0.08      # 8% weight for season count
LANDING_PLAYER_CB_SORT_MAX_BATTLES = 400               # UNUSED — defined but never referenced
LANDING_PLAYER_CB_SORT_MAX_SEASONS = 10                # cap for season depth normalization
```

`MAX_BATTLES = 400` is defined at line 120 but never referenced in any scoring function. It can be repurposed for the volume weight cap (Option A) or removed.

### Sort tiebreakers

**Location:** `server/warships/landing.py:1964-1974`

After scoring, the final sort is:
1. `cb_sort_score` (descending)
2. `clan_battle_win_rate` (descending)
3. `clan_battle_total_battles` (descending)
4. `clan_battle_seasons_participated` (descending)
5. `name` (ascending)

### Data flow

| Step | Location | What happens |
|---|---|---|
| Query | `landing.py:1945-1955` | Fetch from `PlayerExplorerSummary` via F-expressions, ordered by `clan_battle_total_battles` DESC |
| Serialize | `landing.py:1356` (`_serialize_landing_player_rows`) | Populates `clan_battle_total_battles`, `clan_battle_seasons_participated`, `clan_battle_win_rate`, `is_clan_battle_player` |
| Score | `landing.py:1958-1962` | Filters to `is_clan_battle_player`, computes `cb_sort_score` per row |
| Sort | `landing.py:1964-1974` | Sorts by score + tiebreakers |
| Cleanup | `landing.py:1977` | Pops `cb_sort_score` from each row |
| Finalize | `landing.py:1577-1593` | Strips `clan_battle_total_battles` and `clan_battle_seasons_participated` from response |

### Test coverage

**Location:** `server/warships/tests/test_landing.py:271-326`

`test_materialize_landing_player_best_snapshot_persists_cb_order()` tests two players with same CB WR (60%) but different volumes (2400 vs 240 battles). Asserts the higher-volume player ranks first. This validates that WLB differentiates at small sample sizes but does not cover the convergence behavior at 2000+ battles.

## Live Data (2026-04-05, NA realm)

| Rank | Player | CB Battles | CB WR% | CB Seasons | WLB | Current Score |
|---|---|---|---|---|---|---|
| 1 | Snoozing_Mako | 2,136 | 80.5% | 30 | 0.7938 | 0.8103 |
| 2 | John_The_Ruthless | 2,993 | 79.4% | 39 | 0.7844 | 0.8016 |
| 3 | Amatsukaze_DD | 2,645 | 79.0% | 23 | 0.7797 | 0.7973 |
| 4 | Doyl | 2,464 | 76.1% | 40 | 0.7498 | 0.7698 |
| 5 | bfk_ferlyfe | 5,741 | 73.5% | 42 | 0.7275 | 0.7493 |

All five players have seasons >= 10, so the season depth term is capped at 1.0 for everyone — it contributes a flat +0.08 to all scores and does not differentiate.

### Why the ranking feels off

At 2,000+ battles, the WLB is within 1-2% of raw WR. The ranking is effectively: **sort by WR, with season count as a constant**. Volume contributes almost nothing.

The 7-point WR gap between Snoozing_Mako (80.5%) and bfk_ferlyfe (73.5%) completely dominates, even though bfk_ferlyfe has nearly 3x the games.

## Rebalancing Options

The goal: WR should remain the primary signal, but sustained high WR over many games should be rewarded more than it currently is.

### Option A: Add explicit volume weight (recommended)

Add a third component: normalized battle volume.

```python
volume_score = min(battles / MAX_BATTLES, 1.0)
cb_sort_score = (w_wr * WLB) + (w_vol * volume_score) + (w_season * season_depth)
```

**Tuning**: Set `MAX_BATTLES` to a meaningful cap (e.g., 3000-5000) and `w_vol` to 10-20%. This gives a linear bonus for more games up to the cap.

**Pros**: Simple, transparent, easy to tune.
**Cons**: Linear volume scaling may over-reward grinders with mediocre WR. Requires choosing the right cap and weight.

### Option B: Log-scaled volume multiplier

Apply a diminishing-returns volume bonus as a multiplier on the WLB.

```python
volume_multiplier = 1.0 + (alpha * log(battles) / log(MAX_BATTLES))
adjusted_wr = WLB * volume_multiplier
cb_sort_score = (0.92 * adjusted_wr) + (0.08 * season_depth)
```

**Tuning**: `alpha` controls how much volume matters. At `alpha=0.1`, a player with 5,000 battles gets a ~10% boost over a player with 500 battles.

**Pros**: Diminishing returns feel natural — going from 100 to 1,000 games matters more than 5,000 to 6,000.
**Cons**: Less intuitive to explain; multiplier can push adjusted scores above 1.0 (cosmetic, doesn't affect ordering).

### Option C: Lower Wilson z-score

Reduce `z` from 1.2815 to something lower (e.g., 1.0 or 0.6745). This widens the confidence interval, penalizing lower sample sizes more aggressively.

**Pros**: No new component; stays within the WLB framework.
**Cons**: At 2,000+ battles, the WLB is already very tight — lowering z won't meaningfully differentiate players at these volumes. Only helps distinguish <500 battle players from >500 battle players.

### Option D: Bayesian weighted WR (empirical Bayes)

Use a prior based on the population average WR to shrink individual WR estimates toward the mean, weighted by sample size.

```python
prior_wr = population_mean_cb_wr  # e.g., 50%
prior_weight = K  # e.g., 200 "phantom" battles
adjusted_wr = (battles * wr + prior_weight * prior_wr) / (battles + prior_weight)
```

**Pros**: Statistically principled; naturally rewards volume.
**Cons**: Requires choosing `K` and `prior_wr`. At high battle counts (2,000+), the prior washes out and this converges to raw WR — same problem as WLB.

### Recommendation: Option A (explicit volume weight)

Most transparent and tunable. Suggested starting point:

```python
LANDING_PLAYER_CB_SORT_WILSON_Z = 1.2815515655446004
LANDING_PLAYER_CB_SORT_SEASON_DEPTH_WEIGHT = 0.05   # reduce from 0.08
LANDING_PLAYER_CB_SORT_VOLUME_WEIGHT = 0.15          # new
LANDING_PLAYER_CB_SORT_MAX_BATTLES = 4000            # repurpose existing constant
LANDING_PLAYER_CB_SORT_MAX_SEASONS = 10

volume_score = min(battles / MAX_BATTLES, 1.0)
cb_sort_score = (0.80 * WLB) + (0.15 * volume_score) + (0.05 * season_depth)
```

### Projected re-ranking with Option A (0.80/0.15/0.05, MAX_BATTLES=4000)

| Rank | Player | WLB | Volume (battles/4000) | Season | New Score | Old Rank |
|---|---|---|---|---|---|---|
| 1 | John_The_Ruthless | 0.7844 | 0.748 | 1.0 | 0.7897 | 2 |
| 2 | bfk_ferlyfe | 0.7275 | 1.000 | 1.0 | 0.7820 | 5 |
| 3 | Amatsukaze_DD | 0.7797 | 0.661 | 1.0 | 0.7729 | 3 |
| 4 | Snoozing_Mako | 0.7938 | 0.534 | 1.0 | 0.7651 | 1 |
| 5 | Doyl | 0.7498 | 0.616 | 1.0 | 0.7423 | 4 |

**Notable shifts**: Snoozing_Mako drops from #1 to #4 (highest WR but fewest battles). bfk_ferlyfe jumps from #5 to #2 (volume cap saturated at 5,741 battles). John_The_Ruthless takes #1 with the best balance of WR and volume.

**Edge case concern**: bfk_ferlyfe at 73.5% WR ranking #2 above Amatsukaze_DD at 79.0% WR may feel wrong. The 15% volume weight might be too high, or the 4000-battle cap too low. Calibration with the user is required.

## Calibration Approach

The user specified: "the exact ratio of balance will have to be derived from the data." Steps:

1. **Export top ~100 CB players** with battles, WR, seasons, and current scores
2. **Compute candidate scores** under several weight configurations (e.g., volume weights of 0.10, 0.12, 0.15, 0.20) and caps (3000, 4000, 5000)
3. **Review the resulting orderings** with the user to find the right feel
4. **Validate edge cases**: Does a 51% WR grinder with 10,000 games rank unreasonably high? Does a 90% WR player with 50 games rank unreasonably low?
5. Lock in weights and deploy

## Files to Modify

### Chart/tooltip fix (ship first)

| File | Change |
|---|---|
| `server/warships/landing.py:1591-1592` | Stop stripping `clan_battle_total_battles` and `clan_battle_seasons_participated` from API response |
| `client/app/components/entityTypes.ts` | Add `clan_battle_total_battles` and `clan_battle_seasons_participated` to `LandingPlayer` interface |
| `client/app/components/LandingPlayerSVG.tsx` | Accept mode prop; in CB mode use CB fields for axes/tooltip/labels |
| Parent component passing `sort` to `LandingPlayerSVG` | Thread the current Best sub-mode through to the SVG component |

### Ranking recalibration

| File | Change |
|---|---|
| `server/warships/landing.py:118-121` | Add `VOLUME_WEIGHT` constant, update `MAX_BATTLES` from 400 to calibrated cap |
| `server/warships/landing.py:362-378` | Update `_calculate_landing_cb_sort_score()` to include volume component |
| `server/warships/tests/test_landing.py` | Update or add test for volume-weighted scoring |

## Verification

1. Run `_calculate_landing_cb_sort_score()` on test data with old and new weights
2. Compare top-20 ordering before/after
3. Review with user for subjective feel
4. `cd server && python -m pytest warships/tests/test_landing.py -x --tb=short`
5. Deploy, inspect live landing page Best CB mode
6. Verify chart axes and tooltip show CB-specific data in CB mode

## Status

- [x] Investigation complete
- [x] Root cause identified: WLB converges to raw WR above ~500 battles, volume has no meaningful impact
- [x] Chart/tooltip data mismatch identified: chart shows overall PvP stats, not CB stats
- [x] Data flow traced: `_finalize_best_player_payload()` strips CB fields before API response
- [x] Math verified: WLB and projected scores computed to 4 decimal places
- [x] Test coverage reviewed: existing test covers WLB differentiation at low volume, not convergence at high volume
- [x] QA complete (2026-04-05)
- [x] Chart/tooltip fix: CB mode now shows CB battles/WR/seasons on axes and tooltip (v1.6.20)
- [x] Ranking recalibration: Option A implemented (0.80 WLB / 0.15 volume / 0.05 season, MAX_BATTLES=4000) (v1.6.20)
- [x] Backend: stopped stripping `clan_battle_total_battles` and `clan_battle_seasons_participated` from API response
- [ ] Deploy
- [ ] User review of live ranking feel — may need weight tuning
