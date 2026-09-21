# Runbook: DB Table Shape — Remediation at Terminal State

_Created: 2026-09-20_
_Lifecycle: dated-active · Owner: platform_
_Context: the product has reached its end state (90-day rolling window, 105-day retention, no further window moves). `agents/work-items/db-table-shape-audit-2026-09-20.md` (the H-series) tested the design assumption behind each of the seven largest tables against the measured shape of its data; five of six assumptions were false. This runbook is the execution plan those findings imply._
_QA: every figure traces to the H-series work item or to a live check recorded here. Figures measured 2026-09-20._
_Status 2026-09-21: **Steps 1-4 are done and verified; the volume went 78.57% → 59.47% with no feature change and no spend.** Steps 5-9 are structural, undated, and each needs its own approval. **Resume from the "Pickup pointer" section below** — it holds the last measured state, the dated checks still owed, and what each remaining step needs._

## QA Notes

_Reviewed 2026-09-20 against `/home/august/code/battlestats/.claude/worktrees/db-shape-runbook-0920` (linked worktree). 40 assertions checked, 10 corrected._

### Resolved
- **Step 7.2: "`player_last_fetch_idx` … Its only consumer is `incremental_player_refresh`'s three tier queries … Candidate to drop"** -> actual: the index backs the daily enrichment reclassify's `last_fetch >= now - H hours` filter, EXPLAIN-verified as a BitmapAnd, taking that pass from ~36 min to 2.5-6 min per realm (`server/warships/tasks.py:3594-3596`). It was **already dropped once as "unused"** (`migrations/0034_drop_unused_indexes_and_optimize.py:23-26`) and deliberately re-created (`migrations/0067_player_last_fetch_index.py:11-14`). The audit grepped for `last_fetch__lt` and missed the `__gte` consumer. 516 lifetime scans is ~3 realms a day since 0067: **a low scan count is not low value**. -> Step 7.2 rewritten as "keep"; the same error corrected in the H-series work item (H4) in this commit. It is also not in `Player.Meta` — it lives only in the raw-SQL migration.
- **Step 6: "five window aggregations … One already moved … move the remaining four to PDSS"** -> actual: three are primary readers and two are *fallbacks of the reader that already moved*. Primary: the standings snapshot, per (ship, player) (`server/warships/data.py:6162`); the treemap, per ship (`:6616`); the percentile view, per (ship, player) (`:7092`). Fallbacks, taken only when `use_rollup` is false: `total_battles` (`:7069`) and the all-view rows (`:7165`). The targets also differ: per-ship sums belong on `ShipPopDailyAgg`, only the two per-(ship, player) readers need PDSS. -> Step 6 rewritten with the real list and targets.
- **Step 6 ordering defect: cutting retention before the fallbacks move is silently wrong, not just slow.** With `BattleEvent` at 35 days, a failed coverage gate sends `:7069` and `:7165` to a table holding 35 days while the payload is labelled with the 90-day window — exactly the failure `ship_pop_rollup_covers_window`'s docstring exists to prevent ("serving a 40-day sum labelled as a 60-day window", `data.py:7374-7377`). -> Step 6 now states: repoint both fallbacks to PDSS **before** the retention cut. Interpretation picked (repoint rather than return `pending`) because the docstring's contract is "the fallback is merely slower, never wrong".
- **Step 6: "set `BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS` for `BattleEvent` alone to 35"** -> actual: there is no per-table env. The env is only the default of `--retention-days` (`management/commands/archive_battle_history.py:78-80`); per-table retention is achieved with the existing `--tables` flag (`:87`). -> Step 6 now specifies two invocations: `--tables playerdailyshipstats` at the env default, and `--tables battleevent --retention-days 35 --skip-observations` (`:131`) so the observation tier does not run twice.
- **Step 6: "the reconcile audits 30"** -> confirmed (`reconcile_daily_rollup_coverage(audit_days=30)`), and newly recorded: the nightly sweeper rebuilds only the trailing `BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS`, default 3 (`server/warships/tasks.py:3126-3127`), so 35 days covers both. What 35 days **forfeits** is the manual `rebuild_player_daily_ship_stats --since` repair for older days (used for the 37-day Phase-7 backfill in August). -> stated in Step 6. That 3-day delete-and-reinsert is also the source of PDSS's 5.9 M deletes noted in the H-series.
- **Every `data.py` line reference (`6159, 6613, 7066, 7089, 7162`)** -> actual: `6162, 6616, 7069, 7092, 7165` (`server/warships/data.py`), shifted +3 by this session's own comment correction in `810ea02`. -> corrected in Steps 3 and 6.
- **Step 9: "`ship_name` … duplicates `Ship.name`", implying a cheap drop** -> actual: four code sites touch it. The treemap **groups by** `BattleEvent.ship_name` (`data.py:6619`); the rollup rebuild aggregates it with `Max("ship_name")` (`incremental_battles.py:1498`); the event-to-PDSS write copies it (`incremental_battles.py:638, 676`); the timeline reads it with a `Ship` fallback (`views.py:1020`). -> Step 9 now lists all four; the column is still redundant, but the drop is a code change, not just a migration.
- **Step 4: "Clean up with `pg_repack`'s own `--drop`"** -> actual: `pg_repack` has no such flag. Leftover objects from an interrupted run are removed with `DROP EXTENSION pg_repack CASCADE` followed by `CREATE EXTENSION pg_repack`. -> corrected. Also added: `pg_repack` by default **terminates blocking backends** after `--wait-timeout` (60 s); pass `--no-kill-backend` so it gives up instead of killing the floor's connections, and `--no-superuser-check` for `doadmin` on a managed cluster.
- **Step 4 precondition: "a client binary whose version matches (1.5.2)"** -> actual: the droplet has no `pg_repack` client, and the only packaged candidate is **1.5.3** (`apt-cache madison postgresql-18-repack`), while the cluster offers extension **1.5.2**. `pg_repack` refuses to run on a client/extension version mismatch, so `apt install` yields a client that will not work. -> Step 4 now says so; obtaining 1.5.2 is Open Question 1. (`psql` 18.4 is present; `postgresql-server-dev-18` is installable, candidate 18.6.)
- **Step 4: "free space … ~20.2 GB after them", with an abort at 92%** -> the copy is ~11 GB (live TOAST ~9-9.5 GB + heap 0.8 + indexes 0.72), and it is fully WAL-logged. Expected peak ≈ (65.89 − 2.23) + 11 ≈ 74.7 GB ≈ **89%** before any WAL lag. -> Step 4 now states that the standing 90% disk alert (`fd8bc34a-…`) will very likely fire during the run and that this is expected; the 92% abort line is kept and labelled as this runbook's own choice.
- **Step 1: "daily runs need no code change"** -> true for the happy path: the run directory is the UTC date (`incremental_battles.py:2547-2548`) and a same-day rerun after a *successful* run exits early with no candidates (`:2466-2469`). Three things the claim hid, now in Step 1: (1) the export opens its file with `"wb"` (`:2308`), so a same-day rerun after a **partial** delete overwrites the day's archive with only the surviving rows — pre-existing, but daily cadence plus `Persistent=true` makes reruns ~15× likelier; (2) each run ends with a `VACUUM` of both tables unless `--skip-vacuum` (`archive_battle_history.py:119`), so that cost also becomes daily — interpretation picked: keep the vacuum, because freed pages are not reusable until it runs, and watch the unit's duration; (3) six other documents state the old cadence and need reconciling, `ops-env-reference.md` and `runbook-battle-history-archive-prune-2026-06-17.md` first.
- **Step 1 names no test** -> `server/warships/tests/test_local_prereqs.py` already asserts on `ExecStart` lines of two units (compaction, `battles_json` prune) after both bit production. -> Step 1 now asks for a third assertion pinning the archive timer's `OnCalendar`.
- **Step 3: "one migration, following `0087`"** -> two gaps. Numbering: `0088` is taken by the parked truncate, so this migration must be created **after** Step 2 merges and depend on it, or the app gets two leaf nodes. Locking: `0087` only dropped; this one also *creates* the partial index, and a plain `CREATE INDEX` holds a write-blocking `SHARE` lock on a 20.9 M-row table. -> Step 3 now follows the `CONCURRENTLY` + `atomic = False` + vendor-guard pattern of `migrations/0067_player_last_fetch_index.py:16-20` for the create.
- **Step 3: `warships_battleevent_mode_983942c4` "drop"** -> declared as claimed (`models.py:671`), but its 106 scans have a plausible owner the PDSS twin never had: `BattleEvent` readers are window-wide (90 of 105 days), so for `mode='ranked'` (~5% of rows) the mode index is the *selective* predicate — e.g. the ranked treemap (`data.py:6616`). -> Step 3 now requires the `EXPLAIN` check to include a `mode='ranked'` run, and notes the drop becomes unconditional once Step 6 moves that reader.
- **Step 5: "NULL the column as players refresh"** -> not buildable as written: once the two call sites go, nothing touches the column. -> interpretation picked: a one-off batched command modelled on `prune_inactive_player_battles_json`, with the bounds measured today (`--batch-size 250 --max-rows … --statement-timeout 1800`, `deploy_to_droplet.sh` prune unit). The value is stored inline (avg 225 B), so this rewrites tuples and yields reusable space only.
- **Step 9: `Snapshot.survived_battles` "no reader or writer"** -> confirmed by reading: the field exists (`models.py:228`, `default=0`) and every other `survived_battles` hit in `server/warships` belongs to the observation dataclasses or PDSS, none to `Snapshot`.

### Unverified
- `toast_tuple_target = 256` (Step 7.1): valid Postgres storage parameter, but its effect on this table's write volume and read latency is unmeasured. The step already says to measure on a copy first.
- ~~Whether `doadmin` may `CREATE EXTENSION pg_repack`.~~ **Verified 2026-09-20** by read-only query: `doadmin` holds `CREATE` on the database and `pg_repack` is in `extwlist.extensions`. Still not *attempted*; that is part of the supervised run.
- The ~13 GB repack return rests on a 1% sample (14.7% of rows carrying 15 kB). `pgstattuple` is installed and would measure it exactly, but scanning a 24 GB relation on this I/O-bound cluster was judged not worth the load.
- That the five index drops leave plans unchanged: the runbook requires the `EXPLAIN` checks; none has been run yet.

### Open Questions
1. ~~**Where does a `pg_repack` 1.5.2 client come from?**~~ **Answered 2026-09-20: built from source, tag `ver_1.5.2`, by operator decision.** Installed and verified; see Step 4's preconditions. Step 4 is no longer blocked on tooling, only on Steps 2 and 3.
2. ~~**How does `0088` run?**~~ **Answered 2026-09-20: deployed as v5.11.3**, after a from-scratch pre-flight (Step 2). Step 3's migration is now `0089` and depends on `0088`.

## Pickup pointer (session close 2026-09-21 ~05:00 UTC)

**Read this section first when resuming.** Everything above it in the file is
QA history; everything below is the plan with its per-step outcome boxes.

### Where we are

Steps 1-4 are done and verified in production. The volume went **78.57% →
59.47%** on 2026-09-20 with no feature change and no spend; the resize question
in `runbook-db-capacity-remediation-2026-09-19.md` Step 3 is retired. Live
version is **5.11.5**. Nothing is mid-flight: no migration parked, no extension
installed, no credentials left on the droplet.

| Release | What |
|---|---|
| 5.11.1 | compaction `keep=1` actually reaches the command |
| 5.11.2 | two dead PDSS indexes (`0087`); achievements table write stopped; `battles_json` prune armed |
| 5.11.3 | `0088` truncate, 1.43 GB to the OS; `battles_json` prune timer given its measured bounds |
| 5.11.4 | archive/prune timer daily at 07:00 UTC |
| 5.11.5 | `0089`: three indexes dropped, two kept on `EXPLAIN` evidence |
| (no release) | `pg_repack`, 24 → 11 GB |

### Last measured state — Mon 2026-09-21 04:40 UTC, 8.5 h after the repack

| | Value |
|---|---|
| `disk_used_percent` | **60.57%**, 33.1 GB free |
| `pg_database_size` | 44.02 GB |
| `warships_battleobservation` | 12.84 GB — growing ~0.14 GB/h, the normal intake between compactions |
| its indexes | all four valid and ready; 0 dead tuples; no `repack` schema or triggers |
| true DB-level errors since the swap | **0**, across all four Celery services and gunicorn |

### Checks that are owed, in date order

1. **Today after 12:34 UTC** — the daily compaction should pull
   `warships_battleobservation` back toward ~11 GB. This is the first full
   cycle on the repacked table; it establishes the real steady-state band
   (expected ~11-13 GB). If it settles materially higher, the live set is larger
   than the 10.7 GB measured before the repack, which changes nothing
   structurally but should be written down.
2. **Today 07:00 UTC** — first *scheduled* fire of the daily archive (the
   2026-09-20 17:07 run was a `Persistent=true` catch-up). Expect both big tables
   `skipped`, the observation tier deleting a small number of rows, and a run of
   a few minutes.
3. **2026-09-27** — first daily prune with real candidates. `BattleEvent` and
   `PlayerDailyShipStats` must report **deleted** rows, not `skipped`, and the
   unit must finish comfortably inside the hour. If a run fails mid-delete,
   **move that day's archive directory aside before rerunning** (the export opens
   its file `"wb"`; see Step 1).
4. **~2026-10-04** — re-measure `pg_database_size` and read the growth slope
   against the 264 MB/day post-fill projection in the capacity work-item. With
   `keep=1` live and the two time-series tables capped, it should be far lower.
5. **Next Sunday 05:00 UTC** — first *timer-driven* `battles_json` prune with its
   new bounds. The 2026-09-20 run was manual; the unit itself is unexercised.

### What is left, and what each needs

None of Steps 5-9 has a deadline. **Each needs its own operator approval**, one
lever at a time.

| Step | Needs | Note for whoever picks it up |
|---|---|---|
| 5 — stop the achievements fetch | a product "yes" | Both the table and `achievements_json` have no reader. Saves a WG API call per player refresh, which is the scarce resource. Clearing the column is a separate one-off batched job |
| 6 — readers off `BattleEvent`, then 105 → 35 d | approval + per-reader equivalence proofs | **Largest remaining win (~5 GB).** Order is load-bearing: repoint the two *fallbacks* (`data.py:7069, 7165`) before cutting retention, or a failed coverage gate serves 35 days under a 90-day label. Unlocks the held `battleevent_mode` index drop (174 MB) |
| 7 — `toast_tuple_target = 256` on `warships_player` | measure on a copy first | One line, reversible. Do **not** touch `player_last_fetch_idx` |
| 8 — slim `battles_json` | moderate code | ~1.9 GB, arrives gradually |
| 9 — dead / write-only columns | migrations + four code sites for `ship_name` | Savings arrive over one 105-day window, no rewrite |

### Three lessons this session paid for — apply them before acting on any number here

1. **A lifetime `idx_scan` count is frequency, not value.** Three indexes were
   nearly dropped on low counts and turned out to carry expensive, rare queries
   (`player_last_fetch_idx`, `playerdailyshipstats_ship_id`, `battleevent_mode`).
   Before dropping any index: `EXPLAIN` every reader on production, **and** check
   the migration history for a prior drop-and-re-create.
2. **A value that agrees everywhere can still be read by nothing.** The
   compaction `keep` pin, the `battles_json` prune's arguments and the disk
   alerts were each "known" from documents and each wrong in a different
   direction. Query the running system.
3. **Dry-run the real command.** `pg_repack --dry-run` caught a version-handshake
   failure that came from the binary's *filename*.

### Noticed in passing, not part of this plan

- **`Failed ranked incremental refresh … Player matching query does not exist`**:
  exactly **25 per run**, every run, at least since 2026-09-17 (125 / 250 / 175
  / 175 per day). Predates everything in this runbook and is unrelated to it. A
  fixed 25 looks like the same players missing every time — plausibly a realm
  mismatch in that task's lookup. Small, but it is a standing defect that
  nothing alerts on.
- **Wargaming `504 SOURCE_NOT_AVAILABLE` bursts**, NA-side, 2026-09-20 17:00-21:00
  and 2026-09-21 01:00-03:00 UTC (1,337 / 1,140 / 573 / 84 per hour, recovering).
  Upstream, began before the repack, handled by the floor as designed. Expect the
  11:30 UTC ops digest to mention NA if the crawl was affected.

### How to re-measure quickly

```bash
# volume, from the droplet (the local sandbox blocks port 9273)
TOKEN=$(grep -oP '^access-token:\s*\K\S+' ~/.config/doctl/config.yaml)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.digitalocean.com/v2/databases/metrics/credentials   # no database id in this route
# then, on the droplet:  curl -u USER:PASS https://<db-host>:9273/metrics | grep disk_used_percent

# the three timers this plan changed
ssh root@battlestats.online 'systemctl list-timers --no-pager | grep -E "archive|compact|prune-battles"'
ssh root@battlestats.online 'journalctl -u battlestats-archive-battle-history --since "2 days ago" -o cat | grep -E "skipped|deleted"'
```

## Implementation status

| Step | Finding | Code | Deployed | Done in prod | What remains |
|---|---|---|---|---|---|
| 1 — prune daily, not twice monthly | H5 | ✅ | ✅ v5.11.4 | ✅ **2026-09-20** | Done, 12 days inside the deadline. Watch the first runs with candidates, from 2026-09-27 |
| 2 — truncate `PlayerAchievementStat` | H6 | ✅ `0088` | ✅ v5.11.3 | ✅ **2026-09-20 16:57 UTC** | Done. 1.43 GB returned to the OS |
| 3 — drop 4 indexes, make 1 partial | H7 | ✅ `0089` | ✅ v5.11.5 | ✅ **2026-09-20** | **Three dropped, not five.** EXPLAIN kept two: one permanently, one until Step 6 |
| 4 — `pg_repack` `battleobservation` | H1 | n/a | n/a | ✅ **2026-09-20 19:51-20:11 UTC** | Done. 24 GB → 11 GB; **volume 75.93% → 59.47%** |
| 5 — stop fetching achievements | H6 | ☐ | ☐ | ☐ | Product decision |
| 6 — aggregations to PDSS; `BattleEvent` 105 → 35 d | H2 | ☐ | ☐ | ☐ | Per-reader payload equivalence |
| 7 — `Player` row shape | H4 | ☐ | ☐ | ☐ | `toast_tuple_target` only; measure on a copy first. The index stays (see QA) |
| 8 — slim `battles_json` | H3 | ☐ | ☐ | ☐ | Moderate code |
| 9 — drop dead / write-only columns | H7 | ☐ | ☐ | ☐ | Migrations |

## Purpose

Turn the H-series audit into a sequenced plan. Read it before touching the schema. Work the steps in order, **one production lever at a time, with an operator acknowledgement between each.**

## The objective this plan optimises

Battlestats is art, not a commercial product; the job is to run it efficiently and as inexpensively as possible. Storage autoscale is off permanently by operator decision, so the 83.87 GB volume is a hard wall and a full volume is a read-only outage. **Spending is the last resort.** Steps 1-4 take the volume from 78.57% to about 60% with no feature change and no spend, which retires the resize question in `runbook-db-capacity-remediation-2026-09-19.md` Step 3.

Two kinds of reclaim appear below and they are not interchangeable:

- **Returned to the OS** — dropped indexes, `TRUNCATE`, `pg_repack`. The volume actually shrinks.
- **Reusable space** — row deletes and column NULLing. The file stays the same size; future writes land in the freed pages. Bought as slope, not as gigabytes back.

## Sequencing rationale

Step 1 first because it is the only step with a deadline. Steps 2 and 3 next because they are cheap, return space to the OS, and **buy the working room Step 4 needs**. Step 4 is the largest single reclaim in the system and the only supervised operation. Steps 5-9 are structural improvements with no deadline; each stands alone.

## Step 1 — Prune daily ★ time-sensitive

### Why now

The archive timer fires on the 1st and 15th (`deploy_to_droplet.sh:1174`). Retention is 105 days, so `BattleEvent` and `PlayerDailyShipStats` oscillate between 105 and ~120 days. A delete-based prune returns nothing to the OS, so **the files stay at their high-water mark permanently**. The two tables hold 99 days in 16.15 GB, 0.163 GB per day: ~19.6 GB at a 120-day peak against ~17.3 GB pruned daily.

The window fills on 2026-09-26 and the first prune with candidates is 2026-10-01. **The peak has never happened.** Change the cadence before ~2026-10-02 and ~2.3 GB is never allocated. After the first peak the same change only stops the oscillation.

### The change

`server/deploy/deploy_to_droplet.sh`, the `battlestats-archive-battle-history.timer` heredoc: `OnCalendar=*-*-01,15 03:00:00 UTC` → **`OnCalendar=*-*-* 07:00:00 UTC`**. Update the unit `Description` lines, which say "monthly" and "1st + 15th".

**The hour moved too, 03:00 → 07:00, and that was not in the draft.** A job that ran twice a month could share an hour with anything; a daily one cannot. Mapping the live Beat schedule by UTC hour before shipping showed 03:00 inside the nightly standings-warmer window (02:30-03:25), one hour ahead of `battle-history-daily-rollup` at 04:00 — which rewrites the *newest* days of the same two tables this job deletes the oldest days of — and two ahead of the reconcile at 05:00. 07:00 follows all three, carries four light tasks, precedes the 08:00-09:00 enrichment cluster (ten tasks each), and sits in the global traffic trough.

The archive step writes a directory named for the UTC run date (`incremental_battles.py:2547-2548`), so daily runs need no code change on the happy path. Three things to carry with it:

- **Add a test.** `test_local_prereqs.py` already pins the `ExecStart` of two units that bit production; add a third assertion pinning this timer's `OnCalendar`.
- **Reconcile the docs** that state the old cadence, `ops-env-reference.md` and `runbook-battle-history-archive-prune-2026-06-17.md` first.
- **Know the rerun hazard.** The export opens its file with `"wb"` (`:2308`). A same-day rerun after a *successful* run is harmless (no candidates, early exit at `:2466`), but a rerun after a **partial** delete overwrites that day's archive with only the surviving rows. Pre-existing; daily cadence makes reruns likelier. If a run fails mid-delete, move the day's directory aside before rerunning.

### Risk

The same command also runs the `BattleObservation` row-retention tier, which will now run daily too. Each run is a smaller delete, which is the desired direction.

Each run also ends with a `VACUUM` of both tables unless `--skip-vacuum` is passed, so that cost becomes daily as well. **Keep it**: pages freed by the delete are not reusable until a vacuum has run, and reuse is the whole point. Watch the unit's duration for the first week.

### Validation

`systemctl list-timers battlestats-archive-battle-history.timer` shows a next fire within 24 hours. After 2026-09-27 each run reports deleted rows for both tables rather than `skipped (no rows older than cutoff)`.

### Rollback

Restore the `OnCalendar` line and redeploy.

## Step 2 — Truncate `PlayerAchievementStat`

Migration `0088_truncate_playerachievementstat` is written, tested and parked on branch `worktree-db-efficiency-0920` as `f1b3db4`, deliberately **not on `main`**. It returns **1,434 MB to the OS** (612 MB heap + 822 MB index, 5,361,115 rows).

Safe because the rows are a pure derivative: of 3,000 sampled players holding rows, 3,000 still carry the source payload in `Player.achievements_json`. No foreign key points at the table, so the truncate neither fails nor cascades. Both remaining readers no-op against an empty table: `_merge_achievement_rows` (`player_records.py`) and the pre-purge count in `purge_deleted_accounts.py`.

The direct `TRUNCATE` was blocked by the auto-mode classifier as a mass delete, so it shipped as a migration through the deploy path instead. Lock wait is bounded to 5 s, as in `0087`.

### Done 2026-09-20 — and the pre-flight that preceded it

The operator asked for the action to be double-checked before it ran. Re-verified from scratch rather than from this runbook's own text:

| Check | Result |
|---|---|
| Has the write really stopped since v5.11.2? | `n_tup_ins` flat at 1,356,234 across two reads; newest row `refreshed_at` 14:06 UTC, nothing after the 15:43 deploy |
| Anything still touching the table? | 0 sessions; 0 updates lifetime |
| Inbound foreign keys? | none — the truncate can neither fail nor cascade |
| Every reference in the codebase | the model, the merge loop (`player_records.py:72,75`), the purge count (`purge_deleted_accounts.py:206`). No raw SQL, no contract, no client reference |
| The GDPR purge transcript (G4's caveat) | records `rows_deleted.achievements: N`; against an empty table it records 0, which is accurate. Record **shape** unchanged. The payload itself lives on the `Player` row and is still deleted with the player |
| Independent corroboration | G4 reached the same verdict in August: "reversible (the mirror rebuilds from the blob)" |
| Recovery path if this proved wrong | 8 daily managed backups on the cluster, newest 57.7 GB |

**Result.** `0088` applied with no lock contention. Rows 5,361,115 → **0**; table 1,434 MB → **24 kB**; `pg_database_size` 59.20 → **57.29 GB**; `disk_used_percent` 78.57% → **76.44%** (free 17.97 → 19.76 GB), measured from the droplet. The same deploy put the fixed `battles_json` prune unit in place ahead of Sunday's run.

## Step 3 — Drop four indexes, make one partial

> **Outcome 2026-09-20 — read this before the plan below.** The plan said five.
> Twenty `EXPLAIN`s on production said **three**. ~437 MB returned to the OS, not
> ~800 MB. The table and prose below are the plan as drafted and are kept as the
> record; this box is what happened.
>
> | Index | Verdict | Why |
> |---|---|---|
> | `battleevent_player_id_1f7bf48a` (202 MB) | **dropped** | every scan is a bare `player_id = X`; `battle_event_player_time_idx` leads with `player` and serves it identically |
> | `playerdailyshipstats_season_id_69e0cf16` (197 MB) | **dropped outright**, not made partial | one reader, the ranked-season timeline, already player-scoped and narrowed to ~230 rows by `dly_ship_player_date_idx`; the season index was only ever an optional BitmapAnd arm. A partial index would have needed a concurrent, non-atomic build on 20.9 M rows for no gain |
> | `explorer_eff_rank_idx` (38 MB) | **dropped** | zero lifetime scans; nothing orders or ranges on the percentile in SQL |
> | `playerdailyshipstats_ship_id_16c96227` (198 MB) | **KEEP, permanently** | carries the ship combat-profile population query (`_ship_population_brackets_30d` — the 36 s aggregation that blew the gunicorn timeout in August), the legacy per-ship avg-damage scan, and the trailing-days arm of the rollup path. All per-ship across every player; no other index serves them. The audit's "the rollup scans by `date`" was true of the rollup and had looked at nothing else |
> | `battleevent_mode_983942c4` (174 MB) | **HOLD until Step 6** | the ranked treemap (`compute_realm_top_ships`, `mode='ranked'`) plans a Parallel Index Scan on it, exactly as QA predicted. Without it that warm scans the whole 4.5 GB heap |
>
> Both keeps are now documented **on the model fields themselves**, so the next
> audit that sees "225 scans" reads the reason before it reads the number. This
> is the third time in one day that a low scan count turned out to mean *rare but
> expensive*, not *unused* (`player_last_fetch_idx` was the first).

Lifetime scan counts; `pg_stat` counters have never been reset.

| Index | Size | Scans | Action |
|---|---|---|---|
| `warships_battleevent_mode_983942c4` | 174 MB | 106 | drop — `db_index=True` on a two-value column |
| `warships_battleevent_player_id_1f7bf48a` | 202 MB | 2,518 | drop — FK auto-index; `battle_event_player_time_idx` leads with `player` |
| `warships_playerdailyshipstats_ship_id_16c96227` | 198 MB | 225 | drop — the rollup scans by `date` |
| `warships_playerdailyshipstats_season_id_69e0cf16` | 197 MB | 2,043 | make partial: `WHERE season_id IS NOT NULL` (94.1% NULL) |
| `explorer_eff_rank_idx` | 38 MB | 0 | drop |

**~800 MB returned to the OS.** All five are Django-managed (`models.py:671`, the `BattleEvent.player` FK at `:659`, the two `db_index=True` fields on PDSS, and the `Meta` index at `:304`), so this is a model edit plus a migration.

**Do Step 2 first.** `0088` is taken by the parked truncate; this migration must be created after that merges and depend on it, or the app ends up with two leaf nodes.

The four drops follow `0087_drop_unused_pdss_indexes`: bound `lock_timeout` to 5 s, guard on the vendor. The partial index is different, because it is a *create*: a plain `CREATE INDEX` holds a write-blocking lock on a 20.9 M-row table. Build it `CONCURRENTLY` in a non-atomic migration, following `0067_player_last_fetch_index.py`.

**Before dropping**, `EXPLAIN` the readers on production, as was done for `0087`: the `BattleEvent` readers at `data.py:6162, 6616, 7069, 7092, 7165` and the rollup rebuild at `incremental_battles.py:1508`. **Include a `mode='ranked'` run.** These readers span 90 of the table's 105 days, so the date predicate is barely selective and for ranked (~5% of rows) the `mode` index may be the plan — which the PDSS twin dropped in `0087` never was. If so, hold that one drop until Step 6 has moved the ranked treemap off this table.

## Step 4 — `pg_repack` the observation table ★ largest reclaim

> **Outcome 2026-09-20 — read this first.** Ran 19:51:46 → ~20:11 UTC, **19.5
> minutes**, no abort, zero blocked sessions at every check, site answering 200
> throughout.
>
> | | Before | After |
> |---|---|---|
> | `warships_battleobservation` | 24 GB (761 MB heap / 686 MB idx / 23 GB TOAST) | **11 GB** (500 MB / 313 MB / 10 GB) |
> | `pg_database_size` | 56.88 GB | **43.06 GB** |
> | `disk_used_percent` | 75.93% | **59.47%** (free 20.2 → 34.0 GB) |
> | Peak during the run | — | **89.82%** |
>
> Integrity after the swap: 3,449,205 rows, 625,639 carrying a payload, a sample
> payload readable (301 ships), 424 new observations written *after* the swap, all
> four indexes valid, no leftover `repack` tables or triggers. Extension dropped
> and the metrics credentials removed from the droplet afterwards.
>
> **Three things the plan below got wrong, all caught before the table was touched:**
>
> 1. **The binary's NAME is part of the version handshake.** It was installed as
>    `pg_repack-1.5.2` so that apt could never shadow it. `pg_repack` builds its
>    handshake string from its own program name, so it announced itself as
>    `pg_repack-1.5.2 1.5.2` and the server refused it: *"does not match database
>    library 'pg_repack 1.5.2'"*. It now lives at
>    **`/usr/local/lib/pg_repack-1.5.2/pg_repack`** — a versioned *directory*,
>    which keeps the no-shadow property and the right name. **The `--dry-run`
>    caught this**; it exercises the full handshake. Never skip it.
> 2. **The live set was ~10.7 GB, not ~9 GB**, so the copy was ~12 GB and the
>    peak ~90%, not ~89%. The daily compaction was run first to shrink the copy
>    and freed only 0.4 GB (26,673 payloads): the payload *percentage* had risen
>    from 14.7% to 18.9% because that day's archive run deleted 673 K empty rows
>    and shrank the denominator, not because superseded generations had piled up.
>    Size a repack from **payload row counts**, not from a percentage.
> 3. **The abort line was raised 92% → 94%.** At a ~90% projected peak, 92% left
>    1.5 GB of WAL headroom and would likely have killed a good run near its end.
>    At 94% there are still 5 GB free, the watchdog polls every 30 s, and an abort
>    frees the whole half-built copy at once. The peak was 89.82%, so neither line
>    was reached.
>
> The measured rate was a steady ~1 point of volume per minute (~14 MB/s), which
> is the signature of the copy writing alone with WAL not backing up. The runner
> and watchdog that did this are in `server/scripts/pg_repack_client/`. The
> watchdog runs **on the droplet**, under `setsid nohup`, so it outlives the
> operator's session.

### What is wrong

`warships_battleobservation` is 25.79 GB, of which 24.27 GB is TOAST. After the 2026-09-20 `keep=1` fix only **14.7%** of rows carry `ships_stats_json` (1% sample, 41,875 rows), at 15 kB stored each: **~9.0 GB live**, so **~15 GB is free space**. Steady state is one payload per observed player plus about a day of intake before the 12:30 UTC compaction, ~10.5 GB. **About 13 GB is stranded permanently**: reusable space only helps if something grows into it, and nothing will.

### The tool

`pg_repack` **1.5.2** is available on the cluster (`pg_available_extensions`; not installed). It rewrites online, taking `ACCESS EXCLUSIVE` only briefly at start and end. That is the whole difference from the 2026-07-21 incident, where `VACUUM FULL` held that lock on `warships_player` for 24 minutes. **Never `VACUUM FULL` this table.**

### Preconditions

- Steps 2 and 3 done. The repack needs room for a second copy of the live data (~10 GB) plus WAL; free space is 17.97 GB today and ~20.2 GB after them.
- ✅ **A 1.5.2 client — done 2026-09-20.** `pg_repack` refuses to run unless client and extension versions match exactly; the cluster offers 1.5.2 and apt offers only 1.5.3. Operator decision: build tag `ver_1.5.2` from source. Installed at **`/usr/local/lib/pg_repack-1.5.2/pg_repack`** on the droplet — a versioned *directory*, so an `apt install` of 1.5.3 can never shadow it. (It was first installed as `/usr/local/bin/pg_repack-1.5.2`; that name broke the version handshake — see the outcome box above.)
  - Built in an `ubuntu:24.04` container from `server/scripts/pg_repack_client/Dockerfile`, against `postgresql-server-dev-18`. The droplet is 24.04 and the dev box 26.04, so a native build would have linked a newer glibc than production has; the container also keeps a compiler toolchain off the production host. Only the client binary leaves the image — the server-side library is the cluster's own.
  - `sha256 c1a187dbf0b9ab8b168d3625089b4cb931e53b12e58223bbfa43b27492ef4d58`, 273,736 bytes. Verified on the droplet: `--version` prints `1.5.2`, `ldd` reports nothing unresolved (the runtime libraries arrive with the already-installed `postgresql-client-18` / `libpq5`).
  - Rebuild: `DOCKER_BUILDKIT=0 docker build -t pgrepack-152-build server/scripts/pg_repack_client` (the dev box's Docker has no `buildx`), then `docker cp` `/out/pg_repack` out of a created container.
- ✅ **The role can create the extension.** The app connects as `doadmin`: not a superuser (`rolsuper = false`), which is why `--no-superuser-check` is required rather than optional, but it holds `CREATE` on the database and `pg_repack` is on DigitalOcean's extension allow-list (`extwlist.extensions`). Not yet created — that belongs to the supervised run.
- A quiet hour, outside the 12:30 UTC compaction and the realm stripes.

### Procedure

Supervised, never scheduled. All three flags below were confirmed against the installed binary's `--help`, not recalled:

```
CREATE EXTENSION pg_repack;                         -- once, as doadmin
/root/pg_repack_run.sh --dry-run     # first: full connection + version handshake
/root/pg_repack_run.sh               # -k -D --no-order -t public.warships_battleobservation
```

- `-D, --no-kill-backend` — by default `pg_repack` *terminates* the sessions blocking it once `--wait-timeout` (60 s) expires. The floor's connections are not expendable; with `-D` it gives up instead.
- `-k, --no-superuser-check` — `doadmin` is not a superuser.
- `-N, --dry-run` first, which exercises the connection, the version handshake and the privilege check without touching the table.

Watch `disk_used_percent` from the droplet throughout (the metrics scrape must run there; port 9273 is blocked locally). The copy is ~11 GB and fully WAL-logged, so expect a peak near **89%** — the standing 90% disk alert will very likely fire, and that is expected. Abort at 92%; that line is this runbook's own choice, not a measured limit.

### Validation

`pg_total_relation_size('warships_battleobservation')` falls from ~25.8 GB to ~11-12 GB, and `disk_used_percent` falls by about 15 points.

### Rollback

`pg_repack` swaps at the end; an aborted run leaves the original table intact. If a run is interrupted, remove its leftover triggers and temporary tables with `DROP EXTENSION pg_repack CASCADE` and then `CREATE EXTENSION pg_repack` again.

## Step 5 — Stop fetching achievements

With the table gone, `Player.achievements_json` is the only copy **and it has no reader**: it sits in `PlayerSerializer`'s `exclude` list (`serializers.py:180`), the client has zero occurrences of "achievement", and all three callers of `update_achievements_data` discard its return value.

What remains is a Wargaming API call per player refresh, ~660 MB of inline JSON inflating the `Player` row, and `achievements_updated_at`.

**The change (a product decision):** remove the two call sites — `clan_crawl.py:403` and `incremental_player_refresh.py:199`. Keep `backfill_achievements_data` as the way back.

Once those calls are gone nothing touches the column, so clearing it is a separate, one-off batched command modelled on `prune_inactive_player_battles_json`, with the bounds that command needed in production (`--batch-size 250`, a `--max-rows` cap, `--statement-timeout 1800`). The value is stored inline, so this rewrites tuples and yields reusable space, not a smaller volume.

## Step 6 — Aggregations to PDSS, then `BattleEvent` 105 → 35 days

`BattleEvent` was designed as a raw layer with `PlayerDailyShipStats` as its rollup. Measured over the identical window: **22,646,388 events against 20,951,980 rollup rows — 1.081 events per rollup row.** Two 8 GB tables hold nearly the same rows with the same 22 counters.

`BattleEvent` has three primary readers, two fallbacks, and the rollup's own rebuild and reconcile (`incremental_battles.py:1508, 2036`). None needs intra-day time.

| Site | What it is | Grain | Right target |
|---|---|---|---|
| `data.py:6162` | standings snapshot | (ship, player) | PDSS |
| `data.py:6616` | treemap top ships | ship | `ShipPopDailyAgg` |
| `data.py:7092` | percentile view | (ship, player) | PDSS |
| `data.py:7069` | `total_battles` — **fallback** when `use_rollup` is false | ship | PDSS |
| `data.py:7165` | all-view rows — **fallback**, same gate | ship | PDSS |

The ship list already reads `ShipPopDailyAgg` (v5.3.9); the last two rows are what it falls back to when the coverage gate fails.

**The change, in this order:**

1. Move the three primary readers, proving payload equivalence per reader the way v5.3.9 did. The snapshot's `survived` is a count of surviving events, which is exactly what PDSS `survived_battles` accumulates.
2. **Repoint both fallbacks to PDSS.** This is not optional and must precede step 3: at a 35-day `BattleEvent`, a failed coverage gate would otherwise serve a 35-day sum under a 90-day label — the precise failure `ship_pop_rollup_covers_window` exists to prevent. Its contract is "the fallback is merely slower, never wrong".
3. Only then cut retention. There is no per-table env; use the command's existing flags as two invocations: `--tables playerdailyshipstats` at the env default, and `--tables battleevent --retention-days 35 --skip-observations`.

35 days covers both internal consumers: the reconcile audits 30, and the nightly sweeper rebuilds only the trailing 3 (`BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS`). What it **forfeits** is the manual `rebuild_player_daily_ship_stats --since` repair for anything older — the tool used for August's 37-day Phase-7 backfill. Steady state 7.68 → ~2.6 GB, **~5 GB**.

This supersedes G3 (dropping the 14 Phase-7 columns): 1.27 GB today, 0.42 GB on a 35-day table.

## Step 7 — The `Player` row shape

Heap is 1,576 B per row; ~1,140 B of that (~73%) is JSON stored inline. Lifetime 31.5 M updates on 1.12 M rows, 9.1% HOT, across 14 indexes. Every update copies the whole tuple.

1. `ALTER TABLE warships_player SET (toast_tuple_target = 256)` moves those values out of line for new tuple versions. One line, reversible, no lock. **Measure on a copy first**: it trades write volume for an extra TOAST fetch on payload reads.
2. ~~Drop `player_last_fetch_idx`.~~ **Keep it — the audit was wrong here.** Its 516 lifetime scans looked like dead weight, but they are the daily enrichment reclassify, which the index takes from ~36 min to 2.5-6 min per realm (`tasks.py:3594-3596`). It was dropped as "unused" once before (`0034`) and deliberately brought back (`0067`). It does cost HOT updates on every `last_fetch` bump; that is a price already weighed and paid. **A low scan count measures frequency, not value** — three scans a day that each save half an hour outrank three million that each save a millisecond.

## Step 8 — Slim `battles_json`

96.9% of players holding `battles_json` also hold a live observation payload built from the same WG response. 41% of `battles_json` is `ship_name`, `ship_chart_name`, `ship_tier` and `ship_type` copied per player per ship; `pve_battles`, `win_ratio` and `kdr` are arithmetic on the other keys; `distance` has no client reader.

**The change:** store `ship_id` plus counters and join `Ship` metadata at serve time, as `views.py:1020` already does. ~-40%, ~1.9 GB, arriving gradually as players refresh.

## Step 9 — Drop dead and write-only columns

- `Snapshot.survived_battles` — 0 in all 138,714 sampled rows; no reader or writer.
- PDSS `first_event_at`, `last_event_at`, `updated_at` — written, never read; ~500 MB.
- `ship_name` on PDSS and `BattleEvent` — ~435 MB; duplicates `Ship.name`. Redundant, but **not a migration-only drop**: the treemap groups by it (`data.py:6619`), the rollup rebuild aggregates it (`incremental_battles.py:1498`), the event-to-PDSS write copies it (`:638, 676`), and the timeline reads it with a `Ship` fallback (`views.py:1020`). All four change first.

On a rolling table a `DROP COLUMN` needs no rewrite: new rows stop carrying it and the saving arrives over one 105-day window.

## Considered and declined

- **Positional encoding of the observation payload** — 0.61× compressed on 60 real payloads, but a format migration on the diff baseline for a table Step 4 already cuts by 13 GB.
- **Moving the baseline to the droplet's 57 GB of idle disk** — sound, not justified once the repack lands.
- **Partitioning the time-series tables** — the textbook answer and the riskiest change available; Step 1 makes delete-and-reuse stable.
- **Parquet archives** — a heavy dependency to improve files nothing reads.

## Decision gates

| Gate | Who | Blocks |
|---|---|---|
| Stop the achievements fetch | operator | Step 5 |
| `BattleEvent` retention 35 d | operator | Step 6 |

## Validation

- [x] **Step 1 shipped 2026-09-20 (v5.11.4):** timer shows a daily 07:00 UTC next-fire; pinned by `test_battle_history_archive_timer_fires_daily`.
- [ ] Step 1: runs from 2026-09-27 report deleted rows for both tables, and the unit's duration stays comfortably inside the hour.
- [x] **Step 2 verified 2026-09-20:** 0 rows, 24 kB; database 59.20 → 57.29 GB; volume 78.57% → 76.44%.
- [x] **Step 3 shipped 2026-09-20 (v5.11.5):** three indexes gone; two kept on `EXPLAIN` evidence. See the outcome box in Step 3.
- [x] **Step 4 done 2026-09-20:** table 24 → 11 GB; `disk_used_percent` 75.93% → **59.47%** (−16.5 points); peak 89.82%, no abort.
- [x] **Steps 1-4 complete 2026-09-20: `disk_used_percent` 59.47% against a ~60% projection.** Morning figure was 78.57%. The resize question (capacity runbook Step 3) is retired.

## Related

- `agents/work-items/db-table-shape-audit-2026-09-20.md` — the H-series; every number here.
- `agents/runbooks/runbook-db-capacity-remediation-2026-09-19.md` — the capacity plan; its Step 3 (resize) is retired if Steps 1-4 land.
- `agents/runbooks/runbook-db-table-audit-2026-07-19.md` (F-series), `agents/work-items/data-capture-utility-audit-2026-08-05.md` (G-series).
