from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from warships.data import _build_top_ranked_ship_names_by_season, update_ranked_data
from warships.models import Player


class RankedTopShipDataTests(TestCase):
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


class RankedTopShipViewTests(TestCase):
    @patch("warships.views.fetch_ranked_data")
    def test_ranked_data_serializes_top_ship_name(self, mock_fetch_ranked_data):
        now = timezone.now()
        Player.objects.create(
            name="RankedHeaderPlayer",
            player_id=777,
            ranked_updated_at=now,
        )
        mock_fetch_ranked_data.return_value = [
            {
                "season_id": 1025,
                "season_name": "Season 25",
                "season_label": "S25",
                "start_date": "2026-01-10",
                "end_date": "2026-02-10",
                "highest_league": 1,
                "highest_league_name": "Gold",
                "total_battles": 34,
                "total_wins": 20,
                "win_rate": 0.5882,
                "top_ship_name": "Stalingrad",
                "best_sprint": {
                    "sprint_number": 2,
                    "league": 1,
                    "league_name": "Gold",
                    "rank": 4,
                    "best_rank": 4,
                    "battles": 12,
                    "wins": 8,
                },
                "sprints": [
                    {
                        "sprint_number": 1,
                        "league": 2,
                        "league_name": "Silver",
                        "rank": 6,
                        "best_rank": 6,
                        "battles": 10,
                        "wins": 6,
                    },
                    {
                        "sprint_number": 2,
                        "league": 1,
                        "league_name": "Gold",
                        "rank": 4,
                        "best_rank": 4,
                        "battles": 12,
                        "wins": 8,
                    },
                ],
            }
        ]

        response = self.client.get("/api/fetch/ranked_data/777/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["top_ship_name"], "Stalingrad")
        self.assertIn("X-Ranked-Updated-At", response)
