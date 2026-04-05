# Spec: Best Overall Player Ranking Recalibration

_Created: 2026-04-05_
_QA: 2026-04-05 (revised)_
_Status: Implemented — pending deploy_

## Decision

**Option B: Ranked as a multiplier.** Non-ranked players are unaffected (multiplier = 1.0). Ranked achievement amplifies existing quality but cannot compensate for weak randoms performance.

## QA Revision (2026-04-05)

Original spec proposed adding `ranked_overall_win_rate` as a model field with migration, enrichment pipeline changes, and backfill. QA found this is unnecessary:

1. **`_summarize_ranked_medal_history()`** (landing.py:203-242) already computes `ranked_overall_win_rate` from `ranked_json` for the ranked sort path
2. The best overall path already loads `ranked_json` in `_best_landing_player_candidate_rows()` (line 1574) and pops it in `_serialize_landing_player_rows()` (line 1392)
3. We just need to call `_summarize_ranked_medal_history()` during serialization and put `ranked_overall_win_rate` on the row
4. `ranked_seasons_participated` is already on `PlayerExplorerSummary` (model line 186) — just needs an F-annotation in the candidate query

This eliminates Steps 1-2 entirely (no model, no migration, no enrichment, no backfill).

Step 5 was also backwards: the spec said "stop popping `ranked_overall_win_rate`" but the pop at line 1596 should STAY (scoring-only field). We need to ADD a pop for `ranked_seasons_participated`.

## Implementation Steps

Work is ordered so each step is independently deployable and testable.

### Step 1: Wire ranked fields into landing serialization pipeline

**File: `server/warships/landing.py`** — `_best_landing_player_candidate_rows()` annotation block (lines 1546-1561):

Add annotation after line 1554:
```python
ranked_seasons_participated=F('explorer_summary__ranked_seasons_participated'),
```

Add to the `.values()` block (after line 1579):
```python
'ranked_seasons_participated',
```

**File: `server/warships/landing.py`** — `_serialize_landing_player_rows()` (lines 1386-1444):

After the `ranked_rows` pop (line 1392), compute `ranked_overall_win_rate` from `ranked_rows` using `_summarize_ranked_medal_history()`:
```python
ranked_medal_summary = _summarize_ranked_medal_history(ranked_rows or [])
```

After line 1406, add:
```python
row['ranked_overall_win_rate'] = ranked_medal_summary.get('ranked_overall_win_rate')
```

Note: `ranked_seasons_participated` is already on the row dict from the F-annotation + `.values()` — no extra population needed.

### Step 2: Rewrite scoring formula

**File: `server/warships/landing.py`** — constants (lines 101-106):

Replace:
```python
LANDING_PLAYER_BEST_WR_WEIGHT = 0.40
LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT = 0.22
LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT = 0.18
LANDING_PLAYER_BEST_VOLUME_WEIGHT = 0.10
LANDING_PLAYER_BEST_RANKED_WEIGHT = 0.06
LANDING_PLAYER_BEST_CLAN_WEIGHT = 0.04
```

With:
```python
LANDING_PLAYER_BEST_WR_WEIGHT = 0.40
LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT = 0.22
LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT = 0.18
LANDING_PLAYER_BEST_VOLUME_WEIGHT = 0.10
LANDING_PLAYER_BEST_CLAN_WEIGHT = 0.10
LANDING_PLAYER_BEST_RANKED_BOOST = 0.15
LANDING_PLAYER_BEST_RANKED_QUALITY_LEAGUE_WEIGHT = 0.35
LANDING_PLAYER_BEST_RANKED_QUALITY_WR_WEIGHT = 0.25
LANDING_PLAYER_BEST_RANKED_QUALITY_DEPTH_WEIGHT = 0.25
LANDING_PLAYER_BEST_RANKED_QUALITY_VOLUME_WEIGHT = 0.15
LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_SEASONS = 15
LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_BATTLES = 50
```

Note: five base weights sum to 1.0 (`0.40 + 0.22 + 0.18 + 0.10 + 0.10`). Ranked is no longer an additive component — it's a multiplicative boost.

**File: `server/warships/landing.py`** — add new function after `_normalize_best_clan_score()` (after line 288):

```python
def _ranked_quality_score(row: dict) -> float:
    seasons = max(int(row.get('ranked_seasons_participated') or 0), 0)
    latest_battles = max(int(row.get('latest_ranked_battles') or 0), 0)
    league = row.get('highest_ranked_league_recent')
    ranked_wr = row.get('ranked_overall_win_rate')

    if seasons == 0 and latest_battles == 0:
        return 0.0

    league_score = _ranked_league_score(league)
    wr_score = _normalize_best_wr_score(ranked_wr) if ranked_wr is not None else 0.0
    depth_score = _clamp(seasons / LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_SEASONS, 0.0, 1.0)
    volume_score = _clamp(
        math.log1p(latest_battles) / math.log1p(LANDING_PLAYER_BEST_RANKED_QUALITY_MAX_BATTLES),
        0.0, 1.0,
    )

    return round(
        LANDING_PLAYER_BEST_RANKED_QUALITY_LEAGUE_WEIGHT * league_score +
        LANDING_PLAYER_BEST_RANKED_QUALITY_WR_WEIGHT * wr_score +
        LANDING_PLAYER_BEST_RANKED_QUALITY_DEPTH_WEIGHT * depth_score +
        LANDING_PLAYER_BEST_RANKED_QUALITY_VOLUME_WEIGHT * volume_score,
        4,
    )
```

**File: `server/warships/landing.py`** — rewrite `_calculate_landing_best_score()` (lines 312-332):

Replace:
```python
def _calculate_landing_best_score(row: dict) -> float:
    base_score = (
        LANDING_PLAYER_BEST_WR_WEIGHT * _normalize_best_wr_score(
            row.get('high_tier_pvp_ratio')) +
        LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT * _normalize_best_player_score(
            row.get('player_score')) +
        LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT * _normalize_best_efficiency_score(
            row.get('efficiency_rank_percentile'), row.get('shrunken_efficiency_strength')) +
        LANDING_PLAYER_BEST_VOLUME_WEIGHT * _normalize_best_volume_score(
            row.get('high_tier_pvp_battles')) +
        LANDING_PLAYER_BEST_RANKED_WEIGHT * _normalize_best_ranked_score(
            row.get('latest_ranked_battles'), row.get('highest_ranked_league_recent')) +
        LANDING_PLAYER_BEST_CLAN_WEIGHT * _normalize_best_clan_score(
            row.get('is_clan_battle_player'), row.get('clan_battle_win_rate'))
    )

    return round(
        base_score * _competitive_share_multiplier(
            row.get('pvp_battles'), row.get('high_tier_pvp_battles')),
        6,
    )
```

With:
```python
def _calculate_landing_best_score(row: dict) -> float:
    base_score = (
        LANDING_PLAYER_BEST_WR_WEIGHT * _normalize_best_wr_score(
            row.get('high_tier_pvp_ratio')) +
        LANDING_PLAYER_BEST_PLAYER_SCORE_WEIGHT * _normalize_best_player_score(
            row.get('player_score')) +
        LANDING_PLAYER_BEST_EFFICIENCY_WEIGHT * _normalize_best_efficiency_score(
            row.get('efficiency_rank_percentile'), row.get('shrunken_efficiency_strength')) +
        LANDING_PLAYER_BEST_VOLUME_WEIGHT * _normalize_best_volume_score(
            row.get('high_tier_pvp_battles')) +
        LANDING_PLAYER_BEST_CLAN_WEIGHT * _normalize_best_clan_score(
            row.get('is_clan_battle_player'), row.get('clan_battle_win_rate'))
    )

    ranked_multiplier = 1.0 + (
        LANDING_PLAYER_BEST_RANKED_BOOST * _ranked_quality_score(row))

    return round(
        base_score * ranked_multiplier * _competitive_share_multiplier(
            row.get('pvp_battles'), row.get('high_tier_pvp_battles')),
        6,
    )
```

### Step 3: Clean up `_finalize_best_player_payload()`

**File: `server/warships/landing.py`** — `_finalize_best_player_payload()` (lines 1587-1601):

Keep the existing pop for `ranked_overall_win_rate` (line 1596) — it's a scoring-only field. Add a pop for `ranked_seasons_participated` since it's now on the row from the F-annotation:

```python
row.pop('ranked_seasons_participated', None)
```

Sort tiebreakers in `_build_best_overall_landing_players()` (lines 1721-1731) need no changes — `best_competitive_score` is the primary sort key, which now includes the ranked multiplier.

## File Change Summary

| Step | File | Lines | Change |
|---|---|---|---|
| 1 | `server/warships/landing.py` | 1546-1561 | Add `ranked_seasons_participated` F-annotation |
| 1 | `server/warships/landing.py` | 1565-1582 | Add `ranked_seasons_participated` to `.values()` |
| 1 | `server/warships/landing.py` | 1392 | Compute `ranked_overall_win_rate` from `ranked_json` via `_summarize_ranked_medal_history()` |
| 2 | `server/warships/landing.py` | 101-106 | Replace weight constants |
| 2 | `server/warships/landing.py` | after 288 | Add `_ranked_quality_score()` |
| 2 | `server/warships/landing.py` | 312-332 | Rewrite `_calculate_landing_best_score()` with multiplier |
| 3 | `server/warships/landing.py` | 1587-1601 | Add `ranked_seasons_participated` pop |

## Expected Scoring Behavior

### Base score components (sum to 1.0)

| Component | Weight | Max contribution |
|---|---|---|
| High-Tier WR | 0.40 | 0.40 |
| Player Score | 0.22 | 0.22 |
| Efficiency | 0.18 | 0.18 |
| Volume | 0.10 | 0.10 |
| Clan Battle | 0.10 | 0.10 |

### Ranked multiplier

| Player Profile | Ranked Quality | Multiplier |
|---|---|---|
| Gold / 135b / 28s / 53.5% WR | 0.856 | 1.128 |
| Silver / 69b / 17s / ~58% WR | 0.790 | 1.119 |
| Gold / 146b / 4s / ~55% WR | 0.692 | 1.104 |
| Bronze / 3b / 5s / ~50% WR | 0.321 | 1.048 |
| No ranked data | 0.0 | 1.000 |

### Saturation thresholds

- `volume_score` saturates at `latest_ranked_battles >= 50` (configurable via `MAX_BATTLES`)
- `depth_score` saturates at `ranked_seasons_participated >= 15` (configurable via `MAX_SEASONS`)
- Once saturated, only `league_score` and `wr_score` differentiate — quality over quantity

### Score flow

```
base_score (0-1.0)
  × ranked_multiplier (1.0 to 1.15)
  × competitive_share_multiplier (0.55 to 1.0)
= best_competitive_score
```

## Backfill Strategy

No backfill needed. `ranked_overall_win_rate` is computed on-the-fly from `ranked_json` during serialization, not stored on the model. `ranked_seasons_participated` is already populated by the enrichment pipeline.

Post-deploy, re-materialize the snapshot to pick up the new scoring:

```python
from warships.landing import materialize_landing_player_best_snapshot
materialize_landing_player_best_snapshot('overall', realm='na')
```

## Test Plan

### Unit test: `_ranked_quality_score`

```python
def test_ranked_quality_score_no_ranked():
    assert _ranked_quality_score({}) == 0.0

def test_ranked_quality_score_gold_high():
    row = {
        'ranked_seasons_participated': 28,
        'latest_ranked_battles': 135,
        'highest_ranked_league_recent': 'Gold',
        'ranked_overall_win_rate': 53.5,
    }
    score = _ranked_quality_score(row)
    assert 0.85 < score < 0.86  # ~0.856

def test_ranked_quality_score_bronze_minimal():
    row = {
        'ranked_seasons_participated': 1,
        'latest_ranked_battles': 2,
        'highest_ranked_league_recent': 'Bronze',
        'ranked_overall_win_rate': 50.0,
    }
    score = _ranked_quality_score(row)
    assert score < 0.25  # barely above zero
```

### Unit test: multiplier behavior

```python
def test_best_score_no_ranked_multiplier_is_one():
    """Non-ranked player's score should not be affected by ranked multiplier."""
    row = _make_test_row(ranked=False)
    score_before = _calculate_landing_best_score_OLD(row)
    score_after = _calculate_landing_best_score(row)
    # Score should be similar (differs only by clan weight redistribution)

def test_best_score_gold_beats_no_ranked():
    """Gold ranked player with same base stats should score higher."""
    base = _make_test_row(ranked=False)
    gold = _make_test_row(ranked=True, league='Gold', battles=100, seasons=20, wr=55.0)
    assert _calculate_landing_best_score(gold) > _calculate_landing_best_score(base)
```

### Integration test

Existing `test_materialize_landing_player_best_snapshot_persists_cb_order` pattern — create two test players, verify ranked multiplier affects ordering.

### Verification on live

```bash
cd server && python -m pytest warships/tests/test_landing.py -x --tb=short
```

Post-deploy:
1. Materialize snapshot: `materialize_landing_player_best_snapshot('overall', realm='na')`
2. Curl `https://battlestats.online/api/landing/players/?mode=best&sort=overall`
3. Verify Gold-ranked players moved up, non-ranked players didn't move down relative to each other
4. Check that Bronze-with-2-battles had negligible change

## Rollback

If the ranking feels wrong after deploy:

1. Revert the weight constants in `landing.py` to their original values
2. Change `_calculate_landing_best_score()` back to the additive formula
3. Re-materialize the snapshot
4. The `ranked_overall_win_rate` field on the model is harmless — leave it

No migration rollback needed. The field addition is additive (nullable).

## Status

- [x] Investigation complete
- [x] Current formula documented with exact weights, normalization functions, and line numbers
- [x] Enrichment data coverage quantified (NA realm)
- [x] Live top-player data reviewed
- [x] Options evaluated — Option B selected (ranked as multiplier)
- [x] QA complete: normalization formulas verified, projections recomputed with saturation
- [x] Implementation spec with exact code changes, line numbers, and diffs
- [x] QA revision: eliminated model/migration/enrichment steps — `ranked_overall_win_rate` computed on-the-fly from `ranked_json`
- [x] Implementation: ranked multiplier scoring, pipeline wiring, 6 new tests (all pass)
- [ ] Deploy + materialize snapshot
- [ ] User review of live ranking feel — may need weight tuning
