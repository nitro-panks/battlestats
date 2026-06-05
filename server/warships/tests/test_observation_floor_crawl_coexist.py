"""Coverage for the observation floor's crawl-coexistence behavior.

Previously `ensure_daily_battle_observations_task` skipped entirely whenever a
clan crawl held the realm lock. Because the crawl lock is held for hours per
pass, active-player observations were starved for days, producing lumpy battle
history. The floor now runs *during* a crawl at a reduced per-player pace so
combined WG load stays under the ~10 req/s app budget.

See runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from warships.tasks import (
    _clan_crawl_lock_key,
    _daily_observation_floor_lock_key,
    ensure_daily_battle_observations_task,
)


class ObservationFloorCrawlCoexistTests(TestCase):
    def setUp(self):
        cache.clear()

    def _run(self, realm="na"):
        with patch("django.core.management.call_command") as mock_cc:
            result = ensure_daily_battle_observations_task.apply(
                kwargs={"realm": realm}).get()
        return result, mock_cc

    def test_runs_at_reduced_pace_during_crawl_instead_of_skipping(self):
        cache.set(_clan_crawl_lock_key("na"), "crawl-task-id", 300)

        result, mock_cc = self._run("na")

        # Must NOT skip — the whole point of step 2.
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["crawl_coexist"])
        mock_cc.assert_called_once()
        kwargs = mock_cc.call_args.kwargs
        self.assertEqual(kwargs["delay"], 0.8)   # crawl-coexist default
        # Floor lock is released afterwards.
        self.assertIsNone(cache.get(_daily_observation_floor_lock_key("na")))

    def test_runs_at_normal_pace_without_crawl(self):
        result, mock_cc = self._run("na")

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["crawl_coexist"])
        kwargs = mock_cc.call_args.kwargs
        self.assertEqual(kwargs["delay"], 0.3)   # normal default

    def test_still_skips_when_another_floor_sweep_is_running(self):
        # The self-dedup lock still wins — only the crawl deferral changed.
        cache.set(_daily_observation_floor_lock_key("na"), "other-sweep", 300)

        result, mock_cc = self._run("na")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already-running")
        mock_cc.assert_not_called()
