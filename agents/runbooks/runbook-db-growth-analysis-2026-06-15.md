# Runbook: Database growth analysis & runway (managed Postgres `db-postgresql-nyc3-11231`)

_Created: 2026-06-15_
_Author role: DBA_
_Context: The DO managed Postgres backing battlestats grew ~10%+ since 2026-06-09 (user-flagged). This runbook measures the growth from the live cluster, attributes it per-table, answers "are we saving the right info / optimally?", and computes runway against the **60 GiB hard wall** (storage autoscale is **disabled** — a full disk = read-only outage, as happened 2026-05-24). It is the durable follow-up to `runbook-db-size-optimization-2026-05-26.md` (which got `defaultdb` 24 → 19 GB) — the DB has since regrown to 23 GB._
_Status: **ANALYSIS COMPLETE, REMEDIATION NOT YET EXECUTED.** Authored as a measured plan, not run._

## TL;DR

- **Disk: 49.6% used — 30.4 GB of 60 GiB, 30.85 GB free.** Comfortable *today*; not acute.
- **Storage autoscale is OFF.** The 60 GiB is a hard wall; filling it = read-only outage (the 2026-05-24 failure mode). This is the single most important framing fact.
- **There is no single growth driver.** The June-9 thing the user *noticed* (a 20× step in `Snapshot` row count) is real but is only ~10% of the bytes. Disk is being consumed by the **sum of 4–5 append-mostly tables with no retention policy**. Fixing only the snapshot engine reclaims ~10% and disk keeps climbing.
- **Runway: ~2.5–4 months (~70–120 days)** to a ~90% practical ceiling at the current ~200–350 MB/day, *if nothing changes*. The slope is part-decelerating (BattleObservation, coverage-bound) and part-monotonic-forever (Snapshot / BattleEvent / PlayerDailyShipStats — none have retention).
- **Biggest single lever is a retention/downsampling policy** for the no-retention tables, led by `Snapshot` (the only genuinely unbounded *new* vector) — this is a product decision, not just an ops one.

## Current state (measured live, 2026-06-15)

Cluster: `db-s-2vcpu-4gb`, PG 18, NYC3, **60 GiB** storage (`61440 MiB`), 1 node, autoscale **disabled**.

Databases: `defaultdb` **23 GB** · `umami` 38 MB · `test_defaultdb` 14 MB · rest <8 MB.

**Disk volume (`/var/lib/pgsql`, from the DO Prometheus metrics endpoint):**

| Metric | Value |
|---|---|
| `disk_used_percent` | **49.6%** |
| `disk_used` | 30.4 GB |
| `disk_free` | 30.85 GB |
| `pg_database_size('defaultdb')` | 23 GB |
| gap (disk − logical) | ~7 GB |

The ~7 GB gap is **WAL + temp + logs, not table bloat** (`pg_database_size` already counts in-table dead/reusable space). Confirmed healthy: `max_wal_size=2921MB` + `wal_keep_size=2920MB` ⇒ ~3 GB of WAL is *expected* on disk; the only replication slot is DO's backup tool `pghoard_local` (active, retaining **17 MB** — no stuck slot, no leak). **Consequence for remediation: a `VACUUM FULL` reclaims space *within* the 23 GB, NOT the 7 GB gap.**

Top tables (`pg_total_relation_size`, `public`):

| Table | Total | Heap | Idx | TOAST | Live | Dead |
|---|---|---|---|---|---|---|
| `warships_player` | **10 GB** | 1807 MB | 800 MB | **7718 MB** | 1.06M | 174K |
| `warships_battleobservation` | **7335 MB** | 314 MB | 219 MB | **6802 MB** | 1.89M | 52K |
| `warships_playerdailyshipstats` | 1520 MB | 742 MB | 778 MB | 0 | 3.27M | **410K** |
| `warships_battleevent` | 1239 MB | 630 MB | 609 MB | 0 | 3.33M | 0 |
| `warships_playerachievementstat` | 1180 MB | 502 MB | **678 MB** | 0 | 4.22M | 3K |
| `warships_playerexplorersummary` | 456 MB | 239 MB | 217 MB | 0 | 714K | 0.6K |
| `warships_playerweeklyshipstats` | 427 MB | 191 | 236 | 0 | 1.21M | 0 |
| `warships_snapshot` | 423 MB | 226 MB | 197 MB | 0 | 2.52M | 97K |
| `warships_playermonthlyshipstats` | 390 MB | 173 | 218 | 0 | 981K | 0 |
| `warships_playeryearlyshipstats` | 368 MB | 164 | 204 | 0 | 810K | 0 |

Reproduce with the per-table query in `runbook-db-size-optimization-2026-05-26.md` (always `SET statement_timeout`). Connect from `server/` (env files live in the **main checkout** `/home/august/code/battlestats/server`, not the worktree): `set -a; source .env.secrets.cloud; set +a; PGPASSWORD=$DB_PASSWORD PGSSLMODE=require PGSSLROOTCERT=ca-certificate.crt psql -h db-postgresql-nyc3-11231-do-user-8591796-0.m.db.ondigitalocean.com -p 25060 -U doadmin -d defaultdb`. DO disk/load metrics: metrics creds from `GET /v2/databases/metrics/credentials` (doctl token), scrape `https://<host>:9273/metrics`.

## Attribution: what grew, and why (the corrected headline)

Per-table delta vs the 2026-05-26 post-optimization state (`defaultdb` 19 → 23 GB, +4 GB):

| Table | 2026-05-26 | 2026-06-15 | Δ | Shape | Retention? |
|---|---|---|---|---|---|
| `warships_battleobservation` | 4231 MB | 7335 MB | **+3.1 GB** | TOAST regrowth, **coverage-driven → decelerating** | keep=1 compactor (working) |
| `warships_playerdailyshipstats` | 605 MB | 1520 MB | +915 MB | append, battle-driven | **none** |
| `warships_battleevent` | 512 MB | 1239 MB | +727 MB | append, battle-driven | **none** |
| `warships_snapshot` | ~165 MB | 423 MB | +~260 MB | append daily, **+20× step on 2026-06-09** | **none** |
| weekly/monthly/yearly rollups | ~?? | ~1185 MB | +~200 MB | derived from daily | **none** |
| `warships_player` | 11 GB | 10 GB | ~flat | `battles_json` prune held; floor-refresh repopulating | inactive prune (eroding) |

**The June-9 story (what the user saw):** `snapshot_active_players_task` went live 2026-06-09 (runbook `runbook-daily-active-snapshots-2026-06-09.md`). Daily `Snapshot` writes stepped from ~10K/day → **~200K/day** (verified: June row-count is 1.55M vs ~675K for all of April). That step is the most calendar-conspicuous change — but at ~170 B/row it's only **~34 MB/day ≈ ~10%** of the ~350 MB/day the user flagged.

**The byte story (what's actually consuming disk):** the largest single chunk is **BattleObservation +3.1 GB** — its keep=1 JSON set scales with *distinct players ever observed* (137K → 299K distinct players in 3 weeks as the observation floor + hot-players queue expand coverage; ~21 KB retained JSON each). This is **decelerating** (coverage is bound by the daily-active fraction, ~40% ceiling — see memory `project_coverage_ceiling_daily_active`). The rest is `PlayerDailyShipStats` + `BattleEvent` (battle-driven, append, **no retention**) plus the snapshot step.

**Snapshot's hidden amplifier (verified):** `snapshot_active_players` calls `save_player(core_only=True)` for ~200K players/day, which UPDATEs `warships_player`. The table shows **12.5M lifetime `n_tup_upd` / 5.4M HOT**, 174K dead tuples, autovacuum running ~hourly. So the engine's true cost is **WAL volume + reusable bloat churn on the 10 GB table**, not just its own ~34 MB/day of rows. Autovacuum is keeping pace (dead-tuple count stable), so it's not unbounded table growth — but it is a meaningful WAL/IO contributor and it keeps `warships_player` TOAST from compacting.

## Q1 — Are we saving the right information?

Mostly yes, with two policy gaps:

- **`Snapshot` daily rows are kept forever.** Day-over-day tracking is a core value prop, but does it need *full daily granularity for all history*? ~200K rows/day × ~170 B = ~34 MB/day, monotonic, no prune anywhere (grep: only an account-merge dedup `delete` exists). This is the one genuinely **new unbounded vector**. **Decision needed (product): how far back must daily granularity go?** Beyond that, downsample (e.g. keep daily for N days, then weekly/monthly), which the existing rollup tables already model.
- **`BattleEvent` / `PlayerDailyShipStats` are append-forever** with no retention. They are the durable battle-history substrate (charts read them), so they're "right" data — but an old-history retention/archival policy will eventually be needed; they're each adding ~30–45 MB/day and never shrink.
- **BattleObservation keep=1 compactor is working correctly** (JSON-bearing rows 457K ≈ distinct-players, bounded near keep=1). Saving the right amount here; the growth is legitimate coverage expansion, not a bug.

## Q2 — Are we saving it optimally (incl. denormalizations)?

- **`warships_player.battles_json` (the 7.7 GB TOAST)** — the raw `ships/stats/` blob, **never read by the frontend** (0 refs in `client/app`); server-side it derives `tiers/type/randoms_json` on write and seeds the battle-history baseline. It is **disposable** (refetched every 15 min on visit) and now also **deliberately repopulated for the whole active-7d set by the floor-refresh shipped 2026-06-14** (`FLOOR_REFRESH_BATTLES_JSON_ENABLED`). Net: the May inactive-prune has *eroded* (376K rows bear `battles_json` again) and TOAST won't shrink while the floor refresh runs. **This is the biggest "stored but not directly served" blob** — the deferred `PlayerSerializer` wire-trim (below) is still the right call, and the structural fix (compact per-ship baseline table so the raw blob can be dropped) is the real long-term lever.
- **`playerachievementstat` (1.18 GB, indexes 678 MB > heap 502 MB)** — a denormalized mirror of `Player.achievements_json`, rewritten in lockstep on every refresh, **read only by account-merge ops** (no API/view). Intended denormalization, but index-heavy for a non-served table. Tier-3/future lever (the merge read + unique constraint are real).
- **weekly/monthly/yearly rollups (1.18 GB combined)** — derived from the daily layer, **UI pills are hidden** (`incremental_battles.py`), `?period=monthly` is a legacy escape hatch the frontend no longer uses. Real derived data, but currently unserved. Question worth raising: keep writing them? (Note the known nightly-rollup OOM follow-up in the 2026-05-26 runbook — the period writer may be partially failing anyway.)
- **Dead-tuple / autovacuum hygiene:** `playerdailyshipstats` carries **410K dead** (~12%), `player` 174K, `snapshot` 97K. Autovacuum keeps the churn tables stable but these are candidates for **per-table autovacuum scale-factor tuning** (hold reusable space without locking rewrites). Not disk-fatal; planner-stats + IO hygiene.
- **TOAST high-water-mark** is *inside* the 23 GB (so it's reclaimable by VACUUM FULL, but that does **not** touch the 7 GB WAL/temp gap). A windowed `VACUUM FULL` on `battleobservation` (small heap, short lock) and/or `player` (10 GB, ACCESS EXCLUSIVE — maintenance window only) would return reusable TOAST to OS headroom.

## Q3 — Runway to the 60 GiB wall

**Anchor:** disk_used **30.4 GB / 60 GiB (49.6%)**, free **30.85 GB**, of which ~3 GB is fixed WAL. Autoscale **OFF** ⇒ hard wall; DO read-only protection trips near-full (May 2026: read-only at ~37 GB on the old 40 GB disk ≈ 93%). Practical ceiling ≈ **90% ≈ 54 GB** ⇒ usable headroom ≈ **~24 GB**.

**Slope (inferred — one disk reading + two logical anchors, so a range, not a point):**
- Logical: +4 GB / 20 days = **~200 MB/day average**.
- Recent (post-2026-06-09, incl. snapshot engine + June 8–9 backfill spikes): **~300–350 MB/day**.
- Split by behavior:
  - **Decelerating:** BattleObservation (coverage saturating ~40%).
  - **Monotonic, no retention (permanent):** Snapshot (~34 MB/day + player churn) + BattleEvent (~30 MB/day) + PlayerDailyShipStats (~40 MB/day) ≈ **~105 MB/day floor that never slows or shrinks**.

**Runway estimate:** ~24 GB headroom ÷ {200…350 MB/day} = **~70–120 days ≈ 2.5–4 months** to the ~90% ceiling, *if nothing changes*. The monotonic floor (~105 MB/day) means even after BattleObservation saturates, disk keeps climbing ~3 GB/month — so this is a "must establish retention within ~1 quarter," not a "watch it" situation.

⚠️ **Live load note (out of disk scope, but flagged):** `system_load15 = 3.12` on a 2-vCPU node (saturates ~2) at measurement time — the DB is CPU/IO-saturated *right now*. The snapshot engine's 30-min bulk-fetch + 200K player UPDATEs/day plausibly contributes to both the disk *and* this load axis. Worth a separate look (cf. the CPU axis in `runbook-db-cpu-saturation-2026-05-24.md`).

## Recommended next steps (leverage order)

1. **[Safety net — do first, low effort] Enable storage autoscale OR a disk alert.** Autoscale (`doctl databases storage-autoscale update`) converts the hard-wall outage risk into a (paid) auto-resize; or at minimum set a DO disk-utilization alert at 70%/80%. Given autoscale is *off* and the May outage history, this is the cheapest insurance and removes the acute risk while the retention work is decided.
2. **[Biggest durable lever — needs product decision] Define a `Snapshot` retention/downsampling policy.** This is the only new unbounded vector and the one that changed on the date the user noticed. Decide the full-daily-granularity window, then downsample older rows (the weekly/monthly rollups already model the target). Caps the ~34 MB/day + the `warships_player` write churn.
3. **[Ship the deferred May Tier-1 items]** — **`PlayerSerializer` wire-trim is ALREADY SHIPPED** (May, `e8b3172` / release `20260526125032`; `serializers.py:113-116` `Meta.exclude` drops `battles_json`/`tiers_json`/`type_json`/`activity_json`/`achievements_json`, trimming wire **and** the Redis bulk-player copy on every write path). The "needs contract-test update" caveat was **stale** — the player-detail serializer is not ODCS-contract-governed (contracts cover `PlayerSummarySerializer`/`PlayerExplorerRowSerializer`); no contract work needed. Remaining: per-table autovacuum tuning on `playerdailyshipstats`/`player`/`snapshot`; **re-run the inactive `battles_json` prune (it has eroded) — now via the durable `prune_inactive_player_battles_json` management command** (built `db-battles-json-prune-rerun`; see the execute recipe below), not another ad-hoc `psql` UPDATE.
4. **[Headroom hygiene, windowed] `VACUUM FULL` `battleobservation`** (small heap, short lock, returns ~reusable TOAST to OS) and consider `player` in a maintenance window. Note: reclaims *within* the 23 GB, not the WAL gap.
5. **[Decide] weekly/monthly/yearly rollups** — keep writing UI-hidden derived data (1.18 GB)? Resolve alongside the nightly-rollup OOM follow-up.
6. **[Future / structural] Compact `battles_json` baseline** into a per-ship table so the 7.7 GB raw-blob TOAST can be dropped entirely (separate runbook; the floor-refresh now actively repopulates it, so this is increasingly worth it).

## Verification (when executing)

- Before/after: the per-table sizing query + `disk_used_percent` from the metrics endpoint (logical size lags disk; trust the disk metric for runway).
- Snapshot retention: confirm chart day-over-day still renders for the kept window; cross-check rollups vs daily.
- Wire-trim: already shipped (May) — no action; serializer is not contract-governed.
- Inactive-prune reversibility: visit a pruned player → `battles_json` refetches within 15 min.
- Post-`VACUUM`/prune runaway check: `SELECT pid, now()-query_start AS age, state, left(query,80) FROM pg_stat_activity WHERE state='active' ORDER BY age DESC;` (killing `psql` does not cancel the server backend — `pg_cancel_backend(pid)`).

## Execute recipe — inactive `battles_json` prune (durable command)

Built on branch `db-battles-json-prune-rerun`: core `prune_inactive_player_battles_json` in `incremental_battles.py` + the `prune_inactive_player_battles_json` management command (mirrors `prune_battle_observations`'s flags + scan-once/UPDATE-by-PK shape). NULLs **only** `Player.battles_json` (keeps `tiers/type/randoms/activity_json`) where `is_hidden = false AND battles_json IS NOT NULL AND last_battle_date < today - inactive_days AND enrichment_status <> pending`. Reversible (refetched on next visit); disjoint from the floor refresh by cutoff (floor = active-7d, prune = >180d).

**Enrichment-safety preconditions (do not bypass):** `battles_json IS NULL` is an enrichment candidate-match condition, so the command (1) excludes `pending` rows and (2) **refuses to run unless `--inactive-days > ENRICH_MAX_INACTIVE_DAYS`** (prod pins that env to 7; the default 365 means the command refuses at the 180d default in any un-pinned env — that refusal is intended). Confirm prod's env still pins 7 before the live run.

```bash
cd server
# 1. Dry-run first — candidates, PENDING-in-band-excluded count (~0 in healthy
#    data; non-zero = odd populated-PENDING rows guard-1 already excluded, still
#    safe to proceed — NOT a guard failure), approx reclaim. No writes.
python manage.py prune_inactive_player_battles_json --dry-run
# 2. Paced live run (low-traffic window; idempotent + resumable, re-run tops up).
python manage.py prune_inactive_player_battles_json \
    --batch-size 5000 --sleep 0.5 --statement-timeout 180
# 3. Return freed TOAST to reusable space (VACUUM FULL is the separate windowed Tier-2 op).
psql "$DB_URL" -c 'VACUUM (ANALYZE) warships_player;'
```

Metrics note: NULLing `battles_json` on currently-`enriched` inactive rows makes the next `reclassify_enrichment_status` re-bucket them as `skipped_inactive` — a correct-but-cosmetic shift in the enrichment health read, reversible on refetch. **Cadence is an open question** (manual one-shot vs a low-frequency Beat task) — left to the operator; no Beat schedule was added.

## Related

- `runbook-db-size-optimization-2026-05-26.md` — parent; tiered reclaim plan + execution log (24 → 19 GB). This runbook is the 2026-06-15 re-measurement after regrowth to 23 GB.
- `runbook-db-cpu-saturation-2026-05-24.md` — the read-only outage origin (disk axis) + CPU axis; alert log.
- `runbook-daily-active-snapshots-2026-06-09.md` — the snapshot engine whose June-9 go-live is the calendar-visible (but ~10%-of-bytes) change.
- Memories: `project_coverage_ceiling_daily_active` (the ~40% coverage ceiling that bounds BattleObservation growth), `reference_do_db_cpu_metrics_endpoint` (metrics scrape recipe), `project_db_disk_cpu_incident_2026-05-24`.
</content>
</invoke>
