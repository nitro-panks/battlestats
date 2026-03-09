from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.core.cache import cache
from django.utils import timezone

from warships.clan_crawl import save_player
from warships.data import update_snapshot_data, fetch_activity_data, fetch_randoms_data, update_player_data, update_clan_data, update_tiers_data, update_type_data, update_randoms_data, _build_top_ranked_ship_names_by_season, update_ranked_data, refresh_player_explorer_summary, fetch_player_explorer_rows
from warships.models import Player, Snapshot, Clan, PlayerExplorerSummary


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


class ActivityDataRefreshTests(TestCase):
    @patch("warships.data.update_activity_data")
    @patch("warships.data.update_snapshot_data")
    def test_fetch_activity_data_refreshes_cumulative_spike_cache(
        self,
        mock_update_snapshot_data,
        mock_update_activity_data,
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

        fetch_activity_data(player.player_id)

        mock_update_snapshot_data.assert_called_once_with(player.player_id)
        mock_update_activity_data.assert_called_once_with(player.player_id)


class RandomsDataRefreshTests(TestCase):
    @patch("warships.data.update_randoms_data")
    @patch("warships.data.update_battle_data")
    def test_fetch_randoms_data_refreshes_stale_cache_synchronously(
        self,
        mock_update_battle_data,
        mock_update_randoms_data,
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

        def write_fresh_rows(player_id):
            p = Player.objects.get(player_id=player_id)
            p.randoms_json = [
                {
                    "ship_name": "Fresh Ship",
                    "ship_chart_name": "Fresh Ship",
                    "ship_type": "Cruiser",
                    "ship_tier": 10,
                    "pvp_battles": 99,
                    "win_ratio": 0.6,
                    "wins": 59,
                }
            ]
            p.randoms_updated_at = timezone.now()
            p.save(update_fields=["randoms_json", "randoms_updated_at"])

        mock_update_randoms_data.side_effect = write_fresh_rows

        rows = fetch_randoms_data(player.player_id)

        self.assertEqual(rows[0]["ship_name"], "Fresh Ship")
        self.assertEqual(rows[0]["ship_chart_name"], "Fresh Ship")
        mock_update_battle_data.assert_called_once_with(player.player_id)
        mock_update_randoms_data.assert_called_once_with(player.player_id)

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


class RankedDataRefreshTests(TestCase):
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


class PlayerDataHardeningTests(TestCase):
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
        self.assertIsNone(player.battles_json)
        self.assertIsNone(player.randoms_json)
        self.assertIsNone(player.ranked_json)

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

    @patch("warships.data._fetch_clan_membership_for_player")
    @patch("warships.data._fetch_player_personal_data")
    def test_update_player_data_invalidates_landing_players_cache(
        self,
        mock_fetch_player_personal_data,
        mock_fetch_clan_membership,
    ):
        player = Player.objects.create(
            name="CachedCaptain",
            player_id=9191,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        cache.set("landing:players", [{"name": "stale"}], 60)
        mock_fetch_player_personal_data.return_value = {
            "account_id": 9191,
            "nickname": "CachedCaptain",
            "hidden_profile": False,
            "statistics": {"battles": 10, "pvp": {"battles": 8, "wins": 4, "losses": 4}},
        }
        mock_fetch_clan_membership.return_value = {}

        update_player_data(player, force_refresh=True)

        self.assertIsNone(cache.get("landing:players"))


class PlayerExplorerSummaryTests(TestCase):
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
        self.assertEqual(summary.ships_played_total, 2)
        self.assertEqual(summary.ship_type_spread, 2)
        self.assertEqual(summary.tier_spread, 2)
        self.assertEqual(summary.ranked_seasons_participated, 1)
        self.assertEqual(summary.latest_ranked_battles, 12)
        self.assertEqual(summary.highest_ranked_league_recent, "Silver")

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

        self.assertEqual(summary.kill_ratio, 0.83)
        self.assertEqual(summary.ships_played_total, 2)

    def test_fetch_player_explorer_rows_refreshes_stale_battle_metrics(self):
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

        rows = fetch_player_explorer_rows(query="ExplorerStaleSummary", hidden="visible")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kill_ratio"], 0.83)
        self.assertEqual(rows[0]["ships_played_total"], 2)

        summary = PlayerExplorerSummary.objects.get(player=player)
        self.assertEqual(summary.kill_ratio, 0.83)
        self.assertEqual(summary.ships_played_total, 2)

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
        self.assertEqual(summary.player, player)
        self.assertEqual(summary.battles_last_29_days, 0)
        self.assertIsNone(summary.ships_played_total)
        self.assertIsNone(summary.kill_ratio)

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
        cache.set("landing:clans", [{"name": "stale"}], 60)
        mock_fetch_clan_data.return_value = {
            "name": "CacheClan",
            "tag": "CC",
            "members_count": 12,
            "description": "updated",
            "leader_id": 1,
            "leader_name": "Boss",
        }

        update_clan_data(clan.clan_id)

        self.assertIsNone(cache.get("landing:clans"))
