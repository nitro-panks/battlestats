# Runbook: DB Disk Re-check — Attribution, Compaction Keep Defect, Slipped Runway

_Created: 2026-08-15_
_Lifecycle: dated-active · Owner: platform_
_Context: the operator observed managed-PG disk moving from roughly 52% to roughly 63% over two weeks and asked whether two weeks of data can account for more than 10% of the volume. It cannot. This runbook records the re-measurement against the 2026-08-05 baseline, the attribution, one production defect found in the process, and the sequenced next steps._
_QA: every figure below was measured live on 2026-08-15 07:53–07:59 UTC by read-only psql (`statement_timeout='45s'`, `default_transaction_read_only=on`), the DO `:9273` Prometheus endpoint, the droplet journal, and the DO databases API. The 2026-08-05 comparison column is quoted from `agents/work-items/db-growth-capacity-2026-08-05.md`. No production mutation was made._

## Purpose

This is the **re-measurement** companion to `runbook-db-disk-remediation-2026-08-05.md`. That runbook is the execution plan; this one records what the plan actually produced ten days in, corrects the runway, and adds a defect that plan did not anticipate.

Read this when: disk crosses an alert threshold; you are about to arm one of the 08-05 levers; or you need to know whether the observed slope is data, bloat, or misconfiguration. The 08-05 runbook remains the authority on *what to do*. This one is the authority on *where things stand as of 2026-08-15*.

## TL;DR

1. **No, two weeks of data does not cost 10% of the volume.** Genuine new battle-history data (`BattleEvent` + `PlayerDailyShipStats`) runs **176 MB/day**, which is about **2.9% of the volume per fortnight**. The 105-day window is filling exactly as designed.
2. **`warships_battleobservation` is 65% of database growth** at **442 MB/day**, up from the 364 MB/day measured on 08-05. It is the whole story.
3. **That growth is real retained payload, not bloat.** The observation TOAST relation has **zero dead tuples** across 666 autovacuum runs. Autovacuum is keeping up; the bytes are being kept on purpose.
4. **Defect: nightly compaction runs at `keep_per_player=3`, not the intended 1.** When Step 1 moved compaction off Celery onto a systemd timer, it moved onto a code path that never reads `BATTLE_OBSERVATION_COMPACT_KEEP`. The variable is set correctly in three places and consulted in none of them. In effect since 2026-08-06.
5. **The runway slipped by three to five weeks.** 70% arrives around **2026-08-23** (was forecast 09-11); 80% around **2026-09-04** (was 10-06). Lever 3, the DO disk alerts, is still not armed, and the 70% alert would fire next week.
6. **DB health has changed regime.** `cpu_usage_iowait` is 40–54% against 19.3% on 08-05; `system_load15` is 4.5–5.0 against 3.13. Both past the trouble thresholds. A maintenance window is pending Thursday 11:11 UTC.

## Measured state, 2026-08-15 07:55 UTC

Elapsed since the 08-05 19:16 UTC baseline: **9.53 days** (9 d 12 h 39 m). All figures are decimal GB from raw byte counts. This matters: `pg_size_pretty` labels GiB as "GB", and that unit trap already produced one wrong conclusion in the 08-05 investigation. The 08-05 column is decimal too, verified by its own reconciliation (table sum 38.99 against `pg_database_size` 39.02).

| Component | 08-05 | 08-15 | Δ (GB) | MB/day | Character |
|---|---|---|---|---|---|
| `warships_battleobservation` | 15.84 | **20.06** | **+4.22** | **442** | the slope; 65% of DB growth |
| `warships_playerdailyshipstats` | 4.47 | 5.40 | +0.93 | 98 | 105d window fill, by design |
| `warships_battleevent` | 3.88 | 4.63 | +0.75 | 79 | 105d window fill, by design |
| `warships_player` | 10.99 | 11.36 | +0.37 | 38 | `battles_json` ratchet; prune never armed |
| `warships_snapshot` | 1.83 | 1.99 | +0.16 | 17 | |
| all other relations (residual) | 2.02 | 2.06 | +0.05 | 5 | includes the `mv_player_distribution_stats` matview |
| **`pg_database_size`** | **39.02** | **45.49** | **+6.47** | **679** | |
| Non-database gap (WAL, temp, spool) | 7.25 | 8.09 | +0.84 | 88 | one-time; at its ~8 GB ceiling |
| **`disk_used`** | **46.27** | **53.58** | **+7.31** | **767** | 54.98% → **63.66%** |

The five named tables account for **+6.420** of the **+6.466** `pg_database_size` delta: **99.3%**. The residual row is derived by subtraction rather than measured, so it reconciles by construction; the meaningful check is that only **46 MB over 9.53 days** falls outside the five named tables. No unattributed stream is hiding.

Volume unchanged: `disk_total` 84.174 GB, `storage_size_mib` 81920 (80 GiB), autoscale still off.

### `warships_battleobservation` internals

| | 08-05 | 08-15 | Δ |
|---|---|---|---|
| Total | 15.84 | 20.06 | +4.22 |
| Heap | 0.61 | 0.683 | +0.07 |
| Index | 0.55 | 0.586 | +0.04 |
| **TOAST** | **14.68** | **18.79** | **+4.11** |
| Rows | 3,467,534 | ~3,050,433 | **−417K** |

The row figure needs a caveat: 08-05's was an exact `COUNT(*)`; today's is `n_live_tup` (`reltuples` reads 3,030,884). A 12% drop far exceeds estimator noise, so the direction stands, but the two are not the same class of measurement.

## Findings

### F1: The question answers itself once the slope is split

Against the 9.53-day `disk_used` delta of 7.31 GB:

| Share | GB | % | What it is |
|---|---|---|---|
| `BattleObservation` | 4.22 | 58% | retained observation JSON |
| `BattleEvent` + `PDSS` | 1.68 | 23% | **the actual new data** |
| WAL gap | 0.84 | 11% | one-time; will not repeat |
| `warships_player` | 0.37 | 5% | unbounded `battles_json` |
| everything else | 0.21 | 3% | at plateau |

Projected into the operator's two-week frame: genuine new data is **~2.47 GB, or 2.9% of the volume**. The eleven points came from somewhere else.

- **Risk of misreading**: treating the slope as "the product is growing" invites a volume resize, which buys time without touching the mechanism. The mechanism is one table's retention depth.

### F2: The observation slope is retained data, not bloat

Discriminating query at 07:55 UTC (`pg_stat_all_tables` joined to `pg_class`):

| Relation | n_live_tup | n_dead_tup | last_autovacuum | autovacuum_count |
|---|---|---|---|---|
| `warships_battleobservation` (heap) | 3,050,433 | 430,106 | 2026-08-15 03:11 | 12 |
| `pg_toast_13076836` (its TOAST) | 8,589,761 | **0** | 2026-08-15 05:18 | 666 |
| `warships_player` | 1,096,602 | 13,968 | 2026-08-15 07:50 | 2,322 |
| `pg_toast_147697` (its TOAST) | 4,432,693 | 38,539 | 2026-08-15 07:49 | 1,575 |

Zero dead tuples on the observation TOAST across 666 autovacuum runs means freed space is being reclaimed and made reusable. The relation is nonetheless 28% larger while holding 12% **fewer** rows. Retained bytes per surviving row rose sharply: that is the signature of keep-depth, and it is what F3 explains.

- **Consequence**: autovacuum tuning is not the remedy, and neither is a repack. `VACUUM FULL` on a hot table cost a 24-minute outage on 2026-07-21; see `project_db_table_audit_2026-07-19`. Lever 1 (age-bound the JSON) is the remedy.

### F3: The compaction timer ignores `BATTLE_OBSERVATION_COMPACT_KEEP` ★ live defect

Droplet journal, the two most recent runs:

```
Aug 13 12:43:14  Compacted 86,686 observation payloads in 44 batch(es) (keep_per_player=3, min_age_hours=0).
Aug 14 12:52:48  Compacted 78,897 observation payloads in 40 batch(es) (keep_per_player=3, min_age_hours=0).
```

`keep_per_player=3`. The intended pin is **1**, and it is set correctly everywhere it was supposed to be:

| Location | Value | Verified |
|---|---|---|
| `/etc/battlestats-server.env` | `BATTLE_OBSERVATION_COMPACT_KEEP="1"` | live read 2026-08-15 |
| release `.env` (`releases/20260815032722`) | `BATTLE_OBSERVATION_COMPACT_KEEP="1"` | live read 2026-08-15 |
| `server/deploy/deploy_to_droplet.sh:806` | `set_env_value BATTLE_OBSERVATION_COMPACT_KEEP 1` | repo |

Nothing on the timer's path reads it. The chain:

- Only the **Celery** task consults the variable: `server/warships/tasks.py:3098-3099`, `os.getenv("BATTLE_OBSERVATION_COMPACT_KEEP", "3")`. That Beat row (`prune-battle-observations`) was set `enabled=False` on 2026-08-05 when Step 1 moved compaction to the timer, so this code no longer runs.
- The management command's argparse default is the hardcoded constant: `server/warships/management/commands/prune_battle_observations.py:60-69` uses `default=COMPACT_KEEP_PER_PLAYER_DEFAULT`, and `server/warships/incremental_battles.py:1542` sets that to `3`. The command never reads the environment.
- The unit passes only the timeout:

  ```
  ExecStart=/bin/bash -lc 'exec "…/python" manage.py prune_battle_observations \
      --statement-timeout "${BATTLE_OBSERVATION_COMPACT_STATEMENT_TIMEOUT:-1800}"'
  ```

Net effect: since **2026-08-06**, compaction has retained **three** JSON generations per player rather than one. Step 1 succeeded at what it was scoped to do (it unstalled a process that had been compacting nothing), but it silently reverted the keep-depth to the code default.

- **Deliberately not quantified.** The observations-per-player distribution is unmeasured, so no GB figure is attached to this defect. The defensible statement is mechanical: compaction retires ~80K payloads per night, the relation still gains ~440 MB/day, and it retires three-deep instead of the intended one-deep.
- **The timer itself is healthy.** Clean fires every night 08-09 through 08-14, durations 6 to 22 minutes, `Deactivated successfully`, exit 0. Next fire 2026-08-15 12:34 UTC.
- **Remediation**: pass `--keep-per-player "${BATTLE_OBSERVATION_COMPACT_KEEP:-3}"` in the unit's `ExecStart`, or make the command's default read the environment variable so both entry points agree. The second is preferable: it removes the divergence rather than papering over it, and it is the divergence that caused this.
- **Reversible**: yes, entirely. Config and code; no data was destroyed. Compaction NULLs rather than deletes, but the payloads it did *not* touch are still present.

### F4: The runway slipped three to five weeks

Project on the database rate of **679 MB/day**, not the disk rate of 767. The non-database gap is at its structural ceiling (`wal_keep_size` 4013 MB, `max_wal_size` 4014 MB) and has roughly 0.4 GB left to give, ever; it cannot be a slope.

| Threshold | Bytes | Days out | Date | 08-05 forecast |
|---|---|---|---|---|
| 70% | 58.92 GB | ~8 | **~2026-08-23** | ~2026-09-11 |
| 80% | 67.34 GB | ~20 | **~2026-09-04** | ~2026-10-06 |
| 100% | 84.17 GB | ~45 | **~2026-09-29** | not on the central path |

- **Risk**: storage exhaustion on managed PG is a **read-only outage**, the 2026-05-24 failure mode. The 08-05 analysis put the central plateau at 80% with a high band at 88%; the observed slope is tracking the high band, not the central path.
- **Framing note**: this is the expected outcome of arming nothing. Step 1 unstalled a stalled process, Lever 1 shipped deliberately un-armed at `COMPACT_DORMANT_DAYS=0`, and Lever 2 is still pending. The slope was never supposed to bend on its own. F3 is the part that was *supposed* to apply and did not.

### F5: DB health regime change

Two `:9273` samples about four minutes apart, both taken well before the 12:30 UTC compaction fire, so neither is contaminated by it:

| Metric | 2026-08-05 | 2026-08-15 (sample 1 / 2) | Threshold |
|---|---|---|---|
| `cpu_usage_iowait` | 19.3% | **54.2% / 40.2%** | trouble above ~25% |
| `system_load15` | 3.13 | **4.99 / 4.54** | saturates at 2 on 2 vCPU |
| `mem_used_percent` | — | 45.9% | |

Sustained across both samples, so not a single burst. Cluster configuration is unchanged: `db-s-2vcpu-4gb`, one node, PG 18, created 2026-03-19.

A **maintenance window is pending, Thursday 11:11 UTC**. Worth noting on two counts: a node replacement returns with a cold cache, which is a plausible partial explanation for the iowait, and it is the next scheduled event that will perturb both iowait and the backup spool. Not investigated further.

- **Bearing on the plan**: Lever 1 mass-NULLs toasted rows and **transiently raises** disk and WAL before it helps. Running that at iowait already above 40%, with maintenance pending, is the wrong order. Do the cheap items first.

## Next steps

One production lever per operator acknowledgement, per `feedback_prod_levers_one_at_a_time`. Nothing below has been executed.

| # | Action | Kind | Risk | Why this position |
|---|---|---|---|---|
| 1 | **Arm DO disk alerts at 70% (58.9 GB) and 80% (67.3 GB)** | Operator, DO console | none | 70% is ~8 days out and there is no alert. This is Lever 3 from the 08-05 runbook, still not started. Arming an alert is not a prod lever; it needs no gate |
| 2 | **Fix the compaction keep-depth (F3)** | Code + unit | low, reversible | Restores intended behaviour that was silently lost on 08-06. Costs nothing and needs no data decision. Verify by the next run's journal line reading `keep_per_player=1` |
| 3 | **Arm `PRUNE_BATTLES_JSON_ENABLED=1`** (08-05 Lever 2) | Config | low | Bounds `warships_player`, currently unbounded. **Check the 08-05 runbook's "⚠ Blocker found 2026-08-08" section first**: arming the flag alone would ship a silent weekly no-op. Run `--dry-run` before arming |
| 4 | **Arm `COMPACT_DORMANT_DAYS=105`** (08-05 Lever 1) | Config, **irreversible** | medium | The actual slope-bender: roughly 9 GB off the plateau. Transiently raises disk and WAL first. Do not run this into the current iowait regime or across Thursday's maintenance window. `--dry-run`, then in slices |
| 5 | **Re-measure after 2 and 4** | Read-only | none | Repeat the Validation recipe below. The discriminating signal is whether the observation TOAST slope drops below ~200 MB/day |

Escape hatch, unchanged from the 08-05 runbook: resize the volume or enable storage autoscale. That buys time without touching the mechanism, so it is a fallback if item 1's 70% alert fires before items 2 through 4 land, not a substitute for them.

Item 2 is a code change and therefore carries a version bump; per `CLAUDE.md`, **every** bump requires a client rebuild and deploy even when the change is backend-only.

## Validation

How each class of figure was obtained, so the next re-measurement is a repeat rather than a rediscovery.

**Disk, iowait, load** — the DO Prometheus endpoint. The per-cluster credentials route 404s; use the **global** route. The endpoint serves a self-signed chain, so `curl -k` is required.

```bash
TOKEN=$(grep -oP 'access-token:\s*\K\S+' ~/.config/doctl/config.yaml)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.digitalocean.com/v2/databases/metrics/credentials
HOST=db-postgresql-nyc3-11231-do-user-8591796-0.m.db.ondigitalocean.com
curl -sk -u "USER:PASS" "https://$HOST:9273/metrics" \
  | grep -E '^(disk_used_percent|disk_used|disk_total|cpu_usage_iowait|system_load15)\{'
```

**Table sizes and autovacuum state** — read-only psql against the cloud target. Metadata reads only; no scans, no counts. Credentials from `server/.env.cloud` and `server/.env.secrets.cloud`.

```sql
SET statement_timeout='45s';
SET default_transaction_read_only=on;
SELECT pg_database_size('defaultdb');
SELECT relname, pg_total_relation_size(c.oid), pg_relation_size(c.oid),
       pg_indexes_size(c.oid), COALESCE(pg_total_relation_size(c.reltoastrelid),0)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r'
ORDER BY 2 DESC LIMIT 15;
-- bloat-vs-data discriminator
SELECT s.relname, s.n_live_tup, s.n_dead_tup, s.last_autovacuum, s.autovacuum_count, c.reltuples
FROM pg_stat_all_tables s JOIN pg_class c ON c.oid = s.relid
WHERE s.relid IN (
  (SELECT oid FROM pg_class WHERE relname='warships_battleobservation'),
  (SELECT reltoastrelid FROM pg_class WHERE relname='warships_battleobservation'));
```

The `relkind='r'` filter omits `mv_player_distribution_stats` (a matview, ~0.12 GB). Account for it when reconciling against `pg_database_size`.

**Compaction runs** — the droplet journal carries the payload count and, critically, the effective `keep_per_player`:

```bash
ssh root@battlestats.online \
  'systemctl list-timers battlestats-compact-observations.timer --no-pager;
   journalctl -u battlestats-compact-observations.service --since "-7 days" --no-pager | grep Compacted'
```

**Two reading rules learned here.** First, always confirm the units are decimal before diffing two size measurements. Second, a healthy timer is not a working lever: the unit ran clean every night while silently applying the wrong keep-depth. Read the effective parameters out of the run log, not the intended ones out of the env file.

## Follow-ups

- Item 2 above closes the F3 defect. Until it lands, do not treat `BATTLE_OBSERVATION_COMPACT_KEEP` as an effective knob anywhere.
- `check_env_drift.sh:15` carries a comment about `BATTLE_OBSERVATION_COMPACT_KEEP` being inferred as the code default. The drift checker compares the file to Pass; it cannot see that a code path ignores the file. Consider whether the checker should assert on *effective* values for the variables that have two readers.
- `warships_battleobservation` heap holds 430,106 dead tuples against 12 lifetime autovacuum runs, while its TOAST has had 666. The heap is plainly under-vacuumed relative to its churn. Not urgent (the heap is 0.68 GB, so bloat there is cheap) but it is the kind of asymmetry worth a per-table `autovacuum_vacuum_scale_factor` once the disk work settles.
- `Player` still has no `created_at`, so the new-player discovery rate remains unmeasured. It was named the cheapest high-value follow-up on 08-05 and remains open. Player pool, not retention, is the binding constraint.
- Re-check after Thursday 11:11 UTC maintenance to see whether the iowait regime persists or was cache warmth.

## Related

- `agents/runbooks/runbook-db-disk-remediation-2026-08-05.md` — the execution plan; authority on what to do and on the Step 2 blocker
- `agents/work-items/db-growth-capacity-2026-08-05.md` — the baseline this runbook diffs against; growth decomposition and capacity envelope
- `agents/work-items/data-capture-utility-audit-2026-08-05.md` — data-utility delta over the 07-19 F-series
- `agents/runbooks/runbook-battle-history-archive-prune-2026-06-17.md` — the 105-day retention pipeline; retention is a fixed constraint, never a disk lever
- `agents/runbooks/ops-infra-resources.md` — cluster sizing
- `agents/runbooks/ops-env-reference.md` — env-var catalog and the Pass-is-canonical rule
