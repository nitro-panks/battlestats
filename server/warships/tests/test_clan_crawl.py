import os
from datetime import timedelta
from unittest.mock import patch

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
