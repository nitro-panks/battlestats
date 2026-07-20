# ShipPopDailyAgg daily rollup — DB-audit lever F9.2

Status: implemented 2026-07-20 (model + migration 0085 + rollup + rewired bulk warm + tests). Parent finding: `agents/runbooks/runbook-db-table-audit-2026-07-19.md`, F9 recommendation 2.

## Problem

`compute_all_ship_pop_avg_damage` (`server/warships/data.py`) — the nightly per-realm warm behind the damage-treemap baselines — ran one full grouped scan of `PlayerDailyShipStats` (7M+ rows) over the trailing 30d window: ~34 s/realm, nightly, on the shared 2-vCPU managed PG.

## Design

New table `ShipPopDailyAgg` (`server/warships/models.py`, migration `0085_shippopdailyagg`): one row per `(realm, mode, ship_id, date)` summing that realm-day's PDSS rows.

| column | type | source (PDSS) |
|---|---|---|
| realm | char(4), `REALM_CHOICES` | `player__realm` |
| mode | char(8), random/ranked | `mode` (seasons collapsed) |
| ship_id | bigint | `ship_id` |
| date | date | `date` |
| battles, wins, frags, xp | bigint | same-name sums |
| damage_sum | bigint | `Sum(damage)` |
| main/secondary/torpedo `_shots`/`_hits` | bigint | same-name sums |

Uniqueness: `(realm, mode, ship_id, date)` (`unique_ship_pop_daily_agg`); read index `(realm, date)` (`shippop_realm_date_idx`).

Column choice: `battles` + `damage_sum` serve the avg-damage baseline; the rest are exactly what the ship-combat metric catalogue (`_SHIP_COMBAT_METRICS`) consumes — win/frag/xp per-battle rates and the three hit ratios with their shot-count gates. The intentionally-unsurfaced Phase-7 counters (survival, spotting, caps — biased population coverage) are NOT carried.

Maintenance (`server/warships/data.py`):

- `rollup_ship_pop_daily(realm, on_date)` — idempotent delete-and-replace of one realm-day inside a transaction; also prunes the realm's rows older than `SHIP_POP_ROLLUP_RETENTION_DAYS` (100), keeping the table self-bounding with no new timer.
- `rollup_ship_pop_daily_catchup(realm, window_days=30)` — rolls every window date with no agg rows plus, always, the trailing `SHIP_POP_ROLLUP_REFRESH_DAYS` (2: today is still accruing; a midnight-straddling commit lands detected-yesterday rows just after the day flips).

`compute_all_ship_pop_avg_damage` now calls the catch-up, then sums the window from `ShipPopDailyAgg` (mode=random). Cache keys, payload, `SHIP_POP_AVG_MIN_BATTLES` floor, and the 0 below-floor sentinel are unchanged; per-day sums compose associatively into the identical window totals.

## Invariants

- A realm-day is frozen once the UTC day ends: PDSS `date` = `detected_at.date()`, so only the trailing refresh days can change. Exception: the manual `rebuild` repair op rewrites past PDSS days — after using it, re-roll the affected realm-days by hand (`rollup_ship_pop_daily`).
- Realm and mode never mix; ranked seasons collapse into one ranked row per ship-day.
- Legacy per-ship `compute_ship_pop_avg_damage` (direct PDSS aggregate) is kept unchanged as the request-driven gap fallback and as the identity oracle in tests.
- The skill-BRACKETED ship-combat population aggregation (`_ship_population_brackets_30d`) stays on PDSS: it ranks per player (account-WR brackets, ≥200-battle floor, distinct-player counts), which a per-ship-day rollup structurally cannot express. Only the columns are carried so a future 'all'-shape read could migrate.
- An empty realm-day is indistinguishable from an unrolled one, so it re-rolls each catch-up — a cheap indexed empty aggregate; accepted.

## Rollout

No flags, no Beat changes: the first nightly `warm_all_ship_pop_avg_damage_task` after deploy runs the catch-up, which backfills the whole 30d window automatically (~ the cost of one legacy scan, once per realm). Every later run rolls only 2 days + gaps and sums ~30 rows/ship. Retention prune rides inside the rollup.

Known non-issue observed during work: `makemigrations` proposes a Remove/AddIndex churn of `player_realm_lbd_active_idx` — pre-existing Q-condition-ordering drift between 0084's `SeparateDatabaseAndState` state and the Player Meta; deliberately excluded from 0085 (regenerating that index non-CONCURRENTLY would lock the hot player table).

## Tests

`server/warships/tests/test_ship_pop_daily_rollup.py`: realm/mode partition correctness against a hand-seeded PDSS fixture; idempotency; re-roll picks up new rows; catch-up fills gaps, skips frozen days, refreshes trailing days, covers the window edge; output identity vs the legacy per-ship computation (incl. pinned values, floor sentinel, banker's rounding); per-realm-scoped retention prune; second-run cheapness (frozen rows untouched). The pre-existing end-to-end warm tests in `test_incremental_battles.py` now exercise the rollup path unchanged.
