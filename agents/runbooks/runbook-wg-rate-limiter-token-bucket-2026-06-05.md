# Runbook: Shared Redis token-bucket WG API rate limiter

_Created: 2026-06-05_
_Context: All backend workers (clan crawl, observation floor, clan-battle seasonstats warming, on-demand hydration, enrichment) share a single WG `application_id` and one ~10 req/s budget, but each path self-throttles independently with no cross-worker coordination. When two or more paths run hot at once they collectively blow the per-app-id ceiling and WG returns `407 REQUEST_LIMIT_EXCEEDED`. Observed this session as `clans/seasonstats/` 407 bursts (252 in 6h) overlapping the NA crawl; commit `8210db7` capped the CB fan-out as a stopgap but the systemic gap remains._
_Status: **SHIPPED 2026-06-10** (`warships/api/rate_limiter.py`). Implemented as designed: Redis-backed atomic Lua token bucket, clock from `redis.call('TIME')`, fail-open everywhere, single global bucket for the per-app-id budget. Two corrections vs this spec: (1) the egress is NOT a single point — both `_request_api_payload` AND `make_api_request_typed` issue their own GETs, so the `acquire()` call was added at **both**; (2) added caller-context wait budgets — background tasks block up to `WG_RATE_LIMIT_MAX_WAIT` (8s), request threads only `WG_RATE_LIMIT_REQUEST_MAX_WAIT` (0.5s) then fail open, because a synchronous WG call still exists on the request path (`_fetch_player_id_by_name`) and a saturated bucket must not park gunicorn threads. Env catalog in `ops-env-reference.md` (WG rate limiter section). Per-component delays kept as backstops pending a watch period. Lua bucket proven against real Redis in `test_rate_limiter.py`._

## Purpose

Specify a single, shared, cross-worker rate limiter for all outbound Wargaming API traffic so the combined request rate across every Celery worker and management command stays under WG's per-`application_id` ceiling — eliminating `407 REQUEST_LIMIT_EXCEEDED` at the source rather than patching each call path. Capture the design decisions, the chosen insertion point, the failure semantics, and a staged rollout so the implementation does not have to re-derive any of it.

This supersedes the scattered per-path throttles as the *coordination* mechanism (those remain as coarse backstops). Read alongside `archive/runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md` (the floor/crawl coexist work that first surfaced the shared-budget contention).

## Background / motivation

WG enforces rate limits **per `application_id`**, not per host or per process. The 407 error payload names the field explicitly:

```
{'status': 'error', 'error': {'code': 407, 'message': 'REQUEST_LIMIT_EXCEEDED',
 'field': 'application_id', 'value': '<app id>'}}
```

The documented budget is ~10 requests/second per app-id (and a daily cap). Battlestats runs **one** `WG_APP_ID` (`api/client.py:27`), shared across all three realm hosts and every worker queue:

- **crawls** (`-c 1`) — `clan_crawl.py`, paginates the clan universe then walks clans/members; self-throttled by `request_delay` (~0.25s).
- **background** (`-c 3`) — observation floor (`BATTLE_OBSERVATION_FLOOR_CRAWL_DELAY=0.8`), enrichment (`ENRICH_DELAY=0.2`), incrementals, snapshots.
- **hydration** (`-c 3`) — request-driven player/clan refreshes; clan-battle seasonstats fan-out (now capped by `CLAN_BATTLE_SUMMARY_FETCH_CONCURRENCY=3`, commit `8210db7`).
- **default** (`-c 3`) — light API refreshes.

Each path's throttle is **local** — it bounds *that path's* rate but knows nothing about the others. Two hot paths (e.g. the crawl + a 25-clan landing-best warm, each fanning out seasonstats) sum to well over 10 req/s and trip the 407. The per-path delays cannot fix this because the contention is *across* paths.

**Why a 407 hurts beyond the wasted call:** with commit `8210db7`, a 407 on `clans/seasonstats/` no longer poisons the per-player cache (it short-caches via `CLAN_BATTLE_PLAYER_STATS_ERROR_TTL` instead of persisting a wrong "0 CB battles" for 6h). But other paths still treat a 407-induced `None` as a hard miss, and every 407 burns budget against the daily cap. A global limiter removes the burst entirely.

## Design decisions

### D1 — Single insertion point: `api/client.py::_request_api_payload`

`_request_api_payload` (`api/client.py:59`) is the **sole** outbound WG HTTP path — it owns the only `requests.Session` in the codebase (`_get_session`, line 33). Every other module (`clan_crawl.py`, `api/clans.py`, `api/players.py`, `api/ships.py`, `enrich_player_data.py`, `discover_players.py`, `backfill_player_kdr.py`) reaches WG via `make_api_request` → `_request_api_payload`. Gating there catches **100%** of WG traffic with one change and no per-caller edits.

The gate sits immediately before `_get_session().get(...)` (line 72): acquire a token, then issue the request; if no token within the wait budget, skip the HTTP call and return `None`.

### D2 — Global token bucket keyed by app-id, in Redis, via atomic Lua

- **One global bucket** keyed by `application_id` (e.g. `wg:ratelimit:<app_id>`), not per-realm and not per-worker — because the limit itself is global per app-id.
- **Token bucket** (refill at a steady rate, allow a small burst) rather than a fixed window — smooths traffic and avoids the window-edge double-spend a naive counter has. GCRA/sliding-window are acceptable alternatives; token bucket is the simplest correct choice.
- **Atomic check-and-decrement in a Redis Lua script** so concurrent workers cannot race past the limit (read-modify-write from Python is not safe across processes).
- **Redis-side time** (`redis.call('TIME')` inside the Lua) for refill math, so worker clock skew across hosts/containers never corrupts the bucket. Do **not** pass the worker's `time.time()` in.

Sketch of the Lua contract (illustrative, not final):
```
KEYS[1] = bucket key
ARGV    = rate (tokens/sec), capacity (burst), requested (=1)
-- read {tokens, last_refill_micros}; refill = (now - last) * rate, clamp to capacity;
-- if tokens >= requested: tokens -= requested; store; return {1, 0}
-- else: return {0, wait_micros_until_next_token}
-- always set a TTL on the key (a few × capacity/rate) so an idle bucket self-expires
```

### D3 — Block with a bounded wait; return `None` on timeout

Backend tasks are latency-tolerant, so a caller that can't get a token should **wait and retry** (sleep `min(wait_hint, small_ceiling)`, re-attempt) rather than drop the request. But the wait is **bounded** (`WG_RATE_LIMIT_MAX_WAIT`, default a few seconds) so a Celery worker is never pinned indefinitely.

On exhausting the wait budget, return `None` — the *same* shape `_request_api_payload` already returns for an upstream error, so every existing caller already handles it (and, post-`8210db7`, the seasonstats path handles it without cache poisoning). This is why D3 composes cleanly with the stopgap: timeouts degrade exactly like a transient error, not a crash.

### D4 — Fail-open when Redis is unavailable

If the Redis call errors or times out, **allow the request** (log once, increment a counter) rather than blocking all WG traffic on a Redis hiccup. The limiter is an optimization over the existing per-path throttles, which remain as a backstop; it must never become a hard dependency that takes the whole fetch layer down. Guard with a short Redis timeout so a slow Redis doesn't add latency to every call.

### D5 — Keep existing per-path throttles (defense in depth, for now)

Do **not** rip out `clan_crawl` `request_delay`, `ENRICH_DELAY`, `BATTLE_OBSERVATION_FLOOR_CRAWL_DELAY`, or `CLAN_BATTLE_SUMMARY_FETCH_CONCURRENCY` in the same change. They are coarse, per-path, and harmless under the bucket; keeping them means a partial/failed-open limiter still has the old safety net. Revisit retiring or loosening them in a **follow-up** once the bucket is proven in prod (e.g. the crawl could safely raise its throughput once the bucket guarantees the global ceiling).

### D6 — Priority lanes (optional, phase 2)

A single bucket lets a long background crawl starve user-facing hydration of tokens. Phase 2 can add a two-tier scheme so request-driven hydration preempts background warming: either a reserved sub-allocation for the "interactive" lane, or a weighted acquire where background callers request with lower priority / a longer acceptable wait. Phase 1 ships a single fair bucket; only build lanes if prod shows interactive latency regressions.

## Proposed implementation (outline — do not build from this section alone, read the Decisions)

1. New module `warships/api/rate_limit.py`: loads the Lua once, exposes `acquire(tokens=1) -> bool` (True = proceed, False = give up after the wait budget). Encapsulates the Redis client, fail-open, and the bounded wait loop.
2. `_request_api_payload`: call `acquire()` before the HTTP `get`; on False, log at INFO with the endpoint and return `None`. Gate the whole thing behind `WG_RATE_LIMIT_ENABLED`.
3. Reuse the existing prod Redis (the cache/broker Redis) — no new infra. Use a dedicated key namespace so `allkeys-lru` eviction of the bucket key is harmless (it just refills full next call).
4. Config (env, all with safe defaults):
   - `WG_RATE_LIMIT_ENABLED` (default `0` → ship dark, enable in prod after validation)
   - `WG_RATE_LIMIT_RPS` (default ~`9` — headroom under the ~10 ceiling)
   - `WG_RATE_LIMIT_BURST` (default small, e.g. `9`–`18`)
   - `WG_RATE_LIMIT_MAX_WAIT` (seconds a caller will block before giving up, default ~`5`)
   - `WG_RATE_LIMIT_REDIS_TIMEOUT` (short, e.g. `0.25s`, for fail-open)
5. Document all knobs in `CLAUDE.md` (Server runtime env → a new "WG rate limiting" group) and reconcile this runbook on implementation.

## Observability

Post-rollout, these should hold:

- **`407 REQUEST_LIMIT_EXCEEDED` count drops to ~0** across all queues (the headline success metric). Baseline: 252/6h during this session's seasonstats bursts. Grep: `journalctl -u 'battlestats-celery-*' | grep -c REQUEST_LIMIT_EXCEEDED`.
- **Wait/timeout counters**: how often callers blocked, and how often they hit `MAX_WAIT` and returned `None` (a high timeout rate means `RPS` is too low for demand, or a runaway fan-out is still present).
- **Fail-open counter**: how often Redis was unavailable (should be ~0; non-zero means Redis health issue).
- **Crawl/enrichment throughput & ETA** must not regress materially — confirm via `check_enrichment_crawler.sh` and crawl page-rate logs. A token bucket *smooths* but should not *reduce* sustained throughput if `RPS` is set near the real ceiling.

## Test plan

- **Unit (Lua/bucket):** against a real Redis or `fakeredis` — refill math, burst cap, decrement-to-empty, refill-after-wait, TTL set. Assert two rapid acquires past capacity: first wins, second is denied with a sane wait hint.
- **Concurrency:** N threads/processes hammering `acquire()` issue ≤ capacity + rate×elapsed tokens over a window (no over-issue race).
- **Fail-open:** patch the Redis client to raise → `acquire()` returns True and increments the fail-open counter; `_request_api_payload` still makes the call.
- **Timeout path:** bucket empty + `MAX_WAIT` small → `acquire()` returns False → `_request_api_payload` returns `None` without calling `requests`. Pair with a test that the seasonstats caller treats that `None` as a short-TTL miss (regression guard linking to `8210db7`).
- **Kill switch:** `WG_RATE_LIMIT_ENABLED=0` → `acquire()` is bypassed entirely (no Redis call).

## Rollout

1. Ship with `WG_RATE_LIMIT_ENABLED=0` (dark). No behavior change; bucket code present and unit-tested.
2. Enable in prod (`=1`) with conservative `RPS` (~9). Watch the 407 counter (target ~0), timeout counter, and crawl/enrichment ETA for one full crawl cycle + a landing-best warm cycle.
3. Tune `RPS`/`BURST` to the largest value that keeps 407s at ~0 with acceptable timeout rate.
4. **Follow-up (separate change):** once stable, evaluate loosening the now-redundant per-path delays (D5) and whether priority lanes (D6) are warranted.

## Follow-ups / open questions

- **Daily cap vs per-second cap:** this design targets the per-second `REQUEST_LIMIT_EXCEEDED`. If the daily quota is ever the binding constraint, a second (per-day) bucket on the same key namespace covers it — note but don't build unless observed.
- **Retire per-path throttles?** Tracked by D5; explicitly a later change.
- **Priority lanes?** Tracked by D6; build only if interactive latency regresses.
- **Multiple app-ids?** If WG ever issues a second `application_id` (e.g. to split interactive vs batch), the bucket key already keys on app-id, so it generalizes to one bucket per id with no redesign.
