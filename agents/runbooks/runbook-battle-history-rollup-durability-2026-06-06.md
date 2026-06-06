# Runbook: Battle-History Rollup Durability

_Created: 2026-06-06_
_Context: Battle-history data is continuously ingested into `BattleEvent`, and a derived calendrical layer (`PlayerDailyShipStats` → weekly/monthly/yearly) is rebuilt from it. People now rely on this data being current and durable, but the derived layer can silently drift from the source of truth and nothing detects it. This runbook closes the durability drift mode and makes it observable._
_Status: Code landed on branch `feat/battle-history-rollup-durability` (sweeper trailing window, reconciliation, BRIN migration 0063, ops check script, tests — full backend release gate + new suites green on SQLite and Postgres). Production rollout (Step-0 gate verification + deploy + flag flips) is the remaining operator step — gates default off so the deploy is inert until flipped. This runbook is the durable spec._
_Supersedes/extends: `runbook-battle-history-rollout-2026-04-28.md` (Phase 3 nightly sweeper), cross-links `runbook-battle-history-phase7-data-widening-2026-04-29.md` and `runbook-battle-observation-floor-2026-05-02.md`._

## Purpose

The nightly sweeper `roll_up_player_daily_ship_stats_task` (`server/warships/tasks.py:1738`) rebuilds `PlayerDailyShipStats` for **yesterday only**. That is correct *when the sweeper runs* — but it has no self-healing window, no reconciliation, and no observability. If the sweeper is disabled, the `background` worker is down, or Beat misfires for a stretch, the skipped days become **permanent holes**: the sweeper never revisits them and nothing reveals the gap. Recovery today is entirely manual.

This runbook adds three bounded, low-risk durability mechanisms:

1. **Trailing-window self-heal** — the sweeper rebuilds the last N days, not just yesterday.
2. **Alert-only reconciliation** — a gate-independent daily task compares `BattleEvent` vs `PlayerDailyShipStats` battle counts per `(date, mode)` over an audit window and logs the discrepant dates.
3. **A BRIN index + half-open range filter** on `BattleEvent.detected_at` so the widened scans stay cheap.

## Mechanism (why holes are permanent today)

`BattleEvent.detected_at` is `auto_now_add` (`server/warships/models.py:518`) — the DB `NOW()` at insert. **Real battle times are not stored.** Every event therefore lands on its **capture day**, not the day the battle was actually played.

Consequences that shape this design:

- **Yesterday is final.** Because events never arrive for older dates, once the sweeper rebuilds a day it is correct forever — *provided the sweeper ran that night*. This is what makes a small trailing-window rebuild safe and sufficient: re-running an already-correct day is a no-op-equivalent (idempotent delete+rebuild).
- **A missed night is a permanent hole.** The sweeper only ever looks at yesterday, so a day skipped during an outage is never revisited.
- **Recovery cannot re-poll.** WG has no historical per-match endpoint; the only source for rebuilding a past day is the `BattleEvent` rows already captured. Those rows persist forever (retention model in the rollout runbook), so any past day *can* be rebuilt from them — but only if something tells an operator the day is missing.

## Scope

**In scope (Gap A — durability):** self-heal + reconciliation + index. Bounded by design — auto-heal capped at N days; reconciliation only *alerts*; large repairs stay explicit and human-initiated (avoids the multi-day Python-load OOM ceiling the code already warns about at `incremental_battles.py:977-985`).

**Out of scope (documented as follow-ups below):** Gap B accuracy (battle-time attribution), the DB-side rewrite of the daily rebuild, and reactivating the dormant weekly/monthly/yearly UI tiers.

## Design

### Data flow

```
BattleEvent (detected_at = capture day)
        │  nightly sweeper (WIDENED)
        ▼
  ┌───────────────────────────────────────────────┐
  │ roll_up_player_daily_ship_stats_task           │
  │  • rebuild last N days (idempotent del+rebuild) │
  │  • period tiers: rebuild once per DISTINCT      │
  │    week/month/year anchor the window touches    │
  └───────────────────────────────────────────────┘
        ▼
PlayerDailyShipStats  (+ weekly/monthly/yearly tiers)

BattleEvent ──DB-side count──┐
PlayerDailyShipStats ─count──┤
        ▼
  reconcile task (ALERT ONLY, own gate, independent of ROLLUP)
        └─ logger.warning one line per hole → background worker log
                  ▲
   server/scripts/check_battle_history_rollup.sh (single-SSH, read-only)

   rebuild_player_daily_ship_stats --since/--until (human-run repair) → PlayerDailyShipStats

   BRIN index on detected_at → range-prunes both the sweeper and the reconcile scans
```

### 1. Trailing-window self-heal — `tasks.py` (`roll_up_player_daily_ship_stats_task`)

- New env knob, house style: `lookback = int(os.getenv("BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS", "3"))`.
- Build the date list `[yesterday-(lookback-1) .. yesterday]` and loop `rebuild_daily_ship_stats_for_date(d)` per date — the function is already idempotent (`incremental_battles.py:960`, deletes-then-rebuilds for the date).
- **Period tiers — dedup, do not rebuild per day.** `rebuild_period_rollups_for_date(date)` (`incremental_battles.py:1356`) rebuilds week+month+year for a *single* date, so calling it per day across the window redoes the same week/month/year repeatedly (yearly is a full-YTD DB scan via `_aggregate_into_period_table`, `incremental_battles.py:1265`). Instead compute the **distinct per-tier anchors** the window touches using the existing `_week_start` / `_month_start` / `_year_start` helpers (`incremental_battles.py:1251-1262`), then call the lower-level `_aggregate_into_period_table(anchor, anchor_end, table)` **once per distinct `(anchor, tier)`**. Add a small wrapper `rebuild_period_rollups_for_window(dates)` in `incremental_battles.py` that owns the dedup + period-end/leap math (keeps it next to its existing owner, not in the task).
- Preserve `target_date_iso` for manual single-date runs; when supplied, lookback collapses to 1 (one day, one period pass) — back-compatible with the existing manual/`rebuild` semantics.
- Add a **single-run global lock** (`cache.add(_task_lock_key("roll_up_player_daily_ship_stats","global"), …)`, pattern at `tasks.py:1797-1826`) since the windowed run outlasts a single day and could overlap a slow prior run.
- Keep the `BATTLE_HISTORY_ROLLUP_ENABLED` gate and the start/finish log lines; log a per-run summary `{days_rebuilt, periods_rebuilt, per-day result dicts}`.

### 2. BRIN index + half-open range filter — guarded migration + `incremental_battles.py`

- Add the BRIN index `battle_event_detected_brin` on `BattleEvent.detected_at` via a **guarded raw-SQL migration** (`CREATE INDEX CONCURRENTLY ... USING brin (detected_at)`), **not** as a `BattleEvent.Meta.indexes` entry. `detected_at` is monotonic (insert-time `NOW()`), so a BRIN index gives near-perfect range pruning at negligible write cost — the right index type for an append-mostly timestamp on a large/high-write table. **Why a guarded migration and not `Meta`:** the release gate builds the SQLite test DB straight from model `Meta` with `pytest --nomigrations` (`run_test_suite.sh`), and SQLite has no BRIN — declaring it in `Meta` would emit `USING brin` into the syncdb DDL and break the gate. This mirrors the established pg_trgm GIN index pattern (`migrations/0019_add_player_name_trigram_index.py`): the index lives only in the migration, guarded by `schema_editor.connection.vendor != 'postgresql'`, and a `NOTE` in `BattleEvent.Meta` documents it.
- Replace `detected_at__date=target_date` with a **half-open naive-UTC datetime range** `detected_at__gte=day_start, detected_at__lt=next_day_start` at every day-filter site:
  - `rebuild_daily_ship_stats_for_date` main query (`incremental_battles.py:986-988`) **and** the `events_seen` fallback count (`:1039-1041`),
  - the management command's count query (`management/commands/rebuild_player_daily_ship_stats.py:63-65`),
  - the reconciliation aggregates (below).
  A range predicate uses the BRIN index; `__date=` (a function on the column) would not. The project is `USE_TZ=False`/UTC (`[[project_backend_utc_date_bucketing]]`), so the boundaries must be **naive UTC datetimes** (`datetime.combine(d, time.min)` and `+ timedelta(days=1)`).
- Migration `0063_battle_event_detected_brin` runs `CREATE INDEX CONCURRENTLY` inside a `RunPython` with `atomic = False` — `BattleEvent` is large and high-write; a plain `CREATE INDEX` would take a disruptive lock. (`AddIndexConcurrently` was the original plan, but a vendor-guarded `RunPython` is what keeps the index out of SQLite/`--nomigrations` runs while still building concurrently on Postgres.)

### 3. Reconciliation (alert-only) — `incremental_battles.py` + `tasks.py` + `signals.py`

- `reconcile_daily_rollup_coverage(audit_days=30)` in `incremental_battles.py`:
  - Per `(date, mode)` over the window, compare `SUM(BattleEvent.battles_delta)` vs `SUM(PlayerDailyShipStats.battles)`. Both are **DB-side aggregates** (`.values().annotate(Sum(...))`, the `_aggregate_into_period_table` pattern) — no Python row load. Bucket `BattleEvent` by `detected_at` date via the same half-open range (or `TruncDate`).
  - Flag a date when `BattleEvent` has battles but `PlayerDailyShipStats` is missing or under-counts. **Ignore legitimately-zero days** (no events ⇒ no expected rows). Compare **per mode**: the daily layer carries both random and ranked, but the period tiers are randoms-only (`incremental_battles.py:1303`) — so reconcile the **daily** layer, never the period tiers.
  - Return `{discrepancies: [{date, mode, be_battles, pds_battles, delta}], audit_days}`. **No writes.**
- `reconcile_battle_history_rollup_task` in `tasks.py` (`@app.task(queue='background', **TASK_OPTS)`):
  - **Gated by its own flag** `BATTLE_HISTORY_RECONCILE_ENABLED` (default `0`), **independent of `BATTLE_HISTORY_ROLLUP_ENABLED`**. This independence is the point: the reconcile task must be able to detect "rollup is off / holes exist" even when the rollup gate is down. Window from `BATTLE_HISTORY_RECONCILE_AUDIT_DAYS` (default `30`).
  - `logger.warning` one line per discrepant date; `logger.info` a clean summary otherwise.
- Register `battle-history-rollup-reconcile` PeriodicTask in `signals.py` (mirror the block at `signals.py:742-766`), crontab ~05:00 UTC — after the rollup window (04:30) completes.
- Management command `reconcile_battle_history_rollup` (`--audit-days`, prints the report) for on-demand ops and local validation.

### 4. Ops check script — `server/scripts/check_battle_history_rollup.sh`

Single-SSH read-only report mirroring `scripts/check_enrichment_crawler.sh`: runs the reconciliation management command on the droplet and prints discrepant dates + the last rollup-task log summary. Read-only; never mutates. This is the external read that covers the reconcile task's own blind spot (a reconcile task that is itself down cannot warn about itself).

## Production rollout — gate-state branch

The rollout branches on the current value of `BATTLE_HISTORY_ROLLUP_ENABLED` on the droplet. CAPTURE + API appear live; ROLLUP is unconfirmed. **Resolve the gate state first** by either an operator confirmation or, with approval, a read-only check on the non-secret `.env` (do **not** dump `.env.secrets`):

```bash
grep -hoE 'BATTLE_HISTORY_(CAPTURE|ROLLUP|API)_ENABLED=[01]' /root/battlestats/server/.env
```

### Branch B1 — ROLLUP already = 1 (sweeper running nightly)

Deploy the self-heal + reconciliation + index changes. **No initial backfill** — recent days are already built, and the first widened run reconciles the trailing window. Then flip `BATTLE_HISTORY_RECONCILE_ENABLED=1`, restart the workers, and verify with the check script.

### Branch B2 — ROLLUP = 0 (calendrical layer dormant)

This is an *enablement*, not just a patch — only the live 24h read window has data; the daily table is empty for history. Sequence:

1. Deploy code + index migration with `BATTLE_HISTORY_ROLLUP_ENABLED` still **off** (additive, inert).
2. **Initial backfill, strictly day-by-day**, via the existing management command from the capture-start date to yesterday:
   ```bash
   cd /root/battlestats/server
   python manage.py rebuild_player_daily_ship_stats --since <capture-start> --until <yesterday>
   ```
   The command already loops one day at a time (`rebuild_player_daily_ship_stats.py:62-84`); each day is ~40K rows — safe. The OOM ceiling only bites a *single multi-day call* into the Python-load path, which the per-day loop avoids. **Never** attempt a multi-day rebuild in one call.
3. Set `BATTLE_HISTORY_ROLLUP_ENABLED=1` (and `BATTLE_HISTORY_RECONCILE_ENABLED=1`), `systemctl restart battlestats-celery battlestats-celery-beat`.
4. Verify via the check script and a clean reconciliation report.

## Reconciliation interpretation

- **Clean run:** one `logger.info` summary, zero discrepancies. The daily layer matches `BattleEvent` across the audit window.
- **A discrepant date inside the trailing window (≤ N days):** the next nightly sweeper run will self-heal it; no action needed unless it persists across runs (which would indicate the sweeper is failing — check the `background` worker / Beat).
- **A discrepant date beyond the window:** the self-heal will never reach it. Repair manually with `rebuild_player_daily_ship_stats --since <date> --until <date>` (day-by-day if a span). Re-run the check script to confirm it clears.
- **Every recent date discrepant:** the rollup gate is off or the worker/Beat is down — this is exactly the "permanent holes" mode the reconcile task exists to surface. Fix the gate/worker, then backfill the gap day-by-day.

## Rollback

- **Self-heal:** set `BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS=1` to restore yesterday-only behavior without a redeploy (env read at runtime; restart workers).
- **Reconciliation:** `BATTLE_HISTORY_RECONCILE_ENABLED=0` + restart — the task goes dormant. Alert-only, so it never touched data.
- **Index:** reverse the `AddIndexConcurrently` migration (`DROP INDEX CONCURRENTLY`). No code references the index by name except the model `Meta`.
- All three are independently reversible; none deletes data.

## Validation

1. **Local** (`[[project_local_dev_stack]]`): `BATTLE_HISTORY_ROLLUP_ENABLED=1`, seed `BattleEvent` across ~5 days, run the sweeper (lookback=3); inspect `PlayerDailyShipStats` coverage; run reconciliation → clean. Punch an in-window hole (delete one day's PDS) → sweeper heals it. Punch a beyond-window hole → reconciliation flags it; `rebuild_player_daily_ship_stats --since/--until` repairs it.
2. **Migration:** apply locally; `EXPLAIN` the windowed range query to confirm the BRIN index is used.
3. **Gate:** `python -m pytest server/warships/tests/test_incremental_battles.py -x` + the curated backend release gate.
4. **Prod (post-deploy):** tail the `background` worker for the sweeper window summary + reconciliation WARNs; run `server/scripts/check_battle_history_rollup.sh`.

### Test coverage (extend `server/warships/tests/test_incremental_battles.py`)

Extend the existing suites (`RebuildDailyShipStatsTests` 1481, `RankedRollupWriteTests` 1565, `RebuildManagementCommandTests` 1738, `PeriodRollupsTests` 2892):

- Sweeper rebuilds exactly the last N days; older days untouched.
- Self-heal: delete PDS for an in-window day → task restores it; idempotent on re-run.
- Period rebuild runs once per distinct period when the window crosses a week boundary.
- Range filter preserves date isolation (existing canary tests pass after `__date`→range).
- Reconciliation: detects BE-present/PDS-missing and under-counts; clean when consistent; ignores zero-battle days; respects the mode partition; performs no writes.
- Reconcile task respects its own gate and is independent of the ROLLUP gate.

## Known limitations

- **Gap B — bucketing accuracy (structural, out of scope).** Because `detected_at` is the capture time, sparse `BattleObservation` capture collapses many real-play days onto one `detected_at`. The rollup faithfully mirrors *mis-bucketed* events; no amount of rollup recalculation fixes this. It is bounded by observation-floor density — see `runbook-battle-observation-floor-2026-05-02.md` and the floor-starvation note in `runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md`. This runbook guarantees the derived layer is *internally consistent with `BattleEvent`*, not that `BattleEvent` is bucketed on the true battle day.
- **Phase 7 historical zeros (heals forward only).** The Phase 7 widening columns (gunnery/torpedo/spotting/caps deltas) are `0` for all pre-widening `BattleEvent` rows — the raw observations predate those keys. Rebuilding a historical day cannot recover them. See `runbook-battle-history-phase7-data-widening-2026-04-29.md`.
- **Closed ship-badge seasons are frozen.** Ship-standings snapshots for closed seasons are not retroactively corrected by a rollup rebuild; `backfill_ship_seasons --wipe` only helps when the underlying events were dense in the first place.
- **Reconciliation only alerts.** It never repairs. Beyond-window repair is always an explicit, human-initiated `rebuild_player_daily_ship_stats` run — deliberately, to avoid the unbounded multi-day Python-load OOM ceiling.

## Out of scope (follow-ups)

- **DB-side rewrite of `rebuild_daily_ship_stats_for_date`** — retires the `TODO(2026-Q3)` at `incremental_battles.py:982-985`; prerequisite for a *wide* lookback window (the current per-day Python load is only safe at small N).
- **Gap B accuracy** — battle-time attribution / observation-floor density work.
- **Reactivating the weekly/monthly/yearly period tiers in the UI** — they remain dormant; this work only keeps them internally consistent via the dedup'd rebuild.

## Env knobs (full catalog in `ops-env-reference.md`)

| Knob | Default | Effect |
|---|---|---|
| `BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS` | `3` | Trailing-window size the nightly sweeper rebuilds (self-heal). `1` = legacy yesterday-only. |
| `BATTLE_HISTORY_RECONCILE_ENABLED` | `0` | Gates the alert-only reconciliation task; independent of `BATTLE_HISTORY_ROLLUP_ENABLED`. |
| `BATTLE_HISTORY_RECONCILE_AUDIT_DAYS` | `30` | Audit window the reconciliation task scans. |

## File map (implemented)

| File | Change |
|---|---|
| `server/warships/tasks.py:1738` | Widen sweeper to trailing window + dedup period rebuild + global lock; add `reconcile_battle_history_rollup_task` |
| `server/warships/incremental_battles.py:960,986,1039` | Half-open range filter; `rebuild_period_rollups_for_window`; `reconcile_daily_rollup_coverage` |
| `server/warships/models.py:584` | `NOTE` documenting the BRIN index (built in the migration, not `Meta`) |
| `server/warships/migrations/0063_battle_event_detected_brin.py` | Guarded `RunPython` `CREATE INDEX CONCURRENTLY ... USING brin` (`atomic=False`) |
| `server/warships/signals.py:742` | Register `battle-history-rollup-reconcile` PeriodicTask |
| `server/warships/management/commands/` | New `reconcile_battle_history_rollup`; range-filter `rebuild_player_daily_ship_stats.py:63` |
| `server/scripts/check_battle_history_rollup.sh` | New read-only ops report (sibling of `check_enrichment_crawler.sh`) |
| `agents/runbooks/ops-env-reference.md` | Three new knobs (done with this runbook) |

## References

- Rollout / Phase 3 sweeper origin: `agents/runbooks/runbook-battle-history-rollout-2026-04-28.md`.
- Phase 7 widening (historical zeros): `agents/runbooks/runbook-battle-history-phase7-data-widening-2026-04-29.md`.
- Observation floor (Gap B density): `agents/runbooks/runbook-battle-observation-floor-2026-05-02.md`.
- Idempotent rebuild primitives: `server/warships/incremental_battles.py:960` (daily), `:1265,1356` (period).
- Backfill/repair driver: `server/warships/management/commands/rebuild_player_daily_ship_stats.py`.
- Lock + task-option conventions: `server/warships/tasks.py:170` (`_task_lock_key`), `:19` (`TASK_OPTS`), `:1797-1826` (lock usage).
- Check-script conventions: `scripts/check_enrichment_crawler.sh`.
