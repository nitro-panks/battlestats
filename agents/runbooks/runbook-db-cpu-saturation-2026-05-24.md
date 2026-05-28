# Runbook: Managed Postgres CPU saturation (db-postgresql-nyc3-11231)

_Created: 2026-05-24_
_Context: The DigitalOcean managed Postgres cluster backing battlestats has been firing CPU-high monitoring alerts continuously since 2026-05-01. This runbook is the evidence log assembled from every DO alert email (April 1 → May 24), so the investigation can start from data rather than re-reading the inbox. The alert mail was being auto-trashed by the personal mail-tidy rules, so it is logged here before it ages out of Trash._
_Status: **IN PROGRESS.** Disk axis: root cause confirmed from code (unbounded, JSON-heavy `BattleObservation` capture with no retention — see [Findings](#findings-2026-05-24)). A compaction tool has been built and the manual prune ran on prod (~563K payloads cleared, ~15 GB freed to reusable space); the daily compaction Beat job is now **ENABLED on prod**, and a one-time `VACUUM FULL` reclaimed ~15 GB to the OS (see updates below). CPU axis: **root cause confirmed via `pg_stat_statements`** — NOT the same as the disk axis. The CPU hog is `score_best_clans()` (3 heavy aggregates) recomputed ~2,180×/day from **request-driven landing Best-mode cache misses with no single-flight lock**, not the observation writes. See [Findings — CPU axis](#findings--cpu-axis-2026-05-24-later--confirmed-via-pg_stat_statements). **Acute (resolved):** on 2026-05-24 the cluster went read-only (disk full), which broke a backend deploy mid-flight; storage was resized 40 GB → 60 GB to clear the read-only lock and restore writes._

_**Update 2026-05-24 (21:10 UTC):** Step 4 of the operating sequence executed — `BATTLE_OBSERVATION_COMPACT_ENABLED=1` appended to `/etc/battlestats-server.env` (backup `.bak.2026-05-24`), `migrate` re-registered the Beat entry as `enabled=True` (cron 12:30 UTC daily), `battlestats-beat` + `battlestats-celery-background` restarted and confirmed carrying the env var. First scheduled fire: next 12:30 UTC._

_**Update 2026-05-24 (21:40 UTC) — on-disk reclaim done via `VACUUM FULL`, not `pg_repack`:** After compaction, `warships_battleobservation` was **22 GB total / 416 MB live heap** — i.e. ~22 GB of dead TOAST pages that autovacuum had already cleaned to `n_dead_tup=0` but couldn't return to the OS. Because the **live** data was tiny, the long-online-lock rationale for `pg_repack` didn't apply (and the server is **PG 18.3**, so the apt `postgresql-16-repack` 1.5.0 client wouldn't match the 1.5.2 extension anyway). Ran `SET lock_timeout='2min'; SET statement_timeout='20min'; VACUUM (FULL, VERBOSE) warships_battleobservation;` as `doadmin` — completed in **9m 10s**, table **22 GB → 7.0 GB** (heap 179 MB + ~6.85 GB live TOAST for kept observations), **`defaultdb` 37 GB → 22 GB (~15 GB returned to the OS)**. Captures resumed immediately after the brief `ACCESS EXCLUSIVE` lock released (no lost work — `acks_late=True`). Note: DO managed Postgres **cannot shrink storage** (scale-up only), so this is OS-headroom hygiene, not a path to a 60→40 GB downsize. **Still pending:** investigate `warships_player` (~11 GB, 8.4 GB TOAST, ~80k dead tuples) as the next compaction target — needs its own analysis of which JSON columns are the culprit._

## Findings (2026-05-24)

Triggered by an unrelated incident: a backend deploy aborted on `cannot execute SELECT FOR UPDATE in a read-only transaction`, and the app log showed `ReadOnlySqlTransaction: cannot execute UPDATE`. The cluster is **read-only**, which on DO managed Postgres is the disk-exhaustion protection mode — i.e. the May 21 disk alerts in this runbook culminated in a full disk.

**Confirmed root cause of the disk axis (code-verified, not yet DB-measured):**

- `BattleObservation` (`server/warships/models.py:468`) stores a full per-ship `ships_stats_json` blob **plus** a `ranked_ships_stats_json` blob per row; `observed_at = auto_now_add` (append-only).
- The 2026-05-01/02 battle-history rollout writes a row on **every** visit-driven refresh, clan-crawl refresh, and the 6-hourly observation floor — and ranked capture (a *second* large JSON blob) is on for **all three realms** (`BATTLE_HISTORY_RANKED_CAPTURE_REALMS=na,eu,asia`).
- There is **no retention/prune** anywhere for `BattleObservation` or `BattleEvent` (grep confirmed; the only cleanup job targets `EntityVisitEvents`). One sampled player (`Punkhunter25`) had **439 observations × ~327-ship JSON**.

That is the disk filler, and the onset (2026-05-01) + the scheduled-hour CPU peaks both line up with this workload.

**Hard constraint discovered — compact, do not delete:** `BattleEvent.from_observation` and `to_observation` are **`on_delete=CASCADE`** FKs (`server/warships/models.py`), and the event uniqueness constraints are keyed on the observation pair. Deleting observation rows would cascade-delete the durable per-battle `BattleEvent` record that powers the charts. So the reclaim strategy is to **NULL the JSON payloads** on stale observations while keeping the rows.

## Findings — CPU axis (2026-05-24, later — confirmed via `pg_stat_statements`)

Enabled `pg_stat_statements` (`CREATE EXTENSION`; the library was already in DO's `shared_preload_libraries`, so counters span back to `stats_reset = 2026-05-19`, a **5.7-day window**). Live `pg_stat_activity` during an episode showed **multiple concurrent copies** of the same clan-scoring queries, each `active` 25–35s on `DataFileRead` (cold-buffer scans).

**Top queries by `total_exec_time` (5.7-day window):**

| calls | total | mean | source |
|---|---|---|---|
| 12,443 | 64 h | **18.5 s** | `score_best_clans()` candidate hard-filter (`data.py:5564`) — `clan LEFT JOIN player`, `COUNT(player) FILTER` grouped per clan |
| 12,439 | 58 h | **16.8 s** | `score_best_clans()` `member_stats` `AVG(player_score…)` (`data.py:5600`) |
| 12,432 | 49 h | **14 s** | `score_best_clans()` `cb_recency` `MAX(clan_battle_summary_updated_at)` (`data.py:5618`) |
| 6,531 | 54 h | **30 s** | `PlayerDailyShipStats` SUM — battle-history feature (separate; set aside) |

**Root cause (CPU axis ≠ disk axis — answers Open question #3: they are TWO different problems):**

- The three `score_best_clans()` queries run **~12,440 times in 5.7 days ≈ 2,180/day (~1.5/min)** each — an order of magnitude above the warmer-only baseline (~170–300/day from the 55-min landing warmer + 12-h bulk loader). **So the load is request-driven, not periodic warmers.**
- `_build_best_landing_clans()` (`landing.py:812`) calls `score_best_clans()` **live on every landing Best-mode cache miss** (`landing.py:867`) with **no single-flight lock** — concurrent homepage visits during a cold/dirty cache window each recompute the full ~49s-of-CPU trio over the 11 GB `player` + `playerexplorersummary` tables. Classic cache stampede on an expensive uncached computation, across 2 sorts × 3 realms = 6 keys.
- `EXPLAIN (ANALYZE)` of the worst query (#1) shows it is **already index-driven** (bitmap scan on `clan.realm`, nested-loop index scan on `warships_player(clan_id)`), reading ~573 MB buffers. **No missing index** — the 18.5s mean is contention + cold reads from being run constantly, not a bad plan. So the fix is **frequency reduction (cache/lock the scoring), not indexing.**
- Onset (2026-05-01) aligns with the best-clan landing / cache-capacity rollout that put `score_best_clans()` on the request path.

**Primary driver — CONFIRMED `LANDING_CLANS_DIRTY_KEY` thrashing (not user traffic):**

- Every clan write calls `invalidate_landing_clan_caches()` → `_mark_cache_family_dirty()` (sets the dirty key with **`timeout=None`** — no expiry, cleared only when a warm publishes, `landing.py:603`) **and** `_queue_landing_republish()` → `queue_landing_page_warm()`. Both clan-write paths do this: `update_clan_data()` (`data.py:4733`) and `refresh_clan_cached_aggregates()` (`data.py:4781`), driven by the multi-day clan crawl + per-clan refreshes.
- While dirty, every landing Best-mode request serves the published fallback but **re-queues a republish warm** (`_get_cached_landing_payload_with_fallback`, `landing.py:649`). Each warm recomputes `score_best_clans()` from scratch (×2 sorts per realm).
- **Live confirmation (2026-05-24 ~21:40 UTC):** NA dirty key present with `ttl=-1`; **8 `warm_landing_page_content_task` receipts in 60 min** (≈ every 7.5 min vs the nominal 55-min cadence); **two warms ran concurrently** (received 16s apart, both completed) — the dedup lock is **not** effectively throttling; each warm took **346–423 s** (vs 66 s when uncontended) — a self-reinforcing feedback loop (saturation → slow warms → dirty stays set → more warms queued → more overlap).

**Fix direction, in leverage order:**
1. ✅ **DONE + DEPLOYED (2026-05-24 22:03 UTC, release `20260524180220`, branch `perf/best-clans-scoring-cache-2026-05-24` / commit `6f5f45f`): Cache `score_best_clans()` output under its own multi-hour TTL.** Read-through cache keyed on `(realm, sort)` (independent of `limit` — it only slices the tail, so all callers share one computation), `SCORE_BEST_CLANS_CACHE_TTL` env-configurable (default 3h), in `data.py`. **Intentionally NOT wired to the dirty-key invalidation** (that was the storm). Even with warms firing every few minutes, they now reuse the cached ranking instead of rescanning the 11 GB tables. Test: `test_score_best_clans_caches_full_ranking_until_ttl` in `test_landing.py`; full 244-test backend release gate passes. **Prod verification (immediately post-deploy):** invoking `score_best_clans(realm='na', sort='overall')` twice measured **31.03 s (cold) → 0.002 s (cached)**, identical results — confirms the cache is live and effective. **Still watch over ~24h:** (a) the three `score_best_clans` `calls` counters in `pg_stat_statements` stop accelerating; (b) `warm_landing_page_content_task` durations drop from 346–423s toward 10–30s; (c) DO CPU alert volume falls. If a new query (e.g. `_attach_clan_battle_activity_badges`, `landing.py:839`, not yet profiled) bubbles to the top, that's the next target.
2. **Stop per-clan-write invalidation of the Best-clan payload (or debounce it).** A single clan refresh barely moves the realm-wide ranking; the 6 h TTL + 55-min warmer already keep it fresh. Either drop the `invalidate_landing_clan_caches()` call from the hot clan-write paths, or give the dirty key a short TTL so a burst of crawl writes coalesces into one warm instead of keeping it perpetually dirty.
3. **Fix the warm dedup** so `warm_landing_page_content_task` can't run concurrently / pile up (observed 8/hr, overlapping). A working single-flight lock here caps the warm cost regardless of how often republish is queued.
4. (Separate) the `PlayerDailyShipStats` SUM (#3, mean 30s) is battle-history — its own analysis later.

## Remediation built (disabled by default)

A compaction tool ships alongside this runbook (does **not** auto-run):

- **`compact_battle_observation_payloads()`** — `server/warships/incremental_battles.py`. NULLs `ships_stats_json` / `ranked_ships_stats_json` on observations outside the per-player keep set; batched, dry-run aware. Keep set = latest N per player (random diff baseline) ∪ latest non-NULL-ranked per player (ranked walk-back baseline). Verified safe for the scheduled diff path (`record_observation_from_payloads`, `_hydrate_previous_ranked_snapshot`, `_ranked_observation_needs_refresh`); the manual baseline-seeding commands have a documented caveat below.
- **`prune_battle_observations`** management command — `--dry-run` (default first step) reports candidate count + reclaimable bytes (Postgres `pg_column_size`); live mode clears in `--batch-size` chunks with `--sleep` pacing and `--max-rows` cap.
- **`prune_battle_observations_task`** + Beat entry `prune-battle-observations` — daily at **12:30 UTC** (the histogram's quietest hour, clear of the 03:00/23:00 CPU peaks), **gated off by `BATTLE_OBSERVATION_COMPACT_ENABLED=0`**.

### Operating sequence (once the DB is writable)

1. **Resize storage first.** The prune is a write; it cannot run while the DB is read-only. Bump the managed-DB disk (DO dashboard or `doctl databases resize`) to clear the read-only lock and restore the API.
2. **Dry-run:** `python manage.py prune_battle_observations --dry-run` — confirm candidate count + reclaim estimate.
3. **Live, gently:** `python manage.py prune_battle_observations --batch-size 2000 --sleep 0.5 --max-rows 200000`, repeat until the dry-run reports ~0 candidates. (`VACUUM`/autovacuum then returns the space to reusable; growth halts because new rows reuse it.)
4. **Enable the schedule:** set `BATTLE_OBSERVATION_COMPACT_ENABLED=1` so the daily job keeps it compacted. ✅ **Done 2026-05-24 21:10 UTC** — env var added, `migrate` flipped the Beat entry to `enabled=True`, beat + background worker restarted and verified. Daily fire at 12:30 UTC.

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

- ~~Are CPU and the new disk-utilization alerts the same root cause or two?~~ **ANSWERED: two.** Disk = unbounded `BattleObservation` JSON (fixed: compaction + VACUUM FULL). CPU = `score_best_clans()` request-driven cache-miss stampede (fix not yet implemented — see [Findings — CPU axis](#findings--cpu-axis-2026-05-24-later--confirmed-via-pg_stat_statements)).
- **Still open:** does `score_best_clans()`'s ~2,180/day call rate trace to genuine user traffic on the Best-clan homepage, or to `LANDING_CLANS_DIRTY_KEY` thrashing forcing recompute on most requests? Confirm before/while implementing the cache fix.
- Which specific periodic task (if any) owns the 03:00 and 23:00 UTC peaks? (The crawl at `CLAN_CRAWL_SCHEDULE_HOUR=3` and overnight rollups remain candidates for a *second* CPU contributor distinct from the score_best_clans daytime load — verify by sampling `pg_stat_activity` during a 03:00 episode.)
- Is the 770-min May 3 episode a stuck/zombie worker (cf. `runbook-incident-celery-zombie-worker-2026-04-12.md`) rather than steady load?

## Recurrence + fix — 2026-05-27 (the recent-players republish leg)

A fresh "CPU 90%+ since ~23:00 UTC" report. Re-diagnosed from the cluster directly (DO Prometheus metrics endpoint — basic-auth creds from `GET /v2/databases/{id}/metrics/credentials`, scrape `https://<db-host>:9273/metrics`):

- **CONFIRMED the saturation this time** (the 2026-05-24 number was trusted from the user): instantaneous CPU was only ~22% busy when sampled, but **`system_load1=1.27 / load5=3.22 / load15=4.38` on a 1-vCPU node** — run queue ~4 deep, i.e. genuine 15-min saturation that was already receding. Mem 60%, disk 38% — both fine. `pg_stat_activity` showed **0 active client backends** across 6 samples → the load was bursty periodic work between samples, not one long query (the trap that nearly sent this down the "crawl writes are the driver" path — they are not).
- **Driver: the landing republish warm storm via the recent-players leg.** `warm_landing_page_content_task` ran **~20×/40min (~every 2 min) at 13–83s each**. The multi-day ASIA `crawl_all_clans_task` re-dirties the landing clan cache continuously; the 120s `LANDING_REPUBLISH_COOLDOWN_SECONDS` floor let a republish through every ~2 min, and each one ran `warm_landing_page_content(force_refresh=True, include_recent=True)` — **force-rebuilding the recent-*players* 7-day rollup (the 25s `week_battles` `PlayerDailyShipStats` SUM) even though only clan data changed.** ~20 rebuilds/40min on one core ≈ the saturation. At 23:00 the ASIA periodic cluster (recent-players warmer; observation-floor → 9 `update_battle_data`; ranked refresh) stacked on top and tipped it >90%.
- This is exactly **fix-direction #2/#3** from the CPU-axis findings above, never shipped in the 2026-05-25 remediation (only #1, the `score_best_clans` cache, landed — and it held: that fingerprint's calls dropped 12,443→1,707).
- **Red herring ruled out:** `enrich_player_data_task` was firing ~1,190/hr (loud in logs) — the known defer-before-lock fan-out during a crawl (`background-unacked-enrichment-deferral`). It is Redis-only, ~3ms/call, **near-zero DB cost** — not the CPU driver. Separate queue-hygiene bug (defer block at `tasks.py` runs before the lock); track as its own PR.

**Fix shipped (v1.13.2):**
1. `queue_landing_page_warm(realm, include_recent=True)` is now parameterized; `_queue_landing_republish` calls it with **`include_recent=False`** (`tasks.py` / `landing.py`). Clan/player writes no longer rebuild the recent surfaces — those are owned by the dedicated beat warmers (`recent-players-warmer-{realm}` every 3h) and the 55-min landing warmer (which dispatches the task directly with `include_recent=True`, unaffected).
2. `LANDING_REPUBLISH_COOLDOWN_SECONDS` default **120 → 600** (and set durably in `server/.env.cloud`) — caps crawl-driven republishes at ~6/hr.
3. Tests: 4 invalidation/fallback dispatch assertions updated to `include_recent=False`; new `test_queue_landing_republish_excludes_recent_surfaces`. Full backend release gate (251 tests) green.

**Follow-ups — addressed 2026-05-28 (v1.13.3, one branch / three commits):**
- ✅ **Item 1 — post-deploy cold-cache spike.** After a worker restart the 3h `score_best_clans` cache goes cold and multiple `(realm,sort)` keys recompute *concurrently* (single-flight is per-key), spiking the 1-vCPU DB to load ~8 (seen right after the v1.13.2 deploy). Fix: force `WARM_CACHES_ON_STARTUP=1` in the deploy/bootstrap scripts (`set_env_value`, since `migrate_env_value` preserved the on-host `0`) so the background worker pre-warms those rankings *sequentially* before request traffic. Safe now: droplet is 7.8 GB + 2 GB swap (the 2026-03-30 OOM was at 3.8 GB) and the warm is Celery-dispatched since v1.2.14.
- ✅ **Item 2b — clan/player-scoped republish.** `warm_landing_page_content(scope=…)` now narrows both the rebuilt surfaces and the dirty keys it clears; clan writes warm only clan surfaces (no more wasteful `players_best_*`/`players_popular` rebuild), player writes only player surfaces. Periodic/startup warmers keep `scope='all'`.
- ✅ **Item 3 — enrich defer-before-lock fan-out.** Lock acquired before the crawl check; deferrals no longer re-enqueue (the 15-min Beat kickstart is the retry), so the ~1,190/hr chain churn can't accumulate. Defer path never runs the heavy `_maybe_redispatch_enrichment` candidate scan.
- ✅ **Item 2a — resized 1→2 vCPU (2026-05-28).** Initially deferred ("watch first"), then the user upsized the cluster to **Basic / 2 vCPU / 4 GB RAM / connection limit 97** (`db-amd-1vcpu-2gb` → 2-vCPU). The v1.13.3 code/config fixes (items 1/2b/3) still stand — they cut wasteful recompute/fan-out regardless of core count; the resize adds headroom so a startup-warm + crawl overlap no longer pins the single core. Managed-PG resize is an online provision-then-failover (brief connection blip handled by `CONN_HEALTH_CHECKS`); the DO API showed `status: resizing` for ~13.5 min then cut over to `db-s-2vcpu-4gb` / `system_n_cpus=2` with the site serving HTTP 200 throughout. **Post-cutover verification (2-vCPU node, crawl + warmers running): CPU idle 84%, load1 2.08 (~1.0/core), mem 47%** — healthy headroom vs the load 4–8 that pinned the single core. Celery concurrency (`CELERY_*_CONCURRENCY=3`) and `ANALYTICAL_WORK_MEM=8MB` were sized for the 1-vCPU/2-GB box and could be revisited for the larger node, but the 97-connection limit is the binding constraint — leave concurrency as-is unless a new bottleneck appears.
