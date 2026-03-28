# Runbook: Simulated Load Test — 10 Concurrent Users

**Created:** 2026-03-28
**Status:** Complete — executed 2026-03-28, findings and recommendations written
**Goal:** Simulate 10 active website users hitting the battlestats stack concurrently, identify performance bottlenecks, and recommend next steps.

## QA Notes (2026-03-28)

Validated runbook against live codebase. Key corrections and observations:

- **Gunicorn workers = 3** (sync mode, no threads) — confirmed in `server/gunicorn.conf.py`. This is the most obvious bottleneck: max 3 concurrent Django requests at a time. 10 users will absolutely queue.
- **Theme toggling is client-only** — removed from load test scope (no server impact).
- **Landing page makes 4 parallel requests on load:** `/api/landing/clans/?mode=random&limit=30`, `/api/landing/warm-best/`, `/api/landing/recent-clans/`, `/api/landing/recent/`. A single user landing = 4 concurrent Django requests = all 3 workers busy + 1 queued.
- **Player detail page fetches:** `/api/player/{name}` then up to 6 chart endpoints (`/api/fetch/tier_data/{id}`, `activity_data`, `type_data`, `randoms_data`, `ranked_data`, `player_summary/{id}`) plus analytics POST.
- **Clan detail fetches:** `/api/clan/{id}` then `/api/fetch/clan_data/{id}`, `/api/fetch/clan_members/{id}`, `/api/fetch/clan_battle_seasons/{id}` plus analytics POST.
- **Search has two paths:** autocomplete via `/api/landing/player-suggestions/?q=X` (fast, no cache) and full explorer via `/api/players/explorer?q=X` (60s response cache).
- **Analytics POST:** every detail view fires `POST /api/analytics/entity-view/`.
- **Docker note:** In docker-compose, Gunicorn binds `0.0.0.0:8888` (overrides `gunicorn.conf.py` unix socket). Next.js proxies to `http://server:8888`. Load test should hit Next.js at `:3001` for realistic proxy path, or Django directly at `:8888` for backend-only testing.

## Scenario

Simulate realistic browsing patterns of 10 concurrent users:
- Landing page load (4 parallel API calls)
- Player search via autocomplete + explorer
- Player detail navigation (player lookup + 6 chart endpoints + analytics)
- Clan detail navigation (clan lookup + 3 data endpoints + analytics)

## Stack Under Observation

| Service | Container | Key Metrics |
|---|---|---|
| **Next.js** | `battlestats-react` | SSR render time, memory, proxy latency |
| **Django/Gunicorn** | `battlestats-django` | Request latency, 3-worker saturation, DB query count/time |
| **Celery (default)** | `battlestats-celery` | Queue depth, task duration, concurrency=3 saturation |
| **Celery (background)** | `battlestats-celery-background` | Queue depth, long-task duration, concurrency=2 saturation |
| **PostgreSQL** | `battlestats-db` or cloud | Active connections, slow queries, lock contention |
| **Redis** | `battlestats-redis` | Memory, ops/sec, cache hit/miss ratio |
| **RabbitMQ** | `battlestats-rabbitmq` | Queue depth, unacked messages, consumer utilization |

## Tooling

- **Load generator:** `tests/load/load_test.py` — async Python script using `aiohttp`, 10 concurrent user sessions
- **Monitoring:** `docker stats`, container logs, `redis-cli INFO`, RabbitMQ management UI (`:15672`)
- **Profiling (if needed):** `py-spy` for Django/Celery, Next.js `--inspect` for Node

## Test Phases

### Phase 1 — Baseline (no load)
- Record idle resource usage for all services via `docker stats --no-stream`
- Capture Redis `INFO stats` (keyspace_hits, keyspace_misses, used_memory)
- Note Celery queue depths via RabbitMQ API
- Record PostgreSQL active connections

### Phase 2 — Ramp to 10 users
- Simulate 10 users arriving over ~30 seconds (staggered 3s apart)
- Each user follows a scripted journey: landing → search → player detail → clan detail → landing
- Think time: 2-4s between actions (realistic browsing pace)
- Sustain for ~3 minutes total

### Phase 3 — Observation & Collection
- `docker stats` snapshots every 15s during the run
- Tail Django request logs for slow responses (>500ms)
- Redis `INFO stats` delta for cache hit rate
- RabbitMQ queue depths for Celery backlog
- Record any errors (5xx, timeouts, failed tasks)

### Phase 4 — Cooldown & Analysis
- Stop load, observe recovery time
- Diff resource usage against baseline
- Identify top-3 bottlenecks

## Success Criteria

- All 10 users complete their journeys without HTTP errors
- P95 API response time < 2s
- No Celery task queue backlog > 20 tasks
- No OOM kills or container restarts

## Known Risk Areas (hypotheses to test)

1. **Gunicorn worker saturation** — 3 sync workers cannot handle 10 users making 4+ parallel requests each. This is the #1 expected bottleneck.
2. **Cache-miss stampede** — if 10 users hit uncached players simultaneously, lazy-refresh queues Celery tasks that could spike the queue.
3. **PostgreSQL query fan-out** — player detail hydration runs many queries per request; 10x concurrent could cause connection pool exhaustion or lock contention.
4. **Next.js proxy queuing** — Next.js rewrites add a hop; if Django is saturated, the proxy layer queues too, compounding latency.
5. **Redis single-thread bottleneck** — unlikely at 10 users but worth baselining ops/sec.

## Findings (2026-03-28)

### Run Summary

- **170 requests, 0 errors** across 10 users over ~34s
- **P95: 95ms, Max: 113ms** — well under the 2s success criteria
- All 10 users completed full journeys without HTTP errors
- No OOM kills or container restarts

### Baseline vs Load Comparison

| Metric | Idle Baseline | Under Load (peak) | Post-Load |
|---|---|---|---|
| **Django CPU** | 0.01% | 2.64% | 0.01% |
| **Django Memory** | 813MB | 858MB → 888MB | 899MB (+86MB, never reclaimed) |
| **PostgreSQL CPU** | 0.00% | **156.6%** | **137.0%** |
| **PostgreSQL Memory** | 213MB | **1.04GB** → **1.06GB** | 1.06GB (+847MB, 5x increase) |
| **PG Active Connections** | 1 | 1 | 1 |
| **Celery (default) CPU** | 0.06% | 5.67% | 2.62% |
| **Celery (default) Queue** | 0 messages | **35 → 92 messages** | **105 messages** |
| **Celery (background) CPU** | 0.01% | **68.6%** | **58.5%** |
| **Celery (background) Memory** | 625MB | 669MB → 704MB | 684MB |
| **Redis ops/sec** | 0 | **214** → 72 | 52 |
| **Redis Memory** | 4.9MB | 7.1MB → 8.1MB | 9.3MB (nearly 2x) |
| **Redis Cache Hit Rate** | 83.3% (31486/6291) | — | 82.5% (32830/6968) |
| **Next.js** | 1.9GB / 0% CPU | 1.9GB / 0% CPU | unchanged (not tested via proxy) |

### Top Bottlenecks Identified

#### 1. Celery Default Queue Backlog (CRITICAL)
The default queue accumulated **105 pending messages** during a 34-second test with just 10 users. With only 1 consumer at concurrency=3, the queue cannot drain fast enough. Each player/clan lookup triggers background refresh tasks (`warm-best`, lazy-refresh hydration). At 10 users the queue is growing faster than it drains.

**Evidence:** Queue went 0 → 35 → 92 → 105 messages during the test and was still growing post-test.

#### 2. PostgreSQL Resource Spike (HIGH)
PostgreSQL CPU hit **156%** (multi-core) and memory jumped from 213MB to 1.06GB — a 5x increase. The background Celery tasks triggered by `warm-best` and lazy-refresh are running heavy hydration queries. This is not from the 10 direct HTTP requests (which returned in <100ms from Redis cache) but from the cascade of background refresh tasks they enqueue.

**Evidence:** PG CPU 0% → 157% mid-test. Memory 213MB → 1.06GB. PG connections stayed at 1 active (queries are fast individually, but many).

#### 3. Celery Background Worker Saturation (MEDIUM)
The background worker spiked to **68.6% CPU** and **19 PIDs** (up from 7 idle). At concurrency=2 with time_limit=21600, it was running long-running tasks that consumed significant resources. Memory grew from 625MB to 704MB.

**Evidence:** CPU 0.07% → 68.6% during test. Still at 58.5% post-test (tasks still running).

#### 4. `landing:recent-players` Endpoint Latency (LOW)
The `/api/landing/recent/` endpoint was consistently the slowest at **median 94ms, P95 113ms** — about 15-20x slower than other landing endpoints (4-8ms). Not a critical issue at these numbers, but it's the one endpoint that consistently breaks out of the <10ms range.

#### 5. Search Autocomplete Latency (LOW)
`/api/landing/player-suggestions/` was **median 61ms, P95 76ms**. Hits the database directly (no cache). Acceptable now but would scale linearly with user count.

### Hypotheses Verdict

| Hypothesis | Result |
|---|---|
| Gunicorn worker saturation (3 workers) | **NOT triggered** — staggered arrivals + fast cache returns meant workers were never fully blocked. Would trigger under burst traffic or cold cache. |
| Cache-miss stampede | **PARTIALLY confirmed** — cache was warm so HTTP responses were fast, but each hit still queued background refresh tasks, causing the Celery backlog. |
| PostgreSQL query fan-out | **CONFIRMED** — PG spiked to 157% CPU / 5x memory, driven by background Celery hydration tasks, not by direct HTTP requests. |
| Next.js proxy queuing | **NOT tested** — load test hit Django directly at :8888. |
| Redis single-thread bottleneck | **NOT triggered** — peaked at 214 ops/sec, well within capacity. |

## Recommendations & Implementation Status

### Implemented (2026-03-28)

1. **Celery default concurrency 3→6.** Updated `docker-compose.yml` and `server/deploy/bootstrap_droplet.sh`. Doubles throughput on the default queue that was accumulating 105+ pending tasks.

2. **Dedup/throttle for lazy-refresh task enqueuing.** Added 60-second Redis dedup lock in `_delay_task_safely()` (`server/warships/views.py`). Uses `cache.add()` with a key derived from task name + kwargs hash. Prevents duplicate enqueuing of the same refresh task within the cooldown window. Deletes the key on broker failure so retries work.

3. **Optimized `landing:recent-players` endpoint.** Two fixes in `server/warships/landing.py`:
   - **Query optimization:** Collapsed 2 queries (values query + N+1 select_related) into a single query using `.select_related('explorer_summary').only(...)`. Eliminates the second round-trip and reduces data transfer.
   - **Invalidation throttle:** Added 30-second cooldown on `invalidate_landing_recent_player_cache()`. Previously, every player lookup marked the cache dirty, forcing a rebuild on the next request. Now coalesces rapid lookups so the endpoint serves from cache under load.

4. **Gunicorn workers scaled dynamically.** Updated `server/gunicorn.conf.py` to use `min(max(cpu_count() * 2 + 1, 3), 9)` instead of hardcoded 3. Follows Gunicorn's recommended formula, capped at 9 for single-droplet memory budget.

### Remaining (Future Work)

5. **Add pg_stat_statements or query logging** to identify which Celery-triggered queries cause the PG CPU/memory spike. The 5x memory jump suggests aggressive query plans or large result sets being materialized.

6. **Run the test via Next.js proxy (:3001)** to capture the full-stack path including SSR and proxy overhead. The current test only validated the Django backend.

7. **Add a second default Celery consumer** or use autoscaling (`--autoscale=8,3`) to handle load spikes without over-provisioning at idle.

8. **Consider caching search autocomplete** results in Redis (short TTL, 5-10s) to avoid database hits on every keystroke.
