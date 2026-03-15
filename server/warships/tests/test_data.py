from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.core.cache import cache
from django.utils import timezone

from warships.clan_crawl import save_player
from warships.data import update_snapshot_data, fetch_activity_data, fetch_randoms_data, update_player_data, update_clan_data, update_tiers_data, update_type_data, update_randoms_data, update_battle_data, _build_top_ranked_ship_names_by_season, update_ranked_data, refresh_player_explorer_summary, fetch_player_explorer_rows, compute_player_verdict, _inactivity_score_cap, _calculate_tier_filtered_pvp_record, _calculate_ranked_record, get_highest_ranked_league_name, _aggregate_ranked_seasons, fetch_ranked_data, clan_ranked_hydration_needs_refresh, queue_clan_ranked_hydration
from warships.models import Player, Snapshot, Clan, PlayerExplorerSummary, Ship


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


class PlayerDataHardeningTests(TestCase):
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
        self.assertIsNone(player.battles_json)
        self.assertIsNone(player.randoms_json)
        self.assertIsNone(player.ranked_json)
        self.assertIsNone(player.efficiency_json)

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
        self.assertEqual(summary.player_score, 3.15)
        self.assertEqual(summary.ships_played_total, 2)
        self.assertEqual(summary.ship_type_spread, 2)
        self.assertEqual(summary.tier_spread, 2)
        self.assertEqual(summary.ranked_seasons_participated, 1)
        self.assertEqual(summary.latest_ranked_battles, 12)
        self.assertEqual(summary.highest_ranked_league_recent, "Silver")

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

        rows = fetch_player_explorer_rows(
            query="ExplorerStaleSummary", hidden="visible")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kill_ratio"], 0.78)
        self.assertEqual(rows[0]["ships_played_total"], 2)

        summary = PlayerExplorerSummary.objects.get(player=player)
        self.assertEqual(summary.kill_ratio, 0.78)
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

        with patch("warships.data._fetch_efficiency_badges_for_player", return_value=[]):
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

        with patch("warships.data._fetch_efficiency_badges_for_player", return_value=[{"ship_id": 222, "top_grade_class": 2}]):
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

    def test_clan_crawl_save_player_assigns_assassin_to_top_end_players(self):
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

    def test_clan_crawl_save_player_assigns_sealord_to_absolute_top_end_players(self):
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

    def test_clan_crawl_save_player_assigns_leroy_jenkins_to_bottom_shelf_players(self):
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
