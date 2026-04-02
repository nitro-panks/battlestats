from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from warships.clan_crawl import crawl_clan_members
from warships.models import Clan


class ClanCrawlAggregateTests(TestCase):
    @patch("warships.clan_crawl.fetch_players_bulk")
    @patch("warships.clan_crawl.fetch_member_ids")
    @patch("warships.clan_crawl.fetch_clan_info")
    def test_crawl_clan_members_populates_cached_aggregates_for_realm(
        self,
        mock_fetch_clan_info,
        mock_fetch_member_ids,
        mock_fetch_players_bulk,
    ):
        recent_battle_time = int(timezone.now().timestamp())

        mock_fetch_clan_info.return_value = {
            "clan_id": 5001,
            "name": "EU Clan",
            "tag": "EUC",
            "members_count": 2,
            "description": "",
            "leader_id": 9001,
            "leader_name": "CaptainEU",
        }
        mock_fetch_member_ids.return_value = [9001, 9002]
        mock_fetch_players_bulk.return_value = {
            "9001": {
                "account_id": 9001,
                "nickname": "CaptainEU",
                "created_at": 1700000000,
                "last_battle_time": recent_battle_time,
                "hidden_profile": False,
                "statistics": {
                    "battles": 200,
                    "pvp": {
                        "battles": 100,
                        "wins": 60,
                        "losses": 40,
                        "frags": 50,
                        "survived_battles": 25,
                    },
                },
            },
            "9002": {
                "account_id": 9002,
                "nickname": "MateEU",
                "created_at": 1700000000,
                "last_battle_time": recent_battle_time,
                "hidden_profile": False,
                "statistics": {
                    "battles": 300,
                    "pvp": {
                        "battles": 200,
                        "wins": 90,
                        "losses": 110,
                        "frags": 70,
                        "survived_battles": 40,
                    },
                },
            },
        }

        result = crawl_clan_members(
            [{"clan_id": 5001}],
            realm='eu',
            core_only=True,
            request_delay=0,
        )

        clan = Clan.objects.get(clan_id=5001, realm='eu')
        self.assertEqual(result["clans_processed"], 1)
        self.assertEqual(result["players_saved"], 2)
        self.assertEqual(clan.cached_total_battles, 300)
        self.assertEqual(clan.cached_total_wins, 150)
        self.assertEqual(clan.cached_active_member_count, 2)
        self.assertEqual(clan.cached_clan_wr, 50.0)
