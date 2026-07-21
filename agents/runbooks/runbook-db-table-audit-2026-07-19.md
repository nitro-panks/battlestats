# Runbook: Database Table Audit (Normalization, Storage Efficiency, Materialization)

_Created: 2026-07-19_
_Context: optimization and cost-efficiency pass over the cloud Postgres (DO managed `db-s-2vcpu-4gb`, PG 18.4). Nothing is broken; the app is in its final form, so this audit asks where the 38 GB footprint goes, whether we write meaningless data, and how the heavy recomputes should be materialized._
_QA: all numbers measured live 2026-07-19/20 UTC with read-only queries (`statement_timeout` 45s, `default_transaction_read_only=on`); sampled figures used `TABLESAMPLE SYSTEM (1–5)` and are marked "est." Code-side claims carry file:line references from a full read of `models.py` + greps of `data.py`/`tasks.py`/`views.py`/`serializers.py`/`incremental_battles.py`._

## Purpose

A findings ledger for the database estate: where storage actually goes, which writes carry no information, which indexes are dead weight, how bloated the hot tables are, and which recomputed aggregations deserve materialization. Each finding carries Risk and Remediation. Read this before any storage-reclamation, index, or materialization work; refresh the numbers with "How to re-measure". See the **Applied log** at the end for which levers have since shipped (the finding sections keep their as-measured wording).

## Topline

| Fact | Value |
|---|---|
| Database total | **38 GB** |
| `warships_player` | 14 GB (2.0 GB heap · 1.0 GB idx · **11 GB TOAST**) |
| `warships_battleobservation` | 12 GB (685 MB heap · 569 MB idx · **11 GB TOAST**) |
| `warships_playerdailyshipstats` | 3.2 GB (7.0M rows, 32d window) |
| `warships_battleevent` | 3.0 GB (7.1M rows, 32d window) |
| `warships_playerexplorersummary` | 2.3 GB (771K rows; see F1) |
| `warships_snapshot` | 1.7 GB (10.0M rows, ~220K rows/day) |
| `warships_playerachievementstat` | 1.3 GB (4.7M rows) |
| `mv_player_distribution_stats` (matview) | 471 MB incl. indexes |
| Everything else combined | < 400 MB |

The two TOAST stores (Player JSON columns + raw observation JSON) are ~22 GB of the 38; the storage story is a JSON story, not a relational-width story.

## Findings

### F1: `playerexplorersummary` heap is 90% empty space (~1.4 GB reclaimable)

`pgstattuple_approx`: table_len 1.6 GB, live tuples **9.0%**, free space 90.2%; live data is ~145 MB. Cause: 13.0M lifetime whole-row UPDATEs (enrichment, CB backfill, efficiency-rank writes) on a wide ~40-column read-model row; autovacuum reclaims tuples but never shrinks the file. Indexes bloated to match: `explorer_realm_score_idx` 243 MB and `explorer_eff_rank_idx` 218 MB for a 771K-row table.

- **Risk of inaction**: ~2 GB of the footprint is air; every seq scan and vacuum pass pays for it.
- **Remediation**: `VACUUM FULL` off-peak (short exclusive lock; profile payloads are cache-fronted) or `pg_repack` for lock-free. Expect ~1.4 GB heap + several hundred MB of index back. Consider `fillfactor=80` afterward so HOT updates absorb future churn.

### F2: `warships_player` heap 28% free (~600 MB); same mechanism, milder

17.9M lifetime updates on 1.08M rows; 28.4% free of a 2.1 GB heap. Piggyback on F1's maintenance window. TOAST-side bloat unmeasurable on DO managed (`pg_toast` schema permission-denied); assume some additional slack there.

### F3: ~69% of daily `Snapshot` rows are zero-information filler

Over the last 3 full days: 660,575 rows written, of which **454,233 have `interval_battles = 0`**; a daily row recording "this active-pool player played nothing today". ~150K meaningless rows/day, ~69% of a stream growing ~220–226K rows/day (≈ 40 MB/day, ≈ 14.6 GB/yr). The write path also fights same-value churn row-by-row: `pg_stat_statements` shows a `DELETE FROM warships_snapshot WHERE battles = …` shape with **2.3M calls** (490 min cumulative), and the table carries 56M lifetime UPDATEs.

**The decided retention policy exists but has never been armed** (verified on the droplet 2026-07-19): `snapshot_retention.downsample_snapshots` (keep 90d daily, collapse older to one row/player/ISO-week; `server/warships/snapshot_retention.py:47`) is fired by a systemd timer every Monday 04:30 UTC, but `/etc/battlestats-*.env` sets `SNAPSHOT_DOWNSAMPLE_ENABLED="0"`, so every weekly run since shipping (2026-06-21 data-lifecycle decision) has been a no-op; live counters agree (`n_tup_del` ≈ 11K lifetime). Snapshot is therefore unbounded *today*. Armed, the table plateaus around ~3.7 GB with a slow weekly-keeper tail.

Reader contract (verified): product surfaces consume only the trailing ~29 days; `update_activity_data` builds the 28d activity series and **already treats a missing date as zero interval** (`data.py:2468–2490`), so sparse writes are read-compatible there; the gap-1d/mover-capture KPI reads consecutive-day pairs and would need a carry-forward lookback. One wart: `update_activity_data` loads the player's *entire* snapshot history to use 29 days of it (`data.py:2471`, unbounded `filter(player=player)`); bound that query regardless of which lever ships.

- **Risk of inaction**: unbounded growth of the densest meaningless-write stream in the system; WAL, index churn (three indexes ≈ 790 MB), and vacuum load daily.
- **Remediation, in value-per-effort order**:
  1. **Arm the downsampler** (zero code): set `SNAPSHOT_DOWNSAMPLE_ENABLED=1` in Pass and regenerate the droplet env (env files are generated from Pass; do not hand-edit). A dry run on 2026-07-19 would delete only ~126K rows (the bulk snapshot engine is ~41 days old, little has aged past 90d), but the value is the plateau, not the first delete.
  2. **Delta-gate the writes** (structural): only ~60–90K of ~226K daily rows represent a player who actually moved (matches the 69% zero-interval measurement). Skipping unchanged players cuts storage, WAL, and autovacuum churn ~60–70%; `snapshot_movers` becomes "rows written today". Medium effort: reconcile the gap-1d pair logic first.
  3. **Tighter in-window prune**: drop zero-interval rows older than ~35d if 2 is not taken.
- Fold F4 into whatever migration lever 2 or 3 produces.

### F4: `Snapshot.battle_type` is a dead column ('' in 100% of sampled rows)

Empty string in every sampled row (2% whole-table sample + all rows of the last 3 days). Byte savings are trivial; the value is schema honesty; remove it (and re-justify `Snapshot.last_fetch`, 8 B × 10M rows) inside whatever migration F3 produces. Do not ship a standalone migration for this.

### F5: `battleobservation`: JSON bounded, rows unbounded; 19% record nothing

The table has **zero lifetime deletes** (`n_tup_del = 0`); rows go back to 2026-04-28 (table birth). Composition (est.): ~545K rows carry ~9 GB of in-window raw JSON (the diff baseline `compute_battle_events` needs, `incremental_battles.py:441`); ~4.2M rows (88.6%) are JSON-stripped skeletons, and **~19% of all rows are fully empty observations** (no `last_battle_time`, no JSON: polls that observed nothing). Code nuance: the compaction task (`compact_battle_observation_payloads`, keep-latest-3-per-player, JSON-null only) is gated `BATTLE_OBSERVATION_COMPACT_ENABLED` **default OFF in code** (`tasks.py:2705`); live data proves it (or the archive path) runs in prod, so the prod env enables it; the repo default is a trap for any new environment.

- **Risk of inaction**: skeleton + index (569 MB) growth is slow but literally unbounded; empty-poll rows are permanent records of "nothing happened".
- **Remediation**: add a row-retention tier to the existing twice-monthly archive job; delete JSON-stripped rows older than the 32d window except each player's latest observation (floor-freshness anchor), and delete fully-empty rows past ~7d. Check `BattleEvent.from_observation_id`/`to_observation_id` `on_delete` behavior first (both FK indexes are unused per F7 and the provenance columns are never queried; consider whether the FKs should survive at all).

### F6: Player JSON columns: weight is where it should be

Per-column sampling (2%, extrapolated): `battles_json` ≈ **4.3 GB** (avg 11 kB where present; 37% of players), `tiers_json` ≈ 650 MB, `ranked_json` ≈ 600 MB, `achievements_json` ≈ 550 MB, `randoms_json` ≈ 485 MB, rest smaller. By activity bucket, `battles_json` sits on the players being served: est. 3.2 GB on active-30d, 1.0 GB on 31–180d, 164 MB on 181–365d, **14 MB on >1y**; the 180d `prune_inactive_player_battles_json` path (`incremental_battles.py:1804`) is visibly working. **No large waste here.** Two code-side notes:

- Only `battles_json` has a prune; `tiers/type/activity/achievements/ranked/randoms/efficiency_json` are kept forever, but their combined tail-weight (~2.5 GB across *all* players) makes a prune a marginal win at best.
- `models.py:45` carries a TODO to extract these blobs relationally. For a final-form app, do not: `battles_json` is the only career-scope per-ship store (BattleEvent/PDSS cover only 32d), and the serializer already excludes the four heaviest blobs from the payload (`serializers.py:117–120`).

### F7: ~1.0 GB of never-scanned indexes (lifetime counters; `stats_reset` is null)

Confirmed `idx_scan = 0`, non-unique, droppable after a final grep:

| Index | Size | Note |
|---|---|---|
| `explorer_realm_score_idx` | 243 MB | `(realm, player_score)` ordering; no reader |
| `warships_battleevent_from_observation_id` + `_to_observation_id` | 265 MB | Django FK auto-indexes; provenance never queried |
| `warships_playerexplorersummary_realm_…_like` | 78 MB | Django adds a `varchar_pattern_ops` twin for every indexed CharField |
| `dly_ship_date_battles_idx` | 73 MB | `(date, -battles)` on PDSS; no reader |
| `warships_playerdailyshipstats_mode_…_like` | 68 MB | pattern-ops twin |
| `warships_battleevent_mode_…_like` | 66 MB | pattern-ops twin |
| `warships_battleevent_season_id` | 65 MB | single-column, low cardinality |
| `mv_player_dist_{realm_ratio, realm_survival, ratio, survival}_idx` | 152 MB | 4 of the matview's 7 indexes never scanned |
| `warships_player_realm_…_like` + `…_enrichment_status_…_like` | 40 MB | pattern-ops twins |

Model-level redundancies the live counters corroborate (single-column index prefix-covered by a composite/unique): `Player.player_id` (45 MB; covered by `unique_player_per_realm`), `Clan.clan_id`, `Player.realm`, `PlayerExplorerSummary.realm`, `StreamerSubmission.status` (covered by `streamer_sub_status_idx`), `PlayerActivityHourly`'s `(realm,hour)` index duplicating its own unique constraint, and `warships_snapshot_player_id` (102 MB; prefix of the `(player_id, date)` unique; its 2.3M scans would transfer).

**Not droppable on scan-count alone**: `unique_player_achievement_source` (395 MB, 0 scans) and the achievement pkey (265 MB); the unique constraint is what makes the delete+recreate/upsert path correct; constraint enforcement does not reliably increment `idx_scan`.

- **Risk**: the `_like` twins matter only for prefix-`LIKE` on non-C collations; search uses `pg_trgm` GIN + ILIKE. Grep for `startswith`/`LIKE 'x%'` before dropping. Every drop also removes write amplification on the churn-heaviest tables.
- **Remediation**: one migration batch; `db_index=False` where the composite covers, `RunSQL("DROP INDEX CONCURRENTLY …")` for the rest. Re-check `pg_stat_user_indexes` after 30 days for seq-scan regressions.

### F8: Foreign `checkpoints*` tables in the production schema

`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` (~300 kB, columns `thread_id, checkpoint_ns, checkpoint_id, …`) are LangGraph checkpointer tables; not Django's. Zero reads or writes in the stats lifetime. Almost certainly an experiment pointed at the prod DSN. Confirm nothing references them, then drop. The durable lesson is DSN hygiene: prod credentials should not reach experiments.

### F9: Materialization: where the recompute cost actually is

Current architecture is already right-shaped: one true matview (`mv_player_distribution_stats`, `managed=False`, refreshed `CONCURRENTLY` in `data.py:2800`), rollup tables (`PlayerDailyShipStats`, `ShipTopPlayerSnapshot`, `Snapshot`, `EntityVisitDaily`), and Redis published payloads; the request path never recomputes. The costs concentrate here (`pg_stat_statements`, lifetime):

| Query family | Shape | Cost |
|---|---|---|
| Clan-crawl candidate selection | `SELECT clan_id FROM warships_clan LEFT JOIN warships_player …` + twin on player | ~31 s mean × 1.6K calls ≈ **1,670 min**; the single largest DB consumer |
| BattleEvent aggregation warms | `SUM(battles_delta) … GROUP BY ship_id[, ship_name]` ×3 shapes | 15–85 s means, ≈ 1,040 min total |
| JSON-element analytical pass | `WITH qualifying … btrim(elem->>…)` over player JSON | **396 s mean** × 52 ≈ 343 min |
| Enrichment reclassify | `UPDATE warships_player SET enrichment_status …` | 238 s mean × 58 |

Recommendations, in leverage order:

1. **Crawl candidate selection**: an anti-join scan, not an aggregation; a matview does not fit. `EXPLAIN` the two statements; likely fixes are a partial index matching the crawl predicate or persisting the candidate frontier in a small table the crawl maintains. Biggest pure-CPU lever on the 2-vCPU DB.
2. **`compute_all_ship_pop_avg_damage`** (`data.py:6922`): ~34 s/realm full grouped scan of PDSS over the 30d window, nightly per realm. The strongest genuine matview/rollup candidate: a per-(realm, ship, day) damage rollup maintained incrementally by the event pipeline would reduce the nightly warm to a 30-row-window sum. Same rollup would serve the ship-combat hit-ratio aggregation (`data.py:6819–6874`).
3. **BattleEvent ship-grouped warms**: the daily layer (PDSS) already exists; migrate the 84 s ship-grouped warm onto it where column coverage allows, instead of re-summing raw BattleEvent.
4. **JSON-element pass (396 s mean)**: the one place the blob design leaks into analytics; 52 calls ≈ daily cadence. Identify the owning task and extract the parsed elements into a relational side table at write time.
5. **`mv_player_distribution_stats`**: keep; drop its 4 unused indexes (F7); at 210 lifetime scans it barely earns 471 MB, so if the distribution payloads are fully served from Redis published copies, consider retiring the matview in favor of the warm writing Redis directly.
6. **Clan roster `is_active_pvp`**: computed per member per `clan_members` request (5-min cache); a denormalized boolean/last-active-pvp column on `PlayerExplorerSummary`, refreshed nightly, would remove the per-request window aggregation. Minor; only if roster latency ever matters.

### F10: Normalization verdict on the relational core

Largely clean. The event pipeline (BattleObservation → BattleEvent → PlayerDailyShipStats) is a textbook raw→delta→rollup design; wide read-models (`PlayerExplorerSummary`, Player's derived scalars, Clan's `cached_*` columns) are deliberate, indexed denormalization consistent with cache-first serving. Findings that are noted, not action items:

- `ship_name` is denormalized onto BattleEvent (`models.py:603`), PDSS (`:696`), and ShipTopPlayerSnapshot (`:821`); semi-intentional (rename-proof history, join-free hot reads; `views.py:846` falls back to ship metadata only when blank). Largest denormalization by row count; fine as is.
- `StreamerSubmission.realm` is free-text `max_length=8` vs the canonical 4-char `REALM_CHOICES`; inconsistency, not a cost.
- Pure derived-scalar redundancy on Player (`pvp_wins+losses≈battles`, `deaths=battles−survived`) backs the sort indexes; intentional.

### F11: Dead and doubtful columns (code-side sweep)

- **BattleEvent's 14 Phase-7 widening columns are write-only on BattleEvent** (`models.py:622–635`; written only in `incremental_battles.py:934–947`): the read path (`views.py:785–870`) touches 8 delta columns. Their PDSS analogues *are* read (`data.py:6819–6874`), and the 32d archive CSVs carry them as history. Cost inside the window ≈ 400 MB. Verdict: keep while the archive contract stands; if the archive is ever deemed sufficient without them, dropping them from BattleEvent (writing straight to PDSS) saves the 400 MB and index churn.
- **`StreamerSubmission.notes`**: zero reads anywhere (`models.py:509`). Dead; remove opportunistically.
- **`Player.last_lookup` / `Clan.last_lookup`**: 99% NULL live; each has an index (18 MB on player). Grep for the writer before removal.
- **`warships_playeractivityhourly`**: self-bounding 72-row buffer, rebuilt hourly (`tasks.py:2633`), 0 index scans / 380 seq scans; its `(realm,hour)` index duplicates its unique constraint (F7). Verify the consuming surface still exists; the table itself is costless.
- **HotPlayer**: live counters confirm the prod queue is fully idle (0 scans, 0 writes); rows retained by design. Note: code default is *enabled* (`hot_players.py:112`); prod relies on the env override, same trap-shape as F5's compaction gate.

## How to re-measure

```bash
cd server && set -a && source .env && source .env.secrets && set +a
PGPASSWORD="$DB_PASSWORD" psql "host=$DB_HOST port=$DB_PORT dbname=$DB_NAME user=$DB_USER sslmode=require" \
  -P pager=off -c "SET statement_timeout='45s'; SET default_transaction_read_only=on;" -c "<query>"
```

Key probes: `pg_total_relation_size`/`pg_indexes_size`/`reltoastrelid` sweep over `pg_class`; `pg_stat_user_indexes` for `idx_scan=0` (lifetime counters; confirm `pg_stat_database.stats_reset` is still null); `pgstattuple_approx('<table>')` for bloat (installed; `pg_toast` schema inaccessible); `TABLESAMPLE SYSTEM (2)` + `pg_column_size(col)` for per-column JSON attribution; `pg_stat_statements` ordered by `total_exec_time` for the recompute profile; `interval_battles=0` counts over recent `warships_snapshot` dates for the filler ratio.

## Validation

- Live measurements 2026-07-19/20 UTC against `defaultdb`, read-only session, 45 s timeout; no writes, no locks.
- Sampling: player column sizes at 2% (~21K rows), observation composition at 1–2%, achievements at 5%; extrapolations marked "est."
- Code sweep: `models.py` in full; per-field greps across `data.py`, `tasks.py`, `views.py`, `serializers.py`, `incremental_battles.py`, `hot_players.py`, `visit_analytics.py`, `snapshot_retention.py`. Note: retention *scheduling* is DB-driven (`django_celery_beat`) and not statically verifiable from the repo; live-data evidence was used to confirm which retention paths actually run.

## Follow-ups

Ordered by expected return per unit of risk:

1. **Arm the snapshot downsampler** (F3.1): flip `SNAPSHOT_DOWNSAMPLE_ENABLED=1` in Pass, regenerate the droplet env. Zero code; converts ~14.6 GB/yr of growth into a ~3.7 GB plateau.
2. **Repack `playerexplorersummary`** (F1): ~1.5–2 GB for one off-peak maintenance window.
3. **Drop the dead-index batch** (F7): ~1 GB + write-amplification relief on the churn-heaviest tables; one migration, 30-day re-check.
4. **Snapshot delta-gated writes** (F3.2+F4): ~60–70% write reduction; needs a short design note (gap-1d pair logic, mover KPI). Bound `update_activity_data`'s unbounded history load at the same time.
5. **Observation row retention** (F5): extend the archive job to delete stripped/empty rows; decide the provenance-FK question at the same time.
6. **Crawl candidate-selection plan** (F9.1): EXPLAIN and fix the two 30 s scans.
7. **`ship_pop_avg_damage` rollup** (F9.2): per-(realm, ship, day) incremental rollup replacing the nightly full scan.
8. **Identify the 396 s JSON analytical task** (F9.4) and relocate it to relational storage if it is a standing daily.
9. **Drop the LangGraph `checkpoints*` tables** (F8) after a reference check; record the DSN-hygiene rule.
10. **Env-gate traps** — **SHIPPED 2026-07-20**: `BATTLE_OBSERVATION_COMPACT_ENABLED` code default flipped `0`→`1` (`tasks.py` task gate + `signals.py` Beat registration) and `HOT_PLAYERS_ENABLED` flipped `1`→`0` (`hot_players.py`, both `tasks.py` gates, `signals.py` maintain registration), aligning code defaults with prod; `BATTLE_OBSERVATION_COMPACT_ENABLED=1` is now pinned explicitly in `deploy_to_droplet.sh` (was only a manual `/etc` edit). Defaults pinned by tests (`test_periodic_schedule_topology.py::EnvGateDefaultAlignmentTests`, `test_hot_players.py`, `test_incremental_battles.py::PruneTaskEnvGateTests`). Residual trap noted, unchanged: `HOT_PLAYERS_MAX` code default 500 vs deploy-script 800.

## Applied log

| Date | Lever | What shipped | Result |
|---|---|---|---|
| 2026-07-19 | F3.1 arm the downsampler | `SNAPSHOT_DOWNSAMPLE_ENABLED=1` via deploy script (46e9822) | First armed run Mon 2026-07-20 04:30 UTC: deleted 126,188 rows in 26 batches, 475,461 weekly keepers, exit 0 — matched the dry run exactly. Snapshot is now bounded. |
| 2026-07-19 | F7 dead-index drop | Migration `0082_drop_dead_indexes` (831463f), applied in prod | ~1 GB reclaimed. 30-day `pg_stat_user_indexes` seq-scan re-check due ~2026-08-19. |
| 2026-07-19 | F1 PES repack | `VACUUM FULL` + `fillfactor=90` + tightened autovacuum opts (op, end of session) | Heap 1.6 GB → 151 MB (85% tuple density), indexes rebuilt to 16 MB each; DB 38 → 35 GB. F2 (`warships_player`, 28% free) deliberately NOT piggybacked — rewriting the 14 GB hot table incl. 11 GB TOAST needs its own decision. |
| 2026-07-20 | F9.1 candidate-scan fixes | Misattribution corrected: the two 30 s statements were the **hot-entity warmer** (`_get_hot_clan_ids` live SUM over every member row, 30.7 s x 1,768 calls) and the **snapshot engine's candidate query** (full-width rows + Snapshot NOT EXISTS, 31 s; briefly 55 s after the checked-set change grew its LIMIT) — not the clan crawl. Fixes: warmer ranks by the denormalized `cached_clan_wr`/`cached_total_battles` columns (no aggregation); engine checked-set is now the sole idempotency (written players marked too) so the Snapshot anti-join is gone; new partial index `player_realm_lbd_active_idx` `(realm, last_battle_date DESC) WHERE NOT is_hidden AND last_battle_date IS NOT NULL` (migration 0084, built CONCURRENTLY) serves every recency-ordered active-pool scan (engine, floor, benchmark). | Expected: the two heaviest statements drop from ~30-55 s to ms-scale index walks; verify in `pg_stat_statements` after a day. |
| 2026-07-20 | F5 observation row retention | `prune_battle_observation_rows` (delete-only tier riding the `archive_battle_history` command/timer): JSON-stripped skeletons > `BATTLE_OBSERVATION_ROW_RETENTION_DAYS` (32) + fully-empty polls > `BATTLE_OBSERVATION_EMPTY_RETENTION_DAYS` (7); JSON-carrying rows and each player's latest observation are never deleted (guarded in the candidate SQL). Gate `BATTLE_OBSERVATION_ROW_RETENTION_ENABLED`, armed via deploy script. FK-safety pre-verified: 0082 relaxed the provenance FKs. | **First run complete 2026-07-20 (~21:07 UTC): 1,965,056 rows deleted** — exactly the dry-run's guarded candidate count (100K validation slice at 78 s incl. VACUUM, then the full pass at sleep 0.3, ~25 min); table 4.97M → 3.01M rows, zero errors, DB connection headroom untouched. **Repack coda (~21:42–21:59 UTC, August-acked)**: `VACUUM (FULL, ANALYZE)` under nohup with lock_timeout=15s — heap 685 → 425 MB, indexes 579 → 272 MB, relation 12 → 10 GB, database 35 → 33 GB; floor journal shows only baseline WG noise through the lock window (no lock/DB errors). Steady state now owned by the twice-monthly timer. Note (August, 2026-07-20): battle-history (BattleEvent/PDSS) retention is 92d — observation skeleton retention stays independently 32d by design (skeletons are diff-provenance, not history; events + CSV archives carry the 92d record). |
| 2026-07-20 | F3.2 + F4 delta-gated writes | `SNAPSHOT_DELTA_GATE_ENABLED` gate in `update_snapshot_data` + engine checked-set + activity-rebuild throttle + carry-forward interval seed (window-edge zero bug fixed) + `battle_type`/`last_fetch` dropped (migration 0083) + `update_activity_data` bounded to the 29d window + mover-KPI reconcile in `benchmark_observation_floor`. **SHIPPED+LIVE v4.2.4** (merge 0e7e51d; backend release 20260720010414, client 20260720010547) | Spec: `agents/work-items/snapshot-delta-gated-writes-spec.md`. Expected ~150K fewer rows/day (~68% of the stream) + per-player purge-DELETE and 29-row same-value bulk_update churn removed. **First gated run verified live** (ASIA, 05:14 UTC 2026-07-20): `Queued: 3000  Snapshotted: 1533  Unchanged-skipped: 1467  Errors: 0` — 49% skipped on the recency-top (most mover-biased) prefix; the whole-pool rate should settle near ~68%. |

| 2026-07-20 | F9.4 JSON analytical pass | **Identified**: the 396 s x 52 statement is `_TIER_TYPE_POPULATION_SQL` (`data.py`) — the tier-type population correlation's `CROSS JOIN LATERAL jsonb_array_elements(battles_json)` career aggregation, run per realm by the daily `player-correlation-warmer-{realm}` Beat (`warm_player_correlations_task` → `warm_player_tier_type_population_correlation`; the 12 h fresh-key TTL always expired before the daily Beat, so every run re-scanned). Feeds the live player-page Population tier-type heatmap — not dead, and no relational source exists (PDSS/BattleEvent are 30d window deltas; the payload is career sums), so the side-table extraction was rejected as oversized. **Fix (bound, code-only)**: rebuild-interval floor — a marker key (TTL `TIER_TYPE_POPULATION_REBUILD_HOURS`, default 72 h, set only after a successful non-empty rebuild) caps the scan at one run per interval per realm; between rebuilds the warmer serves the durable `:published` payload. Empty/missing published rebuilds straight through the marker (asia tracked_population=0 rescue preserved); marker eviction merely rebuilds early. | Expected: ~20 min/day of DB time (3 realms x ~400 s) drops to ~6.7 min/day at the 72 h default (tunable to weekly for ~2.9). Payload contract unchanged; worst-case staleness of the career population baseline grows from ~1 to ~3 days. Verify in `pg_stat_statements`: call rate for the `WITH qualifying … btrim` statement should fall to ~1/realm/3 days. |

| 2026-07-20 | Item 10 env-gate alignment | `BATTLE_OBSERVATION_COMPACT_ENABLED` code default 0→1 (task gate + Beat registration) and `HOT_PLAYERS_ENABLED` default on→off, matching prod; `BATTLE_OBSERVATION_COMPACT_ENABLED` pinned in the deploy script (it existed only as a manual /etc edit from the 2026-05-24 incident). | Sweep also flagged `HOT_PLAYERS_MAX` code 500 vs deploy 800 and `BATTLE_OBSERVATION_COMPACT_KEEP` code 3 vs prod 1 (left as-is — deploy script is authoritative). |
| 2026-07-20 | F9.2 ship-pop daily rollup | New `ShipPopDailyAgg` (realm, mode, ship_id, date; battles/wins/frags/damage/xp/hit-counter columns; migration 0085) maintained by `rollup_ship_pop_daily` with a trailing-2-day re-roll + 100d self-prune; `compute_all_ship_pop_avg_damage` now sums ~30 tiny rows per ship instead of the nightly 34 s/realm PDSS scan. First warm per realm backfills the window once at legacy cost. Payloads/cache keys byte-identical; per-player hit-ratio brackets deliberately NOT migrated (per-player ranking is unservable from a per-ship-day rollup). Spec: `agents/work-items/ship-pop-daily-rollup-spec.md`. | Verify the ship-pop warm duration collapse in the nightly chain after a day. |
| 2026-07-20 | F8 checkpoints* drop | Reference check clean (repo hits were unrelated file-progress checkpoints); all four tables 0 rows/0 scans/0 inserts; schema backup `shared/archives/checkpoints_schema_backup_2026-07-20.sql`; DROP executed by the operator. | DSN-hygiene rule recorded: prod credentials never point at experiments — scratch runtimes get their own database. |
| 2026-07-21 | **F2 `warships_player` repack — RAN, but caused a ~24-min SITE OUTAGE** | Scheduled 2026-07-20 ~22:49 UTC as a one-shot transient service `battlestats-player-repack-oneshot`, fired 04:40:07 UTC → `/root/player_repack_0440.sh` = `VACUUM (FULL, ANALYZE) warships_player` (lock_timeout=15s ×3). | **INCIDENT.** `VACUUM FULL` holds **ACCESS EXCLUSIVE** on `warships_player` — the first hop of every DB request (`Player.objects.get`, name suggestions) — for its entire ~24-min heap+index+TOAST rewrite, so it blocked 56 sessions on relation-lock: **every DB-touching `/api` endpoint timed out (HTTP 000)** while nginx-static and Django-404 stayed fast; managed-PG disk climbed ~10% transiently (2× rewrite files + WAL held for the DO standby). The `lock_timeout=15s` guard only bounds lock **acquisition**, not **hold** — it gave zero protection (the table had just autovacuumed and was quiet, so the exclusive lock was granted immediately). It **self-completed ~05:06 UTC** (heap 2031→1606 MB; table 9.4 GB total / 1.6 GB heap / 2.0% dead) and the site recovered on its own; no cancel was issued. Diagnosed live via a 3-agent read-only fan-out (DB / app / workload). Cleanup: `/root/player_repack_0440.sh` + `.log` deleted (script embedded a plaintext doadmin DSN password); no repack timers/units/cron remain (transient one-shot, will not re-fire). **LESSON: never `VACUUM FULL` a hot/live table.** `warships_player` self-maintains via its aggressive per-table autovacuum (`scale_factor=0.02, threshold=5000` → steady ~2% dead) and needs **no recurring compaction** — do not schedule one. If a one-off on-disk reclaim is ever needed again, use **online `pg_repack`** (available `1.5.2` on the managed DB; `pg_cron 1.6` also available) run supervised — never a scheduled/unattended VACUUM FULL. Retro-note: F1 (PES) and F5 (BattleObservation) repacks also used VACUUM FULL — lower-traffic tables so less impactful, but retire the pattern. |

## Pickup pointer (session close 2026-07-20)

State: levers F3.1 (downsampler), F7 (dead indexes), F1 (PES repack), and F3.2+F4 (delta-gated writes) are all shipped and live in v4.2.4; nothing is half-applied. To resume, work the remaining follow-ups in this order:

1. **Verify the delta-gate settled** (do this first): after a full UTC day, compare `warships_snapshot` rows/day against the ~220K baseline (expect ~70–90K) and eyeball one `/observation` readout — `snapshot_coverage_frac` now reports `null` by design; `snapshot_movers` should hold its ~72K/day continuity. Engine counters: `journalctl -u 'battlestats-celery*' | grep Unchanged-skipped` on the droplet.
2. **F5 observation row retention** — SHIPPED 2026-07-20 (see Applied log); the twice-monthly timer now maintains it. **Post-first-run verify DONE 2026-07-20 (~23:15 UTC)**: dry-run reports 0 candidates on all three tiers; `pg_stat_user_tables` shows `n_tup_del` = 1,965,056 (the exact candidate count), 1 dead tuple, 3.01M live rows / 10 GB relation post-repack, DB 33 GB; floor journal since 21:00 UTC carries only baseline WG test-ship nulls + deploy-restart SIGTERMs (no DB/lock/integrity errors) — change-gate unaffected. (The launch session's SSH client died in the 22:59 deploy-window disconnect long after completion; its exit 255 was transport, not the run.)
3. **F9.1 candidate scans** — SHIPPED 2026-07-20 (see Applied log; the audit's "crawl" attribution was wrong — it was the hot-entity warmer + snapshot engine). Verify `pg_stat_statements` means after a day.
4. **F9.2 `ship_pop_avg_damage` rollup** — DONE 2026-07-20 (see Applied log). **F9.4** — DONE 2026-07-20 (see Applied log; work item `agents/work-items/f94-json-analytical-pass-fix.md`); verify the call-rate drop in `pg_stat_statements` after ~a week. **F8** — DONE 2026-07-20: the four empty `checkpoint*` tables dropped by the operator after a reference check (0 rows/scans/inserts; schema backup `shared/archives/checkpoints_schema_backup_2026-07-20.sql`). DSN-hygiene rule: prod credentials must never be pointed at experiments — scratch runtimes get their own database. (**Item 10** env-gate default alignment — SHIPPED 2026-07-20, see Follow-ups item 10.)
5. **F2 `warships_player` heap** — **DONE 2026-07-21, but it caused a ~24-min site outage (see Applied log 2026-07-21).** The scheduled `VACUUM FULL` held ACCESS EXCLUSIVE on the hottest table for its whole rewrite → all DB-touching `/api` hung until it self-completed. Reclaim succeeded (heap 2031→1606 MB) but the method was wrong. **Do NOT re-run VACUUM FULL here, and do NOT schedule recurring compaction** — the table self-maintains via its aggressive autovacuum (steady ~2% dead). If a future one-off reclaim is ever justified, use **online `pg_repack` 1.5.2** (available on the managed DB) supervised, never unattended VACUUM FULL.
6. **Calendar**: F7 30-day index re-check due ~2026-08-19 (`pg_stat_user_indexes` for seq-scan regressions).
