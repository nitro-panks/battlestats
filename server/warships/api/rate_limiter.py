"""Global token-bucket rate limiter for outbound Wargaming API requests.

Wargaming enforces a per-application-id budget (~10 req/s, shared across all
three realm hosts). Battlestats runs a single ``WG_APP_ID`` across every worker
queue *and* the gunicorn request threads, so the only correct place to enforce
that budget is a **single, process-shared** limiter. This module is that
limiter; it is invoked once at the sole WG egress point
(``api/client.py::_request_api_payload``), so it covers 100% of WG traffic.

Why Redis (not an in-process bucket): the cap is global across many OS
processes (default/-c3, hydration/-c5, background/-c3, crawls/-c1, gunicorn).
An in-process bucket per worker would each independently allow the full rate.
The bucket state lives in Redis and is mutated by an atomic Lua script (clock
from ``redis.call('TIME')`` so there is no cross-worker skew).

Design notes:
- **Fail-open everywhere.** No Redis (e.g. ``REDIS_URL`` unset in tests), a
  Redis error, a disabled flag, or an exhausted wait budget all let the request
  proceed. A rate limiter must never become a hard dependency that takes the
  site down — better to occasionally nick the WG ceiling than to stall.
- **Caller-context wait budget.** Background celery tasks block up to
  ``WG_RATE_LIMIT_MAX_WAIT`` for a token (they have nowhere to be). Request
  threads (a synchronous WG call still exists on the request path, e.g.
  ``_fetch_player_id_by_name``) get a near-zero budget
  (``WG_RATE_LIMIT_REQUEST_MAX_WAIT``) then fail open, so a saturated bucket can
  never park gunicorn threads into pool exhaustion.

Env (all optional; pinned on prod in server/deploy/deploy_to_droplet.sh):
- ``WG_RATE_LIMIT_ENABLED`` (1) — master kill switch.
- ``WG_RATE_LIMIT_PER_SEC`` (9) — sustained token refill rate (<10 for margin).
- ``WG_RATE_LIMIT_BURST`` (18) — bucket capacity (max short burst).
- ``WG_RATE_LIMIT_MAX_WAIT`` (8.0) — max seconds a background task blocks.
- ``WG_RATE_LIMIT_REQUEST_MAX_WAIT`` (0.5) — max seconds a request thread blocks.
- ``WG_RATE_LIMIT_KEY`` (wg:ratelimit) — Redis bucket key (raw, no cache prefix).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from django.conf import settings

logger = logging.getLogger(__name__)

# Atomic token-bucket. KEYS[1]=bucket key. ARGV: rate (tokens/sec), capacity,
# requested. Returns {allowed(0|1), wait_ms}. Clock is Redis-side (TIME) so all
# workers share one monotonic-ish reference. Caller guarantees rate>0.
_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])

local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

local delta = now - ts
if delta < 0 then delta = 0 end
tokens = math.min(capacity, tokens + delta * rate / 1000.0)

local allowed = 0
local wait_ms = 0
if tokens >= requested then
  allowed = 1
  tokens = tokens - requested
else
  wait_ms = math.ceil((requested - tokens) * 1000.0 / rate)
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, math.ceil(capacity * 1000.0 / rate) + 1000)
return {allowed, wait_ms}
"""

_lock = threading.Lock()
_client = None
_client_pid = None
_script = None


def _get_client():
    """Return (redis_client, registered_script) or (None, None) when no Redis.

    Lazily built per-process (re-created after a fork — celery/gunicorn fork
    workers) so a connection is never shared across processes.
    """
    global _client, _client_pid, _script
    url = getattr(settings, "REDIS_URL", "") or os.getenv("REDIS_URL", "")
    if not url:
        return None, None
    pid = os.getpid()
    if _client is not None and _client_pid == pid:
        return _client, _script
    with _lock:
        if _client is None or _client_pid != pid:
            try:
                import redis
            except Exception:  # pragma: no cover - redis is a prod dependency
                return None, None
            client = redis.from_url(
                url, socket_timeout=1, socket_connect_timeout=1)
            _client = client
            _client_pid = pid
            _script = client.register_script(_LUA)
        return _client, _script


def _in_request_thread() -> bool:
    """True when NOT executing inside a celery task (i.e. a gunicorn request)."""
    try:
        from celery import current_task
        return not (current_task and getattr(current_task, "request", None)
                    and current_task.request.id)
    except Exception:
        return True


def _consume(client, script, rate, capacity, tokens):
    """Single atomic bucket check. Returns (allowed: bool, wait_ms: int)."""
    allowed, wait_ms = script(
        keys=[os.getenv("WG_RATE_LIMIT_KEY", "wg:ratelimit")],
        args=[rate, capacity, tokens], client=client)
    return bool(allowed), int(wait_ms)


def acquire(tokens: int = 1) -> None:
    """Block until a WG-request token is available, then return (fail-open).

    No-op when disabled, misconfigured, or Redis is unavailable.
    """
    if os.getenv("WG_RATE_LIMIT_ENABLED", "1") != "1":
        return
    try:
        rate = float(os.getenv("WG_RATE_LIMIT_PER_SEC", "9"))
        capacity = float(os.getenv("WG_RATE_LIMIT_BURST", "18"))
    except ValueError:
        return
    if rate <= 0 or capacity <= 0:  # guards div-by-zero in the bucket math
        return

    client, script = _get_client()
    if client is None:
        return

    request_thread = _in_request_thread()
    try:
        max_wait = float(os.getenv(
            "WG_RATE_LIMIT_REQUEST_MAX_WAIT", "0.5") if request_thread
            else os.getenv("WG_RATE_LIMIT_MAX_WAIT", "8"))
    except ValueError:
        max_wait = 0.5 if request_thread else 8.0

    deadline = time.monotonic() + max_wait
    waited = 0.0
    while True:
        try:
            allowed, wait_ms = _consume(client, script, rate, capacity, tokens)
        except Exception as exc:
            logger.warning("WG rate limiter unavailable (failing open): %s", exc)
            return
        if allowed:
            if waited > 0:
                logger.debug("WG rate limiter: waited %.0fms for a token",
                             waited * 1000)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "WG rate limiter: %s wait budget %.2fs exhausted, proceeding "
                "(rate=%.1f/s burst=%.0f)",
                "request" if request_thread else "background",
                max_wait, rate, capacity)
            return
        nap = min(wait_ms / 1000.0, remaining)
        if nap <= 0:
            nap = min(0.01, remaining)
        time.sleep(nap)
        waited += nap
