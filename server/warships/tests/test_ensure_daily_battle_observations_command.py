"""Tests for the `ensure_daily_battle_observations` management command.

Daily floor for BattleObservation coverage on active players. Companion
runbook: agents/runbooks/runbook-battle-observation-floor-2026-05-02.md
"""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as django_timezone

from warships.models import BattleObservation, Player


class EnsureDailyBattleObservationsCommandTests(TestCase):
    def setUp(self):
        self.now = django_timezone.now()
        self.today = self.now.date()

    def _make_player(self, name, *, days_idle, latest_obs_hours_ago=None,
                     is_hidden=False, realm="na", **kwargs):
        player = Player.objects.create(
            name=name,
            player_id=kwargs.pop("player_id", abs(hash(name)) % (10 ** 9)),
            realm=realm,
            is_hidden=is_hidden,
            last_battle_date=self.today - timedelta(days=days_idle),
            **kwargs,
        )
        if latest_obs_hours_ago is not None:
            obs = BattleObservation.objects.create(
                player=player,
                pvp_battles=getattr(player, "pvp_battles", 0) or 0,
            )
            # observed_at is auto_now_add; backdate via update.
            BattleObservation.objects.filter(pk=obs.pk).update(
                observed_at=self.now - timedelta(hours=latest_obs_hours_ago),
            )
        return player

    def test_dry_run_reports_count_without_calling_wg(self):
        self._make_player("StaleActive", days_idle=1, latest_obs_hours_ago=30)
        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
        ) as wg_call:
            out = StringIO()
            call_command(
                "ensure_daily_battle_observations",
                "--realm", "na", "--days", "7", "--dry-run",
                stdout=out,
            )
        self.assertIn("1 candidates", out.getvalue())
        wg_call.assert_not_called()

    def test_includes_player_with_no_observation(self):
        self._make_player("NeverObserved", days_idle=2)
        out = StringIO()
        call_command(
            "ensure_daily_battle_observations",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("1 candidates", out.getvalue())

    def test_skips_player_with_fresh_observation(self):
        self._make_player("FreshlyObserved", days_idle=1, latest_obs_hours_ago=2)
        out = StringIO()
        call_command(
            "ensure_daily_battle_observations",
            "--realm", "na", "--days", "7",
            "--stale-hours", "22", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_includes_player_with_stale_observation(self):
        self._make_player("StaleObs", days_idle=1, latest_obs_hours_ago=30)
        out = StringIO()
        call_command(
            "ensure_daily_battle_observations",
            "--realm", "na", "--days", "7",
            "--stale-hours", "22", "--dry-run",
            stdout=out,
        )
        self.assertIn("1 candidates", out.getvalue())

    def test_skips_hidden_players(self):
        self._make_player(
            "HiddenStale", days_idle=1, latest_obs_hours_ago=30,
            is_hidden=True,
        )
        out = StringIO()
        call_command(
            "ensure_daily_battle_observations",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_skips_other_realms(self):
        self._make_player("EuStale", days_idle=1, latest_obs_hours_ago=30, realm="eu")
        out = StringIO()
        call_command(
            "ensure_daily_battle_observations",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_skips_players_outside_activity_window(self):
        self._make_player("Idle60d", days_idle=60, latest_obs_hours_ago=30)
        out = StringIO()
        call_command(
            "ensure_daily_battle_observations",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_orders_recency_first_then_oldest_observation(self):
        # Recency-first: most-recently-active player wins regardless of
        # observation staleness — RecentStale (battled today, stale obs) sorts
        # ahead of OldNeverObs (battled 5d ago, never observed), because scarce
        # capture capacity must go to the likeliest movers. Within the same
        # last_battle_date, never-observed/stalest-obs wins. (All candidates must
        # be stale — a fresh obs <8h excludes a player regardless of recency.)
        self._make_player("RecentStale", days_idle=0, latest_obs_hours_ago=30)
        self._make_player("MidNeverObs", days_idle=1)
        self._make_player("MidStale50h", days_idle=1, latest_obs_hours_ago=50)
        self._make_player("OldNeverObs", days_idle=5)
        seen: list[int] = []

        def record_order(player_id, realm):
            seen.append(player_id)
            return {"status": "completed", "reason": "diff",
                    "random_events_created": 0, "ranked_events_created": 0}

        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
            side_effect=record_order,
        ):
            call_command(
                "ensure_daily_battle_observations",
                "--realm", "na", "--days", "7", "--delay", "0",
                stdout=StringIO(),
            )
        recent_id = Player.objects.get(name="RecentStale").player_id
        mid_never = Player.objects.get(name="MidNeverObs").player_id
        mid_s50 = Player.objects.get(name="MidStale50h").player_id
        old_never = Player.objects.get(name="OldNeverObs").player_id
        # days_idle 0 (today) first, then the two days_idle=1 (never-obs before
        # stale-50h via NULLS-FIRST tiebreak), then days_idle=5 last — even
        # though OldNeverObs has never been observed.
        self.assertEqual(seen, [recent_id, mid_never, mid_s50, old_never])

    def test_invokes_random_worker_when_ranked_capture_off(self):
        self._make_player("Active1", days_idle=1, latest_obs_hours_ago=30)
        with mock.patch.dict("os.environ", {
            "BATTLE_HISTORY_RANKED_CAPTURE_ENABLED": "0",
        }, clear=False):
            with mock.patch(
                "warships.incremental_battles.record_observation_and_diff",
                return_value={"status": "completed", "reason": "diff"},
            ) as random_worker, mock.patch(
                "warships.incremental_battles.record_ranked_observation_and_diff",
                return_value={"status": "completed", "reason": "diff"},
            ) as ranked_worker:
                call_command(
                    "ensure_daily_battle_observations",
                    "--realm", "na", "--days", "7", "--delay", "0",
                    stdout=StringIO(),
                )
        random_worker.assert_called_once()
        ranked_worker.assert_not_called()

    def test_invokes_ranked_worker_when_capture_on_for_realm(self):
        self._make_player("Active1", days_idle=1, latest_obs_hours_ago=30)
        with mock.patch.dict("os.environ", {
            "BATTLE_HISTORY_RANKED_CAPTURE_ENABLED": "1",
            "BATTLE_HISTORY_RANKED_CAPTURE_REALMS": "na",
        }, clear=False):
            with mock.patch(
                "warships.incremental_battles.record_observation_and_diff",
                return_value={"status": "completed", "reason": "diff"},
            ) as random_worker, mock.patch(
                "warships.incremental_battles.record_ranked_observation_and_diff",
                return_value={"status": "completed", "reason": "diff"},
            ) as ranked_worker:
                call_command(
                    "ensure_daily_battle_observations",
                    "--realm", "na", "--days", "7", "--delay", "0",
                    stdout=StringIO(),
                )
        ranked_worker.assert_called_once()
        random_worker.assert_not_called()

    def test_limit_caps_processing(self):
        for i in range(5):
            self._make_player(
                f"Stale{i}", days_idle=1, latest_obs_hours_ago=30 + i,
            )
        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
            return_value={"status": "completed", "reason": "diff"},
        ) as worker:
            call_command(
                "ensure_daily_battle_observations",
                "--realm", "na", "--days", "7",
                "--limit", "2", "--delay", "0",
                stdout=StringIO(),
            )
        self.assertEqual(worker.call_count, 2)

    def test_handles_wg_failure_without_aborting(self):
        self._make_player("Active1", days_idle=1, latest_obs_hours_ago=30)
        self._make_player("Active2", days_idle=1, latest_obs_hours_ago=40)
        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
            side_effect=[
                {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"},
                {"status": "completed", "reason": "diff",
                 "random_events_created": 3, "ranked_events_created": 0},
            ],
        ) as worker:
            out = StringIO()
            call_command(
                "ensure_daily_battle_observations",
                "--realm", "na", "--days", "7", "--delay", "0",
                stdout=out,
            )
        self.assertEqual(worker.call_count, 2)
        self.assertIn("wg_failed=1", out.getvalue())
        self.assertIn("events=3", out.getvalue())
