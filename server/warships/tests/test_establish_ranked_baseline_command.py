"""Tests for the `establish_ranked_baseline` management command."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as django_timezone

from warships.models import (
    BattleObservation,
    Player,
    PlayerExplorerSummary,
)


class EstablishRankedBaselineCommandTests(TestCase):
    """Active-ranked-NA baseline-fill command (Phase 6 of the ranked rollout)."""

    def setUp(self):
        self.today = django_timezone.now().date()

    def _make_player(self, name, *, days_idle, latest_ranked_battles=200,
                     is_hidden=False, realm="na", has_observation=False,
                     has_ranked_payload=False, **kwargs):
        player = Player.objects.create(
            name=name,
            player_id=kwargs.pop("player_id", abs(hash(name)) % (10 ** 9)),
            realm=realm,
            is_hidden=is_hidden,
            last_battle_date=self.today - timedelta(days=days_idle),
            **kwargs,
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            realm=realm,
            latest_ranked_battles=latest_ranked_battles,
        )
        if has_observation:
            BattleObservation.objects.create(
                player=player,
                pvp_battles=getattr(player, "pvp_battles", 0) or 0,
                ranked_ships_stats_json=(
                    [{"ship_id": 42, "season_id": 21, "battles": 5}]
                    if has_ranked_payload else None
                ),
            )
        return player

    def test_dry_run_reports_count_without_calling_wg(self):
        self._make_player("ActiveRanked", days_idle=2, latest_ranked_battles=200)
        with mock.patch(
            "warships.incremental_battles.record_ranked_observation_and_diff",
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_ranked_baseline",
                "--realm", "na", "--days", "14", "--dry-run",
                stdout=out,
            )
        self.assertIn("1 candidates", out.getvalue())
        wg_call.assert_not_called()

    def test_filters_by_min_ranked_battles(self):
        self._make_player("LowVolume", days_idle=2, latest_ranked_battles=50)
        self._make_player("HighVolume", days_idle=2, latest_ranked_battles=300)
        out = StringIO()
        call_command(
            "establish_ranked_baseline",
            "--realm", "na", "--days", "14",
            "--min-ranked-battles", "100", "--dry-run",
            stdout=out,
        )
        # Only HighVolume passes the >=100 gate.
        self.assertIn("1 candidates", out.getvalue())

    def test_skips_players_with_existing_ranked_baseline(self):
        # Player has a prior observation with a non-empty ranked payload.
        self._make_player(
            "AlreadyRankedBaselined", days_idle=2, latest_ranked_battles=200,
            has_observation=True, has_ranked_payload=True,
        )
        out = StringIO()
        call_command(
            "establish_ranked_baseline",
            "--realm", "na", "--days", "14", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_includes_players_with_observation_but_no_ranked_payload(self):
        # Player has a random-only baseline from before ranked capture
        # flipped on. They still need a ranked seed.
        self._make_player(
            "RandomBaselineOnly", days_idle=2, latest_ranked_battles=200,
            has_observation=True, has_ranked_payload=False,
        )
        out = StringIO()
        call_command(
            "establish_ranked_baseline",
            "--realm", "na", "--days", "14", "--dry-run",
            stdout=out,
        )
        self.assertIn("1 candidates", out.getvalue())

    def test_skips_hidden_players(self):
        self._make_player(
            "HiddenRanked", days_idle=2, latest_ranked_battles=300, is_hidden=True,
        )
        out = StringIO()
        call_command(
            "establish_ranked_baseline",
            "--realm", "na", "--days", "14", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_skips_players_outside_activity_window(self):
        self._make_player("Idle60d", days_idle=60, latest_ranked_battles=300)
        out = StringIO()
        call_command(
            "establish_ranked_baseline",
            "--realm", "na", "--days", "14", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_skips_other_realms(self):
        self._make_player(
            "EuRanked", days_idle=2, realm="eu", latest_ranked_battles=300,
        )
        out = StringIO()
        call_command(
            "establish_ranked_baseline",
            "--realm", "na", "--days", "14", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_orders_by_latest_ranked_battles_desc(self):
        self._make_player("Mid", days_idle=2, latest_ranked_battles=200)
        self._make_player("Top", days_idle=2, latest_ranked_battles=500)
        self._make_player("Bottom", days_idle=2, latest_ranked_battles=120)
        seen_order: list[int] = []

        def record_order(player_id, realm):
            seen_order.append(player_id)
            return {"status": "completed", "reason": "baseline",
                    "ranked_events_created": 0}

        with mock.patch(
            "warships.incremental_battles.record_ranked_observation_and_diff",
            side_effect=record_order,
        ):
            out = StringIO()
            call_command(
                "establish_ranked_baseline",
                "--realm", "na", "--days", "14", "--delay", "0",
                stdout=out,
            )
        # Names match the player_ids; reorder by latest_ranked_battles desc.
        top_id = Player.objects.get(name="Top").player_id
        mid_id = Player.objects.get(name="Mid").player_id
        bottom_id = Player.objects.get(name="Bottom").player_id
        self.assertEqual(seen_order, [top_id, mid_id, bottom_id])

    def test_invokes_record_ranked_for_each_candidate(self):
        self._make_player("Active1", days_idle=1, latest_ranked_battles=200)
        self._make_player("Active2", days_idle=3, latest_ranked_battles=300)
        with mock.patch(
            "warships.incremental_battles.record_ranked_observation_and_diff",
            return_value={"status": "completed", "reason": "baseline",
                          "ranked_events_created": 0},
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_ranked_baseline",
                "--realm", "na", "--days", "14", "--delay", "0",
                stdout=out,
            )
        self.assertEqual(wg_call.call_count, 2)
        for call in wg_call.call_args_list:
            self.assertEqual(call.kwargs.get("realm"), "na")
        self.assertIn("baseline=2", out.getvalue())

    def test_limit_caps_processing(self):
        for i in range(5):
            self._make_player(
                f"Ranked{i}", days_idle=i + 1, latest_ranked_battles=200 + i,
            )
        with mock.patch(
            "warships.incremental_battles.record_ranked_observation_and_diff",
            return_value={"status": "completed", "reason": "baseline"},
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_ranked_baseline",
                "--realm", "na", "--days", "14",
                "--limit", "2", "--delay", "0",
                stdout=out,
            )
        self.assertEqual(wg_call.call_count, 2)
        self.assertIn("limited to 2", out.getvalue())

    def test_handles_wg_fetch_failure_without_aborting(self):
        self._make_player("Active1", days_idle=1, latest_ranked_battles=200)
        self._make_player("Active2", days_idle=2, latest_ranked_battles=300)
        with mock.patch(
            "warships.incremental_battles.record_ranked_observation_and_diff",
            side_effect=[
                {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"},
                {"status": "completed", "reason": "baseline",
                 "ranked_events_created": 0},
            ],
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_ranked_baseline",
                "--realm", "na", "--days", "14", "--delay", "0",
                stdout=out,
            )
        self.assertEqual(wg_call.call_count, 2)
        self.assertIn("wg_failed=1", out.getvalue())
        self.assertIn("baseline=1", out.getvalue())
