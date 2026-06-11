"""Coverage for the bounded request-thread WG HTTP timeout (api/client.py).

Tier-2b of runbook-player-refresh-latency-2026-06-10.md: a synchronous WG call
still exists on the gunicorn request thread (the cold player-lookup path,
views.get_object -> _fetch_player_id_by_name / update_player_data). A
slow/unreachable WG there used to hang the worker into a 502. The fix bounds the
per-attempt HTTP timeout tightly on the request thread (mirroring the rate
limiter's request-thread detection) while keeping the longer budget for celery
background tasks.

These tests mock the requests session seam so they never touch the network.
"""
import os
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase

from warships.api import client


class RequestThreadTimeoutSelectionTests(TestCase):
    def setUp(self):
        # APP_ID must be set or _request_api_payload short-circuits before the
        # HTTP call. Patch the module attribute directly (read at call time).
        app_id = patch.object(client, "APP_ID", "test-app-id")
        app_id.start()
        self.addCleanup(app_id.stop)
        # Keep the rate limiter inert (no Redis in the lean gate anyway).
        rl = patch.dict(os.environ, {"WG_RATE_LIMIT_ENABLED": "0"})
        rl.start()
        self.addCleanup(rl.stop)

    def _ok_response(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"status": "ok", "data": {"x": 1}}
        return resp

    def test_request_thread_uses_short_timeout(self):
        with patch("warships.api.rate_limiter._in_request_thread",
                   return_value=True), \
                patch.object(client, "_get_session") as mock_session:
            mock_session.return_value.get.return_value = self._ok_response()
            client.make_api_request("account/list/", {"search": "x"})
            _, kwargs = mock_session.return_value.get.call_args
            self.assertEqual(
                kwargs["timeout"], client.REQUEST_THREAD_TIMEOUT_SECONDS)
            # Must be tight enough that timeout*3 (Retry adapter) stays under the
            # gunicorn 25s backstop.
            self.assertLessEqual(kwargs["timeout"] * 3, 25)

    def test_background_thread_uses_long_timeout(self):
        with patch("warships.api.rate_limiter._in_request_thread",
                   return_value=False), \
                patch.object(client, "_get_session") as mock_session:
            mock_session.return_value.get.return_value = self._ok_response()
            client.make_api_request("account/list/", {"search": "x"})
            _, kwargs = mock_session.return_value.get.call_args
            self.assertEqual(kwargs["timeout"], client.REQUEST_TIMEOUT_SECONDS)

    def test_request_thread_timeout_is_short(self):
        # Guards against a regression that loosens the request-thread bound.
        self.assertLessEqual(client.REQUEST_THREAD_TIMEOUT_SECONDS, 10)
        self.assertLess(
            client.REQUEST_THREAD_TIMEOUT_SECONDS, client.REQUEST_TIMEOUT_SECONDS)

    def test_slow_wg_fails_fast_returns_none_not_hang(self):
        # A timed-out WG call must surface as None (-> 404-fast for a genuinely
        # missing player, or a transient the Tier-1 client retry handles), never
        # propagate or hang the request thread.
        with patch("warships.api.rate_limiter._in_request_thread",
                   return_value=True), \
                patch.object(client, "_get_session") as mock_session:
            mock_session.return_value.get.side_effect = requests.exceptions.ReadTimeout(
                "WG too slow")
            result = client.make_api_request("account/list/", {"search": "x"})
            self.assertIsNone(result)

    def test_request_timeout_selector_picks_by_context(self):
        with patch("warships.api.rate_limiter._in_request_thread",
                   return_value=True):
            self.assertEqual(
                client._request_timeout_seconds(),
                client.REQUEST_THREAD_TIMEOUT_SECONDS)
        with patch("warships.api.rate_limiter._in_request_thread",
                   return_value=False):
            self.assertEqual(
                client._request_timeout_seconds(),
                client.REQUEST_TIMEOUT_SECONDS)
