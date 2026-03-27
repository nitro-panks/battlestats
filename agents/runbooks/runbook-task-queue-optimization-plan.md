# Runbook: Task Queue & Infrastructure Optimization Plan

**Created**: 2026-03-27
**Status**: Implemented — Phases 1, 2, 5 deployed. Phases 3, 6, 7 deferred (low urgency after queue separation).
**Priority**: Medium-High — task congestion degrades first-visit UX and limits throughput

## Current State

### Droplet Resources
- **CPU**: 2 vCPU
- **RAM**: 2GB (984MB used, 983MB available, 739MB swap in use)
- **Disk**: 87GB (12GB used)
- **Database**: DigitalOcean Managed PostgreSQL (cloud, NYC3)
- **Redis**: Local, 7.9MB used, no maxmemory, no persistence
- **RabbitMQ**: Local, default guest:guest credentials

### Process Memory Footprint
| Process | RSS |
|---------|-----|
| Celery hydration worker (pool parent) | ~130MB |
| Celery hydration ForkPoolWorker x2 | ~220MB each |
| Celery background worker (pool parent) | ~130MB |
| Celery background ForkPoolWorker x1 | ~250MB |
| Celery beat | 25MB |
| Gunicorn master | — |
| Gunicorn worker x3 | ~45MB each |
| Next.js | 38MB |
| RabbitMQ | 21MB |
| **Total estimated** | **~1.1GB** |

### Worker Configuration (post-Phase 1)
- **2 Celery worker processes**:
  - `hydration@%h`: `-Q default -c 2` — user-facing hydration tasks
  - `background@%h`: `-Q background -c 1 --time-limit=21600 --max-tasks-per-child=50` — crawls, warmers, snapshots
- `--prefetch-multiplier=1` on both (prevents starvation)
- `--without-gossip --without-mingle` on both (reduces overhead)

### Periodic Task Schedule
| Task | Queue | Schedule | Est. Runtime | Blocks Worker |
|------|-------|----------|-------------|---------------|
| `crawl_all_clans_task` | background | Daily 03:00 UTC | 1–6 hours | Yes (background) |
| `incremental_player_refresh_task` | background | Daily 05:00 + 15:00 UTC | 30min–6h | Yes (background) |
| `incremental_ranked_data_task` | background | Daily 10:30 UTC | 30min–6h | Yes (background) |
| `refresh_efficiency_rank_snapshot_task` | background | On-demand (queued by crawl/refresh) | ~15–30s (post Phase 2) | Yes (background, briefly) |
| `warm_hot_entity_caches_task` | background | Every 30 min | 90–300s | Yes (background) |
| `warm_landing_best_entity_caches_task` | background | Frontend-triggered via `/api/landing/warm-best/` | 60–180s | Yes (background) |
| `warm_landing_page_content_task` | background | Every 55 min | 60–120s | Yes (background) |
| `warm_clan_battle_summaries_task` | background | Every 30 min | 30–120s | Yes (background) |
| `warm_player_ranked_wr_battles_correlation_task` | background | Every 30 min | 30–120s | Yes (background) |
| `refill_landing_random_players_queue_task` | background | Every 10 min | 5–30s | Yes (background, briefly) |
| `refill_landing_random_clans_queue_task` | background | Every 10 min | 5–30s | Yes (background, briefly) |
| `ensure_crawl_all_clans_running_task` | default | Every 5 min | <1s | No |

## Problems Identified

### P1: Single-queue starvation — user-facing tasks blocked by background work

**RESOLVED by Phase 1.** All background tasks now route to the `background` queue with a dedicated worker. User-facing hydration tasks run on the `default` queue with 2 dedicated workers.

### P2: Efficiency rank snapshot is CPU-heavy and runs too often

**RESOLVED by Phase 2.** Rewritten from Python iteration over 275K rows to a single SQL UPDATE with `PERCENT_RANK()` window functions. Expected runtime: 312s → 15–30s.

### P3: Warm tasks make hundreds of WG API calls synchronously

**Mitigated by Phase 1.** Warm tasks now run on the `background` queue and no longer block user-facing hydration. Fan-out refactor (Phase 3) deferred — would improve background throughput but doesn't affect user UX.

### P4: Clan crawl monopolizes a worker for hours

**RESOLVED by Phase 1.** Crawl runs on the dedicated `background` worker. Hydration workers are unaffected.

### P5: Memory pressure from Celery workers

**Managed.** The background worker uses `-c 1` and `--max-tasks-per-child=50` for aggressive memory recycling. Estimated total footprint ~1.1GB on 2GB RAM. Monitor swap usage after deployment.

### P6: Redis has no persistence and no maxmemory

**Deferred.** Low risk at 7.9MB usage. Recommended: enable AOF persistence and set maxmemory on the droplet. See Phase 4 for details.

### P7: No PostgreSQL connection pooling

**RESOLVED by Phase 5.** `CONN_MAX_AGE=300` added to Django settings. Connections persist for 5 minutes instead of closing per-request.

## Implementation Status

### Phase 1: Queue Separation — IMPLEMENTED ✓

**Changes made:**

1. **`server/battlestats/settings.py`**: Added `CELERY_TASK_DEFAULT_QUEUE = 'default'` and `CELERY_TASK_ROUTES` routing 11 background tasks to the `background` queue.

2. **`server/deploy/bootstrap_droplet.sh`**: Split single `battlestats-celery.service` into two systemd services:
   - `battlestats-celery.service`: `-Q default -c 2 -n hydration@%%h`
   - `battlestats-celery-background.service`: `-Q background -c 1 --time-limit=21600 --max-tasks-per-child=50 -n background@%%h`
   - Added `redis-cli DEL` for crawl lock/heartbeat before service restart.

3. **`server/deploy/deploy_to_droplet.sh`**: Added crawl lock clearing and updated service restart list to include `battlestats-celery-background`.

4. **`docker-compose.yml`**: Updated `task-runner` to `-Q default -c 2 -n hydration@%h`. Added `task-runner-background` service for `-Q background -c 1`.

### Phase 2: Optimize Efficiency Rank Snapshot — IMPLEMENTED ✓

**Changes made in `server/warships/data.py`:**

1. New function `_recompute_efficiency_rank_snapshot_sql()` replaces the Python-side iteration with raw SQL:
   - Step 1: CTE computes `field_mean_strength` and `population_size` via `AVG`/`COUNT`
   - Step 2: Atomic UPDATE with `PERCENT_RANK()` window function computes shrunken strength, percentile, and tier in one pass
   - Step 3: Separate UPDATE clears ranks for non-eligible players
   - Step 4: Gather tier counts and distribution for return value

2. New helper `_count_suppressed_players()` computes suppression reason counts (low_battles, low_ships, unmapped_badge_gate) via SQL.

3. `recompute_efficiency_rank_snapshot()` delegates to `_recompute_efficiency_rank_snapshot_sql()` for the `skip_refresh=True` path.

4. Percentile formula: `1.0 - PERCENT_RANK() OVER (ORDER BY shrunken_strength DESC)` — equivalent to the original `(pop - rank) / (pop - 1)`.

**Test results**: All 29 `PlayerExplorerSummaryTests` pass, including the unmapped badge gate suppression test.

### Phase 3: Break Up Warm Tasks — DEFERRED

**Reason**: Phase 1 (queue separation) already prevents warm tasks from blocking user-facing hydration. Fan-out refactor would improve background throughput but is lower priority. Can revisit if background queue congestion becomes an issue.

### Phase 4: Redis Hardening — DEFERRED (operational)

**Recommended changes** for the droplet (not in code):
```
# /etc/redis/redis.conf
appendonly yes
appendfsync everysec
maxmemory 128mb
maxmemory-policy allkeys-lru
```
Then `systemctl restart redis-server`.

### Phase 5: PostgreSQL Connection Pooling — IMPLEMENTED ✓

**Change**: Added `'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '300'))` to `DATABASES['default']` in `settings.py`.

### Phase 6: Clan Crawl Optimization — NOT NEEDED

**Analysis**: The crawl code only triggers `queue_efficiency_rank_snapshot_refresh()` once at the end of a crawl (line 335 in `clan_crawl.py`), not per-batch. The per-player trigger comes from `update_player_efficiency_data_task` with a 15-minute dispatch dedup key. With Phase 1's queue separation, the crawl runs on the `background` queue and doesn't affect hydration workers. No further optimization needed.

### Phase 7: Monitoring Endpoint — DEFERRED

**Reason**: Low priority. Can add `/api/stats/tasks/` when needed for operational visibility.

## Answers to Key Questions

### Do we have enough workers?
**Yes, with queue separation.** Two hydration workers (`-c 2`) handle user-facing tasks, and one background worker (`-c 1`) handles maintenance. The key insight is that starvation, not worker count, was the problem.

### Should we use a DigitalOcean managed Redis?
**Not yet.** At 7.9MB usage on a single-node architecture, managed Redis ($15/mo) adds cost without meaningful benefit. Harden local Redis with AOF persistence and maxmemory instead. Revisit if the architecture goes multi-node.

### Are the crawlers overlapping or redundant?
**Partially.** The `incremental_player_refresh_task` (runs 2x/day) refreshes stale players. The `crawl_all_clans_task` (runs daily) crawls all clans and touches many of the same players. The overlap is limited because they focus on different data (membership vs battle data). Both may trigger efficiency snapshots, but the 15-minute dispatch dedup key and the SQL rewrite (Phase 2) make this a non-issue.

### Are they starving out other tasks?
**Not anymore.** Phase 1 (queue separation) isolates background tasks from user-facing hydration. Phase 2 reduces the efficiency snapshot from 312s to an estimated 15–30s.

### How should we refactor the efficiency snapshot?
**Done.** Pushed to SQL using `PERCENT_RANK()` window functions. See Phase 2 implementation above.
