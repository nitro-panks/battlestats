# Runbook: Enrichment Crawler Progress Log

Created: 2026-04-03
Status: **Active** — enrichment runs on the droplet's Celery `background` worker via `warships.tasks.enrich_player_data_task`, re-seeded by the `player-enrichment-kickstart` Beat schedule. This runbook tracks the live progress of the enrichment pass.

## 2026-04-08 — Reverted from DO Functions

The migration to `functions/enrichment/enrich-batch` was reverted. DO Functions egress from a rotating IP pool that cannot be whitelisted by the Wargaming `application_id`, so every invocation failed with `407 INVALID_IP_ADDRESS`. Silent degradation ran from 2026-04-06 05:00 UTC to 2026-04-08 03:00 UTC. See `archive/spec-serverless-background-workers-2026-04-04.md` for the post-mortem. The crawler is now back on the droplet (where the throughput in batches 6–47 below originally came from) and resumed via `enrich_player_data_task.apply_async(queue='background')` followed by the Beat kickstart.

**Secondary incident during recovery:** the post-revert `deploy_to_droplet.sh` run left the `battlestats-celery-background` worker booted against an `/etc/battlestats-server.env` snapshot missing `CELERY_BROKER_URL`. The worker fell through to a broken `settings.py` fallback default (`amqp://rabbitmq:5672//` — the docker-compose service hostname, unreachable on the droplet) and entered an infinite DNS-failure reconnect loop. Fixed by a manual `systemctl restart battlestats-celery-background` after the env file was finalized. Two hardening changes landed in the same commit as this note:

1. `server/battlestats/settings.py` — `CELERY_BROKER_URL` now raises `ImproperlyConfigured` when unset in production (non-docker, non-debug, non-test), so a misconfigured worker fails fast instead of looping silently. Docker default is explicit; local dev default is `localhost`.
2. `server/deploy/deploy_to_droplet.sh` — (a) stops all app services at the top of the remote block before any env mutation (eliminates the inode-race between `sed -i` and systemd's `EnvironmentFile` reads), and (b) extends `verify_broker_connection` to read `/proc/<pid>/environ` for each celery worker and fail the deploy if `CELERY_BROKER_URL` is missing. This is the canary that would have caught the original incident at deploy time.

## Purpose

Track the progress of the enrichment pass, which populates `PlayerExplorerSummary` rows with efficiency rank, ranked league, clan battle stats, and other derived metrics for the full player population.

## Crawler Configuration

The configuration below describes the original Celery implementation that produced batches 1-47. The current operator path is the DO Function described later in this runbook.

| Parameter                | Value                         |
| ------------------------ | ----------------------------- |
| Batch size               | 500 players (250 NA + 250 EU) |
| Self-chaining delay      | 10s between batches           |
| Min PvP battles filter   | 500                           |
| Min WR filter            | 48.0%                         |
| Queue                    | `background` (`-c 2`)         |
| Estimated population     | ~194K eligible players        |
| Estimated full-pass time | ~4.5 days at steady state     |

## Timeline

### Phase 1: Initial Launch and Block (2026-04-03 01:52 - 03:04 UTC)

The enrichment crawler was dispatched and completed **5 batches** (2,500 players) before stalling.

| Batch | Timestamp (UTC) | Enriched | Errors |
| ----- | --------------- | -------- | ------ |
| 1     | 01:52:26        | 500      | 0      |
| 2     | 02:08:57        | 500      | 0      |
| 3     | 02:26:04        | 500      | 0      |
| 4     | 02:44:23        | 500      | 0      |
| 5     | 03:03:32        | 500      | 0      |

**Blocked ~03:04 UTC.** The EU clan crawl held a Redis mutual-exclusion lock, preventing the enrichment crawler from self-chaining. The crawler was stuck for approximately **11 hours**.

### Phase 2: Unblock and Prioritization (2026-04-03 ~14:00 UTC)

Actions taken to unblock:

1. Cleared Redis crawl locks (`crawl_all_clans_lock`, `enrichment_crawl_lock`)
2. Disabled periodic clan crawl tasks in django-celery-beat
3. Restarted the `battlestats-celery-background` worker to kill the in-progress clan crawl
4. Purged the `background` queue of stale tasks
5. Dispatched a fresh `enrich_player_data_task`

**Decision:** All other crawlers (clan crawl, clan crawl watchdog) halted until the enrichment pass completes. Estimated several days.

### Phase 3: Steady-State Running (2026-04-03 14:13 UTC - ongoing)

Crawler resumed and has been running continuously with zero errors. Batch interval is approximately **17-20 minutes** depending on WG API latency and concurrent warmer tasks.

| Batch | Timestamp (UTC) | Enriched | Errors | Note                                                               |
| ----- | --------------- | -------- | ------ | ------------------------------------------------------------------ |
| 6     | 14:13:37        | 500      | 0      | First batch after unblock                                          |
| 7     | 14:31:45        | 500      | 0      |                                                                    |
| 8     | 14:49:42        | 500      | 0      |                                                                    |
| 9     | 15:09:51        | 500      | 0      |                                                                    |
| 10    | 15:29:40        | 500      | 0      |                                                                    |
| 11    | 15:47:31        | 500      | 0      |                                                                    |
| 12    | 16:05:30        | 500      | 0      |                                                                    |
| 13    | 16:22:56        | 500      | 0      |                                                                    |
| 14    | 16:40:23        | 500      | 0      |                                                                    |
| 15    | 16:58:08        | 500      | 0      |                                                                    |
| 16    | 17:15:58        | 500      | 0      |                                                                    |
| 17    | 17:33:02        | 500      | 0      |                                                                    |
| 18    | 17:50:49        | 500      | 0      |                                                                    |
| 19    | 18:09:17        | 500      | 0      |                                                                    |
| 20    | 18:28:33        | 500      | 0      |                                                                    |
| 21    | 18:46:26        | 500      | 0      |                                                                    |
| 22    | 19:03:14        | 500      | 0      |                                                                    |
| 23    | 19:20:13        | 500      | 0      |                                                                    |
| 24    | 19:37:24        | 500      | 0      |                                                                    |
| 25    | 19:56:05        | 500      | 0      |                                                                    |
| 26    | 20:14:13        | 500      | 0      |                                                                    |
| 27    | 20:31:38        | 500      | 0      |                                                                    |
| 28    | 20:49:21        | 500      | 0      |                                                                    |
| 29    | 21:06:40        | 500      | 0      |                                                                    |
| 30    | 21:23:45        | 500      | 0      |                                                                    |
| 31    | 21:42:53        | 500      | 0      |                                                                    |
| 32    | 22:19:01        | 500      | 0      | Gap: v1.6.3 deploy restarted workers                               |
| 33    | 22:43:23        | 500      | 0      |                                                                    |
| 34    | 23:14:19        | 500      | 0      | Startup warmers competed for worker slots                          |
| 35    | 23:37:57        | 500      | 0      |                                                                    |
| 36    | 23:54:19        | 500      | 0      |                                                                    |
| 37    | 00:12:11        | 500      | 0      | 2026-04-04                                                         |
| 38    | 00:28:52        | 500      | 0      |                                                                    |
| 39    | 00:45:17        | 500      | 0      |                                                                    |
| 40    | 01:02:06        | 500      | 0      |                                                                    |
| 41    | 01:21:01        | 500      | 0      |                                                                    |
| 42    | 01:53:40        | 500      | 0      |                                                                    |
| 43    | 02:11:57        | 500      | 0      |                                                                    |
| 44    | 02:48:15        | 500      | 0      |                                                                    |
| 45    | 03:13:31        | 500      | 0      | Clan crawl re-emerged after this batch                             |
| 46    | 04:29:40        | 500      | 0      | First batch after second fix. 76 min gap from disruption #4        |
| 47    | 12:41:21        | 500      | 0      | Only batch in 8h. Warmers starved background queue (disruption #5) |

## Running Totals

| Metric                           | Value                                                     |
| -------------------------------- | --------------------------------------------------------- |
| Total players enriched (NA)      | 56,688 / 275,989 (20.5%)                                  |
| Total players enriched (EU)      | 38,227 / 471,508 (8.1%)                                   |
| Total errors                     | 0                                                         |
| Error rate                       | 0%                                                        |
| **Celery era** (batches 1-47)    | 23,500 enriched over ~27h (degraded by stalls/starvation) |
| **DO Functions era** (batch 48+) | 71,415+ enriched, 0 errors, ~10k/hr steady                |
| Current throughput               | ~10,000 players/hour (DO Functions)                       |
| Estimated NA completion          | Passed — NA enrichment continued past original 9.5% when EU was added |

_Last updated: 2026-04-05. Both realms are being enriched concurrently via DO Functions cron._

## Check-In Summary

| Check-In | Time (approx)         | Batches Since Prior | Cumulative | Status                                                                                                                                        |
| -------- | --------------------- | ------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 1        | 2026-04-03 ~03:00 UTC | 5                   | 5          | Running, then blocked by clan crawl lock                                                                                                      |
| 2        | 2026-04-03 ~14:00 UTC | 0                   | 5          | Stalled 11h; unblocked, other crawlers halted                                                                                                 |
| 3        | 2026-04-03 ~18:55 UTC | 26                  | 31         | Healthy, steady ~17min cadence, 0 errors                                                                                                      |
| 4        | 2026-04-04 ~03:15 UTC | 14                  | 45         | Healthy, steady, 0 errors. Deploy restart caused brief gap                                                                                    |
| 5        | 2026-04-04 ~04:15 UTC | 0                   | 45         | Stalled again — clan crawl re-emerged. Fixed, enrichment re-dispatched                                                                        |
| 6        | 2026-04-04 ~04:40 UTC | 1                   | 46         | Running, mid-batch (332/500). Memory peak 1.0G, swap 493M peak. Throughput degraded to ~1,213/hr (was ~1,760) due to disruptions in 6h window |
| 7        | 2026-04-04 ~15:37 UTC | 1                   | 47         | Stalled ~3h. 13 worker restarts. 140 queued tasks (warmers). Purged queue, re-dispatched                                                      |

## Disruptions

1. **11-hour stall (03:04 - 14:13 UTC Apr 3):** EU clan crawl lock blocked self-chaining. Root cause: mutual exclusion between clan crawl and enrichment task. Fixed by killing clan crawl and disabling periodic schedule.
2. **~36 min gap at 22:19 UTC Apr 3:** v1.6.3 deploy restarted all Celery workers. Crawler auto-resumed after startup warmers settled.
3. **Wider intervals after 01:00 UTC Apr 4:** Background queue occasionally contested by periodic warmers (landing page, hot entity). No data loss, just slower cadence.
4. **Second stall (03:13 - 04:13 UTC Apr 4):** EU clan crawl re-emerged despite earlier disabling. Root cause: `post_migrate` signal re-registers all periodic tasks on worker restart, and deploy restarts trigger this. The clan crawl task (`daily-clan-crawl-eu`) was re-enabled automatically. The crawl consumed a background worker slot and eventually caused an OOM (899MB peak + 96MB swap → SIGTERM at 04:03). After the OOM restart, RabbitMQ re-delivered the unacknowledged crawl task, starting a new crawl immediately. Fix: stopped worker, purged `background` queue via `rabbitmqctl purge_queue background`, cleared Redis crawl locks, restarted worker, disabled all 4 crawl periodic tasks (`daily-clan-crawl-eu`, `daily-clan-crawl-na`, `clan-crawl-watchdog-eu`, `clan-crawl-watchdog-na`) via Django ORM, dispatched fresh enrichment task.

5. **Warmer queue starvation (04:29 - 15:37 UTC Apr 4):** After batch 47 completed at ~04:29, the enrichment self-chained but the background queue accumulated 140 pending tasks — mostly `warm_landing_page_content_task` and `warm_hot_entity_caches_task` from the 55-min periodic schedule. Each warmer does full-table distribution/correlation scans over ~193K players, takes ~5 minutes, and pushes worker memory past the `max-memory-per-child` threshold (768MB). This triggers worker recycling (`max-tasks-per-child` or memory limit), which kills any in-flight enrichment task. The worker restarted 13 times during this period. The enrichment task kept getting dispatched but was never picked up because warmers consumed all available slots. **CPU pegged at 100%** during this period. Fix: purged 140 queued tasks via `rabbitmqctl purge_queue background`, re-dispatched enrichment. The warmers will naturally re-queue on the next periodic beat cycle.

### Lesson: warmers starve the enrichment crawler

The landing page warmer runs every 55 minutes per realm (NA + EU = 2 invocations per cycle). Each invocation now runs `score_best_clans()` 3 times (overall, wr, cb) plus distributions and correlations — ~5 min total, peaking at 1G+ memory. With `max-memory-per-child=786432` (768MB), each warmer run triggers a worker restart. On restart, Celery re-registers tasks and the beat scheduler immediately dispatches the next warmer. Result: the 2-concurrency background worker spends 100% of its time on warmers, and enrichment tasks rot in the queue.

**Mitigation options:**

- Increase `max-memory-per-child` to 1.5G to avoid warmer-triggered restarts
- Move warmers to a dedicated queue/worker so they don't compete with enrichment
- Reduce warmer frequency while enrichment is running (e.g. every 2h instead of 55min)
- Add task priority so enrichment preempts warmers

### Lesson: `post_migrate` signal re-enables crawl tasks on deploy

The `signals.py` module registers all Celery Beat periodic tasks via `@receiver(post_migrate)`. Every deploy runs `manage.py migrate`, which triggers the signal, which re-creates the periodic task entries with `enabled=True`. This silently undoes manual disabling of crawl tasks.

**Mitigation options:**

- Add an env var (`ENABLE_CLAN_CRAWL_SCHEDULES=0`) and gate registration in `signals.py`
- Manually re-disable crawl tasks after every deploy while enrichment is running
- Accept the risk and monitor after each deploy

## Monitoring Script

```bash
./server/scripts/check_enrichment_crawler.sh [host]
# Default host: battlestats.online
```

Single SSH call, dumps journal once, reports: worker health (memory/swap/CPU/uptime/OOM risk), lock status, batch history, throughput + ETA, errors (enrichment/WorkerLost/SIGTERM/SIGKILL), live progress, clan crawl interference, and periodic task state.

## NA-Only Mode (activated 2026-04-04 16:03 UTC)

To eliminate warmer starvation and halve the enrichment timeline, the crawler was switched to NA-only mode with all periodic tasks suspended.

### Changes applied

1. **`ENRICH_REALMS=na`** added to `/etc/battlestats-server.env` on the droplet (systemd env source for Celery workers). The task reads this env var and passes `realms=('na',)` to `enrich_players()`. All 500 batch slots now go to NA candidates.
2. **Code change:** `server/warships/tasks.py` — `enrich_player_data_task` now reads `ENRICH_REALMS` env var (comma-separated realm list). Empty or unset means all realms (original behavior).
3. **All 23 periodic tasks suspended** in django-celery-beat (see full list below).
4. **Background queue purged** and worker restarted.

### Suspended periodic tasks (re-enable after NA enrichment completes)

| Task                               | Original Schedule | Queue   |
| ---------------------------------- | ----------------- | ------- |
| `bulk-entity-cache-loader-eu`      | every 12h         | default |
| `bulk-entity-cache-loader-na`      | every 12h         | default |
| `celery.backend_cleanup`           | daily             | default |
| `clan-battle-summary-warmer`       | periodic          | default |
| `clan-crawl-watchdog-eu`           | periodic          | default |
| `clan-crawl-watchdog-na`           | periodic          | default |
| `daily-clan-crawl-eu`              | daily             | default |
| `daily-clan-crawl-na`              | daily             | default |
| `daily-clan-tier-dist-warmer-eu`   | daily             | default |
| `daily-clan-tier-dist-warmer-na`   | daily             | default |
| `daily-ranked-incrementals-eu`     | daily             | default |
| `daily-ranked-incrementals-na`     | daily             | default |
| `hot-entity-cache-warmer-eu`       | every 30m         | default |
| `hot-entity-cache-warmer-na`       | every 30m         | default |
| `incremental-player-refresh-am-eu` | daily (AM)        | default |
| `incremental-player-refresh-am-na` | daily (AM)        | default |
| `incremental-player-refresh-pm-eu` | daily (PM)        | default |
| `incremental-player-refresh-pm-na` | daily (PM)        | default |
| `landing-page-warmer-eu`           | every 55m         | default |
| `landing-page-warmer-na`           | every 55m         | default |
| `player-enrichment-kickstart`      | periodic          | default |
| `recently-viewed-player-warmer-eu` | periodic          | default |
| `recently-viewed-player-warmer-na` | periodic          | default |

**Note:** `post_migrate` signals on deploy will re-enable these. After any deploy while in NA-only mode, re-run the suspension script or manually disable in django-celery-beat.

### Impact of suspension

- **Landing page data** will become stale (cached payloads served from last warmer run, no refresh)
- **Hot entity caches** will not refresh (player/clan detail pages may serve older data)
- **Ranked incrementals** paused (ranked data won't update for existing players)
- **Bulk entity cache** won't pre-load
- **Backend cleanup** paused (Celery result backend won't be cleaned — low risk)

All of this is acceptable for the enrichment priority period. Cached data remains served; it just won't be refreshed.

## Re-enablement Plan

### Phase 1: After NA enrichment completes

1. Set `ENRICH_REALMS=eu` in `/etc/battlestats-server.env`
2. Restart background worker
3. Optionally re-enable a subset of warmers (landing-page-warmer-na, hot-entity-cache-warmer-na) to keep NA data fresh
4. Let EU enrichment run to completion

### Phase 2: After all enrichment completes

1. Remove `ENRICH_REALMS` from `/etc/battlestats-server.env` (or set to empty)
2. Re-enable ALL 23 periodic tasks in django-celery-beat:

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  source /opt/battlestats-server/venv/bin/activate && \
  set -a && source .env && source .env.secrets && set +a && \
  python -c \"
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'battlestats.settings'
django.setup()
from django_celery_beat.models import PeriodicTask
count = PeriodicTask.objects.all().update(enabled=True)
print(f'Re-enabled {count} periodic tasks')
\""
```

3. Restart background worker
4. Verify warmers and crawlers resume normally

## Migration to DO Functions (2026-04-04 ~17:00 UTC)

Enrichment processing migrated from the Celery background worker to a DigitalOcean Function (`enrichment/enrich-batch`). The Celery-based `enrich_player_data_task` is no longer running.

### Key changes

- **Function**: boots Django, loops through 500-player batches for up to ~14 min per invocation
- **Scheduling**: droplet crontab `*/15 * * * *` invokes via `doctl serverless functions invoke --no-wait`
- **Concurrency guard**: lock file at `/tmp/enrichment-invoke.lock` with 780s TTL
- **Throughput**: ~10,465 players/hour (vs ~888/hr degraded on Celery, or ~1,760/hr steady state)
- **Cost**: ~$0.016 per invocation (~$0.006/1000 players)

### Validation results

| Invocation | Batches | Enriched | Errors | Elapsed |
| ---------- | ------- | -------- | ------ | ------- |
| Manual #1  | 5       | 2,500    | 0      | 860.9s  |
| Manual #2  | 5       | 2,500    | 0      | 835.8s  |
| Cron #1    | 5       | 2,500    | 0      | 857.3s  |
| Cron #2    | 5       | 2,500    | 0      | 845.5s  |

See `agents/runbooks/spec-serverless-background-workers-2026-04-04.md` for the full architecture spec.

### Steady-state performance (2026-04-04 18:00 - 20:00 UTC)

Cron has been firing every 15 minutes with 100% success rate. All activations complete with 0 errors.

| Activation    | Start | Duration  | Batches | Enriched | Errors |
| ------------- | ----- | --------- | ------- | -------- | ------ |
| `28d56746...` | 02:45 | 879s      | 5       | 2,500    | 0      |
| `b800fa7f...` | 03:00 | 733s      | 5       | 2,500    | 0      |
| `42a6575a...` | 03:15 | 894s      | 5       | 2,500    | 0      |
| `444b3192...` | 03:30 | 859s      | 5       | 2,500    | 0      |
| `95e6a510...` | 03:45 | 723s      | 4       | 2,000    | 0      |
| `d5a086ec...` | 04:00 | in-flight | —       | —        | —      |

Times are UTC. Mix of cold starts (10s init, ~12 min / 4 batches) and warm starts (~14 min / 5 batches). Warm invocations reuse the module-level Django boot and DB connection.

### Current progress (2026-04-04 ~20:15 UTC)

| Metric               | Value                             |
| -------------------- | --------------------------------- |
| NA players enriched  | 26,124                            |
| NA players total     | 275,987                           |
| NA progress          | 9.5%                              |
| Throughput           | ~10k players/hour                 |
| ETA to NA completion | ~25 hours (~2026-04-05 21:00 UTC) |
| Errors (total)       | 0                                 |
| Cron reliability     | 100% (all invocations succeeded)  |

**Note:** The total NA population (275,987) is larger than the earlier estimate (~74,490) because the enrichment query covers all NA players, not just those meeting the 500-battle/48% WR eligibility filter used by the old Celery task.

### Celery enrichment status

The Celery-based `enrich_player_data_task` is **no longer running**. All 23 periodic tasks remain suspended in django-celery-beat. The background worker is idle — it only serves as a fallback if the cron stops. The enrichment is now fully driven by the DO Function cron.

Important scope note: this does **not** mean the full background-worker migration is complete. The repository and droplet still provision the `background` worker, Celery Beat, and background task routes for warmers, crawlers, and incrementals. Only the enrichment lane has been migrated so far. The broader migration status and target architecture live in `agents/runbooks/spec-serverless-background-workers-2026-04-04.md`.

## Next Steps

### Immediate (no action needed — monitor only)

- Cron is autonomous. Check `doctl serverless activations list` periodically to confirm continued success.
- If an activation fails, check `doctl serverless activations result <id>` for error details.
- Lock file at `/tmp/enrichment-invoke.lock` (780s TTL) prevents overlapping invocations.
- Close out the remaining Phase 1 monitoring item in the serverless spec once a full unattended run window has been observed.

### After NA completes

_Status: EU enrichment was activated alongside NA before NA completed. Both realms are now being enriched concurrently. The original plan to run EU sequentially after NA was superseded by the higher throughput of DO Functions._

### After all enrichment completes

1. Decide whether to keep the enrichment cron running continuously or reduce it to a maintenance cadence
2. Do **not** assume the broader migration is done: warmers, clan crawls, ranked incrementals, player refresh, and beat-driven schedule ownership are still droplet/Celery responsibilities today
3. Prefer Phase 2 migration work before permanently restoring the old background load shape
4. Highest-value next migration slices are the no-Redis background jobs first: `incremental_player_refresh_task`, `incremental_ranked_data_task`, and realm clan crawls
5. Resolve the Redis strategy for warmers before migrating `warm_landing_page_content_task`, `warm_hot_entity_caches_task`, and other cache writers
6. Only after enough background work has moved off the droplet should we remove `battlestats-celery-background`, remove `background` queue routing, and stop `post_migrate` from re-registering background periodic tasks
7. Re-enable the 23 suspended periodic tasks only as an explicit interim fallback if we choose to return to the old Celery-operated background model before Phase 2 lands
