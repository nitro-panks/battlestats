# Runbook: Startup Cache Warming

**Status:** Implemented
**Date:** 2026-03-29

## Problem

After a server restart (deploy, crash, `docker compose restart`), all Redis caches are cold. Before this fix, cache warming was handled exclusively by Celery Beat periodic tasks:

| Warmer | Interval | Effect of cold start |
|--------|----------|---------------------|
| Landing page warmer | Every 55 min | First landing page load computes from DB (~2s) |
| Hot entity cache warmer | Every 30 min | First player/clan detail pages compute from DB + WG API (~1.5s) |
| Bulk entity cache loader | Every 12 hours | Top 50 players + 25 best clans' members uncached for up to 12h |

The only startup warming was `python manage.py warm_landing_page_content` launched as a background shell process in the docker-compose command. This covered landing data but left player/clan detail pages cold.

## Root Cause

- `WarshipsConfig.ready()` only imports signals (no startup warming logic)
- `server/warships/signals.py` registers Celery Beat schedules via `@receiver(post_migrate)` but doesn't trigger any immediate runs
- The docker-compose startup command used a background shell subshell (`(... &)`) which had issues:
  - stdout/stderr from background processes disconnected from docker's log driver after `exec gunicorn`
  - Errors in warmers were silently swallowed
  - No visibility into warmer progress or failures

## Solution

### Architecture

Startup warming is now handled by gunicorn's `when_ready` hook in `server/gunicorn.conf.py`. When the gunicorn master is ready to accept connections, it spawns a daemon thread that runs a unified management command:

```
gunicorn starts
  -> when_ready hook fires
    -> daemon thread sleeps CACHE_WARMUP_START_DELAY_SECONDS (default 5)
    -> runs: python manage.py startup_warm_all_caches --delay 0
      -> 1. warm_landing_page_content() (~10s)
      -> 2. warm_hot_entity_caches()   (~60-90s, makes WG API calls)
      -> 3. bulk_load_entity_caches()  (~10-30s, DB reads only)
    -> logs success/failure to gunicorn error log (visible in docker logs)
```

### Files Changed

| File | Change |
|------|--------|
| `server/gunicorn.conf.py` | Added `when_ready` hook that spawns startup warmer thread |
| `server/warships/management/commands/startup_warm_all_caches.py` | New unified management command running all three warmers sequentially |
| `server/warships/management/commands/warm_hot_entity_caches.py` | New management command wrapping `warm_hot_entity_caches()` |
| `server/warships/data.py` | Added `logger = logging.getLogger(__name__)` (was missing, causing NameError) |
| `docker-compose.yml` | Simplified startup command to `exec gunicorn` (warmers moved to gunicorn hook) |

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `WARM_CACHES_ON_STARTUP` | `1` | Set to `0` to disable startup warming |
| `CACHE_WARMUP_START_DELAY_SECONDS` | `5` | Delay before warmers start (lets workers boot) |

### Bug Fix: Missing Logger in data.py

`warm_hot_entity_caches()` in `data.py` used `logger.info(...)` but the module never defined `logger = logging.getLogger(__name__)`. This worked when called from `tasks.py` (which has its own logger) but crashed when called directly via management command. Added the logger definition at module level.

## Performance Results (local, cold Redis)

| Timing | Endpoint | Response Time |
|--------|----------|---------------|
| T+0s (cold) | player detail | ~1.5s |
| T+0s (cold) | landing players | ~2.0s |
| T+15s (landing warm) | landing players | 6ms |
| T+15s (landing warm) | player detail | ~1.4s (still cold) |
| T+120s (all warm) | player detail | 17ms |
| T+120s (all warm) | landing players | 6ms |
| T+120s (all warm) | landing clans | 5ms |

Total warm-up time: ~90-120 seconds from restart to fully warm caches.

## Monitoring

After restart, check docker logs for:
```
[INFO] Running startup cache warmers...
[INFO] Startup cache warmers completed successfully.
```

If warmers fail:
```
[ERROR] Startup cache warmers failed (exit <code>): <stderr tail>
```

## Manual Cache Warming

Individual warmers can be run manually:
```bash
docker compose exec server python manage.py warm_landing_page_content
docker compose exec server python manage.py warm_hot_entity_caches
docker compose exec server python manage.py bulk_load_entity_caches
docker compose exec server python manage.py startup_warm_all_caches --delay 0  # all three
```
