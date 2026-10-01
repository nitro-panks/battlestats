import os
import time
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.clan_crawl import crawl_clan_members
from warships.data import reconcile_clan_departures
from warships.models import Clan, Player


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


class ClanDepartureReconcileTests(TestCase):
    """The roster sync only ever ADDS members; without a departure pass a player
    who LEFT lingers in clan.player_set forever (a "ghost" inflating the member
    list), because the active-player observation floor never sweeps a departed-
    then-inactive player. The crawl must clear the clan FK on stored members
    absent from the live WG roster, using the ids it already fetched."""

    def test_reconcile_clears_only_members_absent_from_roster(self):
        clan = Clan.objects.create(clan_id=6101, realm='na', name='Y', tag='Y')
        stay = Player.objects.create(player_id=1, realm='na', name='Stay', clan=clan)
        go = Player.objects.create(player_id=2, realm='na', name='Go', clan=clan)

        cleared = reconcile_clan_departures(clan, [1], realm='na')

        stay.refresh_from_db()
        go.refresh_from_db()
        self.assertEqual(cleared, 1)
        self.assertEqual(stay.clan_id, clan.pk)   # still in roster → kept
        self.assertIsNone(go.clan_id)             # departed → cleared

    def test_reconcile_invalidates_served_members_cache(self):
        from warships.data import clan_members_cache_key
        clan = Clan.objects.create(clan_id=6102, realm='na', name='Z', tag='Z')
        Player.objects.create(player_id=3, realm='na', name='Gone', clan=clan)
        key = clan_members_cache_key(6102, realm='na')
        cache.set(key, ['stale-roster'], timeout=300)

        reconcile_clan_departures(clan, [99], realm='na')

        # The served members cache is dropped so the clan page reflects the
        # departure immediately instead of waiting out the 5-min TTL. Keyed via
        # the shared builder — never a literal, which is how the read drifted to
        # v4 while every delete stayed behind.
        self.assertIsNone(cache.get(key))

    def test_update_clan_data_invalidates_served_members_cache(self):
        """`update_clan_data` (data.py) must drop the members payload.

        Isolated from reconcile deliberately: `update_clan_data` deletes the
        key and *then* calls `reconcile_clan_departures`, which deletes the
        same key. With a non-empty roster this test would pass even with the
        invalidation removed. Mocking the member fetch to [] makes reconcile
        return at its `if not live_member_ids` guard without touching the
        cache, so a green here proves the delete in update_clan_data fired.
        """
        from warships.data import clan_members_cache_key, update_clan_data

        clan = Clan.objects.create(
            clan_id=6200, realm='na', name='Q', tag='Q', last_fetch=None)
        key = clan_members_cache_key(6200, realm='na')
        cache.set(key, ['stale-roster'], timeout=300)

        with patch('warships.data._fetch_clan_data',
                   return_value={'members_count': 1, 'tag': 'Q', 'name': 'Q'}), \
             patch('warships.data._fetch_clan_member_ids', return_value=[]):
            update_clan_data('6200', realm='na')

        self.assertIsNone(cache.get(key))
        self.assertEqual(clan.clan_id, 6200)

    def test_refresh_clan_cached_aggregates_invalidates_members_cache(self):
        """The aggregates refresh is the ONLY invalidation covering a roster
        that gained a member — reconcile is gated on `if cleared:` and does
        nothing when nobody left. No WG calls here, so no mocking is needed.
        """
        from warships.data import (
            clan_members_cache_key, refresh_clan_cached_aggregates)

        Clan.objects.create(clan_id=6201, realm='na', name='R', tag='R')
        key = clan_members_cache_key(6201, realm='na')
        cache.set(key, ['stale-roster'], timeout=300)

        refresh_clan_cached_aggregates('6201', realm='na')

        self.assertIsNone(cache.get(key))

    def test_reconcile_empty_roster_does_not_orphan_members(self):
        # A transient upstream failure (no member ids) must NOT orphan a clan.
        clan = Clan.objects.create(clan_id=6100, realm='na', name='X', tag='X')
        member = Player.objects.create(
            player_id=42, realm='na', name='Member', clan=clan)

        cleared = reconcile_clan_departures(clan, [], realm='na')

        member.refresh_from_db()
        self.assertEqual(cleared, 0)
        self.assertEqual(member.clan_id, clan.pk)

    @patch("warships.clan_crawl.fetch_players_bulk")
    @patch("warships.clan_crawl.fetch_member_ids")
    @patch("warships.clan_crawl.fetch_clan_info")
    def test_crawl_clears_departed_member_keeps_current(
        self, mock_fetch_clan_info, mock_fetch_member_ids, mock_fetch_players_bulk,
    ):
        recent = int(timezone.now().timestamp())
        clan = Clan.objects.create(
            clan_id=6001, realm='na', name='BN', tag='-BN', members_count=1)
        # Ghost: still FK'd to the clan but no longer in the live roster.
        ghost = Player.objects.create(
            player_id=9999, realm='na', name='GhostGone', clan=clan)
        # Current member that WILL be in the live roster.
        keeper = Player.objects.create(
            player_id=9001, realm='na', name='Keeper', clan=clan)

        mock_fetch_clan_info.return_value = {
            "clan_id": 6001, "name": "BN", "tag": "-BN", "members_count": 1,
            "description": "", "leader_id": 9001, "leader_name": "Keeper",
        }
        mock_fetch_member_ids.return_value = [9001]
        mock_fetch_players_bulk.return_value = {
            "9001": {
                "account_id": 9001, "nickname": "Keeper",
                "created_at": 1700000000, "last_battle_time": recent,
                "hidden_profile": False,
                "statistics": {"battles": 100, "pvp": {
                    "battles": 80, "wins": 40, "losses": 40,
                    "frags": 30, "survived_battles": 20}},
            },
        }

        crawl_clan_members(
            [{"clan_id": 6001}], realm='na', core_only=True, request_delay=0)

        ghost.refresh_from_db()
        keeper.refresh_from_db()
        self.assertIsNone(ghost.clan_id)          # departed → cleared
        self.assertEqual(keeper.clan_id, clan.pk)  # current → retained


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


class ClanCrawlUpstreamFailureAbortTests(TestCase):
    """A total upstream failure must not be reported as a completed pass.

    On 2026-08-10 WG's NA `clans/info/` went to 504 SOURCE_NOT_AVAILABLE and then
    stopped resolving; the pass had walked 4,324 of 35,898 clans, failed the
    remaining 31,573 in ~91 min, and returned normally — so the task emitted a
    yield snapshot describing 12% coverage as a full pass and cleared the pass
    marker, discarding the resume. A run of consecutive per-clan fetch failures
    now aborts the pass instead. See runbook-crawl-upstream-failure-abort.
    """

    def _run(self, side_effect, threshold="25", backoff="0", **kwargs):
        """Walk one clan per `side_effect` entry; None entries are fetch failures.

        members_count=0 keeps the per-clan path to the info fetch alone, so the
        only failure under test is fetch_clan_info returning None.
        """
        stubs = [{"clan_id": 7100 + i} for i in range(len(side_effect))]
        effects = [
            None if info is None
            else {"clan_id": stub["clan_id"], "name": "C", "tag": "C",
                  "members_count": 0}
            for info, stub in zip(side_effect, stubs)
        ]
        env = {"CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES": threshold,
               "CLAN_CRAWL_FAILURE_BACKOFF_S": backoff}
        with patch.dict(os.environ, env):
            with patch("warships.clan_crawl.fetch_clan_info") as mock_info, \
                    patch("warships.clan_crawl.time.sleep") as mock_sleep:
                mock_info.side_effect = effects
                try:
                    result = crawl_clan_members(
                        stubs, resume=False, realm="na", core_only=True,
                        request_delay=0, **kwargs)
                finally:
                    self.sleeps = [c.args[0]
                                   for c in mock_sleep.call_args_list]
        return result, mock_info

    def test_summary_counts_failed_clan_fetches(self):
        # Below the threshold the pass completes, but the failure is now visible.
        result, _ = self._run([True, None, True])
        self.assertEqual(result["clans_failed"], 1)
        self.assertEqual(result["clans_processed"], 2)

    def test_consecutive_failures_abort_the_pass(self):
        from warships.clan_crawl import CrawlUpstreamFailure
        with self.assertRaises(CrawlUpstreamFailure):
            self._run([None] * 6, threshold="3")

    def test_abort_stops_walking_instead_of_burning_the_rest_of_the_list(self):
        from warships.clan_crawl import CrawlUpstreamFailure
        try:
            self._run([None] * 20, threshold="3")
        except CrawlUpstreamFailure as exc:
            self.assertEqual(exc.summary["clans_failed"], 3)
            self.assertEqual(exc.summary["clans_processed"], 0)
            self.assertEqual(exc.consecutive_failures, 3)
        else:
            self.fail("expected CrawlUpstreamFailure")

    def test_successful_fetch_resets_the_consecutive_run(self):
        # Isolated failures around a healthy clan must not accumulate into an abort.
        result, _ = self._run([None, None, True, None, None], threshold="3")
        self.assertEqual(result["clans_failed"], 4)
        self.assertEqual(result["clans_processed"], 1)

    def test_threshold_zero_disables_the_abort(self):
        # Escape hatch: the pre-fix behaviour, should the abort ever misfire.
        result, _ = self._run([None] * 5, threshold="0")
        self.assertEqual(result["clans_failed"], 5)
        self.assertEqual(result["clans_processed"], 0)

    def test_failures_back_off_exponentially_up_to_the_cap(self):
        from warships.clan_crawl import CrawlUpstreamFailure
        with self.assertRaises(CrawlUpstreamFailure):
            self._run([None] * 8, threshold="8", backoff="1")
        # No sleep after the failure that triggers the abort.
        self.assertEqual(self.sleeps, [1, 2, 4, 8, 16, 30, 30])

    def test_default_abort_needs_minutes_of_failure_not_a_burst(self):
        """On 2026-09-30 25 instant 504s in 4.9s aborted the NA pass. At the
        defaults an abort must now require ~10 min of sustained failure."""
        from warships.clan_crawl import CrawlUpstreamFailure
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAN_CRAWL_FAILURE_BACKOFF_S", None)
            os.environ.pop("CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES", None)
            stubs = [{"clan_id": 7300 + i} for i in range(30)]
            with patch("warships.clan_crawl.fetch_clan_info", return_value=None), \
                    patch("warships.clan_crawl.time.sleep") as mock_sleep:
                with self.assertRaises(CrawlUpstreamFailure):
                    crawl_clan_members(stubs, resume=False, realm="na",
                                       core_only=True, request_delay=0)
        total = sum(c.args[0] for c in mock_sleep.call_args_list)
        self.assertGreaterEqual(total, 600)
        self.assertLess(total, 15 * 60)  # heartbeat-stale window

    def test_backoff_resets_after_a_successful_fetch(self):
        self._run([None, None, True, None], threshold="5", backoff="1")
        self.assertEqual(self.sleeps, [1, 2, 1])

    def test_disabled_abort_never_sleeps(self):
        self._run([None] * 5, threshold="0", backoff="1")
        self.assertEqual(self.sleeps, [])

    def test_abort_flushes_yield_counts_earned_before_the_outage(self):
        """The Redis aggregate is what the resumed pass keeps accumulating into,
        so counts earned before the abort must be flushed on the way out.

        Without the flush, everything since the last 25-clan checkpoint dies with
        the aborted execution and the eventual snapshot under-reports the pass.
        """
        from warships.clan_crawl import CrawlUpstreamFailure
        cache.clear()
        fresh_after = timezone.now()
        stubs = [{"clan_id": 7200 + i} for i in range(3)]
        with patch.dict(os.environ, {
                "CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES": "2",
                "CLAN_CRAWL_FAILURE_BACKOFF_S": "0",
                "CRAWL_YIELD_INSTRUMENT_ENABLED": "1"}):
            with patch("warships.clan_crawl.fetch_clan_info") as mock_info, \
                    patch("warships.clan_crawl.fetch_member_ids") as mock_members, \
                    patch("warships.clan_crawl.fetch_players_bulk") as mock_bulk:
                # One healthy clan yielding one net-new active player, then the
                # upstream dies for the rest of the list.
                mock_info.side_effect = [
                    {"clan_id": 7200, "name": "C", "tag": "C",
                     "members_count": 1},
                    None, None,
                ]
                mock_members.return_value = [9500]
                mock_bulk.return_value = {"9500": {
                    "account_id": 9500, "nickname": "p",
                    "last_battle_time": int(timezone.now().timestamp()),
                    "statistics": {"battles": 10, "pvp": {
                        "battles": 10, "wins": 5, "losses": 5, "frags": 6,
                        "survived_battles": 4}}}}
                with self.assertRaises(CrawlUpstreamFailure):
                    crawl_clan_members(
                        stubs, resume=False, realm="na", core_only=True,
                        request_delay=0, fresh_after=fresh_after)

        from warships import clan_crawl
        pass_id = clan_crawl._crawl_yield_pass_id(fresh_after)
        agg = cache.get(clan_crawl._crawl_yield_key("na", pass_id))
        self.assertEqual((agg or {}).get("discovered_active"), 1)


class ClanCrawlAbortBookkeepingTests(TestCase):
    """An aborted pass must keep the pass marker (so the next dispatch resumes
    where it stopped) and must NOT emit a yield snapshot describing the partial
    walk as a full pass."""

    def setUp(self):
        cache.clear()

    def _marker(self, realm="na"):
        from warships.tasks import _clan_crawl_pass_marker_key
        return cache.get(_clan_crawl_pass_marker_key(realm))

    @patch("warships.clan_crawl.emit_crawl_yield_snapshot")
    @patch("warships.clan_crawl.run_clan_crawl")
    def test_aborted_pass_keeps_marker_and_emits_no_snapshot(
            self, mock_run, mock_emit):
        from warships.clan_crawl import CrawlUpstreamFailure
        from warships.tasks import crawl_all_clans_task
        mock_run.side_effect = CrawlUpstreamFailure(
            {"clans_processed": 4324, "clans_failed": 25, "players_saved": 93355,
             "skipped": 0, "yield": {}},
            consecutive_failures=25,
        )
        res = crawl_all_clans_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(res["status"], "aborted")
        self.assertEqual(res["clans_failed"], 25)
        mock_emit.assert_not_called()
        self.assertIsNotNone(self._marker("na"))

    @patch("warships.clan_crawl.emit_crawl_yield_snapshot")
    @patch("warships.clan_crawl.run_clan_crawl")
    def test_completed_pass_still_emits_and_clears_the_marker(
            self, mock_run, mock_emit):
        from warships.tasks import crawl_all_clans_task
        mock_run.return_value = {"clans_processed": 1, "clans_failed": 0,
                                 "players_saved": 0, "skipped": 0, "yield": {}}
        res = crawl_all_clans_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(res["status"], "completed")
        mock_emit.assert_called_once()
        self.assertIsNone(self._marker("na"))


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


class ClanListPaginationFailureTests(TestCase):
    """2026-09-29: one WG 504 on NA clans/list page 79 read as end-of-list, so the
    pass walked 7,800 of ~36,100 clans and closed as complete."""

    def _crawl(self, pages):
        """`pages` maps page -> list of (batch, total_pages) results, in order."""
        from warships.clan_crawl import crawl_clan_ids
        calls = {p: list(results) for p, results in pages.items()}

        def fake(page, realm="na", request_delay=0):
            return calls[page].pop(0)

        with patch("warships.clan_crawl.fetch_clan_list_page", side_effect=fake), \
                patch("warships.clan_crawl.time.sleep") as mock_sleep:
            return crawl_clan_ids(realm="na", request_delay=0), mock_sleep

    def test_transient_page_failure_is_retried_and_the_walk_continues(self):
        stubs, mock_sleep = self._crawl({
            1: [([{"clan_id": 1}], 3)],
            2: [(None, 0), ([{"clan_id": 2}], 3)],
            3: [([{"clan_id": 3}], 3)],
        })
        self.assertEqual([s["clan_id"] for s in stubs], [1, 2, 3])
        self.assertEqual(mock_sleep.call_count, 1)

    def test_persistent_page_failure_aborts_instead_of_truncating(self):
        from warships.clan_crawl import CLAN_LIST_PAGE_ATTEMPTS, CrawlUpstreamFailure
        with self.assertRaises(CrawlUpstreamFailure):
            self._crawl({
                1: [([{"clan_id": 1}], 3)],
                2: [(None, 0)] * CLAN_LIST_PAGE_ATTEMPTS,
            })

    def test_first_page_failure_aborts_through_the_same_path(self):
        from warships.clan_crawl import CLAN_LIST_PAGE_ATTEMPTS, CrawlUpstreamFailure
        with self.assertRaises(CrawlUpstreamFailure):
            self._crawl({1: [(None, 0)] * CLAN_LIST_PAGE_ATTEMPTS})

    def test_ok_but_empty_page_still_ends_pagination(self):
        # The list can shrink under the walk; an ok response with no rows is the end.
        stubs, mock_sleep = self._crawl({
            1: [([{"clan_id": 1}], 3)],
            2: [([], 3)],
        })
        self.assertEqual([s["clan_id"] for s in stubs], [1])
        mock_sleep.assert_not_called()
