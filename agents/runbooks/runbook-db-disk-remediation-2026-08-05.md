# Runbook: DB Disk Growth — Sequenced Remediation

_Created: 2026-08-05_
_Lifecycle: dated-active · Owner: platform_
_Context: managed-PG disk went 45% (07-22) → 49.7% (07-29) → 55% (08-05), ~600 MB/day. A two-agent investigation produced `agents/work-items/db-growth-capacity-2026-08-05.md` (growth/capacity) and `agents/work-items/data-capture-utility-audit-2026-08-05.md` (G-series data-utility delta). This runbook is the **execution plan** those two reports imply: what to do, in what order, with what gate between each._
_QA: every figure here traces to one of those two work-items or to a live check recorded in this runbook's Validation section._
_Status 2026-08-06: **code for Steps 1, 3 and 4b is implemented and merged** (TDD, 874 backend tests green). **No production mutation has been made** — the levers below are still un-armed, and each needs its own operator acknowledgement per the one-lever-at-a-time rule. See "Implementation status" immediately below._

## Implementation status

| Step | Code | Deployed | Armed / done in prod | What remains |
|---|---|---|---|---|
| 0 — disk alerts | n/a | n/a | ☐ | Operator action in the DO console. 70% ≈ 2026-09-11 |
| 1 — compaction on a timer | ✅ | ✅ v5.1.3 | ✅ **timer armed** | Watch the first fire (below), then the catch-up pass |
| 2 — arm `PRUNE_BATTLES_JSON_ENABLED=1` | n/a (config) | — | ✅ **armed 2026-09-20** | Done. Measured yield ~326 MB, not the ~2 GB projected here — see `runbook-db-capacity-remediation-2026-09-19.md` Step 6 |
| 3 — age-bound observation JSON | ✅ **default off** | ✅ v5.1.3 | ☐ | Set `BATTLE_OBSERVATION_COMPACT_DORMANT_DAYS=105`. **Irreversible** — only after Step 1 runs clean |
| 4 — soft-limit triage | n/a | n/a | ☐ | Investigation, not a lever |
| 4b — rollup Phase-7 fix | ✅ | ✅ v5.1.3 | ☐ **backfill pending** | 37 days to rebuild (below) |

Code landed (branch `fix/disk-remediation-2026-08-05`):
- `rebuild_daily_ship_stats_for_date` now carries all 14 Phase-7 columns via `_PHASE7_ROLLUP_COLUMNS` (Step 4b).
- `battlestats-compact-observations.{service,timer}` in the backend deploy script; the Beat row is explicitly **disabled** via `BATTLE_OBSERVATION_COMPACT_BEAT_ENABLED` (default 0) rather than deleted, so prod's existing DB row cannot double-run against the timer (Step 1).
- `BATTLE_OBSERVATION_COMPACT_KEEP=1` and `_STATEMENT_TIMEOUT=1800` pinned in the deploy script (were manual-`/etc`-only / too low).
- `compact_battle_observation_payloads(dormant_after_days=N)` + `--dormant-after-days` + `BATTLE_OBSERVATION_COMPACT_DORMANT_DAYS`, default 0 (Step 3).

## Purpose

Convert two analyses into a sequenced, reversible plan. Read this before touching anything on the disk problem; work the steps **in order**, one production lever at a time, with an operator acknowledgement between each (standing rule: no autonomous batches of prod mutations).

Each step carries: why now, the change, the risk, how to validate, and how to roll back. **Do not batch steps.** The whole point of the ordering is that each one changes the measurement the next one depends on.

## TL;DR

- **The wall is real but not imminent.** Central plateau **~67.5 GB ≈ 80%** of the 84.17 GB volume around 2026-11; band 71–88%. 70% ≈ **2026-09-11**, 80% ≈ **2026-10-06**. Autoscale is OFF, so 80 GiB is hard and a full disk is a read-only outage.
- **Retention is not the problem and is not a lever.** 105d is affordable to ~142d (1.35× headroom) and is deliberately sized for the 90d rolling read. See `runbook-env-value-authority-2026-08-05.md` for why this needs saying.
- **The problem is the player pool** (1.11× headroom) plus **one broken job**.
- **Step 1 is a defect fix, not a tuning choice**: BattleObservation compaction has been failing every night. That single job explains the largest table and the largest slope.
- **Step 4b is a live product-data bug found while QA-ing this runbook**: the nightly rollup sweeper zeroes 14 PDSS columns on every day it rebuilds. 9 of the last 15 days are affected. It degrades the ship combat profile and it **strikes the Phase-7 storage lever**, because BattleEvent is now the only reliable copy.
- Steps 0–2 are cheap and near-riskless. Step 3 is the largest structural win. Everything past 4b is optimization.

## The situation in one table

| Fact | Value | Source |
|---|---|---|
| Disk used | 46.27 / 84.17 GB = **55%** | measured 2026-08-05 |
| `pg_database_size` | 39.02 GB | measured |
| WAL/temp gap | 7.25 GB, config-bounded near 8 GB — **cannot be a slope** | measured |
| Observed slope | ~599 MB/day (558 table + 41 gap) | derived, reconciles to the disk series |
| Largest table | `warships_battleobservation` **15.84 GB**, **364 MB/day** | measured |
| Largest omission in prior forecasts | that table, entirely | — |
| Battle-history depth today | 53 d of 105 (floor `min(detected_at)` = 2026-06-13) | measured |
| Full depth / first real prune | **2026-09-26** / **2026-10-15** | derived |
| Retention headroom | ~142 d affordable → **1.35×** | derived |
| **Player-pool headroom** | **1.11×** | derived |

## Sequencing rationale

The order is not by size. It is by **risk, reversibility, and measurement dependency**:

1. Steps that only add observability go first — they cost nothing and make later steps verifiable.
2. A **defect fix** outranks an optimization of the same size, because its current state is also corrupting the measurements everything else rests on.
3. Config-only, single-flag changes outrank code changes.
4. Anything that permanently destroys data goes last among the storage levers, and only after the cheaper ones have re-based the numbers.
5. **No relation-rewriting operation appears anywhere in this plan.** The 2026-07-21 `VACUUM FULL warships_player` caused a 24-minute site outage; `lock_timeout` bounds lock *acquisition*, not *hold*. `pg_repack` only, supervised, never scheduled — and the current TOAST density (84% on the observation table) means there is little to reclaim anyway.

**No lever below returns bytes to the OS.** They buy **plateau height**. With a central plateau at 80%, plateau height is exactly the thing worth buying.

---

## Step 0 — Disk alerts (do immediately, no gate)

**Why now.** 70% arrives ~2026-09-11 on the central path. There is currently no alert between "fine" and "read-only outage." This is the only step with no downside and no dependency.

**Change.** DO alerts on the database cluster at **70% (58.9 GB)** and **80% (67.3 GB)**.

**Risk.** None. **Rollback.** Delete the alert.

**Validation.** Alert appears in the DO console; trigger-test if the console supports it.

**Meaning when they fire.** 70% = re-run the measurements in this runbook and confirm the plateau still holds. 80% = if the levers have not shipped, resize.

---

## Step 1 — Make BattleObservation compaction finish ★ highest value

**Why now.** This is a **live defect**, not a tuning preference, and it is the largest single contributor to the disk slope.

Verified in the droplet journal 2026-08-05:

```
Aug 05 12:39:00  Task warships.tasks.prune_battle_observations_task[85d4…]
                 raised unexpected: SoftTimeLimitExceeded()
```

- **Aug 02** — died ~3m14s in on `OperationalError('canceling statement due to statement timeout')`: its own 180s `BATTLE_OBSERVATION_COMPACT_STATEMENT_TIMEOUT` killed the candidate scan (a full double-window pass over 3.47M rows). **Compacted zero rows.**
- **Aug 03 / 04 / 05** — `SoftTimeLimitExceeded` at exactly `start + 540 s`. The task carries default `TASK_OPTS` (`tasks.py:24`, `soft_time_limit=540`).

**Cost**, from two agreeing measurements: ~149K uncompacted rows ≈ **2.1 GB residue**, plus a standing **~116 MB/day leak**. This is why `warships_battleobservation` is the biggest table in the estate.

**How long it has been failing is UNKNOWN** — journal retention truncates at ~6–8 days. If it predates the 2026-07-20 repack, the residue exceeds 2.1 GB. Treat 2.1 GB as a floor.

**Why 180s is not merely "a bit tight" (verified).** `_compact_candidate_sql` (`incremental_battles.py:1517`) is a single `FROM warships_battleobservation` scan with **two `ROW_NUMBER()` window functions and no pre-filter** — the `observed_at`/`rn`/`rrn` predicates are applied *after* the windows, so every one of the 3.47M rows is sorted on each run regardless of how few are eligible. It is deliberately heap-only (`IS NOT NULL` reads the null-bitmap, never detoasting the JSON — an earlier version that called `pg_column_size` inline blew the timeout on 2026-05-24). So the cost is the **window sort**, which scales with table size and cannot be indexed away by a `WHERE` clause. Raising the timeout is necessary; treat restructuring the scan as the durable fix.

**Change.** Move the job off the Celery soft-limit budget onto a **systemd timer**, the pattern this repo already uses for `archive_battle_history` — whose runbook states retention sweeps are *"too long for a Celery soft-time-limit / worker slot."* This job never got that treatment.

**The management command already exists** — `server/warships/management/commands/prune_battle_observations.py`, with `--dry-run`, `--statement-timeout`, `--batch-size`, `--max-rows`, `--sleep`, `--keep-per-player`, `--min-age-hours`. Nothing new needs writing. Concretely:

1. Add `battlestats-compact-observations.{service,timer}` invoking that command with a generous `--statement-timeout`.
2. Retire the Beat registration (`signals.py:897-924`, `BATTLE_OBSERVATION_COMPACT_HOUR`/`_MINUTE` = 12/30 — the 12:30 UTC in the failures above) so the two cannot both run.
3. Keep `BATTLE_OBSERVATION_COMPACT_ENABLED` as the master gate.

**Risk.** Low, but the first successful run will be large. It is a mass `UPDATE … SET json = NULL` over ~149K TOASTed rows: it **transiently increases** WAL and disk before it helps, on a box already showing bursty iowait. Use the existing batching (2000 rows/txn, inter-batch sleep) and run the first catch-up pass in slices via `--max-rows`, off-peak. **Do `--dry-run` first** and confirm the candidate count is in the ~149K range.

**Rollback.** Disable the timer. The NULLed JSON is not recoverable, but it is by definition JSON the compactor was already supposed to have removed.

**Validation.**
- `--dry-run` candidate count ≈ 149K before, → small/zero after a full pass.
- `journalctl -u battlestats-compact-observations` shows exit 0 with a non-zero cleared count.
- `n_tup_upd` on `warships_battleobservation` moves; TOAST live fraction rises from ~84%.
- Slope check after ~3 days: the ~116 MB/day leak should be gone.

---

## Step 2 — Arm `PRUNE_BATTLES_JSON_ENABLED=1`

**Why now.** Cheapest structural bound available: one line, no code. `battles_json` is currently an **unbounded per-player ratchet** (~11.1 kB × every player ever observed; 433,458 players ≈ 4.85 GB). The 180-day prune exists, is tested, has a management command — and **has never run in production**.

Note this also corrects the 07-19 audit's F6, which asserted the prune was "visibly working" (inferred from the light long-inactive tail; the real cause is that the floor writes `battles_json` only for recently-observed players).

**Change.** `set_env_value PRUNE_BATTLES_JSON_ENABLED 1` in `deploy_to_droplet.sh`, plus the Pass update.

**Value.** ~2 GB off the plateau. The **immediate reclaim is small** (~178 MB) — the point is converting a ratchet into a rolling window *before* the pool grows, not the bytes today.

**Risk.** A player inactive >180d loses their career per-ship store until a refresh re-fetches it. Unlike Step 3, this **is re-fetchable from WG**. Two existing guards apply: excludes `enrichment_status=pending`, and refuses unless `--inactive-days > ENRICH_MAX_INACTIVE_DAYS` (prod=7). The real risk is that "intended behavior" has never met live traffic.

**Rollback.** Flag back to 0. Pruned JSON is not restored but is re-fetchable.

**Validation.** `--dry-run` candidate count ≈ the ~178 MB estimate before arming. After the first run: no spike in cold-profile latency or WG call volume.

### ⚠ Blocker found 2026-08-08 — arming the flag alone would ship a silent weekly no-op

The gating `--dry-run` **exceeds 900s** — it was run three times: twice at the 180s default
(busy DB, then quiet) and once at `--statement-timeout 900` on a quiet DB, where it still
died at 15m03s. **A bigger timeout is therefore not the fix**; the candidate `COUNT(*)` alone
is structurally too expensive. It is not contention and it is **not** detoast — the
docstrings are right that `battles_json IS NOT NULL` reads the null bitmap. The cost is
random heap I/O:

```
Parallel Index Scan using player_realm_lbd_active_idx on warships_player  (cost=0.42..207411.46 rows=88561)
  Index Cond: (last_battle_date < '2026-02-09'::date)
  Filter: ((battles_json IS NOT NULL) AND ((enrichment_status)::text <> 'pending'::text))
```

`warships_player` is now **1,091,997 rows / 1643 MB heap / 10 GB total**. The index it picks is

```sql
CREATE INDEX player_realm_lbd_active_idx ON warships_player (realm, last_battle_date DESC)
  WHERE last_battle_date IS NOT NULL AND NOT is_hidden;
```

**`realm` is the leading column and the query supplies no realm**, so `last_battle_date <
cutoff` cannot be an index range — Postgres walks every realm group, then **heap-fetches each
candidate** to evaluate `battles_json IS NOT NULL` and `enrichment_status`. No locality, on a
shared 2-vCPU PG. That is the >900s.

**Why this blocks arming.** The live path is not cheaper than the dry-run: it runs the *same*
`SELECT id FROM (candidate_sql)` to materialise ids before batching its UPDATEs. The systemd
unit passes **no** `--statement-timeout`:

```
ExecStart=... manage.py prune_inactive_player_battles_json --batch-size 5000 --sleep 0.5
```

So flipping `PRUNE_BATTLES_JSON_ENABLED=1` today would produce a weekly job that dies on
statement timeout **before writing anything** — a task that appears scheduled and healthy
while doing nothing. That is precisely the failure class fixed twice this week
(`recapture_lapsed_players_task`, `enrichment_reclassify_drift_task`): **an end-of-scan write
that never arrives, reported as silence rather than failure.**

**Do this first, then arm.** The index is required — a longer timeout was tried and failed:

1. Add a partial index whose *predicate* carries the cheap filters and whose *key* is the
   ranged column, so the scan becomes a range over exactly the candidate set:

   ```sql
   CREATE INDEX CONCURRENTLY player_battles_json_prune_idx
     ON warships_player (last_battle_date)
     WHERE battles_json IS NOT NULL AND NOT is_hidden
       AND enrichment_status <> 'pending';
   ```

   **Unlike the reclassify case, this predicate is cheap to maintain on write**: all three
   terms are null-bitmap or scalar reads, with no jsonb comparison — the reason an index was
   rejected there does not apply here. Size is small (one date key over the
   `battles_json IS NOT NULL` subset). The real cost to weigh is churn: `last_battle_date` is
   written by both the floor and the recapture sweep, so the index is maintained on a hot
   path — check it against items 6 and 9 (write-amplification) before committing.
2. Re-run the dry-run to completion, confirm the candidate count against the ~178 MB estimate.
3. Raise the unit's `--statement-timeout` anyway as a belt-and-braces measure, since the live
   path materialises the same id list.
4. **Then** flip the flag.

**Do not simply raise the timeout.** That was attempted (900s) and still failed, and it is the
same mistake the `enrichment_reclassify_drift_task` fix rejected on 2026-08-07: when a
statement is structurally too expensive, a bigger ceiling buys nothing and hides the problem
one run longer.

Until then Step 2 is **not** a one-line env change, and the runbook's "one line, no code"
framing under **Why now** is no longer accurate.

---

## Step 3 — Age-bound the observation JSON ★ largest structural win

**Why now.** After Steps 1–2 the numbers re-base; do this once compaction is actually running, or you cannot tell the two effects apart.

`compact_battle_observation_payloads` keeps the latest `COMPACT_KEEP` (live **1**) observations per player plus the latest ranked-carrying one. **There is no upper age bound**: a player observed once in April 2026 and never again keeps their JSON forever.

Verified in `_compact_candidate_sql`: the only time predicate is `w.observed_at < %(cutoff)s`, where `cutoff = now − min_age_hours` and `BATTLE_OBSERVATION_COMPACT_MIN_AGE_HOURS` is **unset in prod (default 0)** — so `cutoff ≈ now` and it filters nothing. It is a *minimum*-age safety guard, not a dormancy bound.

**Why the row-retention tier does not already handle this.** `BATTLE_OBSERVATION_ROW_RETENTION_DAYS=32` deletes JSON-**stripped** skeletons, and is explicitly guarded to never delete a JSON-carrying row or a player's latest observation. So a dormant player's JSON-carrying row is immune to both mechanisms: compaction spares it (`rn <= keep`) and row-retention spares it (has JSON). That gap is exactly what this step closes.

This is the mechanism coupling disk capacity to the **player pool** rather than to retention.

**Change.** Add an age predicate: NULL both JSON columns for every observation of a player not observed within `N` days, `N = 105` to match battle-history retention. One predicate in `_compact_candidate_sql`, one env knob, one test.

**Ordering is mechanical, not just tidy.** This step *widens* the candidate set of a scan that is already overrunning its limits. Shipping it before Step 1 would make Step 1's failure worse and confound both measurements.

**Value.** Caps the table at ~12 GB relation against a projected 21.0 GB — **~9 GB off the plateau** (80.2% → ~69%), and pool headroom **1.11× → 1.31×**. Immediate effect ~180K rows (the April/May/June cohorts), ~2.9 GB of live JSON made reusable. **Second-order gain**: once NULLed, those rows become JSON-stripped skeletons and therefore become eligible for the existing 32d row-retention delete (except each player's latest), so the step also yields row deletions, not only JSON bytes.

**Risk.** A returning dormant player **permanently loses their diff baseline**; WG serves only current cumulative stats, so it cannot be re-fetched. Mitigation is strong: the delta that baseline would produce spans >105 days, which the battle-history window cannot represent anyway, and the next observation re-establishes a baseline within one floor cycle. Same throttling caveat as Step 1.

**Rollback.** The setting, yes. The data, **no** — this is the one irreversible step. Do it after Steps 1–2 have confirmed the model.

**Validation.** Table relation size stops tracking the ever-observed pool. Spot-check that a dormant player returning still produces correct `BattleEvent` rows on their second post-return observation.

---

## Step 4 — Triage the background-queue soft-limit failures

**Why now.** Step 1 fixes one symptom of what looks like a systemic condition. On **2026-08-05 before 19:00 UTC** — before any investigation load — the `background` worker logged **22 `SoftTimeLimitExceeded`**:

| Task | Failures |
|---|---|
| `warm_realm_top_ships_task` | 9 |
| `warm_player_ranked_wr_battles_correlation_task` | 6 |
| `recapture_lapsed_players_task` | 2 |
| `roll_up_player_daily_ship_stats_task` | 1 |
| `warm_hot_entity_caches_task` | 1 |
| `warm_player_correlations_task` | 1 |
| `startup_warm_caches_task` | 1 |
| `prune_battle_observations_task` | 1 |

All inherit `TASK_OPTS soft_time_limit=540`.

**This is a separate investigation, not a lever** — but it may matter more than any single storage item:

- `warm_realm_top_ships_task` failing 9× means the landing treemap / ship leaderboard warm is not completing; the warm-before-evict design covers this by serving the durable `:published` copy, so it degrades quietly rather than visibly. **Verify that is what is happening** and that boards are not stale beyond intent.
- `roll_up_player_daily_ship_stats_task` failing is the one to check first for **correctness**, not just freshness — PDSS is the daily layer every UI window resolves to.

**Do not** reflexively raise `soft_time_limit`. Establish first whether these tasks are slow because the DB is loaded, because their queries regressed, or because they are structurally too big for a worker slot (in which case they follow Step 1 onto timers).

**Validation.** A daily count of `SoftTimeLimitExceeded` by task, trended for a week.

---

## Step 5 and beyond — optimization backlog

Ordered by expected return per unit of risk. Each needs its own gate; none is urgent.

| # | Item | Value | Notes |
|---|---|---|---|
| 5 | Fix `_get_hot_player_ids` sort | **~3.2 h/day DB CPU** | The #1 DB consumer (5,250 min/27d). F9.1 fixed the *clan* half of this warmer in July and never the player half. Small code change. |
| 6 | `floor_gate_skipped_at` → Redis | ~457 GB dirtied/27d | A cooldown clock stamped onto a wide TOASTed row; only 11.2% of `Player` updates are HOT. Write-amplification, not storage. |
| 7 | Retire/de-duplicate achievements | **~1.9 GB** | `PlayerAchievementStat` (1.3 GB) + `achievements_json` (~596 MB) have **zero product readers** — verified: `achievements_json` has no hit anywhere in `server/warships/*.py` or the client; `PlayerAchievementStat` appears only in a write path (`data.py:576-578`, delete + `bulk_create`) and the player-merge utility (`player_records.py:72-75`). The delete-and-rebuild write path also makes it a churn cost, not just storage. **Check the GDPR purge transcript first** (`purge_deleted_accounts.py:~206,~219` writes an `achievements` count into the record shape — a reconcile, not a blocker). |
| 8 | Drop 5 redundant indexes | ~422 MB | Plus insert relief on churn-heavy tables. |
| 9 | `Player.save(update_fields=)` audit | ~717 GB dirtied/27d | Whole-row UPDATEs on the hottest, widest table. |
| ~~10b~~ | ~~Drop BattleEvent Phase-7 columns~~ | **STRUCK** | See Step 4b. BattleEvent is currently the **only reliable copy** of this data; dropping it would make the loss permanent. Reconsider only after Step 4b has shipped and PDSS has been backfilled and verified. |
| 12 | Add `Player.created_at` | measurement enabler | **The only unmeasured quantity that can put the 90% wall back in view.** Without it the new-player discovery rate is unknowable, and that rate is what decides whether the volume lasts three years or needs resizing in 2027. Cheapest high-value follow-up in this list. |
| 13 | Pin the ~25 unpinned env keys, then `--strict` | durability | `server/scripts/check_env_drift.sh` check 2. Includes `BATTLE_OBSERVATION_COMPACT_KEEP=1`, which exists only as a manual `/etc` edit. While pinning it, fix the stale **"keep-latest-3"** language that still describes prod as keep=3 in `deploy_to_droplet.sh:719`, `incremental_battles.py:2506`, `tests/test_battle_observation_retention.py:6`, and `runbook-db-table-audit-2026-07-19.md` — and the code default `COMPACT_KEEP_PER_PLAYER_DEFAULT = 3`. See `runbook-env-value-authority-2026-08-05.md`. |

---

## Step 4b — The nightly rollup sweeper is zeroing 14 PDSS columns ★ live data bug

**Found during QA of this runbook, not by either agent.** This is a correctness defect on a shipped surface, running nightly, right now. It is placed here because it shares a root with Step 4 (the same task is also in the soft-limit failure list) and because it **blocks** the Phase-7 storage lever.

**Mechanism (verified end to end).**

1. `roll_up_player_daily_ship_stats_task` (`tasks.py:2563`) is a **nightly Beat task**, gated `BATTLE_HISTORY_ROLLUP_ENABLED=1` (live). It is a *self-healing trailing window*: each run rebuilds the last `BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS` (default **3**) days, by design, so a short outage leaves no permanent hole.
2. It calls `rebuild_daily_ship_stats_for_date` (`incremental_battles.py:1421`), which **deletes** the day's PDSS rows and rebuilds them from `BattleEvent`.
3. The rebuilt row dict seeds **only 8 fields** — `battles, wins, losses, frags, damage, xp, planes_killed, survived_battles` (`:1471-1473`) — then `bulk_create`s `PlayerDailyShipStats(**row)`. The **14 Phase-7 columns are absent**, so they take their model default of **0** (`models.py:785-797`).

**Measured impact (live, 2026-08-05).** Over the trailing 15 days, **9 days have `max(main_shots) = 0` and `max(ships_spotted) = 0`** across ~180–250K PDSS rows and 400K+ battles each — not plausible real data. `BattleEvent` retains the deltas on **all 15** days, and on the 6 intact days the PDSS maxima match BattleEvent's **exactly**. Core-8 totals match on every day. So the loss is precisely and only Phase-7, and only where the sweeper has run. (The erratic day-to-day pattern is consistent with this same task also hitting `SoftTimeLimitExceeded` — Step 4 — and dying partway through its window.)

**Why it matters to the product, not just to storage.** `data.py:7116-7215` reads these exact PDSS columns to compute the **ship combat profile** — main-battery and torpedo hit ratios. A gate (`main_shots >= _SHIP_COMBAT_MIN_SHOTS`) means zeroed days silently drop out rather than rendering 0%, which is why this has degraded quietly instead of surfacing as an obvious wrong number. The surface is computed on a fraction of the data it should have.

**Change.** Carry all 14 Phase-7 columns through `rebuild_daily_ship_stats_for_date` (they exist on `BattleEvent` as `*_delta`; sum them exactly as the core 8 are summed). Then backfill: for each affected day, re-run the corrected rebuild — `BattleEvent` still holds the source within the 105d window.

**Risk.** Low and strictly corrective. The rebuild is already idempotent delete+rebuild; this only widens what it writes.

**Rollback.** Revert the code. Days already zeroed stay zeroed until backfilled — which is the current state anyway.

**Validation.** Re-run the two queries in "How to re-measure" below: every day inside the window should show non-zero `max(main_shots)` in PDSS, matching BattleEvent. Then confirm a ship combat profile renders hit ratios for a recent window.

**Caveat on the archive.** The nightly CSV archive exports PDSS as-is, so **archives written for zeroed days carry zeros**. Backfill before those days age past 105d, or the loss becomes permanent in the cold archive too.

**Note for item 10b.** This is why dropping BattleEvent's Phase-7 columns is now struck. Those columns are the only reliable copy of this data. Revisit only once this step has shipped, the backfill is verified, and PDSS has been the durable carrier for a full window.

## The escape hatch: resize or autoscale

Legitimate, and should not be treated as defeat — the volume was already resized once for this roadmap.

- **Resize 80 → 100 GiB**: raises the 90% ceiling to ~94.7 GB, putting even the high-band plateau (74.3 GB) at 69%. DO managed-PG storage can be **increased but never decreased** — a permanent cost commitment. Price not queried; verify in the console before quoting.
- **Autoscale on**: removes the wall and the outage risk. It also removes the forcing function that produced the 07-19 audit's entire yield.

**Verdict.** With a central plateau at 80%, this is a **reasonable pairing with Steps 1–3**, not a substitute. Growth that is unbounded in *shape* is not fixed by more disk — Solve 3 shows that at 1.5× the pool with no lever, affordable retention collapses to ~14 days regardless of volume size.

## Decision gates

| Date / trigger | Check | If it fails |
|---|---|---|
| After Step 1, +3 days | ~116 MB/day leak gone; compaction exits 0 | Re-open Step 4 first — the job may be structurally too big, not just time-limited |
| After Step 4b | every in-window day non-zero in both PDSS and BattleEvent | The sweeper is still dropping columns — do not proceed to any Phase-7 storage change |
| Before any day ages past 105d | affected days backfilled | The cold CSV archive freezes the zeros permanently |
| **2026-08-19** | F7 30-day index re-check (`pg_stat_user_indexes` seq-scan regressions) — **already due** | Restore any index whose drop caused a seq-scan regression |
| **~2026-09-11** (70% alert) | Re-measure; does the plateau still land ≤80%? | Escalate Step 3, or resize |
| **2026-09-26** | Battle-history reaches 105d depth | Confirm the 90d UI foothold is on track |
| **2026-10-15** | First substantial prune run | If it does not fire, the window is not bounded — treat as urgent |
| **~2026-10-06** (80% alert) | Have Steps 1–3 shipped? | If not: resize now, do not wait |

## Validation

- Disk/CPU figures: DO `:9273` Prometheus endpoint, 2026-08-05 (see `reference_do_db_cpu_metrics_endpoint` memory for credentials; the per-cluster metrics route 404s — use the global `/v2/databases/metrics/credentials`).
- Table sizes, row counts, window floor: live read-only psql, `statement_timeout=45s`, `default_transaction_read_only=on`, 2026-08-05.
- Compaction failures: `journalctl -u battlestats-celery-background` on the droplet, Aug 02–05, read directly — not agent-reported.
- Soft-limit census: same journal, 2026-08-05 00:00–19:00 UTC, deliberately bounded **before** investigation load to avoid self-contamination.
- **Code assertions re-verified line-by-line during a QA pass on 2026-08-05** (several first-draft claims were wrong and are corrected above):
  - `TASK_OPTS = {"time_limit": 600, "soft_time_limit": 540, …}` at `tasks.py:22-26`; `prune_battle_observations_task` takes `**TASK_OPTS` at `tasks.py:2745`. ✓
  - `BATTLE_OBSERVATION_COMPACT_STATEMENT_TIMEOUT` default `"180"` at `tasks.py:2786`; `COMPACT_KEEP` read with code default `"3"` at `tasks.py:2776` (prod `/etc` = 1). ✓
  - `_compact_candidate_sql` at `incremental_battles.py:1517` — unfiltered `FROM warships_battleobservation` + two `ROW_NUMBER()` windows; only time predicate is `observed_at < cutoff`, `cutoff = now − min_age_hours`, prod default 0. ✓
  - Beat registration `signals.py:897-924`, hour/minute 12/30. ✓
  - Management command `prune_battle_observations.py` exists with 7 arguments incl. `--dry-run`. ✓ *(first draft wrongly said one may need writing)*
  - BattleEvent: 14 Phase-7 widening columns; `rebuild_daily_ship_stats_for_date` seeds only the 8 core fields; PDSS carries all 14 analogues (`models.py:785-797`); `ARCHIVE_TABLES` covers both tables. ✓
  - `battles_json` prune guards: excludes `enrichment_status = pending` (`:1760`), refuses unless `inactive_days > max_inactive_days` (`:1823`). ✓
  - `_get_hot_player_ids` at `data.py:5328`; `Player.floor_gate_skipped_at` at `models.py:124`. ✓
- **Corrected during QA**: the function behind item 10a is `rebuild_daily_ship_stats_for_date`, **not** `rebuild_daily_rollup` (which does not exist anywhere in the repo); the stale "keep-latest-3" text is at `deploy_to_droplet.sh:719`, **not** 724; the compaction management command already exists.
- **Not validated**: how long compaction has been failing (journal retention ~6–8d, so 2.1 GB is a floor); whether the 22 soft-limit failures are new or long-standing; DO storage pricing; whether any PDSS day has *already* been zeroed by a past repair run (item 10a — worth a spot query before fixing).

## Follow-ups

1. Work Steps 0 → 4 in order, one lever per acknowledgement.
2. Re-measure the whole picture at the 70% alert and update this runbook's TL;DR in place.
3. Archive this runbook when Steps 0–3 have shipped and the plateau has been confirmed at the 70% gate.

## Pickup pointer (session close 2026-08-05 ~22:00 UTC)

**v5.1.3 is live** — backend `releases/20260805174844`, client `releases/20260805175214`, footer verified 5.1.3, healthcheck exit 0.

Live state confirmed on the droplet at close:

```
battlestats-compact-observations.timer   enabled, active
next fire: Thu 2026-08-06 12:32:51 UTC
Beat row `prune-battle-observations`:    enabled = False
BATTLE_OBSERVATION_COMPACT_KEEP=1  BATTLE_OBSERVATION_COMPACT_STATEMENT_TIMEOUT=1800
```

**Resume here, in this order:**

1. ~~**Check the first timer fire** (after 12:32 UTC 2026-08-06).~~ **CLOSED 2026-08-07 — the timer works.** Two consecutive clean fires, both exit 0: 08-06 12:34→12:41 compacted **87,665** payloads in 44 batches; 08-07 12:30→12:41 compacted **56,955** in 29 batches. The declining count is the backlog draining. Full readout: `runbook-post-deploy-verification-2026-08-07.md`. Original instructions retained below for re-measurement.
   This was the whole point of Step 1 — it had never once completed.
   ```bash
   ssh root@battlestats.online 'systemctl status battlestats-compact-observations.service --no-pager | head -20;
     journalctl -u battlestats-compact-observations --since "-1 day" --no-pager | tail -30'
   ```
   Expect exit 0 and a non-zero `cleared` count. **If it times out again**, the scan is structurally too big and the durable fix is restructuring `_compact_candidate_sql`, not a larger timeout (Step 1's "why 180s is not merely tight").
   Then re-measure the table: `warships_battleobservation` was 15.84 GB with ~149K uncompacted rows ≈ 2.1 GB residue + a ~116 MB/day leak.

2. **Backfill Phase-7** (Step 4b). **IN PROGRESS 2026-08-08 — 11 of 37 days done and verified; 26 remain. Resumable, see "Backfill progress" below.** Scope measured 2026-08-05: **37 days, 2026-06-13 → 2026-08-02** — the whole live window, wider than the 15-day sample first suggested. Deployed code now carries the columns, so the existing repair command is sufficient:
   ```bash
   # on the droplet, in current/server, with the env sourced
   manage.py rebuild_player_daily_ship_stats --since 2026-06-13 --until 2026-08-02 --dry-run
   ```
   Not urgent — the first archive prune with candidates is **2026-10-01**, so no zeroed day is frozen into a CSV before then. Verify with the two queries in "How to re-measure": every in-window day non-zero in both PDSS and BattleEvent, maxima matching.

3. **Step 2**, then **Step 3** (irreversible), one operator acknowledgement each.

**Deploy note.** The v5.1.3 backend deploy aborted on a false FATAL from the celery env canary — `systemctl restart` returns before MainPID finishes exec'ing, so an immediate `/proc/<pid>/environ` read can catch a stale pid. Everything had already applied; only the drift check and the old-release cleanup were skipped (cleanup was run by hand, 6→5 releases). Fixed in `5385961`: the canary now retries for a bounded 30s, re-reading MainPID each pass. **The fix is committed but has not yet run a deploy** — the next backend deploy exercises it.

**Not started:** Step 0 (DO console alerts) and Step 4 (soft-limit triage — 22 `SoftTimeLimitExceeded` on 2026-08-05 before 19:00 UTC, incl. `roll_up_player_daily_ship_stats_task`, which is worth checking for *correctness* and not just freshness).

## Backfill progress (Step 4b) — 2026-08-08

**Scope re-measured live, and it matched: exactly 37 zeroed days.** Two findings the
2026-08-05 measurement could not yet see:

- **2026-08-03 → 08-07 are all clean.** The v5.1.3 fix plus the rollup's 3-day lookback
  has been self-healing recent days on its own. Every one of the 37 damaged days sits
  *outside* that lookback, so nothing would ever have reached them unaided.
- The zeroed days are **non-contiguous** — 14 already-correct days are interleaved. Rebuild
  only the 37; passing the whole `--since 2026-06-13 --until 2026-08-02` range would redo
  ~2.8M rows of identical work on the shared 2-vCPU PG for nothing.

**Done and verified (11):** `2026-06-13, 06-14, 06-15, 06-16, 06-17, 06-18, 06-19, 06-22,
06-23, 06-24, 06-25`.

**Remaining (26):** `2026-06-26, 06-29, 06-30, 07-01, 07-02, 07-04, 07-06, 07-07, 07-08,
07-09, 07-10, 07-11, 07-13, 07-14, 07-15, 07-17, 07-20, 07-21, 07-22, 07-23, 07-27, 07-28,
07-29, 07-30, 07-31, 08-02`.

### Validate with SUMS, not maxima

The instruction above to check "maxima matching" is **not a sound criterion** and produced a
false alarm on 2026-06-23 (PDSS 156,921 vs BattleEvent 156,711). A PDSS row **sums** deltas
per `(player, ship, day)`; `max(BattleEvent.main_shots_delta)` is a **single event**. So
`PDSS_max >= BE_max` is the correct relationship, and exact equality happens only when the
top player-ship had one event that day — common, but not guaranteed, and not a rule.

The sound invariant is that the **sums are exactly equal**:

```sql
SELECT d.date, d.s_main, b.s_main, d.s_hits, b.s_hits
FROM (SELECT date, sum(main_shots) s_main, sum(main_hits) s_hits
      FROM warships_playerdailyshipstats WHERE date = ANY(:days) GROUP BY date) d
JOIN (SELECT detected_at::date dt, sum(main_shots_delta) s_main,
             sum(main_hits_delta) s_hits
      FROM warships_battleevent
      WHERE detected_at >= :lo AND detected_at < :hi GROUP BY 1) b ON b.dt = d.date;
```

All 11 rebuilt days are **exact** on `main_shots`, `main_hits` and `ships_spotted`.

### Interrupting is safe

`rebuild_daily_ship_stats_for_date` wraps its delete + `bulk_create` in one
`transaction.atomic()`, so killing mid-day **rolls back cleanly**. Verified: the in-flight
day (2026-06-26) still holds all 191,855 rows, still zeroed — no partial state, no loss.
Kill the `manage.py shell` PID directly; `pkill -f "manage.py shell"` also matches its own
SSH wrapper and kills the connection instead of reporting.

### Cost is superlinear — do not run this through a busy window

Per-row cost rose **1.43 ms/row → 4.86 ms/row** over ten days (2026-06-17: 79k rows/113s;
2026-06-25: 165k rows/801s), i.e. ~3.4×. Events-per-day does not explain it linearly
(06-19 processed 253k events in 407s while 06-24 took 886s for 182k), so the likely driver
is PDSS bloat from repeated whole-day delete+insert with autovacuum lagging, plus general
contention. The code carries a matching `TODO` at `incremental_battles.py:1471` warning that
the day-at-a-time Python aggregation was sized for ~40K events/day; live days are 180-250K.

At the observed rate the remaining 26 days are **~5.8 h**. That is why the run was stopped at
11/37 on 2026-08-08 rather than pushed through: it would have blanketed the 08:20/08:40/09:00
UTC drift-reclassify slots — the one-shot first verification of the v5.1.9 bucket-family split
— and a starved statement timeout there would have looked exactly like that fix failing.
**The backfill is not urgent** (first archive prune with candidates is 2026-10-01); the
verification happened once. Resume it in a genuinely quiet window, ideally a few days at a
time, and consider `VACUUM (ANALYZE) warships_playerdailyshipstats` between batches.

### Resume recipe

```bash
# /tmp/p7backfill.py on the droplet holds the day list and a 5s inter-day pause.
# Trim DATES to the remaining set, then:
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && \
  setsid bash -c "set -a; . /etc/battlestats-server.env; . /etc/battlestats-server.secrets.env; set +a; \
  exec /opt/battlestats-server/venv/bin/python manage.py shell < /tmp/p7backfill.py \
  > /tmp/p7backfill.log 2>&1" </dev/null >/dev/null 2>&1 &'
```

`rebuild_daily_ship_stats_for_date` needs a **`date` object**, not a string — passing a
string fails with `AttributeError: 'str' object has no attribute 'year'` at `_utc_day_bounds`,
*before* any write, so a mistake there is loud and harmless.

## Related

- `agents/work-items/db-growth-capacity-2026-08-05.md` — slope decomposition, plateau projection, capacity envelope
- `agents/work-items/data-capture-utility-audit-2026-08-05.md` — G-series; per-stream utility and write-cost
- `agents/runbooks/runbook-env-value-authority-2026-08-05.md` — why the retention figure was wrong everywhere, and the check that catches the next one
- `agents/runbooks/runbook-db-table-audit-2026-07-19.md` — the F-series this extends; **F6 corrected**, F11 reversed
- `agents/runbooks/runbook-battle-history-archive-prune-2026-06-17.md` — the archive/prune mechanism and the systemd-timer precedent Step 1 follows
