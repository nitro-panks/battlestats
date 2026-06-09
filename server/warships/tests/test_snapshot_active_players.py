"""Tests for the daily active-player snapshot engine.

Command: ``snapshot_active_players`` (+ ``snapshot_active_players_task``).
See agents/runbooks/runbook-daily-active-snapshots-2026-06-09.md.
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player, Snapshot


def _acct(pid, battles, wins, hidden=False):
    if hidden:
        return {"account_id": pid, "nickname": f"P{pid}", "hidden_profile": True}
    return {
        "account_id": pid,
        "nickname": f"P{pid}",
        "last_battle_time": int(timezone.now().timestamp()),
        "statistics": {
            "battles": battles + 5,
            "pvp": {"battles": battles, "wins": wins,
                    "losses": battles - wins, "frags": 0, "survived_battles": 0},
        },
    }


class SnapshotActivePlayersCommandTests(TestCase):
    def _mk(self, pid, **kw):
        d = dict(
            realm="na", is_hidden=False, pvp_battles=1000, pvp_wins=550,
            last_battle_date=timezone.now().date(), days_since_last_battle=0,
        )
        d.update(kw)
        return Player.objects.create(name=f"P{pid}", player_id=pid, **d)

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_creates_today_snapshot_for_active_players(self, mock_bulk):
        self._mk(6001)
        self._mk(6002)
        mock_bulk.return_value = {
            "6001": _acct(6001, 1010, 560),
            "6002": _acct(6002, 2000, 1100),
        }
        call_command("snapshot_active_players", "--realm", "na", "--delay", "0", stdout=StringIO())

        today = timezone.now().date()
        s1 = Snapshot.objects.get(player__player_id=6001, date=today)
        self.assertEqual(s1.battles, 1010)
        self.assertEqual(s1.wins, 560)
        self.assertTrue(Snapshot.objects.filter(player__player_id=6002, date=today).exists())

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_is_idempotent_skips_already_snapshotted(self, mock_bulk):
        self._mk(6101)
        mock_bulk.return_value = {"6101": _acct(6101, 1010, 560)}
        call_command("snapshot_active_players", "--realm", "na", "--delay", "0", stdout=StringIO())
        self.assertEqual(mock_bulk.call_count, 1)

        # Second run: player already has today's snapshot -> not selected -> no fetch.
        mock_bulk.reset_mock()
        call_command("snapshot_active_players", "--realm", "na", "--delay", "0", stdout=StringIO())
        self.assertEqual(mock_bulk.call_count, 0)

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_excludes_inactive_and_hidden(self, mock_bulk):
        self._mk(6201)  # active -> included
        self._mk(6202, last_battle_date=timezone.now().date() - timedelta(days=30))  # stale
        self._mk(6203, is_hidden=True)  # hidden
        mock_bulk.return_value = {"6201": _acct(6201, 1010, 560)}

        call_command("snapshot_active_players", "--realm", "na", "--active-days", "7",
                     "--delay", "0", stdout=StringIO())

        # Only the active visible player was queued for fetch.
        called_ids = mock_bulk.call_args[0][0]
        self.assertEqual(list(called_ids), [6201])

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_hidden_in_response_is_not_snapshotted(self, mock_bulk):
        self._mk(6301)
        mock_bulk.return_value = {"6301": _acct(6301, 0, 0, hidden=True)}
        call_command("snapshot_active_players", "--realm", "na", "--delay", "0", stdout=StringIO())
        today = timezone.now().date()
        self.assertFalse(Snapshot.objects.filter(player__player_id=6301, date=today).exists())

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_dry_run_fetches_nothing(self, mock_bulk):
        self._mk(6401)
        out = StringIO()
        call_command("snapshot_active_players", "--realm", "na", "--dry-run", stdout=out)
        self.assertEqual(mock_bulk.call_count, 0)
        self.assertIn("DRY RUN", out.getvalue())


class SnapshotActivePlayersTaskTests(TestCase):
    @patch("warships.tasks.call_command")
    def test_coexists_with_clan_crawl(self, mock_call):
        from warships.tasks import snapshot_active_players_task, _clan_crawl_lock_key
        from django.core.cache import cache
        cache.set(_clan_crawl_lock_key("na"), "held", timeout=60)
        try:
            result = snapshot_active_players_task(realm="na")
        finally:
            cache.delete(_clan_crawl_lock_key("na"))
        # Coexists: it runs (calls the command) instead of deferring to the crawl.
        self.assertEqual(result["status"], "completed")
        self.assertTrue(mock_call.called)

    @patch("warships.tasks.call_command")
    def test_disable_flag_skips(self, mock_call):
        from warships.tasks import snapshot_active_players_task
        with self.settings():
            import os
            os.environ["SNAPSHOT_ACTIVE_PLAYERS_ENABLED"] = "0"
            try:
                result = snapshot_active_players_task(realm="na")
            finally:
                os.environ.pop("SNAPSHOT_ACTIVE_PLAYERS_ENABLED", None)
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(mock_call.called)
