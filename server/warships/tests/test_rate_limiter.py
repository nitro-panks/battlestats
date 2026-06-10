"""Coverage for the global WG token-bucket rate limiter (api/rate_limiter.py).

Two layers:
- Control-flow tests (always run): mock the Redis seam and assert acquire()'s
  gating, blocking/retry, caller-context wait budget, and fail-open behavior.
- Token-bucket behavior (skipped without a reachable REDIS_URL): run the actual
  Lua against real Redis to prove the bucket allows a burst then throttles and
  refills. The Docker release gate has Redis; the sqlite lean gate skips it.
"""
import os
import time
from unittest.mock import patch

from django.test import TestCase

from warships.api import rate_limiter


class AcquireControlFlowTests(TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "WG_RATE_LIMIT_ENABLED": "1",
            "WG_RATE_LIMIT_PER_SEC": "9",
            "WG_RATE_LIMIT_BURST": "18",
        })
        env.start()
        self.addCleanup(env.stop)

    @patch("warships.api.rate_limiter._consume")
    @patch("warships.api.rate_limiter._get_client", return_value=(None, None))
    def test_noop_when_no_redis(self, _gc, mock_consume):
        rate_limiter.acquire()
        mock_consume.assert_not_called()

    @patch("warships.api.rate_limiter._consume")
    @patch("warships.api.rate_limiter._get_client")
    def test_noop_when_disabled(self, mock_gc, mock_consume):
        with patch.dict(os.environ, {"WG_RATE_LIMIT_ENABLED": "0"}):
            rate_limiter.acquire()
        mock_gc.assert_not_called()
        mock_consume.assert_not_called()

    @patch("warships.api.rate_limiter._consume")
    @patch("warships.api.rate_limiter._get_client")
    def test_noop_when_rate_non_positive(self, mock_gc, mock_consume):
        with patch.dict(os.environ, {"WG_RATE_LIMIT_PER_SEC": "0"}):
            rate_limiter.acquire()
        mock_gc.assert_not_called()
        mock_consume.assert_not_called()

    @patch("time.sleep")
    @patch("warships.api.rate_limiter._consume", return_value=(True, 0))
    @patch("warships.api.rate_limiter._get_client", return_value=("c", "s"))
    def test_returns_immediately_when_token_available(self, _gc, mock_consume, mock_sleep):
        rate_limiter.acquire()
        mock_consume.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("warships.api.rate_limiter._in_request_thread", return_value=False)
    @patch("time.sleep")
    @patch("warships.api.rate_limiter._get_client", return_value=("c", "s"))
    def test_blocks_then_proceeds_when_token_frees(self, _gc, mock_sleep, _ctx):
        with patch("warships.api.rate_limiter._consume",
                   side_effect=[(False, 50), (False, 50), (True, 0)]) as mock_consume:
            rate_limiter.acquire()
        self.assertEqual(mock_consume.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("warships.api.rate_limiter._in_request_thread", return_value=True)
    @patch("warships.api.rate_limiter._consume", return_value=(False, 1000))
    @patch("warships.api.rate_limiter._get_client", return_value=("c", "s"))
    def test_request_thread_fails_open_fast(self, _gc, _consume, _ctx):
        # A saturated bucket must NOT park a request thread: tiny budget, then
        # proceed. Real (short) sleeps here — assert it returns promptly.
        with patch.dict(os.environ, {"WG_RATE_LIMIT_REQUEST_MAX_WAIT": "0.05"}):
            start = time.monotonic()
            rate_limiter.acquire()
            self.assertLess(time.monotonic() - start, 1.5)

    @patch("time.sleep")
    @patch("warships.api.rate_limiter._consume", side_effect=RuntimeError("redis down"))
    @patch("warships.api.rate_limiter._get_client", return_value=("c", "s"))
    def test_fails_open_on_redis_error(self, _gc, mock_consume, _sleep):
        rate_limiter.acquire()  # must not raise
        mock_consume.assert_called_once()


def _reachable_redis():
    url = os.getenv("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis
        client = redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        client.ping()
        return client
    except Exception:
        return None


class TokenBucketRedisBehaviorTests(TestCase):
    """Exercise the real Lua bucket. Skipped without a reachable Redis."""

    def setUp(self):
        self.client = _reachable_redis()
        if self.client is None:
            self.skipTest("no reachable REDIS_URL")
        self.key = "wg:ratelimit:test"
        self.client.delete(self.key)
        self.addCleanup(self.client.delete, self.key)
        self.script = self.client.register_script(rate_limiter._LUA)

    def _consume(self, rate, capacity, tokens=1):
        allowed, wait_ms = self.script(
            keys=[self.key], args=[rate, capacity, tokens])
        return bool(allowed), int(wait_ms)

    def test_allows_burst_up_to_capacity_then_throttles(self):
        rate, capacity = 5, 5
        # Fresh bucket starts full → first `capacity` consumes are allowed.
        self.assertTrue(all(self._consume(rate, capacity)[0] for _ in range(5)))
        # Next one in the same instant is denied with a positive wait hint.
        allowed, wait_ms = self._consume(rate, capacity)
        self.assertFalse(allowed)
        self.assertGreater(wait_ms, 0)

    def test_refills_over_time(self):
        rate, capacity = 100, 1  # 100 tokens/s → ~10ms per token
        self.assertTrue(self._consume(rate, capacity)[0])
        self.assertFalse(self._consume(rate, capacity)[0])
        time.sleep(0.05)  # > 10ms, so at least one token has refilled
        self.assertTrue(self._consume(rate, capacity)[0])
