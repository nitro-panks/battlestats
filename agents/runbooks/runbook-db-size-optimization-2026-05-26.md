# Runbook: Database size optimization (managed Postgres `db-postgresql-nyc3-11231`)

_Created: 2026-05-26_
_Context: The DO managed Postgres backing battlestats went read-only from disk exhaustion on 2026-05-24 (broke a deploy mid-flight). That acute incident was triaged in `runbook-db-cpu-saturation-2026-05-24.md` (BattleObservation compaction shipped + a one-time `VACUUM FULL` reclaimed ~15 GB). This runbook is the durable follow-up: it documents how the DB is actually used today from live measurement, and lays out a tiered, mostly-reversible plan to cap growth and improve query performance. The dataset is genuinely small; the size is JSON/TOAST bloat on two tables._
_Status: **PLANNED** — analysis complete and measured 2026-05-26; remediation not yet executed. Authored as a plan, not run. Supersedes the disk axis of `runbook-db-cpu-saturation-2026-05-24.md` (CPU axis there is already resolved; that runbook retains the alert log)._

## Goal & scope (confirmed with user, 2026-05-26)

- **Goal = prevent another read-only outage + improve performance.** *Not* a cost downsize. DO
  managed Postgres scales storage **up only** — it cannot shrink in place, so reclaimed space is OS
  **headroom**, not a smaller bill or a smaller disk. A real downsize would require a dump→restore
  migration to a smaller cluster: **explicitly out of scope** (flagged below).
- **`battles_json` work = safe/reversible only this round.** Stop shipping it on the wire + prune it
  for inactive players; rely on the existing derived columns. **No structural refactor** of the
  battle-history lifetime baseline this round.

## Current state (measured live, 2026-05-26)

Cluster databases: `defaultdb` **23 GB** · `umami` 13 MB · `test_defaultdb` 10 MB · `_dodb` 8 MB.
Nothing else is large — the "~30 GB" seen on the DO dashboard is the disk-usage figure (incl.
WAL/temp) on the **60 GB** disk. Actual content is **23 GB**, so headroom is currently healthy and
this work is **preventive, not acute**.

Top tables (`pg_total_relation_size`, `public` schema):

| Table | Total | Heap | Idx | TOAST | Live | Dead |
|---|---|---|---|---|---|---|
| `warships_player` | **11 GB** | 2515 MB | 809 MB | **8391 MB** | 1.03M | 163K |
| `warships_battleobservation` | **8.5 GB** | 204 MB | 86 MB | **8286 MB** | 922K | 0 |
| `warships_playerachievementstat` | 1.1 GB | 492 MB | **614 MB** | 0 | 3.68M | 151K |
| `warships_playerdailyshipstats` | 605 MB | 302 MB | 303 MB | 0 | 1.35M | 151K |
| `warships_battleevent` | 512 MB | 259 MB | 253 MB | 0 | 1.37M | 11 |
| `warships_playerexplorersummary` | 370 MB | 190 | 180 | 0 | 617K | 6K |
| `warships_playerweeklyshipstats` | 235 MB | — | — | 0 | 652K | 0 |
| `warships_playermonthlyshipstats` | **106 MB** | — | 76 MB | 0 | **0** | 0 |
| `warships_playeryearlyshipstats` | **64 MB** | — | 34 MB | 0 | **0** | 0 |

Two TOAST/JSON tables are ~83% of the DB. **The data is small; the JSON blobs are not.**

Re-run these to reproduce / measure before & after (always with a `statement_timeout`):

```sql
SET statement_timeout='90s';
SELECT pg_size_pretty(pg_database_size('defaultdb'));
SELECT c.relname,
       pg_size_pretty(pg_total_relation_size(c.oid))               AS total,
       pg_size_pretty(pg_relation_size(c.oid))                     AS heap,
       pg_size_pretty(pg_indexes_size(c.oid))                      AS idx,
       pg_size_pretty(COALESCE(pg_total_relation_size(c.reltoastrelid),0)) AS toast,
       s.n_live_tup, s.n_dead_tup
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relid=c.oid
WHERE n.nspname='public' AND c.relkind='r'
ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 20;
```

Connect from `server/` with the cloud creds: `source .env.secrets.cloud`, then
`psql -h db-postgresql-nyc3-11231-do-user-8591796-0.m.db.ondigitalocean.com -p 25060 -U doadmin -d defaultdb`
with `PGSSLMODE=require PGSSLROOTCERT=ca-certificate.crt PGPASSWORD=$DB_PASSWORD`.

### Findings driving the plan

- **`warships_player` TOAST (8.4 GB) is dominated by `battles_json`** — the raw per-ship
  `ships/stats/` payload, ~10× every other JSON column in a page-level sample, ~24 KB/blob. It exists
  on **354,204 of 1.03M players**; of those, **141,706 are inactive >90 days** (84,398 stale >180d) —
  a live ~3 GB prune lever. **The frontend never reads `battles_json`** (grep of `client/app`: 0
  refs; same for `tiers_json`/`type_json`/`activity_json`/`achievements_json`; only `randoms_json`,
  `ranked_json`, `efficiency_json` are consumed). Server-side, `battles_json` is still load-bearing:
  it derives `tiers/type/randoms_json` on write (`data.py:2520-2522`), seeds the battle-history
  lifetime baseline (`views.py:651`), and the randoms endpoint falls back to `randoms_json` when it
  is NULL (`views.py:449-453`, fallback verified). It is **refetched fresh every 15 min** on visit
  (`views.py:321-328`) → any stored copy is **disposable**. 163K dead tuples (~16% of heap) await
  vacuum.
- **`warships_battleobservation` (8.5 GB, ~all TOAST) regrew** from the post-VACUUM-FULL 7 GB (2 days
  earlier) because the daily compactor keeps **`COMPACT_KEEP_PER_PLAYER_DEFAULT = 3`**
  (`incremental_battles.py:1066`) observations' JSON per player. The diff path consumes **only the
  single most-recent prior observation** per (player, mode): random diff reads `previous` via
  `.first()` (`incremental_battles.py:749-754`, `:596`); ranked walk-back returns the first non-NULL
  match (`:358-403`). So **keep=1 is correct for diff integrity**; keep=3 is ~3× waste. As of
  measurement, 358K rows still held a random payload and 284K a ranked payload (642K vs the ~137K
  distinct players → far above the keep=1 baseline).
- **Dormant rollup tables** `playermonthlyshipstats` (106 MB / ~124K rows) / `playeryearlyshipstats`
  (64 MB / ~119K rows). **CORRECTION (2026-05-26):** an earlier draft of this runbook called these
  "empty / writer disabled" — that was **wrong on both counts**, derived from a stale `n_live_tup=0`
  statistic. The nightly rollup writer **is active** on prod (`BATTLE_HISTORY_ROLLUP_ENABLED=1` lives
  in `.env.cloud:13`, so every deploy writes it to the live env) and populates these tables. The data
  is *dormant* — the weekly/monthly/yearly UI pills are hidden (`incremental_battles.py:1303`, commit
  `7dc7e86`), all live battle-history windows read the intact `PlayerDailyShipStats`
  (`views.py:516-519`), and `?period=monthly` is a legacy API escape hatch the frontend no longer
  uses (`views.py:1150-1153`) — but it is **real derived data**, not bloat-only. **Do NOT `TRUNCATE`**
  to reclaim (that deletes ~243K real rows; it was done in error on 2026-05-26 and recovered by
  rebuilding from the daily layer — see Execution log). The index bloat is reclaimable losslessly via
  `REINDEX TABLE` instead.
- **`playerachievementstat` (1.1 GB; 614 MB indexes > 492 MB heap)** is a denormalized mirror of
  `Player.achievements_json`, rewritten in lockstep on every refresh (`data.py:540-553`). It is read
  only by account-merge ops (`player_records.py`), not by any API/view — but the merge read and the
  unique-constraint index are real, so this is a **Tier-3/future** lever, not a quick win.

## Remediation plan

Tiered by risk × reversibility. Most of Tier 1 is config + ops, not new code — reuse the existing
`prune_battle_observations` tooling. **Every ad-hoc heavy query / `VACUUM` gets
`SET LOCAL statement_timeout`** and a `pg_stat_activity` after-check (lesson from the 2026-05-24
runaway query that pinned a core for 7.6 h: killing the `psql` client does **not** cancel the
server-side backend — confirm with `pg_cancel_backend(pid)` if needed).

### Tier 1 — Safe, mostly-reversible reclaim (cap growth + headroom)

1. **BattleObservation keep-set 3 → 1.** Append `BATTLE_OBSERVATION_COMPACT_KEEP=1` to
   `/etc/battlestats-server.env` (the task already reads it — `tasks.py:1603`; default `"3"`), restart
   `battlestats-beat` + `battlestats-celery-background`. Run the prune live in paced batches until the
   dry-run reports ~0 candidates:
   ```
   python manage.py prune_battle_observations --dry-run --keep-per-player 1
   python manage.py prune_battle_observations --keep-per-player 1 --batch-size 2000 --sleep 0.5 --max-rows 200000   # repeat
   ```
   Then a windowed `VACUUM (FULL, VERBOSE)` on `warships_battleobservation` (heap is only 204 MB →
   short lock, ~minutes; captures resume right after, as in the 2026-05-24 run). Use
   `lock_timeout='2min'` + `statement_timeout='20min'` as `doadmin`. **Est. ~4-5 GB to OS.** Safe:
   diff path needs only keep=1.

2. **REINDEX the dormant rollup tables — do NOT truncate.** `REINDEX TABLE
   warships_playermonthlyshipstats; REINDEX TABLE warships_playeryearlyshipstats;` reclaims the index
   bloat (the bulk of their ~170 MB) **without deleting the ~243K real, writer-maintained rows**.
   Truncating these deletes derived data that the active nightly rollup will only partially refill
   (it rebuilds the current period, not history); full recovery requires rebuilding every
   month/year from the daily layer (see Execution log, 2026-05-26). Reclaim is modest (~120-150 MB
   of index space) and lossless.

3. **Prune `battles_json` for inactive players (new mgmt command).** Mirror the
   `prune_battle_observations` shape (`--dry-run`, `--batch-size`, `--sleep`, `--max-rows`,
   `SET LOCAL statement_timeout`). NULL **only** `battles_json` (keep the small derived
   `tiers/type/randoms_json`) for `is_hidden=false AND last_battle_date < now()-INTERVAL '180 days'`
   (start at 180d conservative ≈ 84K rows; can tighten to 90d ≈ 142K rows later). **Reversible** —
   refetched on next visit (`views.py:321`); randoms endpoint already falls back to `randoms_json`
   (`views.py:451-453`); battle-history for a >180d-inactive player is empty anyway. Follow with a
   **regular `VACUUM`** so freed TOAST returns to reusable. **Est. ~2 GB at 180d.**

4. **Drop unused JSON from the player wire payload.** Replace `PlayerSerializer`'s
   `fields = '__all__'` (`serializers.py:102`) with an explicit field list that **omits
   `battles_json`, `tiers_json`, `type_json`, `activity_json`, `achievements_json`** (frontend reads
   none of them). **0 GB on disk**, but trims ~24 KB/player-page off the wire **and** off the
   Redis-cached `get_cached_player_detail` dict — easing the 3 GB `allkeys-lru` cache. This is an
   **API contract change**: per team-doctrine rule 5, update the data-product contract test
   (`test_data_product_contracts.py`) + any contract doc in the same commit. Verified safe
   server-side: every server read of these fields reads from the model, not the serialized dict
   (derivations at `data.py:2520-2522`, baseline at `views.py:651`, summary `build_player_summary`).

5. **Regular `VACUUM (ANALYZE)`** the dead-tuple tables — `warships_player` (163K),
   `playerachievementstat` (151K), `playerdailyshipstats` (151K), `snapshot` (58K), `clan` (23K).
   0 GB to OS but halts reusable-space drift and refreshes planner stats (perf).

### Tier 2 — Measure, then tune (the performance half)

6. **Decide `warships_player` heap reclaim by measurement, not reflex.** `pgstattuple` is **not**
   installed (only `pg_stat_statements`); `CREATE EXTENSION pgstattuple;` (contrib, DO-supported) and
   measure true bloat **after** the Tier-1 prune. `VACUUM FULL` on the 11 GB hot table takes an
   `ACCESS EXCLUSIVE` lock (write outage for minutes) — only run it in a maintenance window **if**
   pgstattuple shows large reclaimable bloat. Otherwise prefer **per-table autovacuum tuning**
   (lower `autovacuum_vacuum_scale_factor` / `analyze_scale_factor` on the top-5 churn tables) so
   reusable space is held without a locking rewrite. Prior note: a blind `VACUUM FULL` here was
   explicitly deferred on 2026-05-24 pending this measurement.

### Out of scope (do not attempt under this runbook)

- **DO cluster downsize / dump→restore migration** — storage can't shrink in place; separate project.
- **Structural `battles_json` refactor** (move the lifetime baseline into a compact per-ship table so
  the raw blob can be dropped entirely) — possible future runbook.
- **`playerachievementstat` de-duplication** — read by merge ops; Tier-3/future.

## Realistic reclaim ceiling (set expectations honestly)

~**5-7 GB returned to OS headroom**: observation keep-1 ~4-5 GB + empties ~170 MB + inactive
`battles_json` prune ~2 GB — **plus** growth capped and a lighter wire/Redis payload. The DO bill and
the 60 GB allocation do **not** change.

## Critical files

- `server/warships/incremental_battles.py` — `compact_battle_observation_payloads()`,
  `COMPACT_KEEP_PER_PLAYER_DEFAULT` (`:1066`), diff path (`:596`, `:749-754`, `:358-403`),
  rollup gate (`:1299-1308`).
- `server/warships/management/commands/prune_battle_observations.py` — shape to mirror for the new
  `prune_player_battles_json` (inactive-prune) command; `--keep-per-player` etc. (`:57-97`).
- `server/warships/tasks.py:1573-1619` (prune task + env reads incl. `:1603`), `:1532-1569` (rollup
  gate `:1539`).
- `server/warships/data.py:2518-2522` (battles_json write + derivations), `:540-553` (achievements
  lockstep write).
- `server/warships/views.py:321-328` (refetch trigger), `:449-453` (randoms `randoms_json` fallback),
  `:651` (battle-history lifetime baseline).
- `server/warships/serializers.py:80-107` (`PlayerSerializer` wire-trim, `fields='__all__'` at `:102`).
- `server/warships/signals.py:570-586` (`prune-battle-observations` Beat entry).

## Verification (run when executing — not part of authoring this runbook)

- **Before/after sizing:** the per-table `pg_total_relation_size` query above + `pg_database_size`.
- **Diff integrity after keep=1:** confirm new `BattleEvent` rows keep appearing post-compaction for a
  sample active player (the diff only needs the latest observation).
- **Wire-trim safety:** backend `test_data_product_contracts.py` + frontend `npm test` green; load a
  player page in the app and confirm all charts render (dedicated endpoints / `randoms_json`).
- **Inactive-prune reversibility:** visit a pruned >180d-inactive player → `battles_json` refetches
  and the page hydrates within the 15-min refresh path.
- **No runaway backend after every `VACUUM`/prune:**
  `SELECT pid, now()-query_start AS age, state, left(query,80) FROM pg_stat_activity WHERE state='active' ORDER BY age DESC;`

## Execution log — 2026-05-26

Partial execution this session (user-authorized). Result: **`defaultdb` 24 GB → 19 GB.**

**Done:**
- **Compaction env durability fixed + LIVE.** Root cause of the battleobservation regrowth found: the
  deploy script overwrites `/etc/battlestats-server.env` from `.env.cloud` on every deploy, so the
  hand-added `BATTLE_OBSERVATION_COMPACT_ENABLED=1` (2026-05-24) was wiped → the daily compactor had
  been **off**. Added `BATTLE_OBSERVATION_COMPACT_ENABLED=1` + `BATTLE_OBSERVATION_COMPACT_KEEP=1` to
  `server/.env.cloud` (`.env.cloud` is gitignored — the deploy reads the working-tree copy). **Backend
  deployed** (release `20260526113530`); `post_migrate` re-enabled the `prune-battle-observations`
  Beat task (verified `enabled=t`, cron 12:30 UTC) and the live env now carries the vars → the daily
  keep=1 compactor is active and durable.
- **keep=1 prune + VACUUM FULL.** Verified keep=1 diff-safety by reading the diff path directly
  (`record_observation_from_payloads` reads only the single latest prior; compactor preserves
  latest-random + latest-ranked). Dry-run: 233,496 payloads / 82,783 players. Ran live
  (`--keep-per-player 1 --batch-size 2000 --sleep 0.5`): JSON rows 664K → **200,930** (random
  369K→201K, ranked 295K→143K). `VACUUM (FULL, ANALYZE)`: **8,919 MB → 4,231 MB** (~4.7 GB to OS),
  4 min, captures resumed.
- **Inactive `battles_json` prune.** Batched server-side UPDATE (5K/txn, paced, `statement_timeout`)
  NULLing only `battles_json` (derived cols kept) for ~84,398 non-hidden players with
  `last_battle_date` >180d stale → 0 remaining. Reversible (refetched on visit; randoms endpoint
  falls back to `randoms_json`). Followed by `VACUUM (ANALYZE)` → space is now **reusable** within
  `warships_player` (growth capped); ~2 GB to OS still needs a windowed `VACUUM FULL` (Tier 2).
- **`VACUUM (ANALYZE)`** of dead-tuple tables: `warships_player` 163K→1K dead, `playerachievementstat`
  199K→1.2K, `snapshot` 58K→278, `playerexplorersummary` 46K→120, `clan` 23K→3. Planner stats refreshed.
- REINDEX of the rollup tables was **not needed** — the (erroneous) TRUNCATE + clean reinsert already
  produced fresh, unbloated indexes.

**Net result: `defaultdb` 24 GB → 19 GB (~5 GB to OS); growth now capped by the live keep=1 compactor.**

**INCIDENT — erroneous TRUNCATE + recovery.** Acting on this runbook's (now-corrected) "empty rollup
tables" step, ran `TRUNCATE warships_playermonthlyshipstats, warships_playeryearlyshipstats` — but
they held **123,745 / 119,254 real rows** (the `n_live_tup=0` stat was stale; the count printed in the
same script but the TRUNCATE was bundled into the same command, so there was no chance to react).
**Lesson: a destructive op and its pre-check must be SEPARATE tool calls.** Impact was **zero
user-visible** (period UI pills hidden, `incremental_battles.py:1303`; all live windows read daily).
Recovered by rebuilding from the intact daily layer. The codebase function
`rebuild_period_rollups_for_date` **OOM-killed** (7.2 GB RSS) on the memory-constrained droplet — it
loads a full period's daily rows into a Python dict — so recovery used a server-side SQL `GROUP BY`
aggregation instead (memory-safe, seconds). Restored: April 8,784 / **May 617,681** / **yearly-2026
620,308** rows; cross-check passed (monthly≈yearly≈daily random battles ≈ 2,842,600, within 25 of
each other from live writes). The restored data is *more complete* than pre-truncate (123K was
partial → see next).

**NEW ISSUE surfaced — nightly rollup OOM.** Because `rebuild_period_rollups_for_date` OOMs on full
months/years at the current daily-table scale, the nightly `roll_up_player_daily_ship_stats_task` has
likely been **silently failing** on the monthly/yearly tiers (explains the partial 123K/119K
pre-truncate counts). Fix is a separate task: rewrite the period aggregation as server-side SQL
(`INSERT … SELECT … GROUP BY`) instead of a Python row-load. Tracked as a follow-up.

**Remaining follow-ups (deferred):**
- **`PlayerSerializer` wire-trim** (Tier 1 step 4) — a code PR with a contract-test update; skipped
  this session.
- **Tier 2 — `warships_player` OS reclaim:** `CREATE EXTENSION pgstattuple`, measure true bloat, then
  a **windowed** `VACUUM FULL warships_player` (ACCESS EXCLUSIVE on the 11 GB hot table) to return the
  pruned `battles_json` ~2 GB to the OS — or tune per-table autovacuum scale factors instead.
- **Nightly-rollup OOM fix** — rewrite `rebuild_period_rollups_for_date` as server-side SQL so the
  monthly/yearly tiers stop silently failing (see incident note above).

## Related

- `runbook-db-cpu-saturation-2026-05-24.md` — the parent incident (CPU axis resolved; disk axis →
  this runbook). The daily BattleObservation compactor (`BATTLE_OBSERVATION_COMPACT_ENABLED=1`) and
  the one-time `VACUUM FULL` from that session are already in place.
- `runbook-battle-history-rollout-2026-04-28.md` / `runbook-ranked-battle-history-rollout-2026-05-02.md`
  — origin of the BattleObservation capture that drives both bloat sources.
