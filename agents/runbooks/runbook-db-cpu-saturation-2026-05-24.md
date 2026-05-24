# Runbook: Managed Postgres CPU saturation (db-postgresql-nyc3-11231)

_Created: 2026-05-24_
_Context: The DigitalOcean managed Postgres cluster backing battlestats has been firing CPU-high monitoring alerts continuously since 2026-05-01. This runbook is the evidence log assembled from every DO alert email (April 1 → May 24), so the investigation can start from data rather than re-reading the inbox. The alert mail was being auto-trashed by the personal mail-tidy rules, so it is logged here before it ages out of Trash._
_Status: **IN PROGRESS.** Disk axis: root cause confirmed from code (unbounded, JSON-heavy `BattleObservation` capture with no retention — see [Findings](#findings-2026-05-24)). A compaction tool has been built (disabled by default) to reclaim the disk. CPU axis: still correlational — the same write workload is the prime suspect but the expensive-query confirmation (`pg_stat_statements`) is pending DB access. **Acute:** on 2026-05-24 the cluster went read-only (disk full), which broke a backend deploy mid-flight; services were restarted on the prior release but write paths 500 until storage is resized._

## Findings (2026-05-24)

Triggered by an unrelated incident: a backend deploy aborted on `cannot execute SELECT FOR UPDATE in a read-only transaction`, and the app log showed `ReadOnlySqlTransaction: cannot execute UPDATE`. The cluster is **read-only**, which on DO managed Postgres is the disk-exhaustion protection mode — i.e. the May 21 disk alerts in this runbook culminated in a full disk.

**Confirmed root cause of the disk axis (code-verified, not yet DB-measured):**

- `BattleObservation` (`server/warships/models.py:468`) stores a full per-ship `ships_stats_json` blob **plus** a `ranked_ships_stats_json` blob per row; `observed_at = auto_now_add` (append-only).
- The 2026-05-01/02 battle-history rollout writes a row on **every** visit-driven refresh, clan-crawl refresh, and the 6-hourly observation floor — and ranked capture (a *second* large JSON blob) is on for **all three realms** (`BATTLE_HISTORY_RANKED_CAPTURE_REALMS=na,eu,asia`).
- There is **no retention/prune** anywhere for `BattleObservation` or `BattleEvent` (grep confirmed; the only cleanup job targets `EntityVisitEvents`). One sampled player (`Punkhunter25`) had **439 observations × ~327-ship JSON**.

That is the disk filler, and the onset (2026-05-01) + the scheduled-hour CPU peaks both line up with this workload.

**Hard constraint discovered — compact, do not delete:** `BattleEvent.from_observation` and `to_observation` are **`on_delete=CASCADE`** FKs (`server/warships/models.py`), and the event uniqueness constraints are keyed on the observation pair. Deleting observation rows would cascade-delete the durable per-battle `BattleEvent` record that powers the charts. So the reclaim strategy is to **NULL the JSON payloads** on stale observations while keeping the rows.

## Remediation built (disabled by default)

A compaction tool ships alongside this runbook (does **not** auto-run):

- **`compact_battle_observation_payloads()`** — `server/warships/incremental_battles.py`. NULLs `ships_stats_json` / `ranked_ships_stats_json` on observations outside the per-player keep set; batched, dry-run aware. Keep set = latest N per player (random diff baseline) ∪ latest non-NULL-ranked per player (ranked walk-back baseline). Verified safe for the scheduled diff path (`record_observation_from_payloads`, `_hydrate_previous_ranked_snapshot`, `_ranked_observation_needs_refresh`); the manual baseline-seeding commands have a documented caveat below.
- **`prune_battle_observations`** management command — `--dry-run` (default first step) reports candidate count + reclaimable bytes (Postgres `pg_column_size`); live mode clears in `--batch-size` chunks with `--sleep` pacing and `--max-rows` cap.
- **`prune_battle_observations_task`** + Beat entry `prune-battle-observations` — daily at **12:30 UTC** (the histogram's quietest hour, clear of the 03:00/23:00 CPU peaks), **gated off by `BATTLE_OBSERVATION_COMPACT_ENABLED=0`**.

### Operating sequence (once the DB is writable)

1. **Resize storage first.** The prune is a write; it cannot run while the DB is read-only. Bump the managed-DB disk (DO dashboard or `doctl databases resize`) to clear the read-only lock and restore the API.
2. **Dry-run:** `python manage.py prune_battle_observations --dry-run` — confirm candidate count + reclaim estimate.
3. **Live, gently:** `python manage.py prune_battle_observations --batch-size 2000 --sleep 0.5 --max-rows 200000`, repeat until the dry-run reports ~0 candidates. (`VACUUM`/autovacuum then returns the space to reusable; growth halts because new rows reuse it.)
4. **Enable the schedule:** set `BATTLE_OBSERVATION_COMPACT_ENABLED=1` so the daily job keeps it compacted.

### Caveat

`establish_ranked_baseline`'s candidate filter (`server/warships/management/commands/establish_ranked_baseline.py:78`) joins across **all** of a player's observations (`battle_observations__ranked_ships_stats_json__isnull=True`), so after compaction NULLs old ranked payloads it will over-match (its docstring says "most recent observation" but the query doesn't enforce that). It's a manual one-shot, not scheduled, so impact is limited to wasted WG calls if re-run post-compaction — but the query should be tightened to look only at the latest observation (follow-up).

## TL;DR

- **What:** DO alert `CPU is running high` on cluster `db-postgresql-nyc3-11231` (cluster id `5449f4d9-a924-4158-af2d-0614a8cfd485`, region **NYC3**). Alert config: **CPU > 90.00% sustained for 5m**.
- **Scale:** **209 trigger→resolve episodes in 24 days** (2026-05-01 → 2026-05-24), ~9/day, no downward trend. Average peak **95.8%**, repeatedly pegging **100%**. Average episode **70 min**, median 36 min, longest **770 min (12.8 h)**.
- **Onset:** First CPU trigger **2026-05-01 03:40 UTC**. Nothing before May 1 in 50+ days of mail history → this is a **step-change**, almost certainly a workload/query regression, not organic growth.
- **New, possibly related:** **Disk Utilization** alerts began **2026-05-21** (3 triggers) + 5 "database is low on resources" notices → the box may now be resource-starved on two axes.
- **Prime suspect window:** the **2026-05-01/02 rollouts** (see [Correlation](#correlation-with-rollouts)).

## Alert anatomy

Two paired emails per episode, from `support@digitalocean.com`:

- **Triggered:** subject `DigitalOcean monitoring triggered: CPU is running high - db-postgresql-nyc3-11231`, body e.g. `Metric is currently at 99.16, above setting of 90.00 for the last 5m`.
- **Resolved:** subject `…monitoring resolved…`, body `Metric has returned to an acceptable level`.

Cluster console: `https://cloud.digitalocean.com/databases/5449f4d9-a924-4158-af2d-0614a8cfd485`
Alert config: same URL + `/insights/alerts/existing?dbObjectName=db-postgresql-nyc3-11231`

## Raw data

All 432 DO emails for this cluster (April invoices/maintenance included) are exported to an mbox:

```
/tmp/do-cpu-alerts.mbox        # 432 messages, RFC822
```

`/tmp` survives until reboot. To regenerate (mailcap MCP, idempotent/resumable):

```
gmail_export_query  query="from:support@digitalocean.com db-postgresql-nyc3-11231 in:anywhere"  path=<dest>.mbox
```

Counts: CPU triggered **209** / resolved **210**; Disk triggered 3 / resolved 2; "low on resources" 5. (Only the `in:anywhere` query finds them — most are in Trash.)

## Frequency

### Daily CPU trigger count + peak (UTC)

| Date | Triggers | Peak CPU |
|---|---|---|
| 2026-05-01 | 9 | 99.6% |
| 2026-05-02 | 8 | 99.9% |
| 2026-05-03 | 5 | 99.0% |
| 2026-05-04 | 15 | 99.9% |
| 2026-05-05 | 7 | 99.8% |
| 2026-05-06 | 8 | 99.0% |
| 2026-05-07 | 6 | 99.9% |
| 2026-05-08 | 11 | 97.8% |
| 2026-05-09 | 9 | 100.0% |
| 2026-05-10 | 7 | 97.3% |
| 2026-05-11 | 7 | 98.9% |
| 2026-05-12 | 8 | 96.4% |
| 2026-05-13 | 10 | 100.0% |
| 2026-05-14 | 10 | 99.8% |
| 2026-05-15 | 11 | 99.7% |
| 2026-05-16 | 11 | 100.0% |
| 2026-05-17 | 10 | 99.6% |
| 2026-05-18 | 10 | 100.0% |
| 2026-05-19 | 6 | 97.2% |
| 2026-05-20 | 8 | 97.2% |
| 2026-05-21 | 9 | 100.0% |
| 2026-05-22 | 9 | 99.5% |
| 2026-05-23 | 9 | 99.0% |
| 2026-05-24 | 6 | 99.2% (through 11:42 UTC) |

### Trigger count by hour of day (UTC, all 24 days)

```
00:5  01:3  02:1  03:19 04:12 05:13 06:14 07:11 08:15 09:11 10:5  11:4
12:3  13:11 14:10 15:9  16:9  17:5  18:11 19:7  20:4  21:4  22:5  23:18
```

Two clear peaks: **03:00 UTC (19)** and **23:00 UTC (18)**, with a sustained elevated band **04:00–09:00 UTC**. Local time is **EDT (UTC−4)** in May, so those clusters are roughly **7 pm EDT** and **late-night/overnight EDT (≈11 pm–5 am)** — consistent with **scheduled/periodic work** rather than user traffic (which would track daytime). This is the strongest structural clue: map these windows to the Beat schedule (below).

## Episode samples

### First 5 episodes (onset)

| Triggered (UTC) | Resolved | Duration | Peak |
|---|---|---|---|
| 2026-05-01 03:40 | 04:10 | 30 min | 98.7% |
| 2026-05-01 04:50 | 05:15 | 25 min | 92.3% |
| 2026-05-01 05:30 | 06:40 | 70 min | 94.9% |
| 2026-05-01 07:05 | 07:15 | 10 min | 94.1% |
| 2026-05-01 08:50 | 10:20 | 90 min | 90.3% |

### 10 longest episodes

| Triggered (UTC) | Resolved | Duration | Peak |
|---|---|---|---|
| 2026-05-03 01:05 | 13:55 | 770 min | 93.5% |
| 2026-05-05 08:05 | 19:15 | 670 min | 97.0% |
| 2026-05-06 21:38 | 04:56 | 438 min | 94.3% |
| 2026-05-01 22:35 | 05:05 | 390 min | 96.4% |
| 2026-05-05 02:15 | 07:55 | 340 min | 97.3% |
| 2026-05-07 05:08 | 10:44 | 336 min | 98.9% |
| 2026-05-01 14:45 | 20:20 | 335 min | 99.6% |
| 2026-05-23 06:18 | 11:36 | 318 min | 98.8% |
| 2026-05-03 14:15 | 19:30 | 315 min | 97.6% |
| 2026-05-02 15:20 | 20:25 | 305 min | 98.1% |

### Most recent 12 episodes (lead-in to today)

| Triggered (UTC) | Resolved | Duration | Peak |
|---|---|---|---|
| 2026-05-23 12:18 | 12:36 | 18 min | 93.2% |
| 2026-05-23 15:42 | 16:12 | 30 min | 98.7% |
| 2026-05-23 17:30 | 17:54 | 24 min | 90.4% |
| 2026-05-23 18:06 | 20:42 | 156 min | 96.6% |
| 2026-05-23 21:48 | 22:06 | 18 min | 98.8% |
| 2026-05-23 23:12 | 23:24 | 12 min | 98.9% |
| 2026-05-24 03:18 | 04:18 | 60 min | 98.4% |
| 2026-05-24 04:30 | 05:42 | 72 min | 98.5% |
| 2026-05-24 06:00 | 07:42 | 102 min | 98.0% |
| 2026-05-24 07:54 | 08:48 | 54 min | 98.1% |
| 2026-05-24 09:24 | 09:42 | 18 min | 93.4% |
| 2026-05-24 09:54 | 11:42 | 108 min | 99.2% |

### Disk Utilization (new on 2026-05-21)

| Time (UTC) | State |
|---|---|
| 2026-05-21 08:46 | triggered |
| 2026-05-21 09:31 | resolved |
| 2026-05-21 18:46 | triggered |
| 2026-05-22 01:46 | resolved |
| 2026-05-22 03:31 | triggered (no resolve logged before export) |

Plus 5 `Your database … is low on resources` emails over the window.

## Correlation with rollouts

The May 1 onset lines up with a cluster of heavy data rollouts dated **2026-05-01/02**. Check these runbooks first — one of them likely added a periodic or backfill workload that the cluster can't absorb:

- `runbook-ranked-baseline-fill-2026-05-02.md`
- `runbook-ranked-battle-history-rollout-2026-05-02.md`
- `runbook-battle-observation-floor-2026-05-02.md`
- `runbook-cache-capacity-expansion-2026-05-02.md`
- `runbook-post-rollout-followups-2026-05-01.md`

## Where to look in the codebase

Architecture: Django + Celery, Postgres + Redis + RabbitMQ, three Celery workers (`task-runner`, `task-runner-hydration`, `task-runner-background` in `docker-compose.yml:78,103,128`).

- **Beat schedule is DB-backed.** `django_celery_beat` is installed (`server/battlestats/settings.py:33`) with the DatabaseScheduler, so periodic cadence lives in the **`django_celery_beat_periodictask` table, not in code**. Dump it to map the 03:00 / 23:00 / overnight UTC clusters to specific tasks:
  ```sql
  SELECT p.name, p.task, p.enabled, c.minute, c.hour, c.day_of_week, i.every, i.period
  FROM django_celery_beat_periodictask p
  LEFT JOIN django_celery_beat_crontabschedule c ON p.crontab_id = c.id
  LEFT JOIN django_celery_beat_intervalschedule i ON p.interval_id = i.id
  WHERE p.enabled ORDER BY c.hour, c.minute;
  ```
- **Periodic/crawl tasks:** `server/warships/tasks.py` (40+ `@app.task`; note the `CRAWL_TASK_OPTS` clan-crawl tasks ~lines 1140–1394 and `queue='background'` tasks ~1467+). The crawlers and enrichment are the heaviest DB writers.
- **Warmers:** `server/scripts/warm_clan_tiers.py` and the clan-tier-distribution warm task (`tasks.py:1119`).
- **Backfill/bulk management commands** (likely culprits if one is on a schedule or was left running): `server/warships/management/commands/{backfill_battle_data,bulk_load_entity_caches,enrich_player_data,ensure_daily_battle_observations}.py`.
- **Client poll pressure:** `client/app/components/use{ClanHydrationPoll,ClanTiersDistribution,ClanMembers,IntervalRefresh}.ts` — interval-driven refetches that hit request-driven refresh paths; if any poll a heavy uncached endpoint, daytime CPU bumps trace here.
- **Prior related incidents/policy:** `runbook-celery-queue-strategy.md`, `runbook-periodic-task-topology-2026-04-11.md`, `runbook-incident-celery-zombie-worker-2026-04-12.md`, `runbook-daily-data-refresh-schedule-2026-04-05.md`.

## Suggested investigation order

1. **Confirm on the DO side.** Open the cluster CPU graph (link above), set range to last 30 days, eyeball whether spikes are spiky (periodic jobs) or a raised floor (query regression / under-provisioned). Cross-check the DO graph peaks against the hour histogram here.
2. **Find the expensive queries.** Enable/read `pg_stat_statements`:
   ```sql
   SELECT mean_exec_time, calls, total_exec_time, query
   FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 25;
   ```
   Also watch live during an episode: `SELECT pid, now()-query_start AS dur, state, query FROM pg_stat_activity WHERE state!='idle' ORDER BY dur DESC;`
3. **Map clusters → tasks.** Run the Beat-schedule query above; line up task `hour`/`minute` (UTC) against the 03:00 / 23:00 / overnight peaks.
4. **Diff against May 1.** Identify what the 2026-05-01/02 rollouts changed (new periodic task, widened query, larger scan). `git log --since=2026-04-28 --until=2026-05-02 -- server/warships/` is a fast start.
5. **Check missing indexes / seq scans** on the tables those rollouts touched (battle observations/events, ranked history, entity caches).
6. **Disk axis:** the May 21 disk alerts may be a separate growth problem (unbounded table/cache, retention gap) — confirm with table sizes; resolve independently of CPU.

## Mitigations (after diagnosis — do not apply blind)

- Throttle / re-stagger the offending Beat task off the peak windows, or move it to the `background` queue.
- Add the missing index / fix the regressed query.
- If genuinely capacity-bound, resize the cluster — but only after ruling out a single bad query (resizing masks a regression and keeps costing).

## Open questions

- Which specific periodic task (if any) owns the 03:00 and 23:00 UTC peaks?
- Is the 770-min May 3 episode a stuck/zombie worker (cf. `runbook-incident-celery-zombie-worker-2026-04-12.md`) rather than steady load?
- Are CPU and the new disk-utilization alerts the same root cause or two?
