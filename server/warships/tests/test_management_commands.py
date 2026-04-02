from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player, PlayerExplorerSummary


class AuditProfileChartReadinessCommandTests(TestCase):
    def test_reports_battles_and_tier_type_coverage_for_realm(self):
        ready_player = Player.objects.create(
            name="ReadyEU",
            player_id=3001,
            realm="eu",
            is_hidden=False,
            pvp_battles=120,
            battles_json=[{
                "ship_type": "Destroyer",
                "ship_tier": 8,
                "pvp_battles": 120,
                "wins": 66,
                "win_ratio": 0.55,
            }],
        )
        Player.objects.create(
            name="MissingEU",
            player_id=3002,
            realm="eu",
            is_hidden=False,
            pvp_battles=85,
            battles_json=None,
        )
        Player.objects.create(
            name="HiddenEU",
            player_id=3003,
            realm="eu",
            is_hidden=True,
            pvp_battles=55,
            battles_json=[{
                "ship_type": "Cruiser",
                "ship_tier": 7,
                "pvp_battles": 55,
                "wins": 28,
                "win_ratio": 0.51,
            }],
        )

        out = StringIO()
        call_command("audit_profile_chart_readiness", realm="eu", stdout=out)
        output = out.getvalue()

        self.assertIn("EU Profile Chart Readiness", output)
        self.assertIn("Total players:                 3", output)
        self.assertIn("Visible players:               2", output)
        self.assertIn("Visible with battles_json:     1 (50.00%)", output)
        self.assertIn("Visible with tier-type rows:   1 (50.00%)", output)
        self.assertIn("Visible missing battles_json:  1", output)
        self.assertEqual(ready_player.realm, "eu")

    @patch("warships.management.commands.audit_profile_chart_readiness.warm_player_correlations")
    def test_can_include_correlation_output(self, mock_warm_player_correlations):
        Player.objects.create(
            name="ReadyEU",
            player_id=3010,
            realm="eu",
            is_hidden=False,
            pvp_battles=120,
            battles_json=[],
        )
        mock_warm_player_correlations.return_value = {
            "tier_type": {"tracked_population": 12},
            "win_rate_survival": {"tracked_population": 345},
            "ranked_wr_battles": {"tracked_population": 4},
        }

        out = StringIO()
        call_command(
            "audit_profile_chart_readiness",
            realm="eu",
            warm_correlations=True,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Correlations:", output)
        self.assertIn("'tracked_population': 12", output)
        mock_warm_player_correlations.assert_called_once_with(realm="eu")


class BackfillBattleDataCommandTests(TestCase):
    def test_dry_run_prioritizes_recent_lookup_then_score(self):
        now = timezone.now()
        recent = Player.objects.create(
            name="RecentEU",
            player_id=4001,
            realm="eu",
            is_hidden=False,
            pvp_battles=220,
            last_lookup=now,
            battles_json=None,
        )
        PlayerExplorerSummary.objects.create(player=recent, player_score=1.2)

        scored = Player.objects.create(
            name="ScoredEU",
            player_id=4002,
            realm="eu",
            is_hidden=False,
            pvp_battles=180,
            last_lookup=None,
            battles_json=None,
        )
        PlayerExplorerSummary.objects.create(player=scored, player_score=9.4)

        Player.objects.create(
            name="LowEU",
            player_id=4003,
            realm="eu",
            is_hidden=False,
            pvp_battles=40,
            last_lookup=None,
            battles_json=None,
        )

        out = StringIO()
        call_command(
            "backfill_battle_data",
            realm="eu",
            limit=2,
            preview=2,
            dry_run=True,
            stdout=out,
        )
        output = out.getvalue()

        self.assertIn("Selected this run:  2", output)
        self.assertLess(output.index("4001 RecentEU"),
                        output.index("4002 ScoredEU"))

    @patch("warships.management.commands.backfill_battle_data.update_battle_data_task.delay")
    def test_queue_dispatch_is_bounded_by_limit(self, mock_delay):
        now = timezone.now()
        top = Player.objects.create(
            name="TopEU",
            player_id=4011,
            realm="eu",
            is_hidden=False,
            pvp_battles=500,
            last_lookup=now,
            battles_json=None,
        )
        PlayerExplorerSummary.objects.create(player=top, player_score=8.2)

        second = Player.objects.create(
            name="SecondEU",
            player_id=4012,
            realm="eu",
            is_hidden=False,
            pvp_battles=450,
            last_lookup=now,
            battles_json=None,
        )
        PlayerExplorerSummary.objects.create(player=second, player_score=7.7)

        Player.objects.create(
            name="ThirdEU",
            player_id=4013,
            realm="eu",
            is_hidden=False,
            pvp_battles=440,
            last_lookup=now,
            battles_json=None,
        )

        out = StringIO()
        call_command(
            "backfill_battle_data",
            realm="eu",
            limit=2,
            preview=0,
            dispatch="queue",
            stdout=out,
        )

        self.assertEqual(mock_delay.call_count, 2)
        mock_delay.assert_any_call(4011, realm="eu")
        mock_delay.assert_any_call(4012, realm="eu")
