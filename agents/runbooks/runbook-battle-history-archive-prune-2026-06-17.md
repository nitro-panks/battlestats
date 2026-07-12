# Runbook: Twice-monthly cold-archive + prune of battle-history (>32d retention)

_Created: 2026-06-17_
_Author role: DBA / platform_
_Context: The managed Postgres sits on a 60 GiB disk with autoscale OFF (~50% used as of 2026-06-15). `BattleEvent` + `PlayerDailyShipStats` are the monotonic, never-pruned growth slope identified in `archive/runbook-db-growth-analysis-2026-06-15.md`. This runbook specifies a monthly job that introduces a 32-day rolling window in live Postgres: export everything older to a compressed, restorable file on the app droplet, verify, then delete._
_Status: **IMPLEMENTED + ENABLED in prod, 2026-06-17.** The `archive_battle_history` command + core (`incremental_battles.py`), the `battlestats-archive-battle-history.timer` systemd unit (1st + 15th, 03:00 UTC), deploy env knobs, and tests (`test_archive_battle_history.py`, green on sqlite + Postgres) shipped via PR #50. `BATTLE_HISTORY_ARCHIVE_ENABLED=1`. The 2026-06-17 rollout completed: first-run backlog pruned (703,891 + 694,083 rows) + one-time `VACUUM FULL` (~1 GB reclaimed, DB 23→22 GB); timer next fires 2026-07-01. See the Rollout log._

## Purpose

Cap the unbounded growth of the two append-only, no-retention battle-history tables (`BattleEvent`, `PlayerDailyShipStats`) by enforcing a **32-day rolling window** in live Postgres, while preserving the older data as a **cold queryable archive** (compressed CSV + manifest) on a separate host. Read by the operator who implements the archive command, schedules it, or restores a slice for analysis. It is the durable follow-up to the "biggest single lever is a retention policy" conclusion of `archive/runbook-db-growth-analysis-2026-06-15.md`.

## TL;DR

- **Why:** 60 GiB disk, autoscale OFF = hard wall; a full disk is a read-only outage (the 2026-05-24 failure mode). `BattleEvent` (~+730 MB / 20d) and `PlayerDailyShipStats` (~+915 MB / 20d) grow forever with no retention today.
- **What:** a twice-monthly (1st + 15th, 03:00 UTC) `manage.py archive_battle_history` run — export rows older than `cutoff = midnight_utc(now) - 32d` to `gzip` CSV on the **app droplet** (separate host, ~58 GB free), **verify (full decompress + sha256) before any delete**, then batch-delete only the archived ids by PK, then `VACUUM (ANALYZE)`.
- **Accepted behavior change:** the week/month/**year** battle-history UI windows resolve to the daily layer and will **cap at 32 days**. The 24h "day" window is unaffected.
- **Safety spine:** both tables are FK **leaves** (nothing references them → no cascade), and the prune deletes only the **exact PK set that was just exported and verified** — correct regardless of any concurrent write.
- **Not solved by this:** `PlayerDailyShipStats` keeps growing inside the 32-day window as coverage grows; the disk wall is *deferred, not removed*. See Follow-ups.

## Scope & locked decisions

| Decision | Value | Rationale |
|---|---|---|
| In scope | `BattleEvent` (filter `detected_at`), `PlayerDailyShipStats` (filter `date`) | The two monotonic no-retention growth tables (`archive/runbook-db-growth-analysis-2026-06-15.md`). |
| Excluded | `BattleObservation` | Already handled by prod compaction (`prune_battle_observations` NULLs heavy JSON, keeps rows). `BattleEvent.from_observation`/`to_observation` are **CASCADE FKs into `BattleObservation`** (`models.py:617–626`), so deleting BO rows would destroy the durable event record — and keeping BO rows keeps archived events re-insertable in practice (parents persist). |
| Retention | 32 days, both tables | One window for everything in scope. |
| UI impact (accepted) | week/month/year cap at 32 days | Permanent; impact today is ~2 weeks (pipeline ~6 weeks old). |
| Restore | cold queryable archive | Load compressed CSV into a scratch DB for analysis. No one-command live re-insertion guarantee. |
| Archive host | app droplet | Separate host from the DB; ~58 GB free; no offsite (DO Spaces) yet. |

## Data-safety rationale

**Leaf-table proof (no cascade on delete).** Verified against `server/warships/models.py`:

- `BattleEvent` (`models.py:575`): its FKs point **outward** — `player → Player` (`:580`), `from_observation`/`to_observation → BattleObservation` (`:617–626`). **No model declares an FK into `BattleEvent`.** Deleting a `BattleEvent` row cascades to nothing. Filter column `detected_at` is `auto_now_add=True` (`:582`) — set once at insert, monotonic, never backdated.
- `PlayerDailyShipStats` (`models.py:668`): FK only to `Player` (`:673`); filter column `date` is a `DateField` with `db_index=True` (`:675`). **No model declares an FK into it.** Leaf.

**Read paths that bound the behavior change.** The battle-history API (`server/warships/views.py`):

- `BATTLE_HISTORY_WINDOWS` (`views.py:608–613`): `day → 24h`, `week → 7 daily`, `month → 30 daily`, `year → 365 daily`. The week/month/year windows resolve to the `PlayerDailyShipStats` daily layer, so once rows older than 32d are pruned those windows cap at 32 days. The `year` window's 365-day span is the one that visibly shrinks.
- The 24h "day" window routes through `_build_battle_history_payload_24h()` (`views.py:769`, referenced at `views.py:605`), which reads `BattleEvent` directly (`detected_at >= now-24h`). 24h ≪ 32d, so it is **never** affected by the prune.
- `data.py` reads only the **latest** `BattleObservation` as a diff baseline; `BattleObservation` is out of scope and untouched.

**Why the prune is correct regardless of concurrent writes.** The job does **not** assume "no new `<cutoff` rows can appear mid-run." Correctness comes from the **verify-before-delete gate** and the **delete-only-what-you-exported** discipline:

1. Count `SELECT COUNT(*) WHERE <datecol> < cutoff` (reported in the manifest as `candidates`).
2. Export those rows to the gz (`ORDER BY id`) via server-side `COPY`.
3. **Fully decompress the gz** — this is the completeness check (a truncated / disk-full archive raises here) — and in the same pass **read column 0 (`id`) back out of the verified archive** into a temp file; compute the `.csv.gz` sha256; write the manifest **before any delete**.
4. **Delete only the ids that were read back out of the archive** — never a fresh `WHERE <datecol> < cutoff` delete. We can therefore only ever delete rows that physically landed in, and re-read cleanly from, the archive. A row that appears after the export simply isn't in the delete set and waits for next month; a concurrently-deleted row simply isn't there to delete.

On a failed decompress (truncation / disk-full) or an empty archive: **keep the archive, delete nothing, exit non-zero.** (`exported != candidates` with `--max-rows 0` is logged as a warning, not an abort — we still delete only what was archived.)

> Note on the prior draft: there is no literal "~3-day rollup lookback" constant to lean on — `reconcile_battle_history_rollup` takes `--since/--until`/`audit_days`, not a fixed window. The safety argument above deliberately does not depend on the rollup's write window.

## Flow

```
manage.py archive_battle_history  (systemd timer: OnCalendar=*-*-01 03:00 UTC)
        │  cutoff = midnight_utc(now) - retention_days (32)
        ▼
  for each table in {BattleEvent(detected_at), PlayerDailyShipStats(date)}:
        ① EXPORT  COPY (SELECT * FROM <t> WHERE <col> < cutoff ORDER BY id) TO STDOUT WITH CSV HEADER
                  └─ cursor.mogrify(cutoff) + copy_expert(...) → gzip → <archive-dir>/<run-date>/<table>.csv.gz
        ② VERIFY  fully decompress the gz (completeness check) → spill column-0 (id) to a temp file ;
                  sha256 the .csv.gz ; write <table>.manifest.json (count, sha256, columns, cutoff, version)
                  └─ a truncated / disk-full gz fails to decompress here → keep archive, DELETE NOTHING, fail
        ③ DELETE  delete ONLY the ids read back out of the verified archive, batched by PK
                  └─ SET LOCAL statement_timeout per delete txn + inter-batch sleep
        ④ VACUUM (ANALYZE) <t>      ── NO VACUUM FULL (skipped if inside a txn, e.g. tests)
  single-run lock (cache.add / file) prevents overlap with a still-running prior run
```

## Archive format & location

- **Path:** `${BATTLE_HISTORY_ARCHIVE_DIR}/<run-date>/<table>.csv.gz`, default dir `${APP_ROOT}/shared/archives/battle_history/` where `APP_ROOT=/opt/battlestats-server` (`deploy_to_droplet.sh:19`). The `${APP_ROOT}/shared/` tree already survives deploys and is where the deploy installs durable state (e.g. `shared/logs`, `deploy_to_droplet.sh:63–67`). The install step (below) must `install -d` the `archives/battle_history` subtree the same way.
- **Format:** `COPY ... WITH CSV HEADER` streamed through `gzip`. CSV (not `pg_dump`) so the archive is trivially loadable into Postgres *or* sqlite for analysis.
- **`manifest.json`** (one per run-date, per table or combined): `table`, `cutoff` (ISO), `min_date`/`max_date` of exported rows, `rowcount`, `sha256` of the `.csv.gz`, `columns` (ordered column list — pins the CSV header against future schema drift), `app_version` (from root `VERSION`).
- **Why the droplet:** it is a **separate host** from the managed DB (so the archive doesn't consume the disk we're trying to relieve) with ~58 GB free. No offsite/object-storage copy yet — see Follow-ups.

## The command + flags

New management command `server/warships/management/commands/archive_battle_history.py`, with the core logic in a testable function in `warships/incremental_battles.py` (mirroring how `prune_battle_observations.py` delegates to `compact_battle_observation_payloads`).

Flags (mirror `prune_battle_observations.py`'s shape — dry-run-first, validated):

| Flag | Default | Purpose |
|---|---|---|
| `--retention-days` | 32 | Window kept live; `cutoff = midnight_utc(now) - this`. |
| `--tables` | both | Subset to `battleevent` / `playerdailyshipstats`. |
| `--archive-dir` | `$BATTLE_HISTORY_ARCHIVE_DIR` | Output root. |
| `--batch-size` | 2000 | PKs deleted per transaction (matches `COMPACT_BATCH_SIZE_DEFAULT`, `incremental_battles.py:1346`). |
| `--max-rows` | 0 (unlimited) | Cap rows archived+deleted this run (rollout throttle). |
| `--sleep` | 0.0 | Seconds between delete batches. |
| `--statement-timeout` | 180 | Per-query PG timeout (matches `COMPACT_STATEMENT_TIMEOUT_DEFAULT`, `incremental_battles.py:1347`). |
| `--skip-vacuum` | off | Skip the post-delete `VACUUM (ANALYZE)`. |
| `--dry-run` | off | Report counts + destination paths; write **nothing**, delete **nothing**. Always allowed (ungated). |
| `--force` | off | Run live even when `BATTLE_HISTORY_ARCHIVE_ENABLED != 1` (manual rollout). The scheduled timer relies on the env switch, not this. |

**Reuse, do not reinvent:**
- `_apply_statement_timeout(cur, seconds, is_pg)` (`incremental_battles.py:1414`) — call inside each delete txn (`SET LOCAL` scopes to the transaction).
- The candidate-PKs-once-then-batch-by-PK idiom from `compact_battle_observation_payloads` (`incremental_battles.py:1423`) and `prune_inactive_player_battles_json` (`incremental_battles.py:1637`): collect ids in one scan (capped by `--max-rows`), then mutate by PK in `--batch-size` chunks — so each table is scanned once, not once per batch.
- **Export** uses `cursor.copy_expert("COPY (...) TO STDOUT WITH CSV HEADER", gzip_fileobj)` via psycopg2 (`psycopg2-binary` 2.9.9 is the prod driver). Stream straight into a `gzip.GzipFile` — never materialize the full CSV in memory.
- A **single-run lock** (`cache.add(key, ..., timeout)` or a pidfile under `${APP_ROOT}/shared/`) so an overrunning prior run can't overlap the next month's.

## Scheduling

**Primary: systemd timer.** Shipped in `server/deploy/deploy_to_droplet.sh` (heredoc'd next to the existing `battlestats-celery-watchdog.timer` — that watchdog timer is the in-repo systemd-timer precedent this mirrors). Paths are the **QA-corrected** ones (the venv is `${APP_ROOT}/venv/bin/python`, *not* `current/server/venv`, and the env lives in `/etc/battlestats-server.env` + `.secrets.env`, loaded both by Django's dotenv from `WorkingDirectory` and by the unit's `EnvironmentFile`s):

`/etc/systemd/system/battlestats-archive-battle-history.service` (`Type=oneshot`):
```ini
[Service]
Type=oneshot
User=battlestats
Group=battlestats
WorkingDirectory=/opt/battlestats-server/current/server
EnvironmentFile=/etc/battlestats-server.env
EnvironmentFile=/etc/battlestats-server.secrets.env
ExecStart=/bin/bash -lc 'exec "/opt/battlestats-server/venv/bin/python" manage.py archive_battle_history'
```

`/etc/systemd/system/battlestats-archive-battle-history.timer`:
```ini
[Timer]
OnCalendar=*-*-01,15 03:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

The deploy script `install -d`s `${APP_ROOT}/shared/archives/battle_history`, drops both unit files, `daemon-reload`s, and `systemctl enable --now`s the timer (idempotent). The timer fires on the **1st and 15th** of every month at 03:00 UTC; with `BATTLE_HISTORY_ARCHIVE_ENABLED=1` (prod default since 2026-06-17) it maintains the rolling window, and the command no-ops if the switch is ever set to `0`.

**Why systemd, not Celery Beat:** the first-run backlog (~1.4 M rows) deletes over many minutes — too long for a Celery soft-time-limit / worker slot — and a host-level timer is the natural home for a maintenance job. *Alternative noted for the implementer:* a Celery Beat `crontab(day_of_month="1,15", hour=3)` entry in `signals.py` with `CRAWL_TASK_OPTS`-style limits + the run-lock, **if** the operator accepts the long-task caveat. The systemd path is recommended and is what ships.

## Env knobs

Set via `set_env_value` in `server/deploy/deploy_to_droplet.sh` (line 345) — these survive deploys, whereas `.env.cloud` does not:

| Var | Default | Notes |
|---|---|---|
| `BATTLE_HISTORY_ARCHIVE_ENABLED` | `1` (prod) | Master kill switch; the command no-ops (logs + exits 0) when `0`. Set to `1` in `deploy_to_droplet.sh` since the 2026-06-17 rollout. |
| `BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS` | `32` | Backs `--retention-days`. |
| `BATTLE_HISTORY_ARCHIVE_DIR` | `${APP_ROOT}/shared/archives/battle_history` | Backs `--archive-dir`. |
| `BATTLE_HISTORY_ARCHIVE_BATCH_SIZE` | `2000` | |
| `BATTLE_HISTORY_ARCHIVE_SLEEP` | `0.5` | Gentle inter-batch pause in prod. |
| `BATTLE_HISTORY_ARCHIVE_STATEMENT_TIMEOUT` | `180` | |

## First-run vs steady-state, and the VACUUM policy

- **First-run backlog (measured on prod 2026-06-17, cutoff `2026-05-16`):** **703,891** `BattleEvent` rows (of 3,492,084 — ~20%) and **694,083** `PlayerDailyShipStats` rows (of 3,437,135 — ~20%), both dating back to **2026-04-28** (capture began earlier than the "~May 2" first assumed). Table-data weight ≈ **~250 MB** + **~300 MB** = **~550 MB**. So the first run is **not** the "small ~2-week" backlog originally assumed — it removes ~1.4 M rows / ~20% of each table.
- **Steady state (1st + 15th cadence):** each run culls ~14–17 days of newly-aged `>32d` rows (~⅓–½ of the first-run backlog), so per-run deletes are smaller and dead-tuple buildup is gentler than a once-a-month run.
- **VACUUM policy — `VACUUM (ANALYZE)` per run, plus a ONE-TIME `VACUUM FULL` after the first run.** The per-run command does `VACUUM (ANALYZE)` (no exclusive lock); in steady state delete ≈ insert so freed space is **reused in-table** and that is sufficient. **But the first run deletes ~20% of each table**, and `VACUUM (ANALYZE)` only marks that ~550 MB *reusable in-table* — it is **not** returned to the OS / `pg_database_size`. To actually reclaim the ~550 MB against the autoscale-OFF 60 GiB wall, run a **one-time** `VACUUM (FULL, ANALYZE) warships_battleevent;` + `… warships_playerdailyshipstats;` after the first prune. `VACUUM FULL` takes an `ACCESS EXCLUSIVE` lock (rewrites the table → battle-history reads/writes on that table block for the rewrite), but on ~300 MB tables that is seconds, and it needs transient free disk ≈ the table size (ample: ~30 GiB free). Do it once; never needed again at steady state.

## Restore procedure (cold queryable archive)

To analyze archived rows (e.g. a historical window pruned from live):

1. Copy `<run-date>/<table>.csv.gz` + `manifest.json` off the droplet.
2. **Verify integrity:** `sha256sum <table>.csv.gz` must equal the manifest `sha256`; `zcat <table>.csv.gz | tail -n +2 | wc -l` must equal the manifest `rowcount`.
3. **Load into a scratch DB** (do **not** load into prod):
   - Postgres scratch: `CREATE TABLE archive_<table> (LIKE warships_<table> INCLUDING ALL);` then `\copy archive_<table> FROM PROGRAM 'zcat <table>.csv.gz' WITH CSV HEADER;` — the manifest `columns` list pins the header order against schema drift.
   - sqlite: `zcat <table>.csv.gz | sqlite3 scratch.db '.mode csv' '.import /dev/stdin archive_<table>'`.
4. Query for analysis. Re-inserting into live Postgres is possible (the parent `BattleObservation`/`Player` rows still exist) but is **not** a supported one-command path and is out of scope.

## Rollout

1. **Dry-run on prod** with `--dry-run` (and `BATTLE_HISTORY_ARCHIVE_ENABLED=1`): confirm candidate counts, reclaim estimate, and destination paths look right. No writes.
2. **Round-trip test** (the QA gate — see Validation) on a **small slice** via `--max-rows`: export → load into scratch → counts + sha256 match → confirm the would-be delete set equals the exported PK set.
3. **Throttled live run** with a small `--max-rows`; verify the archive on disk + the post-delete live counts.
4. **Full run** (drop `--max-rows`).
5. **Schedule**: enable the systemd timer (or Beat entry).

## Validation / QA gate

The **restore round-trip is the validation gate** and must pass at rollout before any unthrottled delete:

1. Export a small slice (`--max-rows N`) to the archive dir.
2. Load `<table>.csv.gz` into a scratch Postgres/sqlite (Restore steps 1–3).
3. **Row count** in scratch == manifest `rowcount` == `COUNT(*) WHERE <col> < cutoff` for that slice.
4. **sha256** of the `.csv.gz` == manifest `sha256`.
5. Confirm the command's computed delete set == the exported PK set (the delete-only-what-you-exported invariant).

Automated coverage (shipped): `warships/tests/test_archive_battle_history.py` seeds `>cutoff` + `<cutoff` rows for both tables and asserts dry-run writes/deletes nothing; live deletes **only** `<cutoff` rows (and leaves `BattleObservation` untouched); the archive's column-0 ids == the deleted set; manifest count + sha256 match the file; `--max-rows` and `--tables` subsetting; and the command's kill-switch (no-op without `--force`/`ENABLED=1`). It is backend-agnostic: the SQLite test DB exercises the csv-writer fallback, and a Postgres run exercises the real server-side `COPY` export. The export is **driver-agnostic** — psycopg2 (`mogrify` + `copy_expert`) *and* psycopg3 (`cursor.copy(sql, params)`), because **prod's Django runs psycopg3** while `requirements.txt` pins psycopg2 (CI/local). `VACUUM` is autocommit-only, so it is skipped (and asserted skipped) under `TestCase`'s per-test transaction. Verified green on local Postgres 15 against **both** drivers (psycopg2 2.9.11 and psycopg3 3.3.4).

## Rollback

- **Disable** by setting `BATTLE_HISTORY_ARCHIVE_ENABLED=0` (command no-ops) and/or `systemctl disable --now battlestats-archive-battle-history.timer`.
- Archives are **additive and non-destructive until verified** — a failed/aborted run leaves the archive on disk and deletes nothing, so there is nothing to roll back beyond disabling the schedule.
- If a delete already ran, the archived `.csv.gz` is the recovery source (see Restore); the data is not lost, only moved off live.

## Follow-ups

- **`PlayerDailyShipStats` growth is deferred, not solved.** Within the 32-day window it still grows as player coverage grows; this runbook only bounds the *tail*. Revisit if in-window size becomes the binding constraint.
- **Offsite copy.** Archives live only on the app droplet today. Add a DO Spaces (object storage) upload + droplet-side retention/pruning of `<run-date>` dirs so the droplet disk doesn't itself fill.
- **UI relabel.** The week/month/year pills now cap at 32 days; consider a UI affordance noting the window cap (candidate, not required).
- **Systemd-timer install.** This is the second timer in the deploy script (after `battlestats-celery-watchdog.timer`); if more host-level periodic jobs follow, factor the heredoc + enable into a reusable deploy helper.
- **DB driver divergence — RESOLVED 2026-06-17.** `requirements.txt` now pins `psycopg[binary]==3.3.3` (prod's version) alongside `psycopg2-binary`, so CI + local run on **psycopg3, matching prod** (Django 5 prefers v3 when installed). Aligning the driver surfaced a second psycopg3-fragility — the unmanaged-`MvPlayerDistributionStats` probe in `data.py` distribution/correlation fell back via `try/except` around a query that errors-in-transaction where the view is absent (test DB); psycopg3 won't continue on an aborted txn, so the probe is now savepoint-isolated (`with transaction.atomic()`). Prod was unaffected (the Mv exists there), but the full suite now passes on psycopg3 (728). Shipped in the same tranche as this job.

## Rollout log

- **2026-06-17 — first run + enable (this rollout).**
  - **Attempt 1 (15:36 UTC): failed safely.** Prod psycopg3 vs the psycopg2-only export (`mogrify().decode()` / `copy_expert`). The verify-before-delete gate + per-table isolation held: *archive kept, nothing deleted*, **zero rows removed**. Fixed driver-agnostic (PR #51), re-deployed.
  - **Attempt 2 (15:46–15:54 UTC, ~8.5 min): success.** Archived + deleted **703,891** `BattleEvent` rows (`warships_battleevent.csv.gz`, 31.3 MB, sha `48553fd8…`) and **694,083** `PlayerDailyShipStats` rows (`warships_playerdailyshipstats.csv.gz`, 26.7 MB, sha `97700528…`), cutoff `2026-05-16`. Per-table `VACUUM (ANALYZE)` ran. Post-run dry-run = **0 candidates**; both archives `gzip -t` clean and sha256 == manifest; archives `scp`'d off-box (one-time) and re-verified.
  - **One-time `VACUUM (FULL, ANALYZE)`** (`lock_timeout=5s`): `warships_battleevent` 1300→924 MB, `warships_playerdailyshipstats` 1529→993 MB; **`pg_database_size` 23 GB → 22 GB** (~1 GB reclaimed to the OS).
  - **Steady state:** `BATTLE_HISTORY_ARCHIVE_ENABLED=1`, timer next fires **2026-07-01 03:00 UTC** (then the 15th).

## Related runbooks

- `archive/runbook-db-growth-analysis-2026-06-15.md` — the growth attribution + runway analysis this implements the lead remediation for.
- `runbook-db-cpu-saturation-2026-05-24.md` — the read-only outage this prevents recurrence of.
- `runbook-battle-history-rollup-durability-2026-06-06.md` — the rollup/`BattleEvent` pipeline whose read paths bound the safety argument.
