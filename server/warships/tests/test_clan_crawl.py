import os
import time
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.clan_crawl import crawl_clan_members
from warships.models import Clan


class CrawlCoreOnlyFlagTests(TestCase):
    """R2: CLAN_CRAWL_CORE_ONLY env forces core_only for the scheduled crawl
    AND the watchdog re-dispatch (both call the task without core_only=True)."""

    @patch("warships.clan_crawl.run_clan_crawl")
    def test_env_flag_forces_core_only(self, mock_run):
        mock_run.return_value = {"players_saved": 0, "clans_found": 0}
        from warships.tasks import crawl_all_clans_task
        with patch.dict(os.environ, {"CLAN_CRAWL_CORE_ONLY": "1"}):
            crawl_all_clans_task.apply(
                kwargs={"realm": "na", "limit": 1}).get()
        self.assertTrue(mock_run.call_args.kwargs.get("core_only"))

    @patch("warships.clan_crawl.run_clan_crawl")
    def test_no_flag_keeps_core_only_false(self, mock_run):
        mock_run.return_value = {"players_saved": 0, "clans_found": 0}
        from warships.tasks import crawl_all_clans_task
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAN_CRAWL_CORE_ONLY", None)
            crawl_all_clans_task.apply(
                kwargs={"realm": "eu", "limit": 1}).get()
        self.assertFalse(mock_run.call_args.kwargs.get("core_only"))


class ClanCrawlAggregateTests(TestCase):
    @patch("warships.clan_crawl.fetch_players_bulk")
    @patch("warships.clan_crawl.fetch_member_ids")
    @patch("warships.clan_crawl.fetch_clan_info")
    def test_crawl_clan_members_populates_cached_aggregates_for_realm(
        self,
        mock_fetch_clan_info,
        mock_fetch_member_ids,
        mock_fetch_players_bulk,
    ):
        recent_battle_time = int(timezone.now().timestamp())

        mock_fetch_clan_info.return_value = {
            "clan_id": 5001,
            "name": "EU Clan",
            "tag": "EUC",
            "members_count": 2,
            "description": "",
            "leader_id": 9001,
            "leader_name": "CaptainEU",
        }
        mock_fetch_member_ids.return_value = [9001, 9002]
        mock_fetch_players_bulk.return_value = {
            "9001": {
                "account_id": 9001,
                "nickname": "CaptainEU",
                "created_at": 1700000000,
                "last_battle_time": recent_battle_time,
                "hidden_profile": False,
                "statistics": {
                    "battles": 200,
                    "pvp": {
                        "battles": 100,
                        "wins": 60,
                        "losses": 40,
                        "frags": 50,
                        "survived_battles": 25,
                    },
                },
            },
            "9002": {
                "account_id": 9002,
                "nickname": "MateEU",
                "created_at": 1700000000,
                "last_battle_time": recent_battle_time,
                "hidden_profile": False,
                "statistics": {
                    "battles": 300,
                    "pvp": {
                        "battles": 200,
                        "wins": 90,
                        "losses": 110,
                        "frags": 70,
                        "survived_battles": 40,
                    },
                },
            },
        }

        result = crawl_clan_members(
            [{"clan_id": 5001}],
            realm='eu',
            core_only=True,
            request_delay=0,
        )

        clan = Clan.objects.get(clan_id=5001, realm='eu')
        self.assertEqual(result["clans_processed"], 1)
        self.assertEqual(result["players_saved"], 2)
        self.assertEqual(clan.cached_total_battles, 300)
        self.assertEqual(clan.cached_total_wins, 150)
        self.assertEqual(clan.cached_active_member_count, 2)
        self.assertEqual(clan.cached_clan_wr, 50.0)


class ClanCrawlResumeWindowTests(TestCase):
    """Run-scoped resume: `fresh_after` narrows the resume skip to clans already
    fetched during the current pass, so clans last fetched before the pass began
    are re-crawled (periodic refresh) instead of skipped forever.

    See runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.
    """

    def setUp(self):
        self.now = timezone.now()
        # A clan already in the DB, last fetched 10 days ago.
        self.last_fetch = self.now - timedelta(days=10)
        Clan.objects.create(
            clan_id=7001, realm='na', name='Old', tag='OLD',
            last_fetch=self.last_fetch,
        )

    @patch("warships.clan_crawl.fetch_clan_info")
    def _run(self, mock_info, **kwargs):
        # members_count=0 keeps the per-clan path short (no member fetches).
        mock_info.return_value = {
            "clan_id": 7001, "name": "Old", "tag": "OLD", "members_count": 0,
        }
        result = crawl_clan_members(
            [{"clan_id": 7001}], realm='na', core_only=True,
            request_delay=0, **kwargs,
        )
        return result, mock_info

    def test_resume_with_fresh_after_recrawls_clan_fetched_before_pass(self):
        # Pass started after the clan's last_fetch → clan is stale → re-crawl.
        result, mock_info = self._run(
            resume=True, fresh_after=self.now - timedelta(days=1))
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["clans_processed"], 1)
        self.assertTrue(mock_info.called)

    def test_resume_with_fresh_after_skips_clan_fetched_during_pass(self):
        # Pass started before the clan's last_fetch → already done this pass → skip.
        result, mock_info = self._run(
            resume=True, fresh_after=self.now - timedelta(days=20))
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["clans_processed"], 0)
        self.assertFalse(mock_info.called)

    def test_resume_without_fresh_after_skips_any_fetched_clan(self):
        # Original manual --resume semantics: any ever-fetched clan is skipped.
        result, mock_info = self._run(resume=True, fresh_after=None)
        self.assertEqual(result["skipped"], 1)
        self.assertFalse(mock_info.called)

    def test_no_resume_always_crawls(self):
        result, mock_info = self._run(resume=False)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["clans_processed"], 1)
        self.assertTrue(mock_info.called)


class ClanCrawlEnqueueDedupTests(TestCase):
    """Option B (runbook-crawls-queue-depth-alarm-2026-06-12): the daily Beat
    cron + watchdog enqueue through a per-realm pending flag so at most one
    crawl_all_clans_task per realm is ever queued/running — the crawls queue
    idles near zero instead of accumulating duplicate crawl messages behind the
    single-slot worker."""

    def setUp(self):
        cache.clear()

    def _pending(self, realm):
        from warships.tasks import _clan_crawl_pending_key
        return cache.get(_clan_crawl_pending_key(realm))

    @patch("warships.tasks.crawl_all_clans_task.delay")
    def test_dispatch_enqueues_once_when_idle(self, mock_delay):
        from warships.tasks import dispatch_clan_crawl_task
        res = dispatch_clan_crawl_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(res["status"], "enqueued")
        mock_delay.assert_called_once_with(resume=True, realm="na")
        self.assertIsNotNone(self._pending("na"))

    @patch("warships.tasks.crawl_all_clans_task.delay")
    def test_dispatch_is_idempotent_when_already_queued(self, mock_delay):
        from warships.tasks import dispatch_clan_crawl_task
        dispatch_clan_crawl_task.apply(kwargs={"realm": "na"}).get()
        res = dispatch_clan_crawl_task.apply(kwargs={"realm": "na"}).get()
        # Second dispatch must NOT enqueue a duplicate — pending flag suppresses it.
        self.assertEqual(res["status"], "skipped-already-queued")
        mock_delay.assert_called_once()

    @patch("warships.tasks.crawl_all_clans_task.delay")
    def test_dispatch_skips_when_realm_already_running(self, mock_delay):
        from warships.tasks import dispatch_clan_crawl_task, _clan_crawl_lock_key
        cache.set(_clan_crawl_lock_key("na"), "some-task-id", timeout=3600)
        res = dispatch_clan_crawl_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(res["status"], "skipped-running")
        mock_delay.assert_not_called()
        self.assertIsNone(self._pending("na"))

    @patch("warships.tasks.crawl_all_clans_task.delay")
    def test_dispatch_is_per_realm(self, mock_delay):
        from warships.tasks import dispatch_clan_crawl_task
        dispatch_clan_crawl_task.apply(kwargs={"realm": "na"}).get()
        res = dispatch_clan_crawl_task.apply(kwargs={"realm": "eu"}).get()
        # A different realm is independent — eu still enqueues while na is queued.
        self.assertEqual(res["status"], "enqueued")
        self.assertEqual(mock_delay.call_count, 2)

    @patch("warships.clan_crawl.run_clan_crawl")
    def test_task_clears_pending_flag_on_start(self, mock_run):
        from warships.tasks import crawl_all_clans_task, _clan_crawl_pending_key
        mock_run.return_value = {"players_saved": 0, "clans_found": 0}
        cache.set(_clan_crawl_pending_key("na"), time.time(), timeout=3600)
        crawl_all_clans_task.apply(kwargs={"realm": "na", "limit": 1}).get()
        self.assertIsNone(self._pending("na"))

    @patch("warships.clan_crawl.run_clan_crawl")
    def test_task_clears_pending_even_on_already_running_skip(self, mock_run):
        # A duplicate that hits the already-running skip path must still clear the
        # pending flag (cleared before the early return) so the realm isn't wedged.
        from warships.tasks import crawl_all_clans_task, _clan_crawl_lock_key, _clan_crawl_pending_key
        cache.set(_clan_crawl_lock_key("na"), "running-id", timeout=3600)
        cache.set(_clan_crawl_pending_key("na"), time.time(), timeout=3600)
        res = crawl_all_clans_task.apply(kwargs={"realm": "na", "limit": 1}).get()
        self.assertEqual(res["reason"], "already-running")
        mock_run.assert_not_called()
        self.assertIsNone(self._pending("na"))

    def test_watchdog_clears_stale_pending_when_fully_idle(self):
        from warships.tasks import (
            ensure_crawl_all_clans_running_task, _clan_crawl_pending_key,
            CLAN_CRAWL_PENDING_STALE_AFTER)
        cache.set(_clan_crawl_pending_key("na"),
                  time.time() - CLAN_CRAWL_PENDING_STALE_AFTER - 60, timeout=3600)
        res = ensure_crawl_all_clans_running_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(res["status"], "recovered")
        self.assertIsNone(self._pending("na"))

    def test_watchdog_keeps_pending_while_another_realm_crawls(self):
        # eu legitimately waits its turn behind a running na crawl — its pending
        # flag must NOT be cleared even if it is old.
        from warships.tasks import (
            ensure_crawl_all_clans_running_task, _clan_crawl_lock_key,
            _clan_crawl_pending_key, CLAN_CRAWL_PENDING_STALE_AFTER)
        cache.set(_clan_crawl_lock_key("na"), "running-id", timeout=3600)
        cache.set(_clan_crawl_pending_key("eu"),
                  time.time() - CLAN_CRAWL_PENDING_STALE_AFTER - 60, timeout=3600)
        res = ensure_crawl_all_clans_running_task.apply(kwargs={"realm": "eu"}).get()
        self.assertEqual(res["status"], "skipped")
        self.assertIsNotNone(self._pending("eu"))


class BenchmarkCrawlProductivityTests(TestCase):
    """The read-only clan-crawl benchmark emits the metric structure, computes
    catalog coverage / implied pass cadence, and reflects liveness cache keys."""

    def _json(self):
        import io
        import json
        from django.core.management import call_command
        out = io.StringIO()
        call_command("benchmark_crawl_productivity", json=True, stdout=out)
        return json.loads(out.getvalue())

    def test_coverage_and_pass_cadence(self):
        now = timezone.now()
        # 4 na clans, 1 fetched in-window, 1 stale, 1 never-fetched.
        Clan.objects.create(clan_id=1, realm="na", last_fetch=now - timedelta(hours=2))
        Clan.objects.create(clan_id=2, realm="na", last_fetch=now - timedelta(hours=30))
        Clan.objects.create(clan_id=3, realm="na", last_fetch=now - timedelta(hours=1))
        Clan.objects.create(clan_id=4, realm="na", last_fetch=None)

        data = self._json()
        na = data["realms"]["na"]
        self.assertEqual(na["clans_total"], 4)
        self.assertEqual(na["clans_fetched_24h"], 2)       # clans 1 & 3
        self.assertEqual(na["clan_coverage_pct"], 0.5)
        self.assertEqual(na["clans_never_fetched"], 1)
        # 4 clans / 2-per-day → ~2-day full pass
        self.assertEqual(na["implied_full_pass_days"], 2.0)
        self.assertIn("totals", data)
        self.assertEqual(data["totals"]["clans_total"], 4)

    def test_liveness_reflects_cache_keys(self):
        from warships.tasks import (
            _clan_crawl_lock_key, _clan_crawl_pass_marker_key)
        Clan.objects.create(clan_id=9, realm="eu", last_fetch=timezone.now())
        cache.set(_clan_crawl_lock_key("eu"), "task-id", timeout=3600)
        cache.set(_clan_crawl_pass_marker_key("eu"),
                  timezone.now() - timedelta(hours=3), timeout=3600)
        try:
            data = self._json()
        finally:
            cache.delete(_clan_crawl_lock_key("eu"))
            cache.delete(_clan_crawl_pass_marker_key("eu"))
        lv = data["realms"]["eu"]["liveness"]
        self.assertTrue(lv["crawl_lock_held"])
        self.assertIsNotNone(lv["pass_marker_age_s"])
        self.assertEqual(data["totals"]["realms_crawling"], 1)
        # na has no lock set → not crawling
        self.assertFalse(data["realms"]["na"]["liveness"]["crawl_lock_held"])
