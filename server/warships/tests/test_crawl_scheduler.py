from __future__ import annotations

import os
from unittest.mock import patch
import time

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase, override_settings

from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

from warships.signals import ensure_daily_clan_crawl_schedule
from warships.tasks import CLAN_CRAWL_HEARTBEAT_KEY, CLAN_CRAWL_LOCK_KEY, RANKED_INCREMENTAL_LOCK_KEY, crawl_all_clans_task, ensure_crawl_all_clans_running_task, incremental_ranked_data_task, is_ranked_data_refresh_pending, queue_ranked_data_refresh, update_clan_battle_summary_task, update_clan_data_task, update_clan_members_task, update_player_data_task, update_ranked_data_task, warm_clan_battle_summaries_task


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "TIMEOUT": 60,
        }
    }
)
class ClanCrawlSchedulerTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_crawl_task_runs_runner_and_releases_lock(self):
        with patch("warships.clan_crawl.run_clan_crawl", return_value={"clans_found": 12}) as mock_run:
            result = crawl_all_clans_task.run(
                resume=True, dry_run=False, limit=5)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clans_found"], 12)
        mock_run.assert_called_once_with(resume=True, dry_run=False, limit=5)
        self.assertIsNone(cache.get(CLAN_CRAWL_LOCK_KEY))

    def test_crawl_task_skips_when_lock_exists(self):
        cache.add(CLAN_CRAWL_LOCK_KEY, "existing-run", timeout=60)

        with patch("warships.clan_crawl.run_clan_crawl") as mock_run:
            result = crawl_all_clans_task.run(resume=True)

        self.assertEqual(result, {"status": "skipped",
                         "reason": "already-running"})
        mock_run.assert_not_called()

    def test_post_migrate_creates_daily_periodic_task(self):
        app_config = apps.get_app_config("warships")

        with patch.dict(os.environ, {}, clear=False):
            ensure_daily_clan_crawl_schedule(sender=app_config)

        task = PeriodicTask.objects.get(name="daily-clan-crawl")
        self.assertEqual(task.task, "warships.tasks.crawl_all_clans_task")
        self.assertEqual(task.kwargs, '{"resume": true}')
        self.assertTrue(task.enabled)

        schedule = CrontabSchedule.objects.get(id=task.crontab_id)
        self.assertEqual(schedule.hour, "3")
        self.assertEqual(schedule.minute, "0")
        self.assertEqual(str(schedule.timezone), "UTC")

        ranked_task = PeriodicTask.objects.get(
            name="daily-ranked-incrementals")
        self.assertEqual(ranked_task.task,
                         "warships.tasks.incremental_ranked_data_task")
        self.assertTrue(ranked_task.enabled)

        ranked_schedule = CrontabSchedule.objects.get(
            id=ranked_task.crontab_id)
        self.assertEqual(ranked_schedule.hour, "10")
        self.assertEqual(ranked_schedule.minute, "30")
        self.assertEqual(str(ranked_schedule.timezone), "UTC")

        watchdog_task = PeriodicTask.objects.get(name="clan-crawl-watchdog")
        self.assertEqual(watchdog_task.task,
                         "warships.tasks.ensure_crawl_all_clans_running_task")
        self.assertTrue(watchdog_task.enabled)

        watchdog_schedule = IntervalSchedule.objects.get(
            id=watchdog_task.interval_id)
        self.assertEqual(watchdog_schedule.every, 5)
        self.assertEqual(watchdog_schedule.period, IntervalSchedule.MINUTES)

        warm_task = PeriodicTask.objects.get(name="clan-battle-summary-warmer")
        self.assertEqual(
            warm_task.task, "warships.tasks.warm_clan_battle_summaries_task")
        self.assertTrue(warm_task.enabled)
        self.assertEqual(warm_task.kwargs, '{"clan_ids": ["1000055908"]}')

        warm_schedule = IntervalSchedule.objects.get(id=warm_task.interval_id)
        self.assertEqual(warm_schedule.every, 30)
        self.assertEqual(warm_schedule.period, IntervalSchedule.MINUTES)

    def test_watchdog_schedules_crawl_when_not_running(self):
        with patch("warships.tasks.crawl_all_clans_task.delay") as mock_delay:
            result = ensure_crawl_all_clans_running_task.run()

        self.assertEqual(
            result, {"status": "scheduled", "reason": "not-running"})
        mock_delay.assert_called_once_with(resume=True)

    def test_watchdog_skips_when_crawl_has_fresh_heartbeat(self):
        cache.add(CLAN_CRAWL_LOCK_KEY, "existing-run", timeout=60)
        cache.set(CLAN_CRAWL_HEARTBEAT_KEY, time.time(), timeout=60)

        with patch("warships.tasks.crawl_all_clans_task.delay") as mock_delay:
            result = ensure_crawl_all_clans_running_task.run()

        self.assertEqual(result, {"status": "skipped", "reason": "running"})
        mock_delay.assert_not_called()

    def test_watchdog_restarts_when_crawl_heartbeat_is_stale(self):
        cache.add(CLAN_CRAWL_LOCK_KEY, "existing-run", timeout=60)
        cache.set(CLAN_CRAWL_HEARTBEAT_KEY, time.time() - 3600, timeout=60)

        with patch("warships.tasks.crawl_all_clans_task.delay") as mock_delay:
            result = ensure_crawl_all_clans_running_task.run()

        self.assertEqual(
            result, {"status": "scheduled", "reason": "stale-lock"})
        self.assertIsNone(cache.get(CLAN_CRAWL_LOCK_KEY))
        mock_delay.assert_called_once_with(resume=True)

    def test_warm_clan_battle_summaries_task_refreshes_each_configured_clan(self):
        with patch("warships.data.refresh_clan_battle_seasons_cache") as mock_refresh:
            result = warm_clan_battle_summaries_task.run(
                clan_ids=["1000055908", "555"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(mock_refresh.call_count, 2)
        mock_refresh.assert_any_call("1000055908")
        mock_refresh.assert_any_call("555")

    def test_incremental_ranked_task_skips_when_crawl_lock_exists(self):
        cache.add(CLAN_CRAWL_LOCK_KEY, "crawl-run", timeout=60)

        with patch("warships.tasks.call_command") as mock_call_command:
            result = incremental_ranked_data_task.run()

        self.assertEqual(
            result, {"status": "skipped", "reason": "crawl-running"})
        mock_call_command.assert_not_called()

    def test_incremental_ranked_task_invokes_command_and_releases_lock(self):
        with patch("warships.tasks.call_command") as mock_call_command:
            result = incremental_ranked_data_task.run()

        self.assertEqual(result, {"status": "completed"})
        mock_call_command.assert_called_once()
        self.assertIsNone(cache.get(RANKED_INCREMENTAL_LOCK_KEY))

    def test_post_migrate_disables_warmer_when_no_clans_are_configured(self):
        app_config = apps.get_app_config("warships")

        PeriodicTask.objects.update_or_create(
            name="clan-battle-summary-warmer",
            defaults={
                "task": "warships.tasks.warm_clan_battle_summaries_task",
                "enabled": True,
            },
        )

        with patch.dict(os.environ, {"CLAN_BATTLE_WARM_CLAN_IDS": ""}, clear=False):
            ensure_daily_clan_crawl_schedule(sender=app_config)

        self.assertFalse(PeriodicTask.objects.get(
            name="clan-battle-summary-warmer").enabled)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "TIMEOUT": 60,
        }
    }
)
class RefreshTaskLockTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_player_refresh_task_skips_when_lock_exists(self):
        cache.add("warships:tasks:update_player_data:42:lock",
                  "existing-run", timeout=60)

        with patch("warships.data.update_player_data") as mock_update_player_data:
            result = update_player_data_task.run(
                player_id=42, force_refresh=True)

        self.assertEqual(result, {"status": "skipped",
                         "reason": "already-running"})
        mock_update_player_data.assert_not_called()

    def test_clan_refresh_task_skips_when_lock_exists(self):
        cache.add("warships:tasks:update_clan_data:99:lock",
                  "existing-run", timeout=60)

        with patch("warships.data.update_clan_data") as mock_update_clan_data:
            result = update_clan_data_task.run(clan_id=99)

        self.assertEqual(result, {"status": "skipped",
                         "reason": "already-running"})
        mock_update_clan_data.assert_not_called()

    def test_clan_members_refresh_task_skips_when_lock_exists(self):
        cache.add("warships:tasks:update_clan_members:99:lock",
                  "existing-run", timeout=60)

        with patch("warships.data.update_clan_members") as mock_update_clan_members:
            result = update_clan_members_task.run(clan_id=99)

        self.assertEqual(result, {"status": "skipped",
                         "reason": "already-running"})
        mock_update_clan_members.assert_not_called()

    def test_clan_battle_summary_task_skips_when_lock_exists(self):
        cache.add("warships:tasks:update_clan_battle_summary:99:lock",
                  "existing-run", timeout=60)

        with patch("warships.data.refresh_clan_battle_seasons_cache") as mock_refresh_summary:
            result = update_clan_battle_summary_task.run(clan_id=99)

        self.assertEqual(result, {"status": "skipped",
                         "reason": "already-running"})
        mock_refresh_summary.assert_not_called()

    def test_queue_ranked_data_refresh_sets_pending_marker_until_task_finishes(self):
        with patch("warships.tasks.update_ranked_data_task.delay") as mock_delay:
            result = queue_ranked_data_refresh(1234)

        self.assertEqual(result, {"status": "queued"})
        self.assertTrue(is_ranked_data_refresh_pending(1234))
        mock_delay.assert_called_once_with(player_id=1234)

    def test_queue_ranked_data_refresh_skips_when_already_pending(self):
        cache.add("warships:tasks:update_ranked_data_dispatch:1234",
                  "queued", timeout=60)

        with patch("warships.tasks.update_ranked_data_task.delay") as mock_delay:
            result = queue_ranked_data_refresh(1234)

        self.assertEqual(
            result, {"status": "skipped", "reason": "already-queued"})
        mock_delay.assert_not_called()

    def test_ranked_refresh_task_clears_pending_marker(self):
        cache.add("warships:tasks:update_ranked_data_dispatch:4567",
                  "queued", timeout=60)

        with patch("warships.data.update_ranked_data") as mock_update_ranked_data:
            result = update_ranked_data_task.run(player_id=4567)

        self.assertEqual(result, {"status": "completed"})
        mock_update_ranked_data.assert_called_once_with(player_id=4567)
        self.assertFalse(is_ranked_data_refresh_pending(4567))

    def test_post_migrate_updates_existing_periodic_task(self):
        schedule = CrontabSchedule.objects.create(
            minute="15",
            hour="8",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        task = PeriodicTask.objects.get(name="daily-clan-crawl")
        task.task = "warships.tasks.update_clan_data_task"
        task.crontab = schedule
        task.kwargs = "{}"
        task.save()

        app_config = apps.get_app_config("warships")
        ensure_daily_clan_crawl_schedule(sender=app_config)

        task = PeriodicTask.objects.get(name="daily-clan-crawl")
        self.assertEqual(task.task, "warships.tasks.crawl_all_clans_task")
        self.assertEqual(task.kwargs, '{"resume": true}')

        watchdog_task = PeriodicTask.objects.get(name="clan-crawl-watchdog")
        self.assertEqual(watchdog_task.task,
                         "warships.tasks.ensure_crawl_all_clans_running_task")

        warmer_task = PeriodicTask.objects.get(
            name="clan-battle-summary-warmer")
        self.assertEqual(warmer_task.task,
                         "warships.tasks.warm_clan_battle_summaries_task")
