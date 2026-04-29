# Runbook: Efficiency Rank (Sigma Badge) QA — Random Sampling Validation

_Created: 2026-04-02_

_Status: **Planned** — QA command and automated checks not yet implemented_

## Purpose

Establish an empirical QA process to validate that the efficiency rank / sigma badge system is computing and displaying correctly. The system ranks ~56K players using a Bayesian-shrinkage badge strength model, assigns tier labels (Expert / I / II / III), and surfaces sigma icons across multiple frontend surfaces. A miscalculation affects player trust and the integrity of the Sigma landing mode.

## Current System Summary

| Component | Value |
| --- | --- |
| Ranked population (NA) | 55,930 |
| Stored population_size | 56,481 |
| Tier E (Expert, ≥97th pctl) | 1,648 |
| Tier I (≥90th) | 3,885 |
| Tier II (≥75th) | 8,362 |
| Tier III (≥50th) | 14,036 |
| Below threshold (<50th) | 27,999 |
| Snapshot staleness TTL | 48 hours |

### Known Issues (2026-04-02)

1. **Population size drift**: `efficiency_rank_population_size` (56,481) exceeds actual ranked count (55,930) by 551. Players who became ineligible after the last snapshot retain their stale population_size. The snapshot recomputes this on each run, so this self-heals — but the delta indicates ~1% churn between runs.

2. **Input-drift freshness race** (fixed today): The strict `_efficiency_rank_snapshot_is_fresh()` check invalidated landing page badges when the hydration task ran after the rank computation task. Landing surfaces now bypass this check. Individual player pages still use the strict gate.

## QA Strategy: Random Sampling with End-to-End Verification

### Approach

A management command (`audit_efficiency_ranks`) that:

1. **Samples N random ranked players** (default N=50, configurable)
2. **For each player, independently recomputes** the expected badge strength from raw data and compares against stored values
3. **Checks structural invariants** across the full population
4. **Reports pass/fail with detailed discrepancy logs**

### What to Verify per Player (Spot Checks)

| Check | Method | Pass Criteria |
| --- | --- | --- |
| **Badge point sum** | Re-sum `EFFICIENCY_BADGE_CLASS_POINTS[class]` for each badge row where `ship_tier >= 5` | Matches `raw_badge_points` in `PlayerExplorerSummary` |
| **Eligible ship count** | Count distinct ships with `ship_tier >= 5` and valid badge class (1-4) | Matches `eligible_ship_count` |
| **Normalized strength** | `raw_badge_points / (eligible_ship_count * 8)` | Matches `normalized_badge_strength` within ε=0.001 |
| **Shrinkage formula** | `weight = ships / (ships + 12); shrunk = weight * normalized + (1 - weight) * field_mean` | Matches `shrunken_efficiency_strength` within ε=0.001 (requires knowing field_mean from snapshot) |
| **Tier assignment** | Apply `_efficiency_rank_tier_from_percentile(percentile)` | Matches stored `efficiency_rank_tier` |
| **Icon flag** | `has_efficiency_rank_icon == (tier == 'E')` | Matches stored `has_efficiency_rank_icon` |
| **API round-trip** | Fetch `/api/player/<name>/` and check `efficiency_rank_percentile` field | Non-null if snapshot is fresh; matches DB value |

### What to Verify Globally (Population Invariants)

| Invariant | Method | Pass Criteria |
| --- | --- | --- |
| **Tier boundary integrity** | Query min/max percentile per tier | No overlap between tiers; no gaps at thresholds (0.50, 0.75, 0.90, 0.97) |
| **Percentile monotonicity** | `ORDER BY shrunken_efficiency_strength DESC` should match `ORDER BY percentile DESC` | Rank order is identical |
| **Population size consistency** | Count players with non-null percentile vs stored `population_size` | Delta < 2% (allows for inter-run churn) |
| **Tier distribution sanity** | Expert ≈ 3%, Grade I ≈ 7%, Grade II ≈ 15%, Grade III ≈ 25%, Below ≈ 50% | Within ±3pp of expected distribution |
| **No orphaned ranks** | Players with percentile but missing `efficiency_rank_tier` when percentile ≥ 0.50 | Count = 0 |
| **No phantom badges** | Players with `efficiency_rank_tier` but null percentile | Count = 0 |
| **Freshness coverage** | % of ranked players with `efficiency_rank_updated_at` within 48h | ≥ 95% |

### What to Verify on the Frontend (Smoke Tests)

| Check | Method | Pass Criteria |
| --- | --- | --- |
| **Sigma badge renders on player detail** | Navigate to a known Expert player, check for EfficiencyRankIcon | Icon visible with correct tier color |
| **Sigma landing mode shows ≥20 players** | Load landing page in Sigma mode | At least 20 players returned, all with non-null percentile |
| **Badge tooltip shows percentile and population** | Hover sigma icon on player detail | Tooltip text includes percentile and population size |
| **Badge absent for ineligible player** | Navigate to a player with <200 PVP battles | No sigma icon rendered |

## Implementation Plan

### Phase 1: Management Command — `audit_efficiency_ranks`

Create `server/warships/management/commands/audit_efficiency_ranks.py`:

```
python manage.py audit_efficiency_ranks --sample-size 50 --realm na
```

**Output format:**

```
=== Efficiency Rank QA Audit ===
Realm: na
Sample size: 50
Population: 55,930 ranked / 56,481 stored (delta: 551, 0.98%)

--- Population Invariants ---
[PASS] Tier boundary integrity: no overlaps or gaps
[PASS] Percentile monotonicity: rank order matches strength order
[WARN] Population size delta: 551 (0.98%) — within 2% threshold
[PASS] Tier distribution: E=2.9% I=6.9% II=14.9% III=25.1% below=50.1%
[PASS] No orphaned ranks: 0 players with percentile >= 0.50 missing tier
[PASS] No phantom badges: 0 players with tier but null percentile
[PASS] Freshness: 100.0% updated within 48h

--- Spot Checks (50 sampled players) ---
[PASS] 48/50 badge point sums match
[FAIL] 2/50 badge point mismatches:
  - PlayerFoo: stored=142, recomputed=148 (6 point delta, likely stale badge data)
  - PlayerBar: stored=89, recomputed=89 (match after refresh — was stale efficiency_json)
[PASS] 50/50 tier assignments correct
[PASS] 50/50 icon flags correct
[PASS] 50/50 normalized strengths within ε=0.001

--- Summary ---
Population invariants: 6/7 PASS, 1/7 WARN
Spot checks: 48/50 PASS, 2/50 FAIL (badge staleness, not computation error)
```

### Phase 2: Automated Regression (Celery Task)

Add a periodic task that runs the population invariant checks (not the per-player spot checks) daily and logs warnings if any fail. No user-facing impact — just observability.

### Phase 3: Playwright Smoke Tests

Add `client/e2e/efficiency-rank-badges.spec.ts`:
- Navigate to a known Expert player → verify sigma icon renders
- Check Sigma landing mode returns ≥20 players
- Navigate to a low-battle player → verify no sigma icon

## Key Files

| File | Role |
| --- | --- |
| `server/warships/data.py:85-105` | Constants: thresholds, shrinkage K, badge point mapping |
| `server/warships/data.py:258-333` | Badge row building from raw API data |
| `server/warships/data.py:655-726` | `_build_efficiency_rank_inputs()` — eligibility + strength computation |
| `server/warships/data.py:745-838` | Freshness checking, eligibility reasons, tier mapping |
| `server/warships/data.py:841-855` | Bayesian shrinkage formula |
| `server/warships/data.py:937-1190` | `_recompute_efficiency_rank_snapshot_sql()` — SQL-based global ranking |
| `server/warships/models.py:148-199` | `PlayerExplorerSummary` model fields |
| `server/warships/tasks.py:628-667` | Efficiency data refresh + snapshot recomputation tasks |
| `server/warships/serializers.py:77-90` | API serialization of efficiency fields |
| `client/app/components/EfficiencyRankIcon.tsx` | Sigma badge rendering (tier colors, tooltip) |

## Decision Log

| Date | Decision |
| --- | --- |
| 2026-04-02 | Created QA strategy; no implementation yet |
| 2026-04-02 | Chose random sampling approach over exhaustive recomputation (56K players × API calls = too slow for routine QA) |
| 2026-04-02 | Population invariant checks are cheap (SQL only) and should run daily; spot checks are expensive and run on-demand |
| 2026-04-02 | Tier boundaries confirmed clean in initial manual audit; no misassignments found |
| 2026-04-02 | Population size drift (551 players, ~1%) is expected churn — not a bug |
