# Runbook: Player Page Load Priority — Icon Hydration vs Chart Rendering

**Created**: 2026-03-29
**Status**: All 5 fixes implemented. Pending deploy.

## Problem Statement

When a player detail page loads, the clan members section flashes "Updating: N members." while profile tab charts appear to throttle. Icon hydration (ranked league, efficiency rank, clan battle shield) should be the **lowest priority** activity, never competing with chart rendering.

---

## Architecture: What Happens on Page Load

### Request Timeline

```
t=0ms       GET /api/player/{name}/                        [blocking — page waits]

t=X+250ms   Tab warmup (requestIdleCallback, 1500ms timeout):
            ├─ GET /api/fetch/player_correlation/tier_type/{id}/
            ├─ GET /api/fetch/player_correlation/ranked_wr_battles/{id}/
            ├─ GET /api/fetch/ranked_data/{id}/
            └─ GET /api/fetch/player_clan_battle_seasons/{id}/
            (4 requests via Promise.allSettled — all parallel, fire-and-forget)

t=X+2500ms  Clan members (requestIdleCallback, 2500ms timeout):
            └─ GET /api/fetch/clan_members/{clanId}
               → triggers hydration tasks on backend
               → polls every 3s (active) or 6s (deferred), up to 12 times

t=user      On-demand tab fetch (Profile tab shown by default):
            └─ GET /api/fetch/player_correlation/tier_type/{id}/
               (deduplicated with warmup if still in-flight)
```

### Key Timings

| Phase | Trigger | Requests | Priority (intended) |
|-------|---------|----------|-------------------|
| Player data | Immediate | 1 | Critical |
| Tab warmup | idle + 250ms | 4 parallel | High |
| Clan members | idle + 2500ms | 1 + up to 12 polls | Low |
| Profile chart render | Tab active | 0-1 (deduplicated) | High |

---

## Root Cause Analysis

### Finding 1: HTTP/1.1 — 6 concurrent connections per origin

The site serves all traffic over **HTTP/1.1**. Browsers enforce a hard limit of **6 concurrent TCP connections per origin** under HTTP/1.1. Under HTTP/2, streams are multiplexed over a single connection with no practical browser-side limit.

**Impact:** Every in-flight request to `battlestats.online` occupies one of 6 slots. Requests beyond 6 queue in the browser's network stack and wait for a slot to free up.

### Finding 2: Request pile-up in the first 3 seconds

Worst-case concurrent requests within the first 3 seconds of page load:

| Time | Event | In-flight |
|------|-------|-----------|
| t=0 | Player data fetch | 1 |
| t+250ms | Tab warmup fires (4 requests) | **5** |
| t+2500ms | Clan members fetch | **6** (at limit) |
| t+2500ms | Profile chart fetch (user on Profile tab) | **7** → queued behind slot 6 |

If tab warmup hasn't completed by t+2500ms (cold cache → 5-20s backend response times), the profile chart's on-demand fetch and the clan members fetch compete for the remaining connection slots.

### Finding 3: Clan member hydration polling consumes connection slots continuously

After the initial clan members fetch, hydration polling fires every 3 seconds for up to 12 attempts:

```
t+2500ms   Poll 0: GET /api/fetch/clan_members/{clanId}
t+5500ms   Poll 1: GET /api/fetch/clan_members/{clanId}
t+8500ms   Poll 2: GET /api/fetch/clan_members/{clanId}
...
t+35500ms  Poll 11: GET /api/fetch/clan_members/{clanId}
```

Each poll occupies a connection slot for the full round-trip (~200ms cached, but the slot is held during DNS/TLS/proxy overhead). During this 36-second window, any concurrent chart fetch must compete for the remaining 5 slots.

When combined with tab warmup requests that are still pending (cold cache), chart fetches can get queued behind hydration polls.

### Finding 4: Backend — hydration floods Celery default queue

When clan members are fetched and hydration is needed:

1. `queue_clan_ranked_hydration()` enqueues up to **8** Celery tasks (capped by `CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT`)
2. `queue_clan_efficiency_hydration()` enqueues up to **8** Celery tasks
3. Total: **16 concurrent Celery tasks** on the `default` queue

These hydration tasks each make 2-3 HTTP calls to the Wargaming API and hold database connections for 5-60 seconds. They share the `default` Celery queue with **all non-background tasks** — there is no priority routing.

Heavy background tasks (crawls, warmers) are routed to a separate `background` queue, but hydration is not.

### Finding 5: Gunicorn worker starvation on cold correlation cache

The tier-type correlation endpoint (`/api/fetch/player_correlation/tier_type/{id}/`) runs a population scan **synchronously in the gunicorn worker** on cache miss:

```python
Player.objects.filter(
    pvp_battles__gte=min_population_battles,
    battles_json__isnull=False,
).values_list('battles_json', flat=True).iterator(chunk_size=1000)
```

This full table scan over ~194K players takes **10-30 seconds**, blocking one of **3-9 gunicorn workers** (formula: `min(max(cpu_count * 2 + 1, 3), 9)`).

If the population correlation cache is cold and multiple users hit the endpoint simultaneously, gunicorn workers are exhausted. Incoming chart and clan member requests queue in nginx, compounding the perceived throttling.

### Finding 6: No fetch priority or concurrency control on the frontend

`sharedJsonFetch.ts` provides:
- Request deduplication (in-flight promise sharing)
- Settled result caching (with TTL)

It does **not** provide:
- Concurrent request limits
- Priority queuing (chart > hydration)
- Abort-on-supersede for lower-priority requests

The `useClanMembers` hook uses raw `fetch()` directly (not `sharedJsonFetch`), bypassing even the deduplication layer.

---

## Contention Summary

```
┌─────────────────────────────────────────────────────────┐
│  Browser (HTTP/1.1 — 6 connection limit)                │
│                                                         │
│  Slot 1: player data ───────────────────────────────>   │
│  Slot 2: tier_type warmup ──────(cold: 10-30s)──────>  │
│  Slot 3: ranked_wr warmup ──────(cold: 5-20s)───────>  │
│  Slot 4: ranked_data warmup ────(cold: 5-20s)───────>  │
│  Slot 5: clan_battle warmup ────(cold: 5-10s)───────>  │
│  Slot 6: clan_members poll ─────(200ms)──> free         │
│                                                         │
│  QUEUED: profile chart on-demand fetch (blocked!)       │
│  QUEUED: next clan_members poll (3s later)              │
└─────────────────┬───────────────────────────────────────┘
                  │ Next.js rewrite proxy
┌─────────────────▼───────────────────────────────────────┐
│  Gunicorn (3-9 sync workers)                            │
│                                                         │
│  Worker 1: tier_type population scan (10-30s blocked)   │
│  Worker 2: ranked correlation (5-20s blocked)           │
│  Worker 3: clan_members response (fast, 200ms)          │
│  Workers 4-9: available (if they exist)                 │
└─────────────────┬───────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────┐
│  Celery default queue                                   │
│                                                         │
│  8x update_ranked_data_task (each: 2-3 WG API calls)   │
│  8x update_efficiency_data_task (each: 1 WG API call)  │
│  = 16 tasks, 24+ outbound HTTP requests to WG API      │
│  = 16 held DB connections                               │
└─────────────────────────────────────────────────────────┘
```

---

## Implemented Fixes

### Fix 1: Enable HTTP/2 on nginx — IMPLEMENTED

HTTP/2 multiplexes all requests over a single TCP connection. The 6-connection browser limit disappears entirely.

**Change:** Added `sed` commands to `client/deploy/bootstrap_droplet.sh` that patch the certbot-managed 443 listeners with `http2`. Idempotent — only patches exact `listen 443 ssl;` matches.

**Expected impact:** Eliminates browser-side connection queuing entirely. All 5-7 concurrent requests proceed in parallel without slot contention.

### Fix 2: Defer clan member fetch until charts are settled — IMPLEMENTED

Previously, clan members fetched at t+2500ms via `requestIdleCallback` regardless of tab warmup status.

**Change:** Added `onWarmupSettled` callback from `PlayerDetailInsightsTabs` to `PlayerDetail`. Clan member fetching is gated on warmup completion. A 10-second hard timeout ensures clan members always load even if warmup fails.

**Files:** `PlayerDetail.tsx` (warmup gate + fallback timeout), `PlayerDetailInsightsTabs.tsx` (callback prop).

### Fix 3: Pause hydration polling while chart fetches are in-flight — IMPLEMENTED

**Change:** Added module-level chart fetch counter (`chartFetchesInFlight`) to `sharedJsonFetch.ts`. Tab warmup increments before firing and decrements on settlement. `useClanMembers` checks the counter at poll-scheduling time — if chart fetches are active, poll interval is raised to the deferred rate (6s instead of 3s).

**Files:** `sharedJsonFetch.ts` (counter API), `PlayerDetailInsightsTabs.tsx` (increment/decrement), `useClanMembers.ts` (priority-aware delay).

### Fix 4: Route hydration tasks to a separate Celery queue — IMPLEMENTED

**Change:** Added task routes in `settings.py`:

```python
'warships.tasks.update_ranked_data_task': {'queue': 'hydration'},
'warships.tasks.update_player_efficiency_data_task': {'queue': 'hydration'},
}
```

Run a dedicated worker with limited concurrency:
```bash
Added new `battlestats-celery-hydration.service` systemd unit in `server/deploy/bootstrap_droplet.sh` with `-Q hydration -c 4`. Default worker reduced from `-c 6` to `-c 4` and renamed from `hydration@%h` to `default@%h`.

This caps hydration at 4 concurrent tasks (vs current 16) and prevents hydration from blocking other default-queue work.

### Fix 5: Warm tier-type and ranked correlation proactively — IMPLEMENTED

**Change:** Added `warm_player_tier_type_population_correlation()` and `warm_player_correlations()` to `data.py`. Correlation cache TTL raised from 1 hour to 2 hours (matching distribution TTL). Warming added to:
- Landing page task (`tasks.py`) — runs every 55 min
- Startup warmer (`startup_warm_all_caches.py`) — runs on deploy/restart

**Files:** `data.py` (new warm functions + TTL bump), `tasks.py` (landing task integration), `startup_warm_all_caches.py` (startup integration).

---

## Verification Results

### Backend
- 143 tests passed (0 regressions)
- Docker-less local test run via `pipenv run python manage.py test`

### Frontend
- `npm run build` — passes
- 21 test suites passed, 99 tests passed
- 3 pre-existing suite failures (PlayerSearch, PlayerDetail, ClanSVG) — unchanged from before

---

## Files Modified

| File | Fix | Change |
|------|-----|--------|
| `client/deploy/bootstrap_droplet.sh` | 1 | `sed` commands to inject `http2` into certbot-managed 443 listeners |
| `client/app/components/PlayerDetail.tsx` | 2 | Warmup-gated clan member deferral + 10s fallback timeout |
| `client/app/components/PlayerDetailInsightsTabs.tsx` | 2,3 | `onWarmupSettled` callback + chart fetch increment/decrement |
| `client/app/components/useClanMembers.ts` | 3 | Priority-aware poll delay (6s when charts in-flight) |
| `client/app/lib/sharedJsonFetch.ts` | 3 | `chartFetchesInFlight` counter + public API |
| `server/battlestats/settings.py` | 4 | Hydration task routes to `hydration` queue |
| `server/deploy/bootstrap_droplet.sh` | 4 | New `battlestats-celery-hydration` systemd unit |
| `server/warships/data.py` | 5 | `warm_player_correlations()` + correlation TTL → 2 hours |
| `server/warships/tasks.py` | 5 | Correlation warming in landing page task |
| `server/warships/management/commands/startup_warm_all_caches.py` | 5 | Correlation warming on startup |
| `client/app/components/__tests__/*.test.tsx` (7 files) | 3 | Updated `sharedJsonFetch` mocks with new exports |
