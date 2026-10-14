import json
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.data import warm_player_entity_caches
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


class IncrementalPlayerRefreshCommandTests(TestCase):
    @patch("warships.management.commands.incremental_player_refresh.refresh_player_detail_payloads")
    @patch("warships.management.commands.incremental_player_refresh.clan_battle_summary_is_stale", return_value=False)
    @patch("warships.management.commands.incremental_player_refresh.player_achievements_need_refresh", return_value=False)
    @patch("warships.management.commands.incremental_player_refresh.player_efficiency_needs_refresh", return_value=False)
    @patch("warships.management.commands.incremental_player_refresh.save_player")
    @patch("warships.management.commands.incremental_player_refresh.fetch_players_bulk")
    @patch("warships.management.commands.incremental_player_refresh._build_candidate_queue")
    def test_incremental_player_refresh_owns_detail_lanes_for_realm(
        self,
        mock_build_candidate_queue,
        mock_fetch_players_bulk,
        mock_save_player,
        _mock_efficiency_needs_refresh,
        _mock_achievements_need_refresh,
        _mock_clan_battle_summary_is_stale,
        mock_refresh_player_detail_payloads,
    ):
        player = Player.objects.create(
            name="DurableEU",
            player_id=5001,
            realm="eu",
            is_hidden=False,
        )
        mock_build_candidate_queue.return_value = ([player.id], {
            "hot": 0,
            "active": 1,
            "warm": 0,
        })
        mock_fetch_players_bulk.return_value = {
            str(player.player_id): {
                "account_id": player.player_id,
                "nickname": player.name,
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "incremental-player-refresh-state.json"
            call_command(
                "incremental_player_refresh",
                realm="eu",
                limit=1,
                batch_size=1,
                state_file=str(state_file),
                stdout=StringIO(),
            )

        mock_fetch_players_bulk.assert_called_once_with(
            [player.player_id], realm="eu")
        mock_save_player.assert_called_once_with(
            mock_fetch_players_bulk.return_value[str(player.player_id)],
            clan=player.clan,
            realm="eu",
        )
        mock_refresh_player_detail_payloads.assert_called_once()
        refreshed_player = mock_refresh_player_detail_payloads.call_args.args[0]
        self.assertEqual(refreshed_player.player_id, player.player_id)
        self.assertEqual(refreshed_player.realm, "eu")
        self.assertEqual(
            mock_refresh_player_detail_payloads.call_args.kwargs,
            {"force_refresh": False, "refresh_core": False},
        )


class IncrementalPlayerRefreshBudgetTests(TestCase):
    """N2 of runbook-background-queue-stripe-latency-2026-10-13.md: the hot
    tier is capped and a run is bounded by wall clock, so one crawler-built
    tier cannot hold a background slot for hours."""

    def _hot_player(self, player_id, lookup_hours_ago):
        from datetime import timedelta
        now = timezone.now()
        return Player.objects.create(
            name=f"Hot{player_id}", player_id=player_id, realm="asia",
            last_lookup=now - timedelta(hours=lookup_hours_ago),
            last_fetch=now - timedelta(days=2),
        )

    def test_build_candidate_queue_caps_hot_tier_keeping_most_recent_lookups(self):
        from warships.management.commands.incremental_player_refresh import (
            _build_candidate_queue)
        oldest = self._hot_player(7001, lookup_hours_ago=30)
        newest = self._hot_player(7002, lookup_hours_ago=1)
        middle = self._hot_player(7003, lookup_hours_ago=10)

        ordered, tier_counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            hot_limit=2, active_limit=0, warm_limit=0,
            hot_lookback_days=14, active_lookback_days=30,
            warm_lookback_days=90, realm="asia",
        )

        self.assertEqual(tier_counts["hot"], 2)
        self.assertEqual(ordered, [newest.id, middle.id])
        self.assertNotIn(oldest.id, ordered)

    @patch("warships.management.commands.incremental_player_refresh._monotonic")
    @patch("warships.management.commands.incremental_player_refresh._refresh_player")
    @patch("warships.management.commands.incremental_player_refresh._build_candidate_queue")
    def test_run_stops_at_wall_clock_budget_and_stays_resumable(
        self, mock_build_candidate_queue, mock_refresh_player, mock_monotonic,
    ):
        players = [
            Player.objects.create(name=f"Budget{i}", player_id=7100 + i, realm="eu")
            for i in range(3)
        ]
        mock_build_candidate_queue.return_value = (
            [p.id for p in players], {"hot": 3, "active": 0, "warm": 0})
        clock = {"now": 0.0}
        mock_monotonic.side_effect = lambda: clock["now"]

        def slow_refresh(player_id, realm="na"):
            clock["now"] += 6.0

        mock_refresh_player.side_effect = slow_refresh

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            out = StringIO()
            call_command(
                "incremental_player_refresh", realm="eu", limit=0,
                run_budget_seconds=10, state_file=str(state_file), stdout=out,
            )
            state = json.loads(state_file.read_text())

        self.assertEqual(mock_refresh_player.call_count, 2)
        self.assertEqual(state["next_index"], 2)
        self.assertEqual(len(state["pending_player_ids"]), 3)
        self.assertIsNone(state["cycle_completed_at"])
        self.assertIn("run budget", out.getvalue())
        self.assertIn("queue_remaining=1", out.getvalue())


class WarmPlayerEntityCachesTests(TestCase):
    @patch("warships.data.fetch_player_clan_battle_seasons")
    @patch("warships.data.refresh_player_explorer_summary")
    @patch("warships.data.update_ranked_data")
    @patch("warships.data.update_randoms_data")
    @patch("warships.data.update_type_data")
    @patch("warships.data.update_tiers_data")
    @patch("warships.data.update_snapshot_data")
    @patch("warships.data.update_battle_data")
    @patch("warships.data.update_player_data")
    def test_warm_player_entity_caches_propagates_realm_to_detail_lanes(
        self,
        mock_update_player_data,
        mock_update_battle_data,
        mock_update_snapshot_data,
        mock_update_tiers_data,
        mock_update_type_data,
        mock_update_randoms_data,
        mock_update_ranked_data,
        mock_refresh_player_explorer_summary,
        mock_fetch_player_clan_battle_seasons,
    ):
        player = Player.objects.create(
            name="WarmEU",
            player_id=6001,
            realm="eu",
            is_hidden=False,
            pvp_battles=250,
        )

        warmed = warm_player_entity_caches(
            [player.player_id],
            force_refresh=True,
            realm="eu",
        )

        self.assertEqual(warmed, 1)
        mock_update_player_data.assert_called_once_with(
            player, force_refresh=True)
        mock_update_battle_data.assert_called_once_with(
            player.player_id, realm="eu")
        mock_update_snapshot_data.assert_called_once_with(
            player.player_id,
            realm="eu",
            refresh_player=False,
        )
        mock_update_tiers_data.assert_called_once_with(
            player.player_id, realm="eu")
        mock_update_type_data.assert_called_once_with(
            player.player_id, realm="eu")
        mock_update_randoms_data.assert_called_once_with(
            player.player_id, realm="eu")
        mock_update_ranked_data.assert_called_once_with(
            player.player_id, realm="eu")
        mock_refresh_player_explorer_summary.assert_called_once()
        mock_fetch_player_clan_battle_seasons.assert_called_once_with(
            player.player_id,
            realm="eu",
        )
