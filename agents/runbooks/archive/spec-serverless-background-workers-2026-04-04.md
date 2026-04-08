# Spec: Serverless Background Workers via DigitalOcean Functions

Created: 2026-04-04
Archived: 2026-04-08
Status: **Reverted** — migration abandoned for workloads that touch the Wargaming API.

## Post-mortem (2026-04-08)

The enrichment function ran successfully for ~2 days before silently degrading on 2026-04-06 ~05:00 UTC. Every subsequent invocation failed with Wargaming API error `407 INVALID_IP_ADDRESS` on every `seasons/accountinfo/`, `ships/stats/`, `clans/season/`, and `clans/seasonstats/` call, then timed out at the 900s function limit. No enrichment occurred between 2026-04-06 05:00 and 2026-04-08 ~03:00 UTC.

**Root cause:** DigitalOcean Functions do not have a static egress IP. Outbound traffic exits through a rotating pool of addresses that is neither documented nor whitelistable by Wargaming's `application_id`, which IP-locks each key to a single host (the droplet). As soon as the function was scheduled on an egress IP outside the whitelist, every WG API call failed.

**Mitigations considered and rejected:**

1. **Proxy WG API calls through the droplet** (forward proxy + HTTPS_PROXY env on the function). Would preserve the serverless architecture but (a) reintroduces the droplet as a single point of failure the migration was trying to remove, (b) adds a new auth/monitoring surface, and (c) doesn't address the underlying shape mismatch — enrichment is a long sequential crawl rate-limited by a third-party API, which is a daemon workload, not a function workload.
2. **Request a CIDR whitelist from Wargaming.** WG's portal only supports per-IP entries, and DO does not publish or commit to a CIDR for Functions egress.

**Decision:** enrichment returned to the droplet's Celery `background` worker. See `CLAUDE.md` → "Background enrichment" and `server/warships/signals.py` → `player-enrichment-kickstart`. Prior steady-state throughput (batches 6–47 in `runbook-enrichment-crawler-2026-04-03.md`) was ~500 players per 17–20 min with zero errors — adequate for the ~280K pending population. Compute was not the bottleneck; the WG API was.

**What remains useful:**

- `functions/packages/battlestats/db-test` — Postgres connectivity probe from DO Functions, still valid.
- The `functions/` deploy tooling — reusable for any future worker that does **not** call the Wargaming API (e.g., sitemap generation, cache warming against internal endpoints, DB-only aggregations).
- The enrichment function code at `functions/packages/enrichment/enrich-batch/` is kept for reference but is no longer invoked by any cron. Do not re-enable without first solving the egress IP problem.

---

## Original spec below (historical)

Status at time of writing: **Phase 1 Running** — enrichment function deployed, validated, and running autonomously via cron

## Problem

The single production droplet (2 vCPU, 3.8GB RAM, 2GB swap) cannot reliably run background ingress work (enrichment crawl, clan crawls, warmers, distribution/correlation scans) alongside the serving stack (Django, Next.js, Redis, Nginx). Key failure modes:

1. **Warmer queue starvation**: Landing page warmers peak at 1G+ memory, trigger `max-memory-per-child` recycling, and monopolize the 2-concurrency background queue — starving enrichment and other tasks.
2. **OOM cascades**: Worker restarts cause RabbitMQ to redeliver unacknowledged tasks, immediately re-triggering the OOM condition.
3. **`post_migrate` re-enablement**: Every deploy re-registers all periodic tasks via Django signals, silently undoing manual task suspension.
4. **Resource contention**: Background compute (full-table scans over ~194K players, WG API batch calls) competes with request serving for CPU, memory, and DB connections.

A separate DB is not needed — the product has no concurrent user write load. The bottleneck is compute and memory on the droplet, not DB contention.

## Architecture

### What stays on the droplet

| Component | Role |
|---|---|
| Nginx | Reverse proxy, TLS, HTTP/2 |
| Next.js | Frontend SSR + static |
| Django/Gunicorn | API serving (read-heavy) |
| Redis | Cache, locks, Celery broker/backend |
| RabbitMQ | Celery broker for hydration queue |
| Celery (default + hydration queues) | Request-driven hydration only |

### What moves to DigitalOcean Functions

| Function | Current Task | Schedule | Est. Memory | Est. Duration |
|---|---|---|---|---|
| `enrich-batch` | `enrich_player_data_task` | Every 10s (self-chaining) or cron every 20min | 512MB | 5-10 min per 500-player batch |
| `clan-crawl-eu` | `crawl_all_clans_task` (EU) | Daily | 512MB-1GB | 10-15 min |
| `clan-crawl-na` | `crawl_all_clans_task` (NA) | Daily | 512MB-1GB | 10-15 min |
| `warm-landing` | `warm_landing_page_content_task` | Every 55 min per realm | 1GB | ~5 min |
| `warm-hot-entities` | `warm_hot_entity_caches_task` | Every 30 min per realm | 512MB | 2-3 min |
| `warm-distributions` | Distribution/correlation warming | Every 2h | 1GB | 3-5 min |
| `bulk-entity-loader` | `bulk_entity_cache_loader_task` | Every 12h per realm | 512MB | 2-3 min |
| `ranked-incrementals` | `incremental_ranked_refresh_task` | Daily per realm | 256MB | 1-2 min |
| `player-refresh` | `incremental_player_refresh_task` | Daily (AM + PM) per realm | 256MB | 1-2 min |

### What Celery keeps (request-driven hydration only)

These tasks are triggered by user page visits and must respond within the request cycle:

- `refresh_player_data_task` (default queue)
- `refresh_clan_data_task` (default queue)
- `hydrate_ranked_data_task` (hydration queue)
- `hydrate_efficiency_data_task` (hydration queue)
- `hydrate_battle_data_task` (hydration queue)
- `hydrate_clan_members_task` (hydration queue)
- `hydrate_clan_battle_task` (hydration queue)
- `hydrate_clan_battle_summary_task` (hydration queue)

### Queue simplification

| Before | After |
|---|---|
| default (`-c 3`) | default (`-c 3`) — entity refreshes |
| hydration (`-c 3`) | hydration (`-c 3`) — ranked/efficiency/battle hydration |
| background (`-c 2`) | **removed** — all background work moves to Functions |

Removing the background queue and its dedicated worker frees ~768MB-1GB of RAM on the droplet.

### Scheduling

Replace django-celery-beat periodic task registration with DO Functions cron triggers. This eliminates the `post_migrate` re-enablement problem entirely — function schedules are managed outside Django.

Fallback if DO Functions scheduled triggers are still in limited preview: use an external cron (GitHub Actions scheduled workflow, or a simple cron on the droplet) that invokes the function endpoints via HTTP.

## DigitalOcean Functions Details

### Limits

| Resource | Limit |
|---|---|
| Timeout | 15 min max (900,000 ms) |
| Memory | 128 MB - 1 GB |
| Concurrency | 120 per namespace |
| Invocation rate | 600/min per namespace |
| Built function size | 48 MB |
| Output size | 1 MB |

### Pricing

- **Free tier**: 90,000 GiB-seconds/month (~25 GiB-hours)
- **Overage**: $0.0000185/GiB-second ($0.07/GiB-hour)
- Enrichment full pass (~388 batches x 1GB x 5min) = ~116K GiB-seconds = ~$1.70 beyond free tier
- Warmers (~6 invocations/hour x 1GB x 5min x 720h/month) = ~130K GiB-seconds = ~$2.40/month
- **Total estimated monthly cost**: ~$5-8 beyond free tier

### Database connectivity

- Functions connect to managed Postgres via direct connection (port 25060, not PgBouncer)
- SSL mandatory — CA cert bundled as base64 env var, decoded at runtime
- Module-level connection reuse across warm invocations
- Trusted Sources on the DB cluster must allow Functions egress IPs

### Redis connectivity

- Functions that write to Redis cache (warmers, bulk loader) need Redis access
- Options: (a) use DO Managed Redis with public endpoint, (b) expose droplet Redis via SSH tunnel, (c) have functions write to Postgres and let the droplet-side cache pick it up lazily
- Simplest: managed Redis with TLS, or keep warmers writing directly to Postgres and let the serving layer cache on read

## Project Structure

```
functions/
├── project.yml
├── .env                          # DB creds, Redis URL, WG API key
├── packages/
│   ├── enrichment/
│   │   └── enrich-batch/
│   │       ├── __main__.py       # entry point
│   │       ├── requirements.txt  # psycopg2-binary, redis, requests
│   │       └── build.sh          # virtualenv setup
│   ├── crawlers/
│   │   ├── clan-crawl-eu/
│   │   │   └── __main__.py
│   │   └── clan-crawl-na/
│   │       └── __main__.py
│   ├── warmers/
│   │   ├── warm-landing/
│   │   │   └── __main__.py
│   │   ├── warm-hot-entities/
│   │   │   └── __main__.py
│   │   └── warm-distributions/
│   │       └── __main__.py
│   └── maintenance/
│       ├── bulk-entity-loader/
│       │   └── __main__.py
│       ├── ranked-incrementals/
│       │   └── __main__.py
│       └── player-refresh/
│           └── __main__.py
└── lib/                          # shared utilities (DB connection, WG API client)
    ├── db.py
    ├── redis_client.py
    └── wg_api.py
```

## Key Design Decisions

### 1. Extract core logic from Django

The current task implementations in `data.py` and `tasks.py` are tightly coupled to Django ORM. Two options:

- **Option A (quick)**: Boot Django inside the function. Accept the 1-3s cold start. Set `DJANGO_SETTINGS_MODULE`, call `django.setup()`, import from `warships.data`. Requires bundling the full `server/` package or a subset.
- **Option B (clean)**: Extract the core SQL/logic into standalone modules that use `psycopg2` directly. More work upfront but faster cold starts and smaller function packages.

**Chosen**: Option A — validated. Django boots in 2.2s cold start, trimmed package fits in 48MB limit. No need for Option B unless cold starts become a problem at scale.

### 2. Self-chaining vs cron for enrichment

The current enrichment task self-chains (dispatches itself with a 10s delay after each batch). In Functions:

- **Cron approach**: Schedule `enrich-batch` every 15-20 minutes. Each invocation processes one batch of 500. Simple, but slower throughput.
- **Loop approach**: A single function invocation loops through multiple batches until timeout approaches (~14 min mark), then exits. Next cron invocation picks up where it left off. Better throughput.
- **HTTP self-chain**: Function invokes itself via HTTP before returning. Achieves the current self-chaining behavior. Risk of runaway invocations.

**Chosen**: Loop approach. Each invocation loops through batches until 120s remain before timeout, then exits cleanly. First test: 5 batches in 860.9s. Schedule via external cron every 15 minutes.

### 3. Redis access for warmers

Warmers currently write directly to Redis cache keys. Options:

- **Move to managed Redis**: Additional cost (~$15/month for smallest DO managed Redis), but clean.
- **Write to Postgres, cache on read**: Warmers compute results and write to a `cached_payloads` table. Django reads from there and populates local Redis. Adds read latency on cache miss.
- **Expose droplet Redis**: Risky, adds network dependency.

**Recommendation**: Defer this decision. Start with enrichment (which only writes to Postgres) and tackle warmer migration after proving the pattern.

## Implementation Plan

### Phase 0: Prerequisites — COMPLETE

- [x] Install `doctl` and authenticate
- [x] Connect to Functions namespace: `doctl serverless connect` → `fn-8a3da3a9-0287-49e0-ab78-1bec319a6de7` in `nyc1`
- [x] Verify managed Postgres trusted sources configuration (no firewall rules → open)
- [x] Base64-encode DB CA certificate

### Phase 1: Enrichment function (proof of concept) — COMPLETE

- [x] Scaffold project: `doctl serverless init --language python functions`
- [x] Create `packages/enrichment/enrich-batch/__main__.py`
- [x] Boot Django inside function (Option A) — import `enrich_players()` directly
- [x] Configure `project.yml` with DB env vars, 1GB memory, 900s timeout
- [x] `deploy.sh` script: copies server code into function dir, deploys, cleans up
- [x] Remote build for deps (trimmed Django: removed locale/admin static/tests/dist-info → 41MB)
- [x] Deploy: `bash functions/deploy.sh`
- [x] Test: 5 batches × 500 players = 2,500 enriched, 0 errors, 860.9s
- [x] Cold start: 2.2s. Memory: within 1GB. Cost: $0.016 per invocation (861 GiB-seconds)
- [x] Verify enrichment rows written to Postgres (19,981 NA players have battles_json)
- [x] `invoke-enrichment.sh` for manual/cron invocation
- [x] Celery enrichment task already disabled (all periodic tasks suspended)
- [x] Set up cron-based scheduling: droplet crontab `*/15 * * * *` → `doctl serverless functions invoke --no-wait`
- [x] Concurrency guard: lock file with 780s TTL prevents overlapping invocations
- [x] Cron log at `/var/log/enrichment-cron.log` (auto-trimmed to 500 lines)
- [x] Verified cron invocations completing: 5 batches, 2,500 players, 0 errors per invocation
- [ ] Monitor for 24h: check batch completion, memory usage, cold starts

**First invocation results (2026-04-04):**

| Metric | Value |
|---|---|
| Batches per invocation | 5 |
| Players per invocation | 2,500 |
| Elapsed time | 860.9s (~14.3 min) |
| Avg batch time | ~172s |
| Throughput | ~10,465 players/hour |
| Errors | 0 |
| Cold start | 2.2s |
| GiB-seconds | 861 |
| Cost per invocation | ~$0.016 |
| Projected full NA pass (54,508 remaining) | ~22 invocations = ~5.5h |
| Projected monthly cost | ~$5-8 (with warmers in Phase 2) |

## Current Operational State (as of 2026-04-04 ~20:00 UTC)

### Enrichment cron

| Parameter | Value |
|---|---|
| Schedule | `*/15 * * * *` (droplet crontab) |
| Invoke method | `doctl serverless functions invoke enrichment/enrich-batch --no-wait` |
| Concurrency guard | Lock file `/tmp/enrichment-invoke.lock` (780s TTL) |
| Cron log | `/var/log/enrichment-cron.log` on droplet (auto-trimmed to 500 lines) |
| Script | `/usr/local/bin/invoke-enrichment.sh` on droplet |

### doctl on the droplet

`doctl` v1.154.0 installed via snap on the droplet. Authenticated with the DO API token and connected to the `battlestats` Functions namespace. Required for cron-based invocation (HTTP endpoint does not support fire-and-forget for long-running functions).

### NA enrichment progress

| Metric | Value |
|---|---|
| NA eligible players | 74,490 |
| NA enriched (battles_json) | 21,203 |
| NA remaining | 53,287 |
| Progress | 28.5% |
| Cron invocations dispatched | 10 (since 18:10 UTC) |
| All invocations | status=ok, ~724-895s each |
| Projected completion | ~2026-04-05 01:15 UTC (~5.3h from 20:00 UTC) |

### Celery state on droplet

All 23 periodic tasks remain suspended in django-celery-beat. The background Celery worker is running but idle (no tasks dispatched to it). The enrichment Celery task (`enrich_player_data_task`) is not running — the Function has fully replaced it for the NA pass.

`ENRICH_REALMS=na` is still set in `/etc/battlestats-server.env` but is now irrelevant since the Function reads its own `.env`.

### What to do after NA completes

1. **Switch to EU enrichment:**
   - Edit `functions/.env`: change `ENRICH_REALMS=na` to `ENRICH_REALMS=eu`
   - Redeploy: `bash functions/deploy.sh --include enrichment/enrich-batch`
   - The cron continues as-is — it will now enrich EU players
   - Monitor via `doctl serverless functions invoke battlestats/db-test` (update the query to check EU)

2. **After EU completes — re-enable periodic tasks:**
   - Remove `ENRICH_REALMS` from `functions/.env` (or set to empty for all realms)
   - Re-enable the 23 suspended periodic tasks (see suspended task list in `runbook-enrichment-crawler-2026-04-03.md`)
   - OR proceed to Phase 2 (migrate warmers to Functions) before re-enabling
   - Restart the background Celery worker

3. **Optional — disable Celery enrichment permanently:**
   - Remove the `player-enrichment-kickstart` periodic task
   - The Function + cron replaces it entirely
   - Keep the Celery task code for fallback but don't schedule it

### Phase 2: Remove background Celery worker

- [ ] Migrate remaining background tasks to Functions (warmers, crawlers)
- [ ] Solve Redis connectivity for warmers (managed Redis or write-to-Postgres pattern)
- [ ] Remove `battlestats-celery-background` systemd service
- [ ] Remove `background` queue configuration from Celery
- [ ] Remove `post_migrate` signal registration for background periodic tasks
- [ ] Update deploy scripts to skip background worker setup

### Phase 3: Cleanup

- [ ] Remove django-celery-beat periodic task entries for migrated tasks
- [ ] Simplify `signals.py` to only register request-driven task schedules (if any)
- [ ] Update CLAUDE.md and operational runbooks
- [ ] Archive `runbook-enrichment-crawler-2026-04-03.md` (crawler no longer runs on Celery)

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| 1GB memory limit too low for warmers | Warmer fails mid-computation | Split warmer into sub-functions (e.g., distributions separate from correlations) |
| Cold start latency (1-3s Django boot) | Slight delay on first invocation | Acceptable for background work; optimize later with Option B extraction |
| Scheduled triggers in preview (3 limit) | Can't schedule all functions via triggers | Use external cron (GitHub Actions or droplet crontab) to invoke function HTTP endpoints |
| DB connection limits | Too many concurrent function invocations exhaust Postgres connections | Set concurrency limits in project.yml; use connection pooling in function code |
| Function timeout (15 min) for large crawls | Clan crawl may not complete in one invocation | Implement cursor-based pagination; save cursor to DB; next invocation resumes |

## CLI Quick Reference

```bash
# Deploy all functions (copies server code, deploys, cleans up)
bash functions/deploy.sh

# Deploy specific function only
bash functions/deploy.sh --include enrichment/enrich-batch

# Invoke enrichment (fire-and-forget)
./functions/invoke-enrichment.sh

# Invoke enrichment (wait for result — blocks up to 15 min)
./functions/invoke-enrichment.sh --wait

# Invoke manually via doctl
doctl serverless functions invoke enrichment/enrich-batch --no-wait

# Check activation result
doctl serverless activations result <activation-id>

# Check logs
doctl serverless activations logs <activation-id>

# List all deployed functions
doctl serverless functions list

# Get function URL
doctl serverless functions get enrichment/enrich-batch --url

# Check DB connectivity and enrichment progress
doctl serverless functions invoke battlestats/db-test

# Check cron log on droplet
ssh root@battlestats.online "cat /var/log/enrichment-cron.log"

# List completed activations
doctl serverless activations list enrichment/enrich-batch --limit 10

# Switch enrichment to EU after NA completes
# 1. Edit functions/.env: ENRICH_REALMS=eu
# 2. bash functions/deploy.sh --include enrichment/enrich-batch
```

## Next Steps (prioritized)

### Immediate (while NA enrichment runs autonomously)

1. **Monitor cron health** — check `/var/log/enrichment-cron.log` periodically; verify activations complete with status=ok
2. **Track NA completion** — when `db-test` shows `na_remaining = 0`, switch to EU

### After NA enrichment completes (~2026-04-05 01:15 UTC)

3. **Switch to EU** — update `.env`, redeploy, update `db-test` to query EU progress
4. **Update `db-test` function** — add EU eligible/enriched counts alongside NA for monitoring both realms

### After all enrichment completes

5. **Decide on warmers** — either:
   - (a) Re-enable periodic tasks on Celery and accept the memory pressure, OR
   - (b) Proceed to Phase 2: migrate warmers to Functions (recommended)
6. **If Phase 2:** start with `warm-landing` function (highest memory consumer, biggest benefit from offloading)
7. **DB firewall** — add trusted sources to managed Postgres (currently open to all; should restrict to droplet IP + Functions egress IPs)
8. **Evaluate `doctl` deprecation warning** — the `doctl serverless connect` command shows a deprecation notice for API-based namespace connection; migrate to access-key-based auth before it's removed

### Phase 2 candidates (by impact)

| Function | Memory Impact | Frequency | Migration Complexity |
|---|---|---|---|
| `warm-landing` | 1G+ peak, triggers OOM | Every 55 min | Medium — needs Redis write access |
| `warm-hot-entities` | 512MB peak | Every 30 min | Medium — needs Redis write access |
| `warm-distributions` | 1G peak | Every 2h | Medium — needs Redis write access |
| `clan-crawl-eu/na` | 512MB-1G | Daily | Low — only writes to Postgres |
| `bulk-entity-loader` | 512MB | Every 12h | Medium — needs Redis write access |
| `ranked-incrementals` | 256MB | Daily | Low — only writes to Postgres |
| `player-refresh` | 256MB | Daily (AM+PM) | Low — only writes to Postgres |

**Redis blocker for warmers:** warmers write directly to Redis cache keys. Moving them to Functions requires either (a) managed Redis with public endpoint (~$15/month), (b) write-to-Postgres pattern with lazy Redis fill on the serving side, or (c) tunneled Redis access. Decision deferred until enrichment is complete.

**No-Redis candidates** (clan-crawl, ranked-incrementals, player-refresh) could be migrated immediately after enrichment, further reducing droplet background load without solving the Redis question.
