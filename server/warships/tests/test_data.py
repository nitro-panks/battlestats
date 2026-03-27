from datetime import datetime, timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.core.cache import cache
from django.utils import timezone

from warships.clan_crawl import run_clan_crawl, save_player
from warships.api.players import _fetch_player_achievements
from warships.data import update_snapshot_data, fetch_activity_data, fetch_clan_plot_data, fetch_randoms_data, fetch_player_summary, fetch_tier_data, fetch_type_data, update_player_data, update_clan_data, update_clan_members, update_tiers_data, update_type_data, update_randoms_data, update_battle_data, _build_top_ranked_ship_names_by_season, update_ranked_data, refresh_player_explorer_summary, fetch_player_explorer_rows, compute_player_verdict, _inactivity_score_cap, _calculate_actual_kdr, _calculate_tier_filtered_pvp_record, _calculate_ranked_record, get_highest_ranked_league_name, _aggregate_ranked_seasons, fetch_ranked_data, clan_ranked_hydration_needs_refresh, queue_clan_efficiency_hydration, queue_clan_ranked_hydration, normalize_player_achievement_rows, recompute_efficiency_rank_snapshot, update_achievements_data, _efficiency_rank_tier_from_percentile, fetch_player_population_distribution
from warships.landing import LANDING_CLANS_CACHE_KEY, LANDING_CLANS_DIRTY_KEY, LANDING_PLAYER_LIMIT, LANDING_PLAYERS_DIRTY_KEY, LANDING_RECENT_CLANS_CACHE_KEY, LANDING_RECENT_CLANS_DIRTY_KEY, LANDING_RECENT_PLAYERS_CACHE_KEY, LANDING_RECENT_PLAYERS_DIRTY_KEY, landing_player_cache_key
from warships.models import Player, Snapshot, Clan, PlayerAchievementStat, PlayerExplorerSummary, Ship


class SnapshotDataTests(TestCase):
    @patch("warships.data.update_player_data")
    def test_update_snapshot_data_creates_snapshot_and_intervals(self, mock_update_player_data):
        player = Player.objects.create(
            name="ActivityUser", player_id=222,
            pvp_battles=103, pvp_wins=52,
        )

        def hydrate_player(p, force_refresh=False):
            p.pvp_battles = 103
            p.pvp_wins = 52
            p.save()

        mock_update_player_data.side_effect = hydrate_player

        update_snapshot_data(player.player_id)

        today = datetime.now().date()
        snapshot = Snapshot.objects.get(player=player, date=today)
        self.assertEqual(snapshot.battles, 103)
        self.assertEqual(snapshot.wins, 52)


class ClanCrawlPublicationTests(TestCase):
    @patch("warships.clan_crawl.crawl_clan_members", return_value={"clans_processed": 1, "players_saved": 3, "skipped": 0})
    @patch("warships.clan_crawl.crawl_clan_ids", return_value=[{"clan_id": 1001}])
    @patch("warships.tasks.queue_efficiency_rank_snapshot_refresh")
    @patch("warships.clan_crawl.APP_ID", "fixture-app-id")
    def test_run_clan_crawl_queues_efficiency_rank_snapshot_after_saving_players(
        self,
        mock_queue_efficiency_rank_snapshot_refresh,
        mock_crawl_clan_ids,
        mock_crawl_clan_members,
    ):
        summary = run_clan_crawl()

        self.assertEqual(summary["players_saved"], 3)
        mock_crawl_clan_ids.assert_called_once()
        mock_crawl_clan_members.assert_called_once()
        mock_queue_efficiency_rank_snapshot_refresh.assert_called_once_with()

    @patch("warships.clan_crawl.crawl_clan_members", return_value={"clans_processed": 1, "players_saved": 0, "skipped": 1})
    @patch("warships.clan_crawl.crawl_clan_ids", return_value=[{"clan_id": 1001}])
    @patch("warships.tasks.queue_efficiency_rank_snapshot_refresh")
    @patch("warships.clan_crawl.APP_ID", "fixture-app-id")
    def test_run_clan_crawl_skips_efficiency_rank_snapshot_when_no_players_saved(
        self,
        mock_queue_efficiency_rank_snapshot_refresh,
        mock_crawl_clan_ids,
        mock_crawl_clan_members,
    ):
        summary = run_clan_crawl()

        self.assertEqual(summary["players_saved"], 0)
        mock_crawl_clan_ids.assert_called_once()
        mock_crawl_clan_members.assert_called_once()
        mock_queue_efficiency_rank_snapshot_refresh.assert_not_called()

    @patch("warships.clan_crawl.crawl_clan_members")
    @patch("warships.clan_crawl.crawl_clan_ids", return_value=[{"clan_id": 1001}])
    @patch("warships.tasks.queue_efficiency_rank_snapshot_refresh")
    @patch("warships.clan_crawl.APP_ID", "fixture-app-id")
    def test_run_clan_crawl_dry_run_does_not_queue_efficiency_rank_snapshot(
        self,
        mock_queue_efficiency_rank_snapshot_refresh,
        mock_crawl_clan_ids,
        mock_crawl_clan_members,
    ):
        summary = run_clan_crawl(dry_run=True)

        self.assertTrue(summary["dry_run"])
        mock_crawl_clan_ids.assert_called_once()
        mock_crawl_clan_members.assert_not_called()
        mock_queue_efficiency_rank_snapshot_refresh.assert_not_called()


class ActivityDataRefreshTests(TestCase):
    def test_fetch_activity_data_for_missing_player_returns_empty_list(self):
        self.assertEqual(fetch_activity_data("999999"), [])

    @patch("warships.data.update_activity_data")
    @patch("warships.data.update_snapshot_data")
    def test_fetch_activity_data_keeps_fresh_all_zero_cache(
        self,
        mock_update_snapshot_data,
        mock_update_activity_data,
    ):
        player = Player.objects.create(
            name="QuietUser",
            player_id=332,
            activity_json=[
                {"date": "2026-03-01", "battles": 0, "wins": 0},
                {"date": "2026-03-02", "battles": 0, "wins": 0},
            ],
            activity_updated_at=timezone.now(),
        )

        rows = fetch_activity_data(player.player_id)

        self.assertEqual(len(rows), 2)
        mock_update_snapshot_data.assert_not_called()
        mock_update_activity_data.assert_not_called()

    @patch("warships.data.update_snapshot_data_task.delay")
    def test_fetch_activity_data_queues_refresh_for_cumulative_spike_cache(
        self,
        mock_update_snapshot_task,
    ):
        player = Player.objects.create(
            name="SpikeUser",
            player_id=333,
            activity_json=[
                {"date": "2026-03-01", "battles": 0, "wins": 0},
                {"date": "2026-03-02", "battles": 6500, "wins": 3000},
            ],
            activity_updated_at=timezone.now(),
        )

        rows = fetch_activity_data(player.player_id)

        self.assertEqual(rows, player.activity_json)
        mock_update_snapshot_task.assert_called_once_with(player.player_id)

    @patch("warships.data.update_snapshot_data_task.delay")
    def test_fetch_activity_data_returns_empty_and_queues_refresh_when_cache_missing(
        self,
        mock_update_snapshot_task,
    ):
        player = Player.objects.create(
            name="ColdActivityUser",
            player_id=334,
            activity_json=None,
        )

        rows = fetch_activity_data(player.player_id)

        self.assertEqual(rows, [])
        mock_update_snapshot_task.assert_called_once_with(player.player_id)


class RandomsDataRefreshTests(TestCase):
    @patch("warships.data.update_randoms_data_task.delay")
    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_randoms_data_returns_stale_cache_and_queues_refresh(
        self,
        mock_update_battle_task,
        mock_update_randoms_task,
    ):
        player = Player.objects.create(
            name="RandomsUser",
            player_id=444,
            battles_json=[
                {
                    "ship_name": "Old Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 10,
                    "win_ratio": 0.5,
                    "wins": 5,
                }
            ],
            randoms_json=[
                {
                    "ship_name": "Old Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 10,
                    "win_ratio": 0.5,
                    "wins": 5,
                }
            ],
            battles_updated_at=timezone.now() - timedelta(hours=1),
            randoms_updated_at=timezone.now() - timedelta(days=2),
        )

        rows = fetch_randoms_data(player.player_id)

        self.assertEqual(rows[0]["ship_name"], "Old Ship")
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)
        mock_update_randoms_task.assert_not_called()

    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_randoms_data_returns_empty_and_queues_battle_refresh_when_cache_missing(
        self,
        mock_update_battle_task,
    ):
        player = Player.objects.create(
            name="ColdRandomsUser",
            player_id=447,
            battles_json=None,
            randoms_json=None,
        )

        rows = fetch_randoms_data(player.player_id)

        self.assertEqual(rows, [])
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)

    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_randoms_data_clears_inconsistent_cache_timestamps_before_queueing_refresh(
        self,
        mock_update_battle_task,
    ):
        player = Player.objects.create(
            name="ImpossibleRandomsUser",
            player_id=4471,
            battles_json=None,
            battles_updated_at=timezone.now() - timedelta(hours=2),
            randoms_json=None,
            randoms_updated_at=timezone.now() - timedelta(hours=1),
        )

        rows = fetch_randoms_data(player.player_id)

        self.assertEqual(rows, [])
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)

    @patch("warships.data.update_randoms_data_task.delay")
    def test_fetch_randoms_data_uses_extractable_battle_rows_when_randoms_cache_missing(
        self,
        mock_update_randoms_task,
    ):
        player = Player.objects.create(
            name="BattleOnlyRandomsUser",
            player_id=4472,
            pvp_battles=31,
            battles_json=[
                {
                    "ship_name": "Fallback Ship",
                    "ship_chart_name": "Fallback Ship",
                    "ship_type": "Cruiser",
                    "ship_tier": 8,
                    "pvp_battles": 31,
                    "win_ratio": 0.58,
                    "wins": 18,
                }
            ],
            randoms_json=None,
        )

        rows = fetch_randoms_data(player.player_id)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ship_name"], "Fallback Ship")
        mock_update_randoms_task.assert_called_once_with(player.player_id)

    @patch("warships.tasks.queue_ranked_data_refresh")
    def test_fetch_ranked_data_returns_stale_cache_and_queues_refresh(self, mock_queue_ranked_data_refresh):
        player = Player.objects.create(
            name="RankedUser",
            player_id=448,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(hours=2),
        )

        rows = fetch_ranked_data(player.player_id)

        self.assertEqual(rows, [])
        mock_queue_ranked_data_refresh.assert_called_once_with(
            player.player_id)


class DerivedChartCacheRefreshTests(TestCase):
    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_tier_data_returns_empty_and_queues_battle_refresh_when_cache_missing(
        self,
        mock_update_battle_task,
    ):
        player = Player.objects.create(
            name="ColdTierUser",
            player_id=449,
            battles_json=None,
            tiers_json=None,
        )

        rows = fetch_tier_data(player.player_id)

        self.assertEqual(rows, [])
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)

    @patch("warships.data.update_tiers_data_task.delay")
    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_tier_data_keeps_cached_rows_and_only_queues_battle_refresh(
        self,
        mock_update_battle_task,
        mock_update_tiers_task,
    ):
        player = Player.objects.create(
            name="WarmTierUser",
            player_id=4491,
            battles_json=[
                {
                    "ship_name": "Ship",
                    "ship_type": "Cruiser",
                    "ship_tier": 8,
                    "pvp_battles": 20,
                    "wins": 10,
                }
            ],
            battles_updated_at=timezone.now() - timedelta(hours=1),
            tiers_json=[{"ship_tier": 8, "pvp_battles": 20,
                         "wins": 10, "win_ratio": 0.5}],
            tiers_updated_at=timezone.now() - timedelta(days=2),
        )

        rows = fetch_tier_data(player.player_id)

        self.assertEqual(rows, player.tiers_json)
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)
        mock_update_tiers_task.assert_not_called()

    @patch("warships.data.update_type_data_task.delay")
    def test_fetch_type_data_returns_empty_and_queues_derived_refresh_when_battles_exist(
        self,
        mock_update_type_task,
    ):
        player = Player.objects.create(
            name="ColdTypeUser",
            player_id=450,
            battles_json=[
                {
                    "ship_name": "Test Ship",
                    "ship_type": "Cruiser",
                    "ship_tier": 8,
                    "pvp_battles": 20,
                    "win_ratio": 0.5,
                    "wins": 10,
                }
            ],
            battles_updated_at=timezone.now(),
            type_json=None,
        )

        rows = fetch_type_data(player.player_id)

        self.assertEqual(rows, [])
        mock_update_type_task.assert_called_once_with(player.player_id)

    @patch("warships.data.update_type_data_task.delay")
    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_type_data_keeps_cached_rows_and_only_queues_battle_refresh(
        self,
        mock_update_battle_task,
        mock_update_type_task,
    ):
        player = Player.objects.create(
            name="WarmTypeUser",
            player_id=4501,
            battles_json=[
                {
                    "ship_name": "Test Ship",
                    "ship_type": "Cruiser",
                    "ship_tier": 8,
                    "pvp_battles": 20,
                    "win_ratio": 0.5,
                    "wins": 10,
                }
            ],
            battles_updated_at=timezone.now() - timedelta(hours=1),
            type_json=[{"ship_type": "Cruiser",
                        "pvp_battles": 20, "wins": 10, "win_ratio": 0.5}],
            type_updated_at=timezone.now() - timedelta(days=2),
        )

        rows = fetch_type_data(player.player_id)

        self.assertEqual(rows, player.type_json)
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)
        mock_update_type_task.assert_not_called()

    @patch("warships.data.update_clan_members_task.delay")
    @patch("warships.data.update_clan_data_task.delay")
    def test_fetch_clan_plot_data_keeps_cached_rows_and_queues_stale_refreshes(
        self,
        mock_update_clan_data_task,
        mock_update_clan_members_task,
    ):
        clan = Clan.objects.create(
            clan_id=4601,
            name="PlotClan",
            tag="PC",
            members_count=2,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        Player.objects.create(
            name="PlotMember",
            player_id=46011,
            clan=clan,
            pvp_battles=250,
            pvp_ratio=55.0,
        )
        cached_plot = [{
            "player_name": "PlotMember",
            "pvp_battles": 250,
            "pvp_ratio": 55.0,
            "pvp_avg_damage_dealt": 70000,
            "actual_kdr": 1.3,
        }]
        cache.set("clan:plot:v1:4601:active", cached_plot, 900)

        rows = fetch_clan_plot_data("4601", filter_type="active")

        self.assertEqual(rows, cached_plot)
        mock_update_clan_data_task.assert_called_once_with(clan_id="4601")
        mock_update_clan_members_task.assert_called_once_with(clan_id="4601")

    @patch("warships.data.update_clan_members_task.delay")
    @patch("warships.data.update_clan_data_task.delay")
    def test_fetch_clan_plot_data_rebuilds_cached_empty_rows_for_populated_clan(
        self,
        mock_update_clan_data_task,
        mock_update_clan_members_task,
    ):
        clan = Clan.objects.create(
            clan_id=4602,
            name="WarmPlotClan",
            tag="WPC",
            members_count=2,
            last_fetch=timezone.now(),
        )
        Player.objects.create(
            name="PlotMemberA",
            player_id=46021,
            clan=clan,
            pvp_battles=250,
            pvp_ratio=55.0,
        )
        Player.objects.create(
            name="PlotMemberB",
            player_id=46022,
            clan=clan,
            pvp_battles=180,
            pvp_ratio=52.5,
        )
        cache.set("clan:plot:v1:4602:active", [], 900)

        rows = fetch_clan_plot_data("4602", filter_type="active")

        self.assertEqual(
            rows,
            [
                {"player_name": "PlotMemberA", "pvp_battles": 250, "pvp_ratio": 55.0},
                {"player_name": "PlotMemberB", "pvp_battles": 180, "pvp_ratio": 52.5},
            ],
        )
        self.assertEqual(cache.get("clan:plot:v1:4602:active"), rows)
        mock_update_clan_data_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.data.update_clan_members_task.delay")
    @patch("warships.data.update_clan_data_task.delay")
    def test_fetch_clan_plot_data_serves_rows_while_stale_clan_refreshes_in_background(
        self,
        mock_update_clan_data_task,
        mock_update_clan_members_task,
    ):
        clan = Clan.objects.create(
            clan_id=4603,
            name="StalePlotClan",
            tag="SPC",
            members_count=2,
            last_fetch=timezone.now() - timedelta(hours=13),
        )
        Player.objects.create(
            name="PlotMemberA",
            player_id=46031,
            clan=clan,
            pvp_battles=250,
            pvp_ratio=55.0,
        )
        Player.objects.create(
            name="PlotMemberB",
            player_id=46032,
            clan=clan,
            pvp_battles=180,
            pvp_ratio=52.5,
        )

        rows = fetch_clan_plot_data("4603", filter_type="active")

        self.assertEqual(
            rows,
            [
                {"player_name": "PlotMemberA", "pvp_battles": 250, "pvp_ratio": 55.0},
                {"player_name": "PlotMemberB", "pvp_battles": 180, "pvp_ratio": 52.5},
            ],
        )
        self.assertEqual(cache.get("clan:plot:v1:4603:active"), rows)
        mock_update_clan_data_task.assert_called_once_with(clan_id="4603")
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.data.update_clan_members_task.delay")
    @patch("warships.data.update_clan_data_task.delay")
    def test_fetch_clan_plot_data_serves_rows_while_member_refresh_catches_up(
        self,
        mock_update_clan_data_task,
        mock_update_clan_members_task,
    ):
        clan = Clan.objects.create(
            clan_id=4604,
            name="PartialPlotClan",
            tag="PPC",
            members_count=4,
            last_fetch=timezone.now(),
        )
        Player.objects.create(
            name="PlotMemberA",
            player_id=46041,
            clan=clan,
            pvp_battles=250,
            pvp_ratio=55.0,
        )
        Player.objects.create(
            name="PlotMemberB",
            player_id=46042,
            clan=clan,
            pvp_battles=180,
            pvp_ratio=52.5,
        )

        rows = fetch_clan_plot_data("4604", filter_type="active")

        self.assertEqual(
            rows,
            [
                {"player_name": "PlotMemberA", "pvp_battles": 250, "pvp_ratio": 55.0},
                {"player_name": "PlotMemberB", "pvp_battles": 180, "pvp_ratio": 52.5},
            ],
        )
        self.assertEqual(cache.get("clan:plot:v1:4604:active"), rows)
        mock_update_clan_data_task.assert_not_called()
        mock_update_clan_members_task.assert_called_once_with(clan_id="4604")


class PlayerSummaryRefreshTests(TestCase):
    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.data.update_snapshot_data_task.delay")
    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_player_summary_returns_partial_summary_and_queues_refresh_when_bootstrap_needed(
        self,
        mock_update_battle_task,
        mock_update_snapshot_task,
        mock_queue_ranked_data_refresh,
    ):
        player = Player.objects.create(
            name="SummaryBootstrapUser",
            player_id=451,
            is_hidden=False,
            pvp_battles=120,
            pvp_ratio=52.0,
            battles_json=None,
            activity_json=None,
            ranked_json=None,
        )

        summary = fetch_player_summary(player.player_id)

        self.assertEqual(summary["player_id"], player.player_id)
        self.assertEqual(summary["name"], "SummaryBootstrapUser")
        self.assertIsNone(summary["kill_ratio"])
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)
        mock_update_snapshot_task.assert_called_once_with(player.player_id)
        mock_queue_ranked_data_refresh.assert_called_once_with(
            player.player_id)

    @patch("warships.data.refresh_player_explorer_summary")
    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.data.update_snapshot_data_task.delay")
    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_player_summary_keeps_cached_explorer_summary_without_recompute(
        self,
        mock_update_battle_task,
        mock_update_snapshot_task,
        mock_queue_ranked_data_refresh,
        mock_refresh_player_explorer_summary,
    ):
        now = timezone.now()
        player = Player.objects.create(
            name="CachedSummaryUser",
            player_id=4511,
            is_hidden=False,
            pvp_battles=120,
            pvp_ratio=52.0,
            battles_json=[
                {
                    "ship_name": "Ship",
                    "ship_type": "Cruiser",
                    "ship_tier": 8,
                    "pvp_battles": 30,
                    "wins": 15,
                }
            ],
            battles_updated_at=now - timedelta(hours=1),
            activity_json=[{"date": "2026-03-01", "battles": 2, "wins": 1}],
            activity_updated_at=now - timedelta(hours=1),
            ranked_json=[
                {"season_id": 1, "highest_league_name": "Silver", "total_battles": 8}],
            ranked_updated_at=now - timedelta(hours=1),
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            battles_last_29_days=2,
            wins_last_29_days=1,
            active_days_last_29_days=1,
            recent_win_rate=0.5,
            kill_ratio=0.3,
            player_score=1.5,
            ships_played_total=1,
            ship_type_spread=1,
            tier_spread=1,
            ranked_seasons_participated=1,
            latest_ranked_battles=8,
            highest_ranked_league_recent="Silver",
        )

        summary = fetch_player_summary(player.player_id)

        self.assertEqual(summary["player_score"], 1.5)
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)
        mock_update_snapshot_task.assert_called_once_with(player.player_id)
        mock_queue_ranked_data_refresh.assert_called_once_with(
            player.player_id)
        mock_refresh_player_explorer_summary.assert_not_called()


class RandomsCachePolicyTests(TestCase):
    @patch("warships.data.update_randoms_data_task.delay")
    @patch("warships.data.update_battle_data_task.delay")
    def test_fetch_randoms_data_keeps_cached_rows_and_only_queues_battle_refresh(
        self,
        mock_update_battle_task,
        mock_update_randoms_task,
    ):
        player = Player.objects.create(
            name="WarmRandomsUser",
            player_id=4471,
            battles_json=[
                {
                    "ship_name": "Old Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 10,
                    "win_ratio": 0.5,
                    "wins": 5,
                }
            ],
            randoms_json=[
                {
                    "ship_name": "Old Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 10,
                    "win_ratio": 0.5,
                    "wins": 5,
                }
            ],
            battles_updated_at=timezone.now() - timedelta(hours=1),
            randoms_updated_at=timezone.now() - timedelta(days=2),
        )

        rows = fetch_randoms_data(player.player_id)

        self.assertEqual(rows[0]["ship_name"], "Old Ship")
        mock_update_battle_task.assert_called_once_with(
            player_id=player.player_id)
        mock_update_randoms_task.assert_not_called()

    def test_update_randoms_data_uses_plain_python_sorting(self):
        player = Player.objects.create(
            name="Sorter",
            player_id=445,
            battles_json=[
                {"ship_name": "Low", "ship_type": "Destroyer", "ship_tier": 6,
                    "pvp_battles": 3, "win_ratio": 0.33, "wins": 1},
                {"ship_name": "High", "ship_type": "Cruiser", "ship_tier": 10,
                    "pvp_battles": 15, "win_ratio": 0.6, "wins": 9},
            ],
        )

        update_randoms_data(player.player_id)
        player.refresh_from_db()

        self.assertEqual([row["ship_name"]
                         for row in player.randoms_json], ["High", "Low"])
        self.assertEqual([row["ship_chart_name"]
                         for row in player.randoms_json], ["High", "Low"])

    def test_update_randoms_data_adds_abbreviated_chart_name(self):
        player = Player.objects.create(
            name="LongNameUser",
            player_id=4460,
            battles_json=[
                {"ship_name": "Admiral Graf Spee", "ship_type": "Cruiser", "ship_tier": 6,
                    "pvp_battles": 12, "win_ratio": 0.58, "wins": 7},
            ],
        )

        update_randoms_data(player.player_id)
        player.refresh_from_db()

        self.assertEqual(
            player.randoms_json[0]["ship_chart_name"], "Adm. Graf Spee")

    @patch("warships.data._fetch_ship_info")
    @patch("warships.data._fetch_ship_stats_for_player")
    def test_update_battle_data_keeps_rows_when_ship_metadata_is_missing(
        self,
        mock_fetch_ship_stats_for_player,
        mock_fetch_ship_info,
    ):
        player = Player.objects.create(
            name="ShipFallbackUser",
            player_id=4461,
            pvp_battles=20,
        )
        mock_fetch_ship_stats_for_player.return_value = [
            {
                "ship_id": 999001,
                "battles": 20,
                "distance": 1000,
                "pvp": {
                    "battles": 20,
                    "wins": 12,
                    "losses": 8,
                    "frags": 18,
                },
            },
        ]
        mock_fetch_ship_info.return_value = None

        update_battle_data(player.player_id)
        player.refresh_from_db()

        self.assertEqual(len(player.battles_json), 1)
        self.assertEqual(player.battles_json[0]["ship_id"], 999001)
        self.assertEqual(
            player.battles_json[0]["ship_name"], "Unknown Ship 999001")
        self.assertEqual(player.battles_json[0]["ship_type"], "Unknown")
        self.assertEqual(player.battles_json[0]["ship_tier"], 0)

    @patch("warships.data._fetch_ship_stats_for_player")
    def test_update_battle_data_records_attempt_timestamp_when_ship_stats_empty(
        self,
        mock_fetch_ship_stats_for_player,
    ):
        player = Player.objects.create(
            name="EmptyShipStatsPlayer",
            player_id=4462,
            pvp_battles=50,
            battles_json=None,
            battles_updated_at=None,
        )
        mock_fetch_ship_stats_for_player.return_value = []

        before = datetime.now()
        update_battle_data(player.player_id)
        after = datetime.now()

        player.refresh_from_db()
        self.assertIsNone(player.battles_json)
        self.assertIsNotNone(player.battles_updated_at)
        self.assertGreaterEqual(player.battles_updated_at, before)
        self.assertLessEqual(player.battles_updated_at, after)


class AggregateChartDataTests(TestCase):
    def test_update_tiers_data_aggregates_without_pandas(self):
        player = Player.objects.create(
            name="TierCaptain",
            player_id=446,
            battles_json=[
                {"ship_tier": 10, "pvp_battles": 10, "wins": 6},
                {"ship_tier": 10, "pvp_battles": 5, "wins": 2},
                {"ship_tier": 8, "pvp_battles": 3, "wins": 1},
            ],
        )

        update_tiers_data(player.player_id)
        player.refresh_from_db()

        tier_ten = next(
            row for row in player.tiers_json if row["ship_tier"] == 10)
        self.assertEqual(tier_ten["pvp_battles"], 15)
        self.assertEqual(tier_ten["wins"], 8)
        self.assertEqual(tier_ten["win_ratio"], 0.53)

    def test_update_type_data_aggregates_without_pandas(self):
        player = Player.objects.create(
            name="TypeCaptain",
            player_id=447,
            battles_json=[
                {"ship_type": "Destroyer", "pvp_battles": 10, "wins": 5},
                {"ship_type": "Destroyer", "pvp_battles": 6, "wins": 4},
                {"ship_type": "Cruiser", "pvp_battles": 20, "wins": 11},
            ],
        )

        update_type_data(player.player_id)
        player.refresh_from_db()

        self.assertEqual(player.type_json[0]["ship_type"], "Cruiser")
        destroyer = next(
            row for row in player.type_json if row["ship_type"] == "Destroyer")
        self.assertEqual(destroyer["pvp_battles"], 16)
        self.assertEqual(destroyer["wins"], 9)

    @patch("warships.data._fetch_ship_info")
    def test_build_top_ranked_ship_names_by_season_prefers_most_played_ship(self, mock_fetch_ship_info):
        class ShipStub:
            def __init__(self, name):
                self.name = name

        mock_fetch_ship_info.side_effect = lambda ship_id: ShipStub({
            "101": "Yamato",
            "202": "Des Moines",
        }[ship_id])

        rows = [
            {
                "ship_id": 101,
                "seasons": {
                    "1100": {"battles": 12},
                    "1101": {"rank_solo": {"battles": 3}, "rank_div2": {"battles": 1}},
                },
            },
            {
                "ship_id": 202,
                "seasons": {
                    "1100": {"battles": 8},
                    "1101": {"rank_solo": {"battles": 9}},
                },
            },
        ]

        result = _build_top_ranked_ship_names_by_season(rows, [1100, 1101])

        self.assertEqual(result[1100], "Yamato")
        self.assertEqual(result[1101], "Des Moines")

    def test_fetch_player_population_distribution_uses_single_query_for_battles_bins(self):
        Player.objects.create(
            name="BattlesAggA",
            player_id=4481,
            is_hidden=False,
            pvp_battles=150,
            pvp_ratio=48.0,
            pvp_survival_rate=30.0,
        )
        Player.objects.create(
            name="BattlesAggB",
            player_id=4482,
            is_hidden=False,
            pvp_battles=9800,
            pvp_ratio=62.0,
            pvp_survival_rate=46.0,
        )
        cache.delete('player_distribution:v1:battles_played')

        with CaptureQueriesContext(connection) as queries:
            payload = fetch_player_population_distribution('battles_played')

        self.assertEqual(payload['tracked_population'], 2)
        self.assertTrue(
            any(row['bin_min'] == 100 and row['count'] == 1 for row in payload['bins']))
        self.assertTrue(
            any(row['bin_min'] == 6400 and row['count'] == 1 for row in payload['bins']))
        self.assertLessEqual(len(queries), 2)


class PlayerAchievementsDataTests(TestCase):
    def _lil_boots_payload(self):
        return {
            'battle': {
                'PCH003_MainCaliber': 109,
                'PCH016_FirstBlood': 359,
                'PCH023_Warrior': 27,
                'PCH070_Campaign1Completed': 1,
                'PCH087_FillAlbum': 1,
                'PCH097_PVE_HON_WIN_ALL_DONE': 529,
                'PCH999_CombatMaybe': 3,
            },
            'progress': {
                'PCH031_EarningMoney1': 0,
            },
        }

    @patch('warships.api.players._make_api_request')
    def test_fetch_player_achievements_returns_per_account_payload(self, mock_make_api_request):
        mock_make_api_request.return_value = {
            '555': {
                'battle': {'PCH016_FirstBlood': 9},
                'progress': {'PCH031_EarningMoney1': 0},
            }
        }

        result = _fetch_player_achievements(555)

        self.assertEqual(result, {
            'battle': {'PCH016_FirstBlood': 9},
            'progress': {'PCH031_EarningMoney1': 0},
        })

    @patch('warships.api.players._make_api_request', return_value={'555': None})
    def test_fetch_player_achievements_tolerates_null_account_payload(self, _mock_make_api_request):
        self.assertIsNone(_fetch_player_achievements(555))

    @patch('warships.api.players._make_api_request', return_value={})
    def test_fetch_player_achievements_tolerates_hidden_omission(self, _mock_make_api_request):
        self.assertIsNone(_fetch_player_achievements(555))

    def test_normalize_player_achievement_rows_filters_mixed_payload(self):
        rows = normalize_player_achievement_rows(self._lil_boots_payload())

        self.assertEqual(
            [row['achievement_slug'] for row in rows],
            ['first-blood', 'kraken-unleashed', 'main-caliber'],
        )
        self.assertEqual(
            [row['achievement_label'] for row in rows],
            ['First Blood', 'Kraken Unleashed', 'Main Caliber'],
        )

    @patch('warships.data._fetch_player_achievements')
    def test_update_achievements_data_stores_raw_payload_and_curated_rows(self, mock_fetch_player_achievements):
        player = Player.objects.create(
            name='AchievementCaptain',
            player_id=6001,
            is_hidden=False,
        )
        mock_fetch_player_achievements.return_value = self._lil_boots_payload()

        rows = update_achievements_data(player.player_id, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.achievements_json, self._lil_boots_payload())
        self.assertIsNotNone(player.achievements_updated_at)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            list(player.achievement_stats.order_by(
                'achievement_slug').values_list('achievement_slug', flat=True)),
            ['first-blood', 'kraken-unleashed', 'main-caliber'],
        )

    @patch('warships.data._fetch_player_achievements')
    def test_update_achievements_data_is_idempotent(self, mock_fetch_player_achievements):
        player = Player.objects.create(
            name='IdempotentCaptain',
            player_id=6002,
            is_hidden=False,
        )
        mock_fetch_player_achievements.return_value = self._lil_boots_payload()

        update_achievements_data(player.player_id, force_refresh=True)
        update_achievements_data(player.player_id, force_refresh=True)

        self.assertEqual(PlayerAchievementStat.objects.filter(
            player=player).count(), 3)

    @patch('warships.data._fetch_player_achievements')
    def test_update_achievements_data_keeps_prior_data_for_hidden_players(self, mock_fetch_player_achievements):
        player = Player.objects.create(
            name='HiddenAchievementCaptain',
            player_id=6003,
            is_hidden=True,
            achievements_json={'battle': {'PCH016_FirstBlood': 2}},
            achievements_updated_at=timezone.now(),
        )
        PlayerAchievementStat.objects.create(
            player=player,
            achievement_code='PCH016_FirstBlood',
            achievement_slug='first-blood',
            achievement_label='First Blood',
            category='combat',
            count=2,
            source_kind='battle',
            refreshed_at=timezone.now(),
        )

        rows = update_achievements_data(player.player_id, force_refresh=True)

        mock_fetch_player_achievements.assert_not_called()
        player.refresh_from_db()
        self.assertEqual(player.achievements_json, {
                         'battle': {'PCH016_FirstBlood': 2}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(PlayerAchievementStat.objects.filter(
            player=player).count(), 1)

    @patch('warships.data._fetch_player_achievements', return_value={'progress': {'PCH031_EarningMoney1': 0}})
    def test_update_achievements_data_handles_missing_battle_map(self, _mock_fetch_player_achievements):
        player = Player.objects.create(
            name='MissingBattleCaptain',
            player_id=6004,
            is_hidden=False,
        )

        rows = update_achievements_data(player.player_id, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(rows, [])
        self.assertEqual(player.achievements_json, {
                         'progress': {'PCH031_EarningMoney1': 0}})
        self.assertFalse(PlayerAchievementStat.objects.filter(
            player=player).exists())


class RankedDataRefreshTests(TestCase):
    def test_aggregate_ranked_seasons_keeps_full_non_empty_history(self):
        result = _aggregate_ranked_seasons(
            {
                "1001": {
                    "1": {
                        "1": {"battles": 3, "victories": 2, "rank": 6, "best_rank_in_sprint": 6},
                    },
                },
                "1002": {
                    "1": {
                        "1": {"battles": 4, "victories": 3, "rank": 5, "best_rank_in_sprint": 5},
                    },
                },
                "1003": {
                    "1": {
                        "1": {"battles": 5, "victories": 4, "rank": 4, "best_rank_in_sprint": 4},
                    },
                },
                "1004": {
                    "1": {
                        "1": {"battles": 6, "victories": 4, "rank": 3, "best_rank_in_sprint": 3},
                    },
                },
                "1005": {
                    "1": {
                        "1": {"battles": 7, "victories": 5, "rank": 2, "best_rank_in_sprint": 2},
                    },
                },
                "1006": {
                    "1": {
                        "1": {"battles": 8, "victories": 6, "rank": 1, "best_rank_in_sprint": 1},
                    },
                },
                "1007": {
                    "1": {
                        "1": {"battles": 9, "victories": 6, "rank": 4, "best_rank_in_sprint": 4},
                    },
                },
                "1008": {
                    "1": {
                        "1": {"battles": 10, "victories": 7, "rank": 3, "best_rank_in_sprint": 3},
                    },
                },
                "1009": {
                    "1": {
                        "1": {"battles": 11, "victories": 8, "rank": 2, "best_rank_in_sprint": 2},
                    },
                },
                "1010": {
                    "1": {
                        "1": {"battles": 12, "victories": 9, "rank": 1, "best_rank_in_sprint": 1},
                    },
                },
                "1011": {
                    "1": {
                        "1": {"battles": 13, "victories": 9, "rank": 5, "best_rank_in_sprint": 5},
                    },
                },
                "1012": {
                    "1": {
                        "1": {"battles": 14, "victories": 10, "rank": 4, "best_rank_in_sprint": 4},
                    },
                },
            },
            {season_id: {"label": f"S{season_id - 1000}"}
                for season_id in range(1001, 1013)},
        )

        self.assertEqual([row["season_id"]
                         for row in result], list(range(1001, 1013)))

    def test_aggregate_ranked_seasons_ignores_impossible_zero_battle_victories(self):
        result = _aggregate_ranked_seasons(
            {
                "1002": {
                    "1": {
                        "3": {"battles": 0, "victories": 33, "rank": 1, "best_rank_in_sprint": 1},
                    },
                    "2": {
                        "2": {"battles": 0, "victories": 35, "rank": 1, "best_rank_in_sprint": 1},
                    },
                    "3": {
                        "1": {"battles": 0, "victories": 45, "rank": 4, "best_rank_in_sprint": 4},
                    },
                    "4": {
                        "1": {"battles": 22, "victories": 12, "rank": 4, "best_rank_in_sprint": 4},
                    },
                    "5": {
                        "1": {"battles": 0, "victories": 29, "rank": 6, "best_rank_in_sprint": 6},
                    },
                },
            },
            {
                1002: {"name": "The Second Season", "label": "S2", "start_date": "2021-02-17", "end_date": "2021-05-14"},
            },
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["season_id"], 1002)
        self.assertEqual(result[0]["highest_league"], 1)
        self.assertEqual(result[0]["total_battles"], 22)
        self.assertEqual(result[0]["total_wins"], 12)
        self.assertAlmostEqual(result[0]["win_rate"], 0.5455)
        self.assertEqual(result[0]["best_sprint"]["sprint_number"], 4)
        self.assertEqual(result[0]["best_sprint"]["wins"], 12)
        self.assertEqual(
            [sprint["wins"] for sprint in result[0]["sprints"]],
            [0, 0, 0, 12, 0],
        )

    @patch("warships.data.update_ranked_data")
    def test_fetch_ranked_data_uses_fresh_cache_even_without_top_ship_enrichment(self, mock_update_ranked_data):
        now = timezone.now()
        player = Player.objects.create(
            name="FreshRankedCache",
            player_id=7010,
            ranked_updated_at=now,
            ranked_json=[
                {
                    "season_id": 1100,
                    "highest_league": 2,
                    "highest_league_name": "Silver",
                    "total_battles": 25,
                    "total_wins": 14,
                    "win_rate": 0.56,
                }
            ],
        )

        result = fetch_ranked_data(str(player.player_id))

        self.assertEqual(result, player.ranked_json)
        mock_update_ranked_data.assert_not_called()

    @patch("warships.data.update_ranked_data")
    def test_fetch_ranked_data_uses_fresh_empty_cache(self, mock_update_ranked_data):
        now = timezone.now()
        player = Player.objects.create(
            name="FreshEmptyRankedCache",
            player_id=7011,
            ranked_updated_at=now,
            ranked_json=[],
        )

        result = fetch_ranked_data(str(player.player_id))

        self.assertEqual(result, [])
        mock_update_ranked_data.assert_not_called()

    @patch("warships.data._fetch_ranked_ship_stats_for_player")
    @patch("warships.data._fetch_ship_info")
    @patch("warships.data._fetch_ranked_account_info")
    @patch("warships.data._get_ranked_seasons_metadata")
    def test_update_ranked_data_adds_top_ship_name(
        self,
        mock_get_ranked_seasons_metadata,
        mock_fetch_ranked_account_info,
        mock_fetch_ship_info,
        mock_fetch_ranked_ship_stats_for_player,
    ):
        class ShipStub:
            def __init__(self, name):
                self.name = name

        player = Player.objects.create(name="RankedCaptain", player_id=7001)
        mock_get_ranked_seasons_metadata.return_value = {
            1100: {"name": "Season 100", "label": "S100", "start_date": "2026-01-01", "end_date": "2026-02-01"},
        }
        mock_fetch_ranked_account_info.return_value = {
            "rank_info": {
                "1100": {
                    "1": {
                        "1": {"battles": 7, "victories": 4, "rank": 5, "best_rank_in_sprint": 5},
                    },
                },
            },
        }
        mock_fetch_ranked_ship_stats_for_player.return_value = [
            {
                "ship_id": 999,
                "seasons": {
                    "1100": {"battles": 6},
                },
            },
        ]
        mock_fetch_ship_info.return_value = ShipStub("Stalingrad")

        update_ranked_data(player.player_id)
        player.refresh_from_db()

        self.assertEqual(player.ranked_json[0]["top_ship_name"], "Stalingrad")

    def test_clan_ranked_hydration_needs_refresh_for_missing_timestamp(self):
        player = Player.objects.create(
            name="MissingRankedTimestamp", player_id=7101)

        self.assertTrue(clan_ranked_hydration_needs_refresh(player))

    def test_clan_ranked_hydration_needs_refresh_respects_24_hour_budget(self):
        fresh_player = Player.objects.create(
            name="FreshRankedHydration",
            player_id=7102,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(hours=6),
        )
        stale_player = Player.objects.create(
            name="StaleRankedHydration",
            player_id=7103,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(days=2),
        )

        self.assertFalse(clan_ranked_hydration_needs_refresh(fresh_player))
        self.assertTrue(clan_ranked_hydration_needs_refresh(stale_player))

    @override_settings(USE_TZ=True)
    def test_clan_ranked_hydration_needs_refresh_handles_aware_timestamps(self):
        fresh_player = Player.objects.create(
            name="AwareFreshRankedHydration",
            player_id=7109,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(hours=3),
        )
        stale_player = Player.objects.create(
            name="AwareStaleRankedHydration",
            player_id=7110,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(days=2),
        )

        self.assertFalse(clan_ranked_hydration_needs_refresh(fresh_player))
        self.assertTrue(clan_ranked_hydration_needs_refresh(stale_player))

    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.tasks.is_ranked_data_refresh_pending")
    def test_queue_clan_ranked_hydration_only_enqueues_missing_or_stale_players(
        self,
        mock_is_ranked_data_refresh_pending,
        mock_queue_ranked_data_refresh,
    ):
        fresh_player = Player.objects.create(
            name="FreshRankedMember",
            player_id=7104,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(hours=2),
        )
        missing_player = Player.objects.create(
            name="MissingRankedMember",
            player_id=7105,
            ranked_json=None,
            ranked_updated_at=None,
        )
        queued_player = Player.objects.create(
            name="AlreadyQueuedRankedMember",
            player_id=7106,
            ranked_json=None,
            ranked_updated_at=timezone.now() - timedelta(days=2),
        )

        mock_is_ranked_data_refresh_pending.side_effect = lambda player_id: player_id == queued_player.player_id
        mock_queue_ranked_data_refresh.return_value = {"status": "queued"}

        hydration_state = queue_clan_ranked_hydration(
            [fresh_player, missing_player, queued_player]
        )

        self.assertEqual(hydration_state["pending_player_ids"], {7105, 7106})
        self.assertEqual(hydration_state["queued_player_ids"], {7105})
        self.assertEqual(hydration_state["deferred_player_ids"], set())
        mock_queue_ranked_data_refresh.assert_called_once_with(7105)

    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.tasks.is_ranked_data_refresh_pending")
    @patch("warships.data.CLAN_RANKED_HYDRATION_MAX_IN_FLIGHT", 1)
    def test_queue_clan_ranked_hydration_defers_when_in_flight_budget_is_full(
        self,
        mock_is_ranked_data_refresh_pending,
        mock_queue_ranked_data_refresh,
    ):
        already_pending_player = Player.objects.create(
            name="AlreadyPendingRankedMember",
            player_id=7107,
            ranked_json=None,
            ranked_updated_at=timezone.now() - timedelta(days=2),
        )
        deferred_player = Player.objects.create(
            name="DeferredRankedMember",
            player_id=7108,
            ranked_json=None,
            ranked_updated_at=timezone.now() - timedelta(days=2),
        )

        mock_is_ranked_data_refresh_pending.side_effect = lambda player_id: player_id == already_pending_player.player_id

        hydration_state = queue_clan_ranked_hydration(
            [already_pending_player, deferred_player]
        )

        self.assertEqual(hydration_state["pending_player_ids"], {7107, 7108})
        self.assertEqual(hydration_state["queued_player_ids"], set())
        self.assertEqual(hydration_state["deferred_player_ids"], {7108})
        self.assertEqual(hydration_state["max_in_flight"], 1)
        mock_queue_ranked_data_refresh.assert_not_called()

    @patch("warships.tasks.queue_efficiency_data_refresh")
    @patch("warships.tasks.is_efficiency_data_refresh_pending")
    @patch("warships.tasks.is_efficiency_rank_snapshot_refresh_pending", return_value=False)
    def test_queue_clan_efficiency_hydration_only_enqueues_missing_or_stale_players(
        self,
        mock_is_efficiency_rank_snapshot_refresh_pending,
        mock_is_efficiency_data_refresh_pending,
        mock_queue_efficiency_data_refresh,
    ):
        fresh_player = Player.objects.create(
            name="FreshEfficiencyMember",
            player_id=7116,
            efficiency_json=[],
            efficiency_updated_at=timezone.now() - timedelta(hours=2),
            pvp_battles=500,
        )
        missing_player = Player.objects.create(
            name="MissingEfficiencyMember",
            player_id=7117,
            efficiency_json=None,
            efficiency_updated_at=None,
            pvp_battles=500,
        )
        queued_player = Player.objects.create(
            name="AlreadyQueuedEfficiencyMember",
            player_id=7118,
            efficiency_json=None,
            efficiency_updated_at=timezone.now() - timedelta(days=2),
            pvp_battles=500,
        )

        mock_is_efficiency_data_refresh_pending.side_effect = lambda player_id: player_id == queued_player.player_id
        mock_queue_efficiency_data_refresh.return_value = {"status": "queued"}

        hydration_state = queue_clan_efficiency_hydration(
            [fresh_player, missing_player, queued_player]
        )

        self.assertEqual(hydration_state["pending_player_ids"], {7117, 7118})
        self.assertEqual(hydration_state["queued_player_ids"], {7117})
        self.assertEqual(hydration_state["deferred_player_ids"], set())
        mock_queue_efficiency_data_refresh.assert_called_once_with(7117)

    @patch("warships.tasks.queue_efficiency_data_refresh")
    @patch("warships.tasks.is_efficiency_data_refresh_pending")
    @patch("warships.data.CLAN_EFFICIENCY_HYDRATION_MAX_IN_FLIGHT", 1)
    @patch("warships.tasks.is_efficiency_rank_snapshot_refresh_pending", return_value=False)
    def test_queue_clan_efficiency_hydration_defers_when_in_flight_budget_is_full(
        self,
        mock_is_efficiency_rank_snapshot_refresh_pending,
        mock_is_efficiency_data_refresh_pending,
        mock_queue_efficiency_data_refresh,
    ):
        already_pending_player = Player.objects.create(
            name="AlreadyPendingEfficiencyMember",
            player_id=7119,
            efficiency_json=None,
            efficiency_updated_at=timezone.now() - timedelta(days=2),
            pvp_battles=500,
        )
        deferred_player = Player.objects.create(
            name="DeferredEfficiencyMember",
            player_id=7120,
            efficiency_json=None,
            efficiency_updated_at=timezone.now() - timedelta(days=2),
            pvp_battles=500,
        )

        mock_is_efficiency_data_refresh_pending.side_effect = lambda player_id: player_id == already_pending_player.player_id

        hydration_state = queue_clan_efficiency_hydration(
            [already_pending_player, deferred_player]
        )

        self.assertEqual(hydration_state["pending_player_ids"], {7119, 7120})
        self.assertEqual(hydration_state["queued_player_ids"], set())
        self.assertEqual(hydration_state["deferred_player_ids"], {7120})
        self.assertEqual(hydration_state["max_in_flight"], 1)
        mock_queue_efficiency_data_refresh.assert_not_called()

    @patch("warships.tasks.queue_efficiency_rank_snapshot_refresh")
    @patch("warships.tasks.is_efficiency_rank_snapshot_refresh_pending")
    @patch("warships.tasks.is_efficiency_data_refresh_pending", return_value=False)
    def test_queue_clan_efficiency_hydration_marks_snapshot_stale_players_pending(
        self,
        mock_is_efficiency_data_refresh_pending,
        mock_is_efficiency_rank_snapshot_refresh_pending,
        mock_queue_efficiency_rank_snapshot_refresh,
    ):
        player = Player.objects.create(
            name="PublicationStaleMember",
            player_id=7121,
            pvp_battles=500,
            efficiency_json=[{"ship_id": 1, "top_grade_class": 4}],
            efficiency_updated_at=timezone.now(),
            battles_updated_at=timezone.now(),
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            eligible_ship_count=6,
            efficiency_badge_rows_total=6,
            badge_rows_unmapped=0,
            normalized_badge_strength=0.6,
            efficiency_rank_updated_at=None,
        )

        mock_is_efficiency_rank_snapshot_refresh_pending.return_value = False
        mock_queue_efficiency_rank_snapshot_refresh.return_value = {
            "status": "queued"}

        hydration_state = queue_clan_efficiency_hydration([player])

        self.assertEqual(hydration_state["pending_player_ids"], {7121})
        self.assertEqual(hydration_state["queued_player_ids"], {7121})
        self.assertEqual(hydration_state["deferred_player_ids"], set())
        mock_queue_efficiency_rank_snapshot_refresh.assert_called_once_with()


class PlayerDataHardeningTests(TestCase):
    def test_calculate_actual_kdr_uses_kills_over_deaths(self):
        deaths, actual_kdr = _calculate_actual_kdr(120, 180, 30)

        self.assertEqual(deaths, 90)
        self.assertEqual(actual_kdr, 2.0)

    def test_calculate_actual_kdr_returns_null_without_battles(self):
        deaths, actual_kdr = _calculate_actual_kdr(0, 0, 0)

        self.assertEqual(deaths, 0)
        self.assertIsNone(actual_kdr)

    def test_calculate_actual_kdr_returns_null_without_deaths(self):
        deaths, actual_kdr = _calculate_actual_kdr(12, 24, 12)

        self.assertEqual(deaths, 0)
        self.assertIsNone(actual_kdr)

    def test_compute_player_verdict_uses_new_playstyle_bands(self):
        self.assertEqual(compute_player_verdict(500, 65.1, 34.0), "Sealord")
        self.assertEqual(compute_player_verdict(500, 65.0, 34.0), "Assassin")
        self.assertEqual(compute_player_verdict(500, 60.0, 24.0), "Kraken")
        self.assertEqual(compute_player_verdict(500, 57.1, 35.0), "Stalwart")
        self.assertEqual(compute_player_verdict(500, 57.1, 28.0), "Daredevil")
        self.assertEqual(compute_player_verdict(500, 55.0, 35.0), "Warrior")
        self.assertEqual(compute_player_verdict(500, 55.0, 28.0), "Raider")
        self.assertEqual(compute_player_verdict(500, 53.0, 35.0), "Survivor")
        self.assertEqual(compute_player_verdict(500, 53.0, 28.0), "Jetsam")
        self.assertEqual(compute_player_verdict(500, 50.0, 35.0), "Flotsam")
        self.assertEqual(compute_player_verdict(500, 50.0, 28.0), "Drifter")
        self.assertEqual(compute_player_verdict(500, 45.0, 35.0), "Pirate")
        self.assertEqual(compute_player_verdict(500, 45.0, 24.0), "Potato")
        self.assertEqual(compute_player_verdict(500, 44.9, 35.0), "Hot Potato")
        self.assertEqual(compute_player_verdict(
            500, 44.9, 24.0), "Leroy Jenkins")
        self.assertIsNone(compute_player_verdict(500, 50.0, None))

    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_hidden_profile_clears_cached_views(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
    ):
        player = Player.objects.create(
            name="VisibleCaptain",
            player_id=8080,
            is_hidden=False,
            total_battles=100,
            pvp_battles=90,
            pvp_wins=50,
            pvp_losses=40,
            pvp_ratio=55.5,
            battles_json=[{"ship_name": "Old Ship"}],
            randoms_json=[{"ship_name": "Old Ship"}],
            ranked_json=[{"season_id": 1}],
            efficiency_json=[{"ship_id": 1, "top_grade_class": 1}],
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 8080,
            "nickname": "VisibleCaptain",
            "hidden_profile": True,
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertTrue(player.is_hidden)
        self.assertEqual(player.total_battles, 0)
        self.assertEqual(player.pvp_frags, 0)
        self.assertEqual(player.pvp_survived_battles, 0)
        self.assertEqual(player.pvp_deaths, 0)
        self.assertIsNone(player.actual_kdr)
        self.assertIsNone(player.battles_json)
        self.assertIsNone(player.randoms_json)
        self.assertIsNone(player.ranked_json)
        self.assertIsNone(player.efficiency_json)

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_computes_actual_kdr(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        player = Player.objects.create(
            name="KdrCaptain",
            player_id=8081,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 8081,
            "nickname": "KdrCaptain",
            "hidden_profile": False,
            "statistics": {
                "battles": 200,
                "pvp": {
                    "battles": 120,
                    "wins": 70,
                    "losses": 50,
                    "frags": 180,
                    "survived_battles": 30,
                    "survived_wins": 20,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.pvp_frags, 180)
        self.assertEqual(player.pvp_survived_battles, 30)
        self.assertEqual(player.pvp_deaths, 90)
        self.assertEqual(player.actual_kdr, 2.0)

    @patch("warships.data._fetch_efficiency_badges_for_player")
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_hydrates_efficiency_badges(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        mock_fetch_efficiency_badges,
    ):
        Ship.objects.create(
            ship_id=111,
            name="Badge Ship",
            chart_name="Badge Ship",
            nation="usa",
            ship_type="Cruiser",
            tier=8,
        )
        player = Player.objects.create(
            name="BadgeCaptain",
            player_id=9291,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9291,
            "nickname": "BadgeCaptain",
            "hidden_profile": False,
            "statistics": {
                "battles": 120,
                "pvp": {
                    "battles": 100,
                    "wins": 55,
                    "losses": 45,
                    "survived_battles": 30,
                    "survived_wins": 20,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}
        mock_fetch_efficiency_badges.return_value = [
            {"ship_id": 111, "top_grade_class": 1},
        ]

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.efficiency_json, [{
            "ship_id": 111,
            "top_grade_class": 1,
            "top_grade_label": "Expert",
            "badge_label": "Expert",
            "ship_name": "Badge Ship",
            "ship_chart_name": "Badge Ship",
            "ship_type": "Cruiser",
            "ship_tier": 8,
            "nation": "usa",
        }])
        self.assertIsNotNone(player.efficiency_updated_at)

    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_does_not_overwrite_on_empty_upstream_response(self, mock_fetch_player_personal_data):
        player = Player.objects.create(
            name="StableCaptain",
            player_id=9090,
            total_battles=77,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.name, "StableCaptain")
        self.assertEqual(player.total_battles, 77)

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_does_not_stamp_battle_cache_timestamp_without_battles_json(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        player = Player.objects.create(
            name="PendingBattleCaptain",
            player_id=9190,
            battles_json=None,
            battles_updated_at=timezone.now() - timedelta(days=2),
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9190,
            "nickname": "PendingBattleCaptain",
            "hidden_profile": False,
            "stats_updated_at": int(timezone.now().timestamp()),
            "statistics": {
                "battles": 250,
                "pvp": {
                    "battles": 200,
                    "wins": 120,
                    "losses": 80,
                    "survived_battles": 50,
                    "survived_wins": 35,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        # battles_updated_at is stamped from the WG API stats_updated_at field
        self.assertIsNotNone(player.battles_updated_at)
        self.assertEqual(player.pvp_battles, 200)

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_preserves_existing_battle_cache_timestamp(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        original_battles_updated_at = timezone.now() - timedelta(hours=3)
        player = Player.objects.create(
            name="CachedBattleCaptain",
            player_id=9192,
            battles_json=[{"ship_name": "Ship A",
                           "pvp_battles": 25, "wins": 15}],
            battles_updated_at=original_battles_updated_at,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9192,
            "nickname": "CachedBattleCaptain",
            "hidden_profile": False,
            "stats_updated_at": int(timezone.now().timestamp()),
            "statistics": {
                "battles": 180,
                "pvp": {
                    "battles": 150,
                    "wins": 90,
                    "losses": 60,
                    "survived_battles": 40,
                    "survived_wins": 25,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        # battles_updated_at is overwritten with stats_updated_at from WG API
        self.assertNotEqual(player.battles_updated_at, original_battles_updated_at)
        self.assertIsNotNone(player.battles_updated_at)
        self.assertEqual(player.pvp_battles, 150)

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_invalidates_landing_players_cache(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        player = Player.objects.create(
            name="CachedCaptain",
            player_id=9191,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        stale_player_key = landing_player_cache_key(
            "random", LANDING_PLAYER_LIMIT)
        cache.set(stale_player_key, [{"name": "stale"}], 60)
        cache.set(LANDING_RECENT_PLAYERS_CACHE_KEY,
                  [{"name": "recent-stale"}], 60)
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9191,
            "nickname": "CachedCaptain",
            "hidden_profile": False,
            "statistics": {"battles": 10, "pvp": {"battles": 8, "wins": 4, "losses": 4}},
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        self.assertEqual(cache.get(landing_player_cache_key(
            "random", LANDING_PLAYER_LIMIT)), [{"name": "stale"}])
        self.assertEqual(cache.get(LANDING_RECENT_PLAYERS_CACHE_KEY), [
                         {"name": "recent-stale"}])
        self.assertIsNotNone(cache.get(LANDING_PLAYERS_DIRTY_KEY))
        self.assertIsNotNone(cache.get(LANDING_RECENT_PLAYERS_DIRTY_KEY))

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_assigns_assassin_playstyle_at_unicum_threshold(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        player = Player.objects.create(
            name="AssassinCandidate",
            player_id=9292,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9292,
            "nickname": "AssassinCandidate",
            "hidden_profile": False,
            "statistics": {
                "battles": 2000,
                "pvp": {
                    "battles": 1800,
                    "wins": 1080,
                    "losses": 720,
                    "survived_battles": 650,
                    "survived_wins": 350,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.pvp_ratio, 60.0)
        self.assertEqual(player.verdict, "Assassin")

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_assigns_sealord_playstyle_above_super_unicum_threshold(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        player = Player.objects.create(
            name="SealordCandidate",
            player_id=9293,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9293,
            "nickname": "SealordCandidate",
            "hidden_profile": False,
            "statistics": {
                "battles": 2200,
                "pvp": {
                    "battles": 2000,
                    "wins": 1310,
                    "losses": 690,
                    "survived_battles": 700,
                    "survived_wins": 450,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.pvp_ratio, 65.5)
        self.assertEqual(player.verdict, "Sealord")

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_assigns_warrior_playstyle_for_good_band(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        player = Player.objects.create(
            name="StalwartCandidate",
            player_id=9393,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9393,
            "nickname": "StalwartCandidate",
            "hidden_profile": False,
            "statistics": {
                "battles": 1200,
                "pvp": {
                    "battles": 1000,
                    "wins": 550,
                    "losses": 450,
                    "survived_battles": 360,
                    "survived_wins": 240,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.pvp_ratio, 55.0)
        self.assertEqual(player.verdict, "Warrior")

    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_assigns_flotsam_to_average_band_players(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
        _mock_fetch_efficiency_badges,
    ):
        player = Player.objects.create(
            name="AverageCandidate",
            player_id=9394,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9394,
            "nickname": "AverageCandidate",
            "hidden_profile": False,
            "statistics": {
                "battles": 1200,
                "pvp": {
                    "battles": 1000,
                    "wins": 500,
                    "losses": 500,
                    "survived_battles": 360,
                    "survived_wins": 180,
                },
            },
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        player.refresh_from_db()
        self.assertEqual(player.pvp_ratio, 50.0)
        self.assertEqual(player.verdict, "Flotsam")


class PlayerExplorerSummaryTests(TestCase):
    def test_calculate_ranked_record_aggregates_battles_and_wins(self):
        battles, win_rate = _calculate_ranked_record([
            {"season_id": 9, "total_battles": 20, "total_wins": 12},
            {"season_id": 8, "total_battles": 15, "win_rate": 0.6},
            {"season_id": 7, "total_battles": 0, "total_wins": 0},
        ])

        self.assertEqual(battles, 35)
        self.assertEqual(win_rate, 60.0)

    def test_calculate_tier_filtered_pvp_record_ignores_tiers_one_through_four(self):
        battles, win_rate = _calculate_tier_filtered_pvp_record([
            {"ship_tier": 3, "pvp_battles": 400, "wins": 320},
            {"ship_tier": 4, "pvp_battles": 300, "wins": 210},
            {"ship_tier": 5, "pvp_battles": 120, "wins": 66},
            {"ship_tier": 10, "pvp_battles": 80, "wins": 44},
        ])

        self.assertEqual(battles, 200)
        self.assertEqual(win_rate, 55.0)

    def test_calculate_tier_filtered_pvp_record_returns_none_without_eligible_rows(self):
        battles, win_rate = _calculate_tier_filtered_pvp_record([
            {"ship_tier": 1, "pvp_battles": 200, "wins": 150},
            {"ship_tier": 4, "pvp_battles": 100, "wins": 55},
        ])

        self.assertEqual(battles, 0)
        self.assertIsNone(win_rate)

    def test_refresh_player_explorer_summary_persists_denormalized_metrics(self):
        now = timezone.now()
        player = Player.objects.create(
            name="ExplorerSummaryPlayer",
            player_id=9911,
            is_hidden=False,
            pvp_ratio=53.4,
            pvp_battles=1234,
            pvp_survival_rate=39.5,
            creation_date=now - timedelta(days=250),
            activity_json=[
                {"date": "2026-03-01", "battles": 2, "wins": 1},
                {"date": "2026-03-02", "battles": 4, "wins": 3},
            ],
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 8, "wins": 5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 4, "wins": 2},
            ],
            ranked_json=[
                {"season_id": 3, "highest_league_name": "Silver", "total_battles": 12},
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertEqual(summary.battles_last_29_days, 6)
        self.assertEqual(summary.wins_last_29_days, 4)
        self.assertEqual(summary.active_days_last_29_days, 2)
        self.assertEqual(summary.kill_ratio, 0.0)
        self.assertAlmostEqual(summary.player_score, 3.12, places=2)
        self.assertEqual(summary.ships_played_total, 2)
        self.assertEqual(summary.ship_type_spread, 2)
        self.assertEqual(summary.tier_spread, 2)
        self.assertEqual(summary.ranked_seasons_participated, 1)
        self.assertEqual(summary.latest_ranked_battles, 12)
        self.assertEqual(summary.highest_ranked_league_recent, "Silver")

    def test_refresh_player_explorer_summary_persists_efficiency_rank_inputs(self):
        player = Player.objects.create(
            name="ExplorerEfficiencyInputs",
            player_id=9922,
            is_hidden=False,
            pvp_battles=640,
            battles_json=[
                {"ship_name": "Ship A", "ship_tier": 10, "pvp_battles": 8},
                {"ship_name": "Ship B", "ship_tier": 9, "pvp_battles": 12},
                {"ship_name": "Ship C", "ship_tier": 8, "pvp_battles": 15},
                {"ship_name": "Ship D", "ship_tier": 7, "pvp_battles": 5},
                {"ship_name": "Ship E", "ship_tier": 6, "pvp_battles": 11},
                {"ship_name": "Ship F", "ship_tier": 4, "pvp_battles": 30},
            ],
            efficiency_json=[
                {"ship_id": 1, "top_grade_class": 1, "ship_tier": 10},
                {"ship_id": 2, "top_grade_class": 2, "ship_tier": 9},
                {"ship_id": 3, "top_grade_class": 4, "ship_tier": 8},
                {"ship_id": 4, "top_grade_class": 3},
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertEqual(summary.eligible_ship_count, 3)
        self.assertEqual(summary.efficiency_badge_rows_total, 4)
        self.assertEqual(summary.badge_rows_unmapped, 1)
        self.assertEqual(summary.expert_count, 1)
        self.assertEqual(summary.grade_i_count, 1)
        self.assertEqual(summary.grade_ii_count, 0)
        self.assertEqual(summary.grade_iii_count, 1)
        self.assertEqual(summary.raw_badge_points, 13)
        self.assertEqual(summary.normalized_badge_strength, 0.541667)

    def test_recompute_efficiency_rank_snapshot_uses_mapped_efficiency_rows_denominator(self):
        top_player = Player.objects.create(
            name="ExplorerEfficiencyTop",
            player_id=9923,
            is_hidden=False,
            pvp_battles=900,
            battles_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_json=[
                {"ship_id": index, "top_grade_class": 1, "ship_tier": 10}
                for index in range(1, 6)
            ],
        )
        middle_player = Player.objects.create(
            name="ExplorerEfficiencyMiddle",
            player_id=9924,
            is_hidden=False,
            pvp_battles=700,
            battles_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_json=[
                {"ship_id": 20 + index, "top_grade_class": 2, "ship_tier": 8}
                for index in range(5)
            ],
        )
        ineligible_player = Player.objects.create(
            name="ExplorerEfficiencyRandomsOnly",
            player_id=9925,
            is_hidden=False,
            battles_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_json=[
                {"ship_id": 30, "top_grade_class": 1, "ship_tier": 10},
            ],
        )

        refresh_player_explorer_summary(top_player)
        refresh_player_explorer_summary(middle_player)
        refresh_player_explorer_summary(ineligible_player)

        report = recompute_efficiency_rank_snapshot(skip_refresh=True)

        top_player.refresh_from_db()
        middle_player.refresh_from_db()
        ineligible_player.refresh_from_db()

        self.assertEqual(report['population_size'], 2)
        self.assertEqual(
            top_player.explorer_summary.efficiency_rank_percentile, 1.0)
        self.assertEqual(top_player.explorer_summary.efficiency_rank_tier, 'E')
        self.assertTrue(top_player.explorer_summary.has_efficiency_rank_icon)
        self.assertLess(
            middle_player.explorer_summary.efficiency_rank_percentile, 1.0)
        self.assertIsNone(
            middle_player.explorer_summary.efficiency_rank_tier)
        self.assertFalse(
            middle_player.explorer_summary.has_efficiency_rank_icon)
        self.assertFalse(
            ineligible_player.explorer_summary.has_efficiency_rank_icon)
        self.assertIsNone(
            ineligible_player.explorer_summary.efficiency_rank_percentile)

    def test_recompute_efficiency_rank_snapshot_does_not_require_battles_json_when_badge_rows_are_mapped(self):
        player = Player.objects.create(
            name="ExplorerEfficiencyBadgeRowsOnly",
            player_id=9928,
            is_hidden=False,
            pvp_battles=820,
            battles_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_updated_at=timezone.now() - timedelta(hours=2),
            battles_json=None,
            efficiency_json=[
                {"ship_id": index, "top_grade_class": 1, "ship_tier": 10}
                for index in range(1, 6)
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertEqual(summary.eligible_ship_count, 5)
        self.assertEqual(summary.raw_badge_points, 40)
        self.assertEqual(summary.normalized_badge_strength, 1.0)

        report = recompute_efficiency_rank_snapshot(skip_refresh=True)
        player.refresh_from_db()

        self.assertEqual(report['population_size'], 1)
        self.assertEqual(player.explorer_summary.efficiency_rank_tier, 'E')
        self.assertTrue(player.explorer_summary.has_efficiency_rank_icon)

    def test_recompute_efficiency_rank_snapshot_suppresses_players_with_unmapped_badges(self):
        gated_player = Player.objects.create(
            name="ExplorerEfficiencyUnmapped",
            player_id=9926,
            is_hidden=False,
            pvp_battles=640,
            battles_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_json=[
                {"ship_id": 1, "top_grade_class": 1, "ship_tier": 8},
                {"ship_id": 2, "top_grade_class": 1, "ship_tier": 8},
                {"ship_id": 3, "top_grade_class": 2, "ship_tier": 9},
                {"ship_id": 4, "top_grade_class": 3, "ship_tier": 10},
                {"ship_id": 5, "top_grade_class": 4, "ship_tier": 7},
                {"ship_id": 6, "top_grade_class": 2},
                {"ship_id": 7, "top_grade_class": 4},
            ],
        )

        refresh_player_explorer_summary(gated_player)
        report = recompute_efficiency_rank_snapshot(skip_refresh=True)
        gated_player.refresh_from_db()

        self.assertEqual(gated_player.explorer_summary.eligible_ship_count, 5)
        self.assertEqual(gated_player.explorer_summary.badge_rows_unmapped, 2)
        self.assertIsNone(
            gated_player.explorer_summary.efficiency_rank_percentile)
        self.assertIsNone(gated_player.explorer_summary.efficiency_rank_tier)
        self.assertFalse(
            gated_player.explorer_summary.has_efficiency_rank_icon)
        self.assertEqual(report['suppressed_counts']['unmapped_badge_gate'], 1)

    def test_efficiency_rank_tier_from_percentile_uses_rank_bands(self):
        self.assertIsNone(_efficiency_rank_tier_from_percentile(0.49))
        self.assertEqual(_efficiency_rank_tier_from_percentile(0.50), 'III')
        self.assertEqual(_efficiency_rank_tier_from_percentile(0.75), 'II')
        self.assertEqual(_efficiency_rank_tier_from_percentile(0.90), 'I')
        self.assertEqual(_efficiency_rank_tier_from_percentile(0.97), 'E')

    def test_recompute_efficiency_rank_snapshot_with_limit_is_analysis_only_by_default(self):
        player = Player.objects.create(
            name='ExplorerEfficiencyPublished',
            player_id=9927,
            is_hidden=False,
            pvp_battles=900,
            battles_updated_at=timezone.now() - timedelta(hours=2),
            efficiency_updated_at=timezone.now() - timedelta(hours=2),
            battles_json=[
                {'ship_name': f'Published Ship {index}',
                    'ship_tier': 10, 'pvp_battles': 12}
                for index in range(5)
            ],
            efficiency_json=[
                {'ship_id': index, 'top_grade_class': 1, 'ship_tier': 10}
                for index in range(1, 6)
            ],
        )

        summary = refresh_player_explorer_summary(player)
        summary.efficiency_rank_percentile = 0.91
        summary.efficiency_rank_tier = 'I'
        summary.has_efficiency_rank_icon = True
        summary.efficiency_rank_population_size = 120
        summary.efficiency_rank_updated_at = timezone.now() - timedelta(hours=1)
        summary.save(update_fields=[
            'efficiency_rank_percentile',
            'efficiency_rank_tier',
            'has_efficiency_rank_icon',
            'efficiency_rank_population_size',
            'efficiency_rank_updated_at',
        ])

        report = recompute_efficiency_rank_snapshot(
            player_limit=1,
            skip_refresh=True,
        )

        player.refresh_from_db()

        self.assertFalse(report['publish_applied'])
        self.assertTrue(report['partial_population'])
        self.assertEqual(player.explorer_summary.efficiency_rank_tier, 'I')
        self.assertEqual(
            player.explorer_summary.efficiency_rank_percentile, 0.91)

    def test_get_highest_ranked_league_name_returns_best_historical_league(self):
        self.assertEqual(
            get_highest_ranked_league_name([
                {"season_id": 7, "highest_league_name": "Bronze", "total_battles": 21},
                {"season_id": 8, "highest_league": 1,
                    "highest_league_name": "Gold", "total_battles": 11},
                {"season_id": 9, "highest_league_name": "Silver", "total_battles": 0},
            ]),
            "Gold",
        )

    def test_refresh_player_explorer_summary_calculates_weighted_kill_ratio_from_kdr_rows(self):
        player = Player.objects.create(
            name="ExplorerKRCaptain",
            player_id=9914,
            is_hidden=False,
            pvp_battles=30,
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 10, "kdr": 1.5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "kdr": 0.5},
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertEqual(summary.kill_ratio, 0.78)
        self.assertEqual(summary.player_score, 1.89)
        self.assertEqual(summary.ships_played_total, 2)

    def test_refresh_player_explorer_summary_heavily_discounts_low_tier_kill_ratio(self):
        player = Player.objects.create(
            name="ExplorerTierWeightedCaptain",
            player_id=9916,
            is_hidden=False,
            pvp_battles=120,
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 3, "pvp_battles": 100, "kdr": 2.5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 10, "pvp_battles": 20, "kdr": 1.1},
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertEqual(summary.kill_ratio, 1.29)
        self.assertEqual(summary.player_score, 1.91)

    def test_refresh_player_explorer_summary_crushes_low_tier_farmed_scores(self):
        player = Player.objects.create(
            name="ExplorerLowTierFarmer",
            player_id=9919,
            is_hidden=False,
            total_battles=34161,
            pvp_battles=28873,
            pvp_ratio=86.79,
            pvp_survival_rate=78.33,
            days_since_last_battle=0,
            activity_json=[
                {"date": "2026-03-09", "battles": 8, "wins": 7},
                {"date": "2026-03-10", "battles": 6, "wins": 5},
            ],
            battles_json=[
                {"ship_name": "Ship T1", "ship_type": "Cruiser",
                    "ship_tier": 1, "pvp_battles": 28508, "kdr": 2.4},
                {"ship_name": "Ship T5", "ship_type": "Cruiser",
                    "ship_tier": 5, "pvp_battles": 106, "kdr": 1.4},
                {"ship_name": "Ship T4", "ship_type": "Cruiser",
                    "ship_tier": 4, "pvp_battles": 117, "kdr": 1.0},
                {"ship_name": "Ship T3", "ship_type": "Cruiser",
                    "ship_tier": 3, "pvp_battles": 88, "kdr": 0.9},
                {"ship_name": "Ship T2", "ship_type": "Cruiser",
                    "ship_tier": 2, "pvp_battles": 54, "kdr": 0.9},
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertLess(summary.player_score, 3.2)
        self.assertGreater(summary.player_score, 2.5)

    def test_inactivity_score_cap_accelerates_toward_requested_thresholds(self):
        self.assertEqual(_inactivity_score_cap(1), 10.0)
        self.assertEqual(_inactivity_score_cap(7), 10.0)
        self.assertEqual(_inactivity_score_cap(8), 10.0)
        self.assertEqual(_inactivity_score_cap(30), 9.61)
        self.assertEqual(_inactivity_score_cap(90), 6.24)
        self.assertEqual(_inactivity_score_cap(140), 3.09)
        self.assertEqual(_inactivity_score_cap(180), 2.0)
        self.assertEqual(_inactivity_score_cap(365), 1.0)
        self.assertEqual(_inactivity_score_cap(500), 0.47)

    def test_refresh_player_explorer_summary_caps_long_inactive_players_at_curve_ceiling(self):
        player = Player.objects.create(
            name="ExplorerInactiveCapCaptain",
            player_id=9920,
            is_hidden=False,
            total_battles=12000,
            pvp_battles=9200,
            pvp_ratio=59.4,
            pvp_survival_rate=41.0,
            days_since_last_battle=140,
            activity_json=[],
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 90, "kdr": 1.6},
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertEqual(summary.player_score, 3.09)

    def test_refresh_player_explorer_summary_caps_dormant_accounts_below_one(self):
        player = Player.objects.create(
            name="DormantScoreCaptain",
            player_id=9917,
            is_hidden=False,
            total_battles=12000,
            pvp_battles=9200,
            pvp_ratio=59.4,
            pvp_survival_rate=41.0,
            days_since_last_battle=500,
            activity_json=[],
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 90, "kdr": 1.6},
            ],
        )

        summary = refresh_player_explorer_summary(player)

        self.assertEqual(summary.player_score, 0.47)

    def test_fetch_player_explorer_rows_preserves_existing_denormalized_summary_values(self):
        player = Player.objects.create(
            name="ExplorerStaleSummary",
            player_id=9915,
            is_hidden=False,
            pvp_battles=30,
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 10, "kdr": 1.5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "kdr": 0.5},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            ships_played_total=0,
            kill_ratio=None,
        )

        rows = fetch_player_explorer_rows(
            query="ExplorerStaleSummary", hidden="visible")

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["kill_ratio"])
        self.assertEqual(rows[0]["ships_played_total"], 0)

        summary = PlayerExplorerSummary.objects.get(player=player)
        self.assertIsNone(summary.kill_ratio)
        self.assertEqual(summary.ships_played_total, 0)

    def test_update_player_data_hidden_profile_clears_denormalized_summary_values(self):
        player = Player.objects.create(
            name="SummaryHiddenCaptain",
            player_id=9912,
            is_hidden=False,
            activity_json=[{"date": "2026-03-01", "battles": 5, "wins": 3}],
            battles_json=[{"ship_name": "Ship A", "ship_type": "Destroyer",
                           "ship_tier": 10, "pvp_battles": 5, "wins": 3}],
            ranked_json=[
                {"season_id": 1, "highest_league_name": "Bronze", "total_battles": 7}],
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            battles_last_29_days=5,
            wins_last_29_days=3,
            active_days_last_29_days=1,
            ships_played_total=1,
            ranked_seasons_participated=1,
        )

        with patch("warships.data._fetch_player_personal_data") as mock_fetch_player_personal_data, patch("warships.data._fetch_clan_membership_for_player") as mock_fetch_clan_membership:
            mock_fetch_player_personal_data.return_value = {
                "account_id": 9912,
                "nickname": "SummaryHiddenCaptain",
                "hidden_profile": True,
            }
            mock_fetch_clan_membership.return_value = {}

            update_player_data(player, force_refresh=True)

        summary = PlayerExplorerSummary.objects.get(player=player)
        self.assertIsNone(summary.battles_last_29_days)
        self.assertIsNone(summary.ships_played_total)
        self.assertIsNone(summary.ranked_seasons_participated)

    def test_clan_crawl_save_player_creates_explorer_summary_row(self):
        clan = Clan.objects.create(clan_id=9913, name="CrawlerClan", tag="CC")

        with patch("warships.data._fetch_efficiency_badges_for_player", return_value=[]), patch("warships.data.update_achievements_data", return_value=[]):
            save_player(
                {
                    "account_id": 9913,
                    "nickname": "CrawlerCaptain",
                    "created_at": int((timezone.now() - timedelta(days=400)).timestamp()),
                    "last_battle_time": int((timezone.now() - timedelta(days=2)).timestamp()),
                    "hidden_profile": False,
                    "statistics": {
                        "battles": 250,
                        "pvp": {
                            "battles": 200,
                            "wins": 110,
                            "losses": 90,
                            "survived_battles": 70,
                        },
                    },
                },
                clan,
            )

        player = Player.objects.get(player_id=9913)
        summary = PlayerExplorerSummary.objects.get(player=player)

        self.assertEqual(player.clan, clan)
        self.assertEqual(player.verdict, "Warrior")
        self.assertEqual(summary.player, player)
        self.assertEqual(summary.battles_last_29_days, 0)
        self.assertIsNone(summary.ships_played_total)
        self.assertIsNone(summary.kill_ratio)

    def test_clan_crawl_save_player_hydrates_efficiency_badges(self):
        clan = Clan.objects.create(
            clan_id=9921, name="BadgeCrawlerClan", tag="BC")
        Ship.objects.create(
            ship_id=222,
            name="Crawler Badge Ship",
            chart_name="Crawler Badge",
            nation="japan",
            ship_type="Destroyer",
            tier=10,
        )

        with patch("warships.data._fetch_efficiency_badges_for_player", return_value=[{"ship_id": 222, "top_grade_class": 2}]), patch("warships.data.update_achievements_data", return_value=[]):
            save_player(
                {
                    "account_id": 9921,
                    "nickname": "BadgeCrawler",
                    "created_at": int((timezone.now() - timedelta(days=250)).timestamp()),
                    "last_battle_time": int((timezone.now() - timedelta(days=1)).timestamp()),
                    "hidden_profile": False,
                    "statistics": {
                        "battles": 800,
                        "pvp": {
                            "battles": 700,
                            "wins": 385,
                            "losses": 315,
                            "survived_battles": 210,
                        },
                    },
                },
                clan,
            )

        player = Player.objects.get(player_id=9921)
        self.assertEqual(player.efficiency_json, [{
            "ship_id": 222,
            "top_grade_class": 2,
            "top_grade_label": "Grade I",
            "badge_label": "Grade I",
            "ship_name": "Crawler Badge Ship",
            "ship_chart_name": "Crawler Badge",
            "ship_type": "Destroyer",
            "ship_tier": 10,
            "nation": "japan",
        }])
        self.assertIsNotNone(player.efficiency_updated_at)

    @patch("warships.data._fetch_player_achievements")
    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    def test_clan_crawl_save_player_hydrates_achievements(
        self,
        _mock_fetch_efficiency_badges,
        mock_fetch_player_achievements,
    ):
        clan = Clan.objects.create(
            clan_id=9922, name="AchievementCrawlerClan", tag="ACH")
        mock_fetch_player_achievements.return_value = {
            "battle": {
                "PCH016_FirstBlood": 12,
                "PCH023_Warrior": 3,
                "PCH087_FillAlbum": 1,
            },
            "progress": {
                "PCH031_EarningMoney1": 0,
            },
        }

        save_player(
            {
                "account_id": 9922,
                "nickname": "AchievementCrawler",
                "created_at": int((timezone.now() - timedelta(days=300)).timestamp()),
                "last_battle_time": int((timezone.now() - timedelta(days=1)).timestamp()),
                "hidden_profile": False,
                "statistics": {
                    "battles": 900,
                    "pvp": {
                        "battles": 750,
                        "wins": 390,
                        "losses": 360,
                        "survived_battles": 225,
                    },
                },
            },
            clan,
        )

        player = Player.objects.get(player_id=9922)
        self.assertEqual(player.achievements_json, {
            "battle": {
                "PCH016_FirstBlood": 12,
                "PCH023_Warrior": 3,
                "PCH087_FillAlbum": 1,
            },
            "progress": {
                "PCH031_EarningMoney1": 0,
            },
        })
        self.assertIsNotNone(player.achievements_updated_at)
        self.assertEqual(
            list(player.achievement_stats.order_by(
                "achievement_slug").values_list("achievement_slug", flat=True)),
            ["first-blood", "kraken-unleashed"],
        )

    @patch("warships.data.update_achievements_data", return_value=[])
    @patch("warships.data._fetch_efficiency_badges_for_player", return_value=[])
    def test_clan_crawl_save_player_collapses_duplicate_players_by_player_id(
        self,
        _mock_fetch_efficiency_badges,
        _mock_update_achievements,
    ):
        clan = Clan.objects.create(
            clan_id=9923, name="DuplicateCrawlerClan", tag="DCC")
        canonical = Player.objects.create(name="Original", player_id=9923)
        duplicate = Player.objects.create(name="Duplicate", player_id=9923)
        Snapshot.objects.create(
            player=duplicate,
            date=timezone.now().date(),
            battles=20,
            wins=11,
        )
        PlayerAchievementStat.objects.create(
            player=duplicate,
            achievement_code="PCH016_FirstBlood",
            achievement_slug="first-blood",
            achievement_label="First Blood",
            category="battle",
            count=4,
            refreshed_at=timezone.now(),
        )
        PlayerExplorerSummary.objects.create(
            player=duplicate,
            player_score=7.2,
            efficiency_rank_tier="E",
            has_efficiency_rank_icon=True,
        )

        save_player(
            {
                "account_id": 9923,
                "nickname": "DedupedCrawler",
                "created_at": int((timezone.now() - timedelta(days=120)).timestamp()),
                "last_battle_time": int((timezone.now() - timedelta(days=1)).timestamp()),
                "hidden_profile": False,
                "statistics": {
                    "battles": 300,
                    "pvp": {
                        "battles": 250,
                        "wins": 140,
                        "losses": 110,
                        "survived_battles": 80,
                    },
                },
            },
            clan,
        )

        self.assertEqual(Player.objects.filter(player_id=9923).count(), 1)
        canonical.refresh_from_db()
        self.assertEqual(canonical.name, "DedupedCrawler")
        self.assertEqual(canonical.clan, clan)
        self.assertEqual(Snapshot.objects.filter(player=canonical).count(), 1)
        self.assertEqual(PlayerAchievementStat.objects.filter(
            player=canonical).count(), 1)
        self.assertTrue(PlayerExplorerSummary.objects.filter(
            player=canonical).exists())

    @patch("warships.data.update_player_data")
    @patch("warships.data._fetch_clan_member_ids", return_value=[9924])
    def test_update_clan_members_collapses_duplicate_players_by_player_id(
        self,
        _mock_fetch_clan_member_ids,
        mock_update_player_data,
    ):
        clan = Clan.objects.create(
            clan_id=9924, name="DedupedMembersClan", tag="DMC")
        canonical = Player.objects.create(name="First", player_id=9924)
        duplicate = Player.objects.create(name="Second", player_id=9924)
        Snapshot.objects.create(
            player=duplicate,
            date=timezone.now().date(),
            battles=12,
            wins=7,
        )

        update_clan_members(str(clan.clan_id))

        self.assertEqual(Player.objects.filter(player_id=9924).count(), 1)
        canonical.refresh_from_db()
        self.assertEqual(canonical.clan, clan)
        self.assertEqual(Snapshot.objects.filter(player=canonical).count(), 1)
        mock_update_player_data.assert_called_with(canonical)

    @patch("warships.data.update_achievements_data", return_value=[])
    def test_clan_crawl_save_player_assigns_assassin_to_top_end_players(self, _mock_update_achievements_data):
        clan = Clan.objects.create(clan_id=9916, name="AssassinClan", tag="AC")

        save_player(
            {
                "account_id": 9916,
                "nickname": "AssassinCrawler",
                "created_at": int((timezone.now() - timedelta(days=700)).timestamp()),
                "last_battle_time": int((timezone.now() - timedelta(days=1)).timestamp()),
                "hidden_profile": False,
                "statistics": {
                    "battles": 5000,
                    "pvp": {
                        "battles": 4200,
                        "wins": 2604,
                        "losses": 1596,
                        "survived_battles": 1500,
                    },
                },
            },
            clan,
        )

        player = Player.objects.get(player_id=9916)
        self.assertEqual(player.pvp_ratio, 62.0)
        self.assertEqual(player.verdict, "Assassin")

    @patch("warships.data.update_achievements_data", return_value=[])
    def test_clan_crawl_save_player_assigns_sealord_to_absolute_top_end_players(self, _mock_update_achievements_data):
        clan = Clan.objects.create(clan_id=9918, name="SealordClan", tag="SC")

        save_player(
            {
                "account_id": 9918,
                "nickname": "SealordCrawler",
                "created_at": int((timezone.now() - timedelta(days=700)).timestamp()),
                "last_battle_time": int((timezone.now() - timedelta(days=1)).timestamp()),
                "hidden_profile": False,
                "statistics": {
                    "battles": 5000,
                    "pvp": {
                        "battles": 4200,
                        "wins": 2772,
                        "losses": 1428,
                        "survived_battles": 1300,
                    },
                },
            },
            clan,
        )

        player = Player.objects.get(player_id=9918)
        self.assertEqual(player.pvp_ratio, 66.0)
        self.assertEqual(player.verdict, "Sealord")

    @patch("warships.data.update_achievements_data", return_value=[])
    def test_clan_crawl_save_player_assigns_leroy_jenkins_to_bottom_shelf_players(self, _mock_update_achievements_data):
        clan = Clan.objects.create(
            clan_id=9917, name="HotPotatoClan", tag="HP")

        save_player(
            {
                "account_id": 9917,
                "nickname": "HotPotatoCrawler",
                "created_at": int((timezone.now() - timedelta(days=300)).timestamp()),
                "last_battle_time": int((timezone.now() - timedelta(days=1)).timestamp()),
                "hidden_profile": False,
                "statistics": {
                    "battles": 700,
                    "pvp": {
                        "battles": 600,
                        "wins": 240,
                        "losses": 360,
                        "survived_battles": 120,
                    },
                },
            },
            clan,
        )

        player = Player.objects.get(player_id=9917)
        self.assertEqual(player.pvp_ratio, 40.0)
        self.assertEqual(player.verdict, "Leroy Jenkins")

    @patch("warships.data._fetch_clan_data")
    def test_update_clan_data_does_not_blank_existing_clan_on_empty_upstream_response(self, mock_fetch_clan_data):
        clan = Clan.objects.create(
            clan_id=555,
            name="ExistingClan",
            tag="EC",
            members_count=33,
        )
        mock_fetch_clan_data.return_value = {}

        update_clan_data(clan.clan_id)

        clan.refresh_from_db()
        self.assertEqual(clan.name, "ExistingClan")
        self.assertEqual(clan.tag, "EC")
        self.assertEqual(clan.members_count, 33)

    @patch("warships.data._fetch_clan_member_ids", return_value=[])
    @patch("warships.data._fetch_clan_data")
    def test_update_clan_data_invalidates_landing_clans_cache(
        self,
        mock_fetch_clan_data,
        _mock_fetch_member_ids,
    ):
        clan = Clan.objects.create(
            clan_id=556,
            name="CacheClan",
            tag="CC",
            members_count=12,
        )
        cache.set(LANDING_CLANS_CACHE_KEY, [{"name": "stale"}], 60)
        cache.set(LANDING_RECENT_CLANS_CACHE_KEY,
                  [{"name": "recent-stale"}], 60)
        mock_fetch_clan_data.return_value = {
            "name": "CacheClan",
            "tag": "CC",
            "members_count": 12,
            "description": "updated",
            "leader_id": 1,
            "leader_name": "Boss",
        }

        update_clan_data(clan.clan_id)

        self.assertEqual(cache.get(LANDING_CLANS_CACHE_KEY),
                         [{"name": "stale"}])
        self.assertEqual(cache.get(LANDING_RECENT_CLANS_CACHE_KEY), [
                         {"name": "recent-stale"}])
        self.assertIsNotNone(cache.get(LANDING_CLANS_DIRTY_KEY))
        self.assertIsNotNone(cache.get(LANDING_RECENT_CLANS_DIRTY_KEY))
