# Runbook: DB CPU Alerts — Steal and Parallel Scan Bursts

_Created: 2026-11-23_
_Lifecycle: dated-active · Owner: platform_
_Context: DigitalOcean's "CPU is running high" alert fired on the managed Postgres on 2026-11-22 13:11 UTC and 2026-11-23 10:41 UTC (98.9% against a 90% / 5 minute policy). The operator described it as a recurring pattern and asked for the root cause._
_QA: every figure below was measured on 2026-11-23 between 21:45 and 21:58 UTC, from `pg_stat_statements`, the droplet journal, the DO API, or the `:9273` metrics endpoint. Nothing here is inferred from a dashboard._
_Status 2026-11-24 02:10 UTC: **step 4 shipped as v5.13.3 and the startup stagger (step 8, first candidate) as v5.13.4; both verified. The asia ship-standings start moved 10:30 to 09:30 in v5.13.5 (step 8, third candidate); NOT yet verified, first run 11-24 09:30 UTC. The 11-22 mix is not addressed.** Three alerts are explained and each had a different workload mix (F3, F6), so the cause is low headroom under any two or three concurrent heavy scans, not one statement. A read-only sampler is armed until about 2026-11-24 23:50 UTC. **Resume at "Next steps", step 2, then decide the remaining step 8 candidates.**_

## QA Notes

_Reviewed 2026-11-23 against `/home/august/code/battlestats/.claude/worktrees/db-cpu-alerts-runbook-1123` (linked worktree). 19 assertions checked, 6 corrected. At the operator's instruction the review was followed by implementation of step 4 in the same worktree; line numbers below are post-change._

### Resolved
- **"compute the list once per realm per day and have the warmer read the stored result" (step 4 premise: the list is used)** -> actual: the score list was the last of four sources and the dedup loop stopped at `limit`; on prod `_top_visited_entity_ids('player', 20)` returns 20 ids, which alone fills `HOT_ENTITY_PLAYER_LIMIT` (20), so the 32 s ranking was computed and discarded on every cycle (`server/warships/data.py:5486`, `server/warships/data.py:5412`) -> step 4 rewritten: sources are consulted lazily, and the score list is cached for a day for the case where it is reached. Expected runs: about zero per day, at most three.
- **"The companion 'recent lookups' query ... must stay fresh; give it a `(realm, last_lookup DESC)` partial index"** -> actual: it is the third source and is likewise unreachable while top-visited fills the limit (`server/warships/data.py:5486`); prod holds 2,995 to 25,868 looked-up players per realm, so it always filled the remainder before the score list was reached -> index dropped from step 4; lazy evaluation removes the query without a migration.
- **"guard recomputation with a lock"** -> actual: `warm_hot_entity_caches_task` already holds a per-realm `cache.add` lock for the whole warm (`server/warships/tasks.py:2148`) -> picked: no second lock. The only unlocked callers are two management commands, and a duplicate compute is one extra scan, not a correctness fault.
- **"compute it only on a miss or from a once-daily striped task"** -> actual: Beat tasks are registered in a `post_migrate` receiver (`server/warships/signals.py:417`), so a new task needs a migrate and a schedule row -> picked: TTL cache on miss, `HOT_ENTITY_SCORE_IDS_TTL` default 26 h (`server/warships/data.py:172`). No Beat task, no migration.
- **Step 4 named no tests** -> actual: only clan candidates were covered (`server/warships/tests/test_hot_entity_candidates.py:15`) -> added `server/warships/tests/test_hot_player_candidates.py` (five tests: laziness, priority order, reuse, empty-result caching, per-realm keys).
- **"The F2 statement accrues about 3 calls per day" (Validation)** -> actual: with lazy sources the statement runs only when the first three sources fall short of `limit` (`server/warships/data.py:5486`) -> corrected to "at most 3 calls per day; about zero expected".

### Unverified
- All `pg_stat_statements`, steal, plan and sizing figures in F1 to F5: runtime measurements from 2026-11-23, not reproducible from the repository.
- The alert policy slug `v1/dbaas/alerts/cpu_alerts` and its 90% / 5m threshold: taken from an operator note dated 2026-09-20; the DO alerts API was not re-queried.
- "every 30 minutes per realm": `HOT_ENTITY_CACHE_WARM_MINUTES` defaults to 30 in code (`server/warships/signals.py:418`); the live value was not read, but the journal shows the resulting 10-minute cadence.
- Sampler liveness after this review: it was running at 21:58 UTC and was not re-checked.

### Open Questions
1. **Should the hot-entity warmer warm the realm it was dispatched for?** `warm_hot_entity_caches` selects candidates for `realm` but calls `warm_player_entity_caches` and `warm_clan_entity_caches` without it (`server/warships/data.py:5571`, `server/warships/data.py:5575`), so both default to `na`. The top-visited list is also not realm-scoped (`server/warships/data.py:5416`). The eu and asia runs therefore warm only ids that happen to exist on NA: the asia run at 10:38 on 2026-11-23 warmed 4 of 20 player candidates. Passing `realm=realm` is a one-line fix, but it raises WG calls and database writes on eu and asia by up to 20 players and 10 clans per cycle, during a load investigation. Leaving it keeps load flat and the warmer largely idle outside NA. Not changed here. Blocks nothing; decide before step 5.

## Purpose

Record what was measured about the managed-Postgres CPU alerts, separate what is established from what is not, and give the next agent an ordered path from the armed sampler to a fix. Read this before acting on any further "CPU is running high" mail, and before touching the hot-entity warmer or the database's planner settings.

This is not an ops-digest condition. The alert comes from DigitalOcean's own policy (`v1/dbaas/alerts/cpu_alerts`, > 90% for 5m) and arrives from `support@digitalocean.com`; the exception-only digest was clear on both days.

## Timeline

| When (UTC) | Event |
|---|---|
| 2026-10-16 | Last backend release on the droplet (`releases/20261016224645`). No backend code has changed since. |
| 2026-11-19 11:22 | DO Thursday maintenance. `pg_stat_statements` and the cumulative table statistics were reset, which indicates the database moved to a new node. |
| 2026-11-22 13:11 → 13:17 | Alert triggered, then recovered. |
| 2026-11-23 00:33 | App droplet rebooted into kernel 6.8.0-151. Unrelated to the database node. |
| 2026-11-23 10:41 → 10:45 | Alert triggered at 98.9%, then recovered. |
| 2026-11-23 21:51 | Sampler armed on the droplet. |
| 2026-11-26 11:11 | Next DO maintenance window; the database object reports `pending: true`. |

The mailbox holds only these two alert pairs in the last 120 days. Earlier alerts may have been deleted; the mailbox is not a send record.

## Findings

### F1. The alert percentage counts hypervisor steal

The node is `db-s-2vcpu-4gb`: a **shared-CPU** plan. A live sample taken while one routine 32-second hot-entity warm was running read:

```
idle=5 user=15 sys=25 iowait=13 steal=36 load1=2.06
```

Across the first 43 samples (about 7 minutes, mostly quiet) the averages were `idle=68 user=10 sys=9 iowait=2 steal=9`. Steal ran at 5 to 14% even near idle, and at roughly a third of demanded CPU under load. The alert therefore measures `100 - idle`, and a material part of a "98.9%" reading is time the hypervisor withheld, not work Postgres performed. The node behaves closer to 1.3 vCPU than 2.

### F2. The largest single statement is the hot-player score list

`_get_hot_player_ids` in `server/warships/data.py` (near line 5450) builds a "top 20 by `explorer_summary__player_score`" candidate list on every run of `warm_hot_entity_caches_task`. That task fires every 30 minutes per realm, striped, so the query runs every 10 minutes.

Since the 2026-11-19 statistics reset:

| Measure | Value |
|---|---|
| Calls | 637 (144 per day) |
| Mean / max execution | 31.8 s / 148 s |
| Blocks read per call | 285,351 (about 2.2 GB) |
| Temp blocks written per call | 4,449 |
| Share of all leader execution time | 20,264 s of 165,183 s (12%) |

The plan is a parallel index scan of about 536k player rows through `player_realm_battles_surv_idx`, a parallel hash right join to `warships_playerexplorersummary`, and a sort, with two workers plus the leader and JIT enabled. Three processes on two vCPUs saturate the node for the duration. `pg_stat_statements` counts only the leader's time, so the true CPU share exceeds 12%.

The companion "recent lookups" query in the same function costs a further 2,949 s (mean 4.6 s); it has no `(realm, last_lookup)` index and combines two bitmap scans.

### F3. Both alert windows were pile-ups

No single runaway statement appears in either window. Long tasks overlapping each:

- **2026-11-23 10:36 to 10:41:** two `ensure_daily_battle_observations_task` passes, `recapture_lapsed_players_task` (eu), `warm_realm_ships_pct_task` (asia, 1,187 s), `snapshot_active_players_task`, `warm_hot_entity_caches_task`.
- **2026-11-22 13:06 to 13:11:** `ensure_daily_battle_observations_task`, `incremental_ranked_data_task` (361 s), `incremental_player_refresh_task` (629 s), `warm_hot_entity_caches_task`.

`snapshot_active_players_task` normally completes in 10 to 70 s; it took 398 s, 456 s and 580 s around the windows. It is a victim of the contention, not its source.

### F4. Planner and memory settings favour these bursts

`random_page_cost=1` (equal to `seq_page_cost`), `work_mem=4MB`, `shared_buffers=780MB`, `effective_cache_size=2.3GB`, `max_parallel_workers_per_gather=2`, `jit=on`, `track_io_timing=off`. The player heap is 1.8 GB (plus 8.8 GB TOAST) and `warships_battleevent` is 5.1 GB, against 4 GB of RAM. A repeated 2.2 GB read cannot stay cached, and random access is priced as free. This matches two earlier findings: the EU snapshot fixed with `enable_nestloop=off` (2026-09-26) and the chronic 10x random-access latency.

### F5. Other statements worth knowing

By total leader time since the reset: the observation floor's bulk player fetch with its `_last_obs_battles` subquery (15,354 s over 22,653 calls), the `warm_realm_ships_pct` battle-event aggregate (11,328 s, mean 52 s), the snapshot candidate scan whose `LIMIT` grows with the day's checked set (10,108 s, mean 15.9 s), and the incremental ranked candidate scans (5,125 s combined, mean 39 to 80 s, max 220 s).

### F6. A backend deploy fires the alert by itself (captured live, 2026-11-23 23:01 to 23:09)

The v5.13.3 backend deploy restarted gunicorn at 22:58. Its `when_ready` hook dispatched `startup_warm_caches_task`, which fans out to all three realms at once, outside the striping. The sampler recorded `idle=0` for seven consecutive minutes (23:02 to 23:08, `load1` up to 6.35), and DigitalOcean mailed "Triggered" at 23:11 and "Recovered" at 23:15.

Active statements during the saturation, by sample count:

| Samples | Statement | Source |
|---|---|---|
| 93 | `FETCH FORWARD 2000` on a server-side cursor, mostly waiting on IO | three concurrent `warm_player_ranked_wr_battles_correlation_task` runs (239 s, 351 s, 381 s) |
| 25 | `REFRESH MATERIALIZED VIEW CONCURRENTLY mv_player_distribution_stats` | `warm_player_distributions_task` x3 (47 s, 81 s, 139 s) |
| 12 | observation-floor candidate scan with parallel workers | `ensure_daily_battle_observations_task` |

Steal was only 4 to 12% in this window (`user` 45 to 49, `sys` 31 to 36): this saturation was real work, not withheld CPU. F1's one-third steal figure is therefore not constant, and steal alone does not explain the alerts.

**The alert mail lags the saturation.** "Triggered" arrived about 3 minutes after the saturated window ended. The two original alerts therefore correspond to roughly 13:01 to 13:08 on 11-22 and 10:31 to 10:38 on 11-23, earlier than F3 assumed. The 11-23 window then coincides with the asia nightly ship-standings pipeline starting at 10:30 (`snapshot_ship_top_players_task`, `warm_all_ship_pop_avg_damage_task`, two `warm_top_ships_treemap_task`, `warm_realm_ships_pct_task`), the eu recapture sweep at 10:30, two floor passes, and the weekly traffic digest at 10:31. Neither original alert followed a service restart; the only restart in the period was the droplet reboot at 00:33 on 11-23.

### Verification of step 4 (v5.13.3)

Across the 23:07 and 23:17 warm cycles the score-ranking statement stayed at 644 calls and the recent-lookups statement at 644 calls; both had been rising by one per cycle. `warm_hot_entity_caches_task` took 3.7 s at 23:17 against 25 to 82 s earlier in the day. Full backend suite: 1393 passed, 2 skipped.

### Verification of the startup stagger (v5.13.4)

The v5.13.4 backend deploy (release `20261123191038`) restarted gunicorn at 00:11:59 UTC on 11-24 and was itself the test. `startup_warm_caches_task` completed at 00:12:07.

| measure | v5.13.3 deploy (F6) | v5.13.4 deploy |
|---|---|---|
| longest run of samples with idle < 10% | 7 minutes | about 90 s (9 samples at 10 s) |
| samples with idle < 10%, 00:10 to 01:05 | not measured | 34 of 326 |
| ranked-correlation warmers | three concurrent | sequential: 00:35:10 to 00:38:39 (209 s), 00:44:33 to 00:48:27 (233 s), 00:49:09 to 00:51:43 (153 s) |
| DO alert mail | Triggered 23:11 | none |

The three distributions warms finished at 00:14:41, 00:23:17 and 00:28:48 (34 s, 80 s, 41 s). The na Beat correlation lane at 00:45:00 skipped on the lock, as designed. Full backend suite: 1394 passed, 2 skipped.

Two reads to keep:

- **The deploy still produces short saturated minutes.** 00:16 and 00:23 averaged 2% and 1% idle. Neither lasted long enough to trip a 5-minute policy, but the headroom is thin: a deploy that lands on a scheduled heavy window could still alert.
- **The worker receives all six heavy tasks at once and holds them until their ETA.** A journal showing six `received` lines at 00:12 is the stagger working, not failing; pair `received` to `succeeded` by task id.

## What is not established

- Whether steal rose after the 2026-11-19 node move. No pre-move sample exists.
- The exact statement mix inside a real 5-minute alert window. The journal gives task overlap, not database activity.
- Whether the pattern predates 2026-11-22.
- How steal varies across a full day. Seven minutes of samples is not a baseline.

## Decisions

- **2026-11-23, operator: the "top 20 players by score" list should run once a day.** Player score moves on the timescale of the nightly pipelines, not of a 10-minute warmer. This fixes the shape of step 4 below: compute the list once per realm per day and have the warmer read the stored result. It takes the statement from 144 runs a day to 3.

## The armed sampler

A disposable, read-only process on the droplet, running as the `battlestats` user until about 2026-11-24 23:50 UTC. It holds one database connection.

| Item | Path |
|---|---|
| Script | `/tmp/dbcpu_sampler.py` |
| Log | `/tmp/dbcpu_sampler.log` |
| Metrics credentials (mode 600) | `/tmp/.dbmetrics_creds`, `/root/.dbmetrics_creds` |

Every 10 s it writes one line of `idle / user / sys / iow / steal / load1` from the DO `:9273` endpoint. When idle is below 55 it also writes the active backends from `pg_stat_activity` (pid, backend type, leader pid, age in seconds, wait type, query prefix), indented under the sample. The endpoint refreshes about every 30 s, so values repeat in threes and lag the workload by up to 30 s.

`/tmp` is cleared on reboot. If the droplet restarts, the sampler and its log are gone; re-arm it using the credentials recipe in the operator memory `reference_do_db_cpu_metrics_endpoint`. The working DO token is `DO_API_TOKEN` in `~/.bashrc` on the dev machine; the doctl config token is dead. Delete both credential files when the investigation closes.

## Next steps

One at a time. Steps 5 onward are production levers and each needs the operator's acknowledgement.

1. **Read the sampler against the next alert.** When a "CPU is running high" mail arrives, take its trigger time, subtract 5 minutes, and read that window:
   ```bash
   ssh root@battlestats.online 'awk "\$2>=\"10:36:00\" && \$2<=\"10:46:00\"" /tmp/dbcpu_sampler.log'
   ```
   Done once on 2026-11-23 (F6). Per F6 the mail lags by about 3 minutes, so read from 10 minutes before the trigger time, not 5.
   Record the steal share and the statements present. If no alert fires before the sampler expires, read the day's busiest windows instead (`idle < 20`).
2. **Establish a steal baseline.** Average steal by hour over the full log. A node that loses a third of demand at all hours is a platform fact; a figure that swings points to a noisy neighbour.
3. **Confirm the fix order from that evidence.** Step 4 is decided and proceeds regardless: it is correct on its own merits. The sampler determines what follows it.
4. **Make the hot-player score list daily (code; decided, implemented 2026-11-23).** QA found the list's result was discarded on every cycle, because earlier sources already fill the limit. As built in `server/warships/data.py`:
   - `_get_hot_player_ids` consults its four sources lazily (pinned, top-visited, recent lookups, score) and stops querying once `limit` distinct ids are collected. Output order and content are unchanged.
   - `_top_scored_player_ids` caches the per-realm score list under `hot_entity:score_player_ids:v1:<realm>:<limit>` for `HOT_ENTITY_SCORE_IDS_TTL` (26 h default). An empty list is cached too. On eviction under `allkeys-lru` the list is recomputed only if a cycle actually reaches the fourth source.
   - No Beat task, no migration, no new index, no second lock (see QA Notes).
   - Tests: `server/warships/tests/test_hot_player_candidates.py`.
   - Verify with `pg_stat_statements` deltas: the F2 statement and the "recent lookups" statement should each stop accruing calls, and `warm_hot_entity_caches_task` durations in the journal should drop.
5. **Consider serial plans for the periodic warmers.** `SET LOCAL max_parallel_workers_per_gather = 0` inside the warm paths trades wall time for not occupying three processes on two vCPUs. Measure one task before generalising.
6. **Review `random_page_cost`.** It is a cluster setting on a managed database, so a change affects every plan. Test per-session first on the statements in F2 and F5; do not change the cluster value without a before/after on the ship-standings and floor queries.
7. **If steal proves chronic, raise it with DigitalOcean.** A support ticket citing sampled steal on a shared-CPU node costs nothing. A plan change to dedicated CPU is spend, and is the last option: this project minimises spend and never autoscales the database.

8. **Stop heavy scan families from overlapping (the remaining cause; undecided).** F3 and F6 show three alerts from three different mixes. Candidates, cheapest first: stagger the startup fan-out so the three realms' ranked-correlation warmers run in sequence, not together; give the heaviest warm families a shared "one heavy scan at a time" lock; move the asia 10:30 ship-standings start off the eu recapture stripe. Each changes scheduling behaviour and needs the operator's choice. **The first candidate shipped as v5.13.4 and is verified above; a backend deploy no longer fires the alert by itself.** **The third candidate shipped as v5.13.5 on the operator's instruction (2026-11-24): asia's snapshot moved 10:30 to 09:30** (`SHIP_SNAPSHOT_REALM_HOUR_OFFSETS` in `server/warships/signals.py`; na 02:30 and eu 06:30 unchanged; the asia top-ships warmer at 15:15 is scheduled separately and did not move). 09:30 was chosen over 08:30 because 08:40 to 08:55 already holds the eu correlations, eu distributions, clan tier distributions and the efficiency-rank snapshot; 09:17 to 09:45 held only floor passes and active-player snapshots on 11-22 and 11-23. Known neighbours at the new time: the incremental ranked pass near 09:45, and the drift reclassify (about 300 s, ending near 09:45 on Monday 11-23). The asia chain is the 82 s snapshot, the treemap warms, then the ships-pct fan-out for about 20 minutes, so expect it to end near 09:52. **First run at the new time: 2026-11-24 09:30 UTC; read the sampler from 09:20 to 11:15 to verify both hours.** The shared heavy-scan lock remains undecided and is what the 11-22 mix needs.
   - **Operator chose the first candidate on 2026-11-23; implemented.** `startup_warm_caches_task` (`server/warships/tasks.py`) keeps the 20 s stagger for the hot-entity and bulk-load warmers and spaces the six distributions and correlations dispatches by `STARTUP_WARM_HEAVY_SPACING_SECONDS` (default 420), after the light ones. The last dispatch lands about 37 minutes after startup. Test: `test_startup_warm_fanout.py::test_heavy_warmers_never_start_closer_than_the_heavy_spacing`. One realm's three correlation metrics still run together, as they do on the daily Beat lane.
   - The other two candidates (a shared heavy-scan lock; moving the asia 10:30 start) remain undecided and are what the two original, non-deploy alerts need.

After the 2026-11-26 maintenance, re-sample steal for one hour. If the node moves again, the figure may change in either direction, and that comparison is the only available evidence on the node-move question.

## Validation

A fix is validated when all of the following hold over seven days:

- No "CPU is running high" mail from DigitalOcean.
- The F2 statement accrues at most 3 calls per day in `pg_stat_statements`; about zero is expected.
- `snapshot_active_players_task` durations stay under 120 s in the journal.

Do not treat a quiet day as validation on its own; the alert fired on only two known days.

## Guardrails

- Every ad-hoc query against this database carries a `statement_timeout`. The diagnostics here used 20 s.
- Do not reset `pg_stat_statements`; snapshot and diff instead.
- Do not raise or remove the 90% alert policy to quiet the mail. It is the only detector for this condition.
- Do not flip floor concurrency or any worker `-c` value as a response to this alert without the operator's approval.

## Related

- `agents/runbooks/runbook-db-table-shape-remediation-2026-09-20.md` (disk plan, table shapes)
- `agents/runbooks/ops-infra-resources.md` (sizing)
- `agents/runbooks/runbook-celery-queue-strategy.md` and `agents/runbooks/runbook-realm-schedule-striping-2026-08-15.md` (what runs when)
- `agents/runbooks/archive/runbook-db-cpu-saturation-2026-05-24.md` (the earlier, single-runaway-query incident; a different cause)
