from unittest.mock import patch
from datetime import datetime, timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from warships.models import Player, Clan, PlayerExplorerSummary
from warships.views import PUBLIC_API_THROTTLES, landing_players


class PlayerViewSetTests(TestCase):
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_lookup_updates_last_lookup_timestamp(
        self,
        _mock_update_player_task,
        _mock_update_clan_task,
        _mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=950,
            name="LookupClan",
            members_count=1,
            last_fetch=now,
        )
        player = Player.objects.create(
            name="LookupPlayer",
            player_id=9050,
            clan=clan,
            last_fetch=now,
            last_lookup=None,
        )

        response = self.client.get("/api/player/LookupPlayer/")

        self.assertEqual(response.status_code, 200)
        player.refresh_from_db()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.views.update_battle_data")
    def test_player_lookup_hydrates_missing_battle_rows_for_kill_ratio(
        self,
        mock_update_battle_data,
        _mock_update_player_task,
        _mock_update_clan_task,
        _mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=951,
            name="BattleHydrationClan",
            members_count=1,
            last_fetch=now,
        )
        player = Player.objects.create(
            name="BattleHydrationPlayer",
            player_id=9051,
            clan=clan,
            last_fetch=now,
            pvp_battles=30,
            pvp_ratio=53.0,
            pvp_survival_rate=40.0,
            battles_json=None,
        )

        def write_battle_rows(player_id):
            hydrated = Player.objects.get(player_id=player_id)
            hydrated.battles_json = [
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 10, "kdr": 1.5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "kdr": 0.5},
            ]
            hydrated.save(update_fields=["battles_json"])

        mock_update_battle_data.side_effect = write_battle_rows

        response = self.client.get("/api/player/BattleHydrationPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kill_ratio"], 0.78)
        mock_update_battle_data.assert_called_once_with(player.player_id)
        player.refresh_from_db()
        self.assertIsNotNone(player.last_lookup)
        self.assertLess(
            abs((timezone.now() - player.last_lookup).total_seconds()), 5)

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.data.update_player_data")
    @patch("warships.views._fetch_player_id_by_name")
    def test_player_lookup_falls_back_to_remote_then_persists(
        self,
        mock_fetch_player_id,
        mock_update_player_data,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        mock_fetch_player_id.return_value = "777"

        def hydrate_player(player, force_refresh=False):
            player.name = "RemotePlayer"
            player.pvp_battles = 10
            player.pvp_wins = 5
            player.save()

        mock_update_player_data.side_effect = hydrate_player

        response = self.client.get("/api/player/RemotePlayer/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["player_id"], 777)
        self.assertEqual(payload["name"], "RemotePlayer")
        self.assertTrue(Player.objects.filter(
            player_id=777, name="RemotePlayer").exists())
        mock_update_player_task.assert_called_once_with(
            player_id=777,
            force_refresh=True,
        )
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.data.update_clan_data")
    @patch("warships.data.update_player_data")
    def test_player_lookup_without_clan_hydrates_clan_details_before_serializing(
        self,
        mock_update_player_data,
        mock_update_clan_data,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        player = Player.objects.create(
            name="NoClanYet",
            player_id=7001,
            clan=None,
            last_fetch=timezone.now(),
        )

        def hydrate_player(player, force_refresh=False):
            clan, _ = Clan.objects.get_or_create(clan_id=70010)
            player.clan = clan
            player.save(update_fields=["clan"])

        def hydrate_clan(clan_id):
            clan = Clan.objects.get(clan_id=clan_id)
            clan.name = "Hydrated Clan"
            clan.tag = "HC"
            clan.members_count = 5
            clan.last_fetch = timezone.now()
            clan.save(update_fields=["name", "tag",
                      "members_count", "last_fetch"])

        mock_update_player_data.side_effect = hydrate_player
        mock_update_clan_data.side_effect = hydrate_clan

        response = self.client.get("/api/player/NoClanYet/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["clan_id"], 70010)
        self.assertEqual(payload["clan_name"], "Hydrated Clan")
        self.assertEqual(payload["clan_tag"], "HC")
        mock_update_player_data.assert_called_once_with(
            player=player, force_refresh=True)
        mock_update_clan_data.assert_called_once_with(70010)
        mock_update_player_task.assert_not_called()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_called_once_with(clan_id=70010)

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_lookup_does_not_enqueue_when_data_is_fresh(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=900,
            name="FreshClan",
            members_count=1,
            last_fetch=now,
        )
        Player.objects.create(
            name="FreshPlayer",
            player_id=9001,
            clan=clan,
            last_fetch=now,
        )

        response = self.client.get("/api/player/FreshPlayer/")

        self.assertEqual(response.status_code, 200)
        mock_update_player_task.assert_not_called()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_lookup_recomputes_missing_verdict_from_stored_stats(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=902,
            name="VerdictClan",
            members_count=1,
            last_fetch=now,
        )
        player = Player.objects.create(
            name="VerdictGapPlayer",
            player_id=9003,
            clan=clan,
            last_fetch=now,
            pvp_battles=1000,
            pvp_ratio=50.0,
            pvp_survival_rate=35.0,
            verdict=None,
        )

        response = self.client.get("/api/player/VerdictGapPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "Survivor")
        player.refresh_from_db()
        self.assertEqual(player.verdict, "Survivor")
        mock_update_player_task.assert_not_called()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_lookup_enqueues_when_data_is_stale(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        old = timezone.now() - timedelta(days=2)
        clan = Clan.objects.create(
            clan_id=901,
            name="StaleClan",
            members_count=2,
            last_fetch=old,
        )
        Player.objects.create(
            name="StalePlayer",
            player_id=9002,
            clan=clan,
            last_fetch=old,
        )

        response = self.client.get("/api/player/StalePlayer/")

        self.assertEqual(response.status_code, 200)
        mock_update_player_task.assert_called_once_with(player_id=9002)
        mock_update_clan_task.assert_called_once_with(clan_id=901)
        mock_update_clan_members_task.assert_called_once_with(clan_id=901)


class ClanMembersEndpointTests(TestCase):
    def test_clan_members_null_returns_empty_list(self):
        response = self.client.get("/api/fetch/clan_members/null/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_returns_data_when_members_exist(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
    ):
        clan = Clan.objects.create(
            clan_id=42, name="Test Clan", members_count=1)
        Player.objects.create(
            name="MemberOne",
            player_id=1,
            clan=clan,
            days_since_last_battle=6,
            last_battle_date=timezone.now().date() - timedelta(days=6),
        )

        response = self.client.get("/api/fetch/clan_members/42/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [
                         {
                             "name": "MemberOne",
                             "is_hidden": False,
                             "pvp_ratio": None,
                             "days_since_last_battle": 6,
                             "activity_bucket": "active_7d",
                         }])
        mock_update_clan_data.assert_not_called()
        mock_update_clan_members.assert_not_called()

    def test_clan_members_exposes_activity_buckets_for_histogram(self):
        clan = Clan.objects.create(
            clan_id=78, name="Activity Clan", members_count=5)
        today = timezone.now().date()
        Player.objects.create(
            name="VeryActive",
            player_id=7801,
            clan=clan,
            pvp_ratio=58.2,
            days_since_last_battle=2,
            last_battle_date=today - timedelta(days=2),
        )
        Player.objects.create(
            name="Warm",
            player_id=7802,
            clan=clan,
            pvp_ratio=55.0,
            days_since_last_battle=12,
            last_battle_date=today - timedelta(days=12),
        )
        Player.objects.create(
            name="Cooling",
            player_id=7803,
            clan=clan,
            pvp_ratio=53.1,
            days_since_last_battle=60,
            last_battle_date=today - timedelta(days=60),
        )
        Player.objects.create(
            name="Dormant",
            player_id=7804,
            clan=clan,
            pvp_ratio=50.4,
            days_since_last_battle=120,
            last_battle_date=today - timedelta(days=120),
        )
        Player.objects.create(
            name="GoneDark",
            player_id=7805,
            clan=clan,
            pvp_ratio=47.8,
            days_since_last_battle=280,
            last_battle_date=today - timedelta(days=280),
        )

        response = self.client.get("/api/fetch/clan_members/78/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {
                row["name"]: row["activity_bucket"]
                for row in response.json()
            },
            {
                "VeryActive": "active_7d",
                "Warm": "active_30d",
                "Cooling": "cooling_90d",
                "Dormant": "dormant_180d",
                "GoneDark": "inactive_180d_plus",
            },
        )

    def test_clan_members_orders_by_player_score_desc(self):
        clan = Clan.objects.create(
            clan_id=77, name="Score Clan", members_count=3)
        low = Player.objects.create(
            name="LowScoreMember",
            player_id=7701,
            clan=clan,
            pvp_ratio=52.0,
            last_battle_date=timezone.now().date() - timedelta(days=1),
        )
        high = Player.objects.create(
            name="HighScoreMember",
            player_id=7702,
            clan=clan,
            pvp_ratio=55.0,
            last_battle_date=timezone.now().date() - timedelta(days=3),
        )
        no_score = Player.objects.create(
            name="NoScoreMember",
            player_id=7703,
            clan=clan,
            pvp_ratio=57.0,
            last_battle_date=timezone.now().date(),
        )
        PlayerExplorerSummary.objects.create(player=low, player_score=3.4)
        PlayerExplorerSummary.objects.create(player=high, player_score=8.6)

        response = self.client.get("/api/fetch/clan_members/77/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()],
            ["HighScoreMember", "LowScoreMember", "NoScoreMember"],
        )


class ApiContractTests(TestCase):
    def test_player_name_suggestions_prioritize_prefix_matches_and_limit_results(self):
        today = timezone.now().date()
        Player.objects.create(name="CaptainAlpha",
                              player_id=5001, last_battle_date=today)
        Player.objects.create(name="AlphaCaptain",
                              player_id=5002, last_battle_date=today)
        Player.objects.create(name="CaptainBravo",
                              player_id=5003, last_battle_date=today)

        for index in range(10):
            Player.objects.create(
                name=f"CaptainExtra{index}",
                player_id=5100 + index,
                last_battle_date=today,
            )

        response = self.client.get("/api/landing/player-suggestions/?q=cap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 8)
        self.assertEqual(payload[0]["name"], "CaptainAlpha")
        self.assertEqual(payload[1]["name"], "CaptainBravo")
        self.assertNotEqual(payload[0]["name"], "AlphaCaptain")

    def test_landing_players_excludes_hidden_players(self):
        Player.objects.create(
            name="HiddenLandingPlayer",
            player_id=4242,
            is_hidden=True,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="VisibleLandingPlayer",
            player_id=4243,
            is_hidden=False,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/landing/players/")

        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()]
        self.assertIn("VisibleLandingPlayer", names)
        self.assertNotIn("HiddenLandingPlayer", names)

    def test_landing_activity_attrition_returns_monthly_cohorts(self):
        cache.clear()

        def shift_month_start(month_start, month_delta):
            absolute_month = (month_start.year * 12) + \
                month_start.month - 1 + month_delta
            return timezone.make_aware(datetime(absolute_month // 12, (absolute_month % 12) + 1, 15))

        now = timezone.now()
        current_month_start = now.date().replace(day=1)
        latest_complete_month = (
            current_month_start - timedelta(days=1)).replace(day=1)

        for month_delta in range(-11, 1):
            creation_date = shift_month_start(
                latest_complete_month, month_delta)
            month_key = creation_date.strftime('%Y-%m-01')

            if month_delta == 0:
                Player.objects.create(
                    name=f"AttritionActive-{month_delta}",
                    player_id=4500 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=9,
                    last_battle_date=now.date() - timedelta(days=9),
                )
                Player.objects.create(
                    name=f"AttritionCooling-{month_delta}",
                    player_id=4501 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=45,
                    last_battle_date=now.date() - timedelta(days=45),
                )
                Player.objects.create(
                    name=f"AttritionDormant-{month_delta}",
                    player_id=4502 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=140,
                    last_battle_date=now.date() - timedelta(days=140),
                )
            elif month_delta >= -5:
                Player.objects.create(
                    name=f"RecentActiveA-{month_delta}",
                    player_id=4600 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=8,
                    last_battle_date=now.date() - timedelta(days=8),
                )
                Player.objects.create(
                    name=f"RecentActiveB-{month_delta}",
                    player_id=4601 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=15,
                    last_battle_date=now.date() - timedelta(days=15),
                )
                Player.objects.create(
                    name=f"RecentCooling-{month_delta}",
                    player_id=4602 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=50,
                    last_battle_date=now.date() - timedelta(days=50),
                )
            else:
                Player.objects.create(
                    name=f"PriorCooling-{month_delta}",
                    player_id=4700 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=55,
                    last_battle_date=now.date() - timedelta(days=55),
                )
                Player.objects.create(
                    name=f"PriorDormantA-{month_delta}",
                    player_id=4701 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=130,
                    last_battle_date=now.date() - timedelta(days=130),
                )
                Player.objects.create(
                    name=f"PriorDormantB-{month_delta}",
                    player_id=4702 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=190,
                    last_battle_date=now.date() - timedelta(days=190),
                )

        Player.objects.create(
            name="AttritionHidden",
            player_id=4991,
            is_hidden=True,
            creation_date=shift_month_start(latest_complete_month, 0),
            days_since_last_battle=5,
            last_battle_date=now.date() - timedelta(days=5),
        )
        Player.objects.create(
            name="AttritionNoCreation",
            player_id=4992,
            is_hidden=False,
            creation_date=None,
            days_since_last_battle=5,
            last_battle_date=now.date() - timedelta(days=5),
        )
        Player.objects.create(
            name="CurrentMonthExcluded",
            player_id=4993,
            is_hidden=False,
            creation_date=timezone.make_aware(
                datetime(current_month_start.year, current_month_start.month, 15)),
            days_since_last_battle=5,
            last_battle_date=now.date() - timedelta(days=5),
        )

        response = self.client.get("/api/landing/activity-attrition/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "landing_activity_attrition")
        self.assertEqual(payload["tracked_population"], 36)
        self.assertEqual(len(payload["months"]), 18)
        self.assertEqual(payload["summary"]["population_signal"], "growing")
        self.assertEqual(payload["summary"]["latest_month"],
                         latest_complete_month.isoformat())

        latest_row = next(
            row for row in payload["months"]
            if row["month"] == latest_complete_month.isoformat()
        )
        self.assertEqual(latest_row["total_players"], 3)
        self.assertEqual(latest_row["active_players"], 1)
        self.assertEqual(latest_row["cooling_players"], 1)
        self.assertEqual(latest_row["dormant_players"], 1)
        self.assertAlmostEqual(latest_row["active_share"], 33.3)

    def test_landing_players_orders_by_player_score_desc(self):
        cache.clear()
        today = timezone.now().date()
        high = Player.objects.create(
            name="LandingHighScore",
            player_id=4301,
            is_hidden=False,
            pvp_ratio=55.0,
            last_battle_date=today - timedelta(days=3),
        )
        low = Player.objects.create(
            name="LandingLowScore",
            player_id=4302,
            is_hidden=False,
            pvp_ratio=53.0,
            last_battle_date=today,
        )
        no_score = Player.objects.create(
            name="LandingNoScore",
            player_id=4303,
            is_hidden=False,
            pvp_ratio=57.0,
            last_battle_date=today - timedelta(days=1),
        )
        PlayerExplorerSummary.objects.create(player=high, player_score=9.1)
        PlayerExplorerSummary.objects.create(player=low, player_score=4.2)

        response = self.client.get("/api/landing/players/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:3]],
            ["LandingHighScore", "LandingLowScore", "LandingNoScore"],
        )

    def test_landing_recent_players_orders_recent_slice_by_player_score_desc(self):
        cache.clear()
        now = timezone.now()
        recent_high = Player.objects.create(
            name="RecentHighScore",
            player_id=4401,
            pvp_ratio=58.0,
            last_lookup=now - timedelta(minutes=30),
        )
        recent_low = Player.objects.create(
            name="RecentLowScore",
            player_id=4402,
            pvp_ratio=51.0,
            last_lookup=now - timedelta(minutes=5),
        )
        recent_none = Player.objects.create(
            name="RecentNoScore",
            player_id=4403,
            pvp_ratio=54.0,
            last_lookup=now - timedelta(minutes=1),
        )
        PlayerExplorerSummary.objects.create(
            player=recent_high, player_score=8.2)
        PlayerExplorerSummary.objects.create(
            player=recent_low, player_score=2.4)

        response = self.client.get("/api/landing/recent/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:3]],
            ["RecentHighScore", "RecentLowScore", "RecentNoScore"],
        )

    def test_player_summary_returns_derived_metrics(self):
        now = timezone.now()
        Player.objects.create(
            name="SummaryPlayer",
            player_id=8181,
            is_hidden=False,
            pvp_ratio=54.2,
            pvp_battles=1200,
            pvp_survival_rate=38.5,
            creation_date=now - timedelta(days=365),
            days_since_last_battle=3,
            last_battle_date=now.date() - timedelta(days=3),
            activity_json=[
                {"date": "2026-02-10", "battles": 1, "wins": 1},
                {"date": "2026-02-11", "battles": 2, "wins": 1},
                {"date": "2026-02-12", "battles": 7, "wins": 4},
                {"date": "2026-02-13", "battles": 9, "wins": 5},
            ],
            activity_updated_at=now,
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 15, "wins": 8},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 12, "wins": 7},
            ],
            ranked_json=[
                {"season_id": 5, "highest_league_name": "Silver",
                    "total_battles": 21, "top_ship_name": "Stalingrad"},
                {"season_id": 4, "highest_league_name": "Bronze",
                    "total_battles": 10, "top_ship_name": "Yamato"},
            ],
            ranked_updated_at=now,
        )

        response = self.client.get("/api/fetch/player_summary/8181/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "SummaryPlayer")
        self.assertEqual(payload["battles_last_29_days"], 19)
        self.assertEqual(payload["wins_last_29_days"], 11)
        self.assertEqual(payload["active_days_last_29_days"], 4)
        self.assertEqual(payload["ships_played_total"], 2)
        self.assertEqual(payload["ship_type_spread"], 2)
        self.assertEqual(payload["tier_spread"], 2)
        self.assertEqual(payload["ranked_seasons_participated"], 2)
        self.assertEqual(payload["latest_ranked_battles"], 21)
        self.assertEqual(payload["highest_ranked_league_recent"], "Silver")

    def test_player_detail_includes_kill_ratio(self):
        now = timezone.now()
        Player.objects.create(
            name="DetailKillRatioPlayer",
            player_id=8182,
            is_hidden=False,
            pvp_ratio=53.0,
            pvp_battles=30,
            pvp_survival_rate=40.0,
            creation_date=now - timedelta(days=180),
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 10, "kdr": 1.5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "kdr": 0.5},
            ],
        )

        response = self.client.get("/api/player/DetailKillRatioPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kill_ratio"], 0.78)
        self.assertEqual(response.json()["player_score"], 3.22)

    def test_player_detail_backfills_missing_kill_ratio_from_stale_summary(self):
        now = timezone.now()
        player = Player.objects.create(
            name="DetailKillRatioBackfill",
            player_id=8183,
            is_hidden=False,
            pvp_ratio=53.0,
            pvp_battles=30,
            pvp_survival_rate=40.0,
            creation_date=now - timedelta(days=180),
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 10, "kdr": 1.5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "kdr": 0.5},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            kill_ratio=None,
            ships_played_total=0,
        )

        response = self.client.get("/api/player/DetailKillRatioBackfill/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kill_ratio"], 0.78)
        self.assertEqual(response.json()["player_score"], 3.22)

        player.refresh_from_db()
        self.assertEqual(player.explorer_summary.kill_ratio, 0.78)
        self.assertEqual(player.explorer_summary.player_score, 3.22)

    def test_player_distribution_returns_survival_payload(self):
        Player.objects.create(
            name="DistributionOne",
            player_id=8801,
            is_hidden=False,
            pvp_battles=1200,
            pvp_ratio=54.2,
            pvp_survival_rate=38.5,
        )
        Player.objects.create(
            name="DistributionTwo",
            player_id=8802,
            is_hidden=False,
            pvp_battles=2400,
            pvp_ratio=57.8,
            pvp_survival_rate=44.1,
        )
        Player.objects.create(
            name="DistributionHidden",
            player_id=8803,
            is_hidden=True,
            pvp_battles=2200,
            pvp_ratio=59.0,
            pvp_survival_rate=50.0,
        )

        response = self.client.get(
            "/api/fetch/player_distribution/survival_rate/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "survival_rate")
        self.assertEqual(payload["label"], "Survival Rate")
        self.assertEqual(payload["scale"], "linear")
        self.assertEqual(payload["value_format"], "percent")
        self.assertEqual(payload["tracked_population"], 2)
        self.assertTrue(any(row["count"] > 0 for row in payload["bins"]))

    def test_player_distribution_returns_battles_payload_with_log_scale(self):
        Player.objects.create(
            name="BattlesA",
            player_id=8811,
            is_hidden=False,
            pvp_battles=150,
            pvp_ratio=48.0,
            pvp_survival_rate=30.0,
        )
        Player.objects.create(
            name="BattlesB",
            player_id=8812,
            is_hidden=False,
            pvp_battles=9800,
            pvp_ratio=62.0,
            pvp_survival_rate=46.0,
        )
        Player.objects.create(
            name="BattlesTooSmall",
            player_id=8813,
            is_hidden=False,
            pvp_battles=90,
            pvp_ratio=50.0,
            pvp_survival_rate=35.0,
        )

        response = self.client.get(
            "/api/fetch/player_distribution/battles_played/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "battles_played")
        self.assertEqual(payload["scale"], "log")
        self.assertEqual(payload["value_format"], "integer")
        self.assertEqual(payload["tracked_population"], 2)
        self.assertTrue(
            any(row["bin_min"] == 100 and row["count"] == 1 for row in payload["bins"]))
        self.assertTrue(
            any(row["bin_min"] == 6400 and row["count"] == 1 for row in payload["bins"]))

    def test_player_distribution_rejects_unknown_metric(self):
        response = self.client.get("/api/fetch/player_distribution/not-real/")

        self.assertEqual(response.status_code, 404)

    def test_player_correlation_distribution_returns_wr_survival_payload(self):
        Player.objects.create(
            name="CorrelationOne",
            player_id=8821,
            is_hidden=False,
            pvp_battles=1000,
            pvp_ratio=52.0,
            pvp_survival_rate=34.0,
        )
        Player.objects.create(
            name="CorrelationTwo",
            player_id=8822,
            is_hidden=False,
            pvp_battles=2200,
            pvp_ratio=58.0,
            pvp_survival_rate=42.0,
        )
        Player.objects.create(
            name="CorrelationHidden",
            player_id=8823,
            is_hidden=True,
            pvp_battles=2800,
            pvp_ratio=61.0,
            pvp_survival_rate=48.0,
        )

        response = self.client.get(
            "/api/fetch/player_correlation/win_rate_survival/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "win_rate_survival")
        self.assertEqual(payload["label"], "Win Rate vs Survival")
        self.assertEqual(payload["x_label"], "Win Rate")
        self.assertEqual(payload["y_label"], "Survival Rate")
        self.assertEqual(payload["tracked_population"], 2)
        self.assertTrue(payload["correlation"]
                        is None or payload["correlation"] > 0)
        self.assertTrue(any(tile["count"] > 0 for tile in payload["tiles"]))
        self.assertTrue(any(point["count"] > 0 for point in payload["trend"]))

    def test_player_correlation_distribution_returns_tier_type_payload(self):
        cache.clear()

        Player.objects.create(
            name="TierTypeOne",
            player_id=8831,
            is_hidden=False,
            pvp_battles=1400,
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 40, "wins": 24},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "wins": 10},
                {"ship_name": "Ship C", "ship_type": "Battleship",
                    "ship_tier": 8, "pvp_battles": 10, "wins": 5},
            ],
        )
        Player.objects.create(
            name="TierTypeTwo",
            player_id=8832,
            is_hidden=False,
            pvp_battles=2100,
            battles_json=[
                {"ship_name": "Ship D", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 15, "wins": 8},
                {"ship_name": "Ship E", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 30, "wins": 18},
                {"ship_name": "Ship F", "ship_type": "Battleship",
                    "ship_tier": 9, "pvp_battles": 25, "wins": 14},
            ],
        )
        Player.objects.create(
            name="TierTypeHidden",
            player_id=8833,
            is_hidden=True,
            pvp_battles=2500,
            battles_json=[
                {"ship_name": "Ship G", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 100, "wins": 60},
            ],
        )

        response = self.client.get(
            "/api/fetch/player_correlation/tier_type/8831/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "tier_type")
        self.assertEqual(payload["label"], "Tier vs Ship Type")
        self.assertEqual(payload["x_label"], "Ship Type")
        self.assertEqual(payload["y_label"], "Tier")
        self.assertEqual(payload["tracked_population"], 2)
        self.assertEqual(payload["player_cells"][0]["ship_type"], "Destroyer")
        self.assertEqual(payload["player_cells"][0]["ship_tier"], 10)
        self.assertEqual(payload["player_cells"][0]["pvp_battles"], 40)
        self.assertAlmostEqual(payload["player_cells"][0]["win_ratio"], 0.6)
        self.assertTrue(any(
            tile["ship_type"] == "Destroyer"
            and tile["ship_tier"] == 10
            and tile["count"] == 55
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(
            point["ship_type"] == "Battleship"
            and point["count"] == 35
            and point["avg_tier"] > 8.7
            for point in payload["trend"]
        ))

    def test_player_correlation_distribution_returns_404_for_missing_tier_type_player(self):
        response = self.client.get(
            "/api/fetch/player_correlation/tier_type/999999/")

        self.assertEqual(response.status_code, 404)

    def test_player_correlation_distribution_rejects_unknown_metric(self):
        response = self.client.get("/api/fetch/player_correlation/not-real/")

        self.assertEqual(response.status_code, 404)

    def test_players_explorer_sorts_by_recent_battles_desc_and_filters_ranked(self):
        now = timezone.now()
        Player.objects.create(
            name="ExplorerAlpha",
            player_id=9101,
            is_hidden=False,
            pvp_ratio=55.0,
            pvp_battles=500,
            creation_date=now - timedelta(days=100),
            days_since_last_battle=2,
            activity_json=[
                {"date": "2026-02-10", "battles": 2, "wins": 1},
                {"date": "2026-02-11", "battles": 3, "wins": 2},
            ],
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 10, "wins": 5},
            ],
            ranked_json=[
                {"season_id": 8, "highest_league_name": "Gold", "total_battles": 11},
            ],
        )
        Player.objects.create(
            name="ExplorerBravo",
            player_id=9102,
            is_hidden=False,
            pvp_ratio=50.0,
            pvp_battles=800,
            creation_date=now - timedelta(days=200),
            days_since_last_battle=4,
            activity_json=[
                {"date": "2026-02-10", "battles": 1, "wins": 1},
            ],
            battles_json=[
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 25, "wins": 13},
            ],
            ranked_json=[],
        )

        response = self.client.get(
            "/api/players/explorer/?sort=battles_last_29_days&direction=desc&ranked=yes")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["name"], "ExplorerAlpha")
        self.assertEqual(payload["results"][0]["battles_last_29_days"], 5)

    def test_players_explorer_backfills_missing_denormalized_summary(self):
        now = timezone.now()
        player = Player.objects.create(
            name="ExplorerBackfill",
            player_id=9103,
            is_hidden=False,
            pvp_ratio=51.2,
            pvp_battles=450,
            creation_date=now - timedelta(days=80),
            days_since_last_battle=5,
            activity_json=[
                {"date": "2026-02-10", "battles": 4, "wins": 2},
                {"date": "2026-02-11", "battles": 1, "wins": 1},
            ],
            battles_json=[
                {"ship_name": "Ship C", "ship_type": "Battleship",
                    "ship_tier": 9, "pvp_battles": 11, "wins": 6},
            ],
            ranked_json=[],
        )

        response = self.client.get("/api/players/explorer/?q=ExplorerBackfill")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["battles_last_29_days"], 5)
        self.assertTrue(PlayerExplorerSummary.objects.filter(
            player=player).exists())

    def test_players_explorer_sorts_by_kill_ratio_desc(self):
        now = timezone.now()
        Player.objects.create(
            name="ExplorerKRAlpha",
            player_id=9104,
            is_hidden=False,
            pvp_ratio=52.0,
            pvp_battles=30,
            creation_date=now - timedelta(days=100),
            days_since_last_battle=1,
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 10, "kdr": 1.5},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "kdr": 0.5},
            ],
            ranked_json=[],
        )
        Player.objects.create(
            name="ExplorerKRBravo",
            player_id=9105,
            is_hidden=False,
            pvp_ratio=54.0,
            pvp_battles=20,
            creation_date=now - timedelta(days=90),
            days_since_last_battle=2,
            battles_json=[
                {"ship_name": "Ship C", "ship_type": "Battleship",
                    "ship_tier": 9, "pvp_battles": 20, "kdr": 1.2},
            ],
            ranked_json=[],
        )

        response = self.client.get(
            "/api/players/explorer/?sort=kill_ratio&direction=desc&q=ExplorerKR")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["results"][0]["name"], "ExplorerKRBravo")
        self.assertEqual(payload["results"][0]["kill_ratio"], 1.01)
        self.assertEqual(payload["results"][1]["name"], "ExplorerKRAlpha")
        self.assertEqual(payload["results"][1]["kill_ratio"], 0.78)

    def test_players_explorer_sorts_by_player_score_desc(self):
        now = timezone.now()
        Player.objects.create(
            name="ExplorerScoreAlpha",
            player_id=9106,
            is_hidden=False,
            total_battles=4000,
            pvp_ratio=54.0,
            pvp_battles=1800,
            pvp_survival_rate=42.0,
            creation_date=now - timedelta(days=300),
            days_since_last_battle=2,
            activity_json=[
                {"date": "2026-03-08", "battles": 6, "wins": 4},
                {"date": "2026-03-09", "battles": 4, "wins": 3},
            ],
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 20, "kdr": 1.4},
            ],
            ranked_json=[],
        )
        Player.objects.create(
            name="ExplorerScoreBravo",
            player_id=9107,
            is_hidden=False,
            total_battles=2200,
            pvp_ratio=52.0,
            pvp_battles=1600,
            pvp_survival_rate=34.0,
            creation_date=now - timedelta(days=260),
            days_since_last_battle=20,
            activity_json=[
                {"date": "2026-02-10", "battles": 1, "wins": 1},
            ],
            battles_json=[
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 18, "kdr": 0.9},
            ],
            ranked_json=[],
        )

        response = self.client.get(
            "/api/players/explorer/?sort=player_score&direction=desc&q=ExplorerScore")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["results"][0]["name"], "ExplorerScoreAlpha")
        self.assertGreater(
            payload["results"][0]["player_score"], payload["results"][1]["player_score"])
        self.assertEqual(payload["results"][1]["name"], "ExplorerScoreBravo")

    @patch("warships.views.fetch_clan_battle_seasons")
    def test_clan_battle_seasons_returns_serialized_rows(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "season_id": 50,
                "season_name": "Valhalla",
                "season_label": "S50",
                "start_date": "2026-01-10",
                "end_date": "2026-02-14",
                "ship_tier_min": 10,
                "ship_tier_max": 10,
                "participants": 7,
                "roster_battles": 128,
                "roster_wins": 71,
                "roster_losses": 57,
                "roster_win_rate": 55.5,
            }
        ]

        response = self.client.get("/api/fetch/clan_battle_seasons/42/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["season_id"], 50)
        self.assertEqual(response.json()[0]["participants"], 7)
        self.assertEqual(response.json()[0]["roster_battles"], 128)


class ApiThrottleTests(TestCase):
    def test_landing_players_endpoint_declares_public_api_throttles(self):
        self.assertEqual(landing_players.cls.throttle_classes,
                         PUBLIC_API_THROTTLES)

    def test_landing_recent_players_orders_by_last_lookup_desc_and_limits_to_40(self):
        cache.delete('landing:recent_players')
        now = timezone.now()

        for index in range(45):
            Player.objects.create(
                name=f"RecentPlayer{index}",
                player_id=10000 + index,
                last_lookup=now - timedelta(minutes=index),
            )

        response = self.client.get("/api/landing/recent/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 40)
        self.assertEqual(payload[0]["name"], "RecentPlayer0")
        self.assertEqual(payload[39]["name"], "RecentPlayer39")

    def test_clan_data_rejects_invalid_filter_type(self):
        response = self.client.get("/api/fetch/clan_data/42:invalid")

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

    def test_randoms_data_for_missing_player_returns_empty_list(self):
        response = self.client.get("/api/fetch/randoms_data/999999999/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_activity_data_returns_activity_rows(self):
        now = timezone.now()
        Player.objects.create(
            name="ActivityMetaPlayer",
            player_id=654,
            activity_json=[
                {
                    "date": "2026-03-03",
                    "battles": 3,
                    "wins": 2,
                }
            ],
            activity_updated_at=now,
        )

        response = self.client.get("/api/fetch/activity_data/654/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0]["battles"], 3)
        self.assertEqual(payload[0]["wins"], 2)

    def test_randoms_data_includes_refresh_headers(self):
        now = timezone.now()
        Player.objects.create(
            name="HeaderPlayer",
            player_id=321,
            battles_json=[
                {
                    "ship_name": "Test Ship",
                    "ship_chart_name": "Test Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 25,
                    "win_ratio": 0.52,
                    "wins": 13,
                }
            ],
            randoms_json=[
                {
                    "ship_name": "Test Ship",
                    "ship_chart_name": "Test Ship",
                    "ship_type": "Destroyer",
                    "ship_tier": 8,
                    "pvp_battles": 25,
                    "win_ratio": 0.52,
                    "wins": 13,
                }
            ],
            battles_updated_at=now,
            randoms_updated_at=now,
        )

        response = self.client.get("/api/fetch/randoms_data/321/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["ship_chart_name"], "Test Ship")
        self.assertIn("X-Randoms-Updated-At", response)
        self.assertIn("X-Battles-Updated-At", response)

    @patch("warships.views.update_clan_battle_summary_task.delay")
    def test_clan_battle_seasons_flags_pending_refresh_on_empty_cache(self, mock_delay):
        Clan.objects.create(clan_id=42, name="PendingClan",
                            tag="PC", members_count=0)

        response = self.client.get("/api/fetch/clan_battle_seasons/42/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response["X-Clan-Battles-Pending"], "true")
        mock_delay.assert_called_once_with(clan_id="42")

    @patch("warships.views.fetch_ranked_data")
    def test_ranked_data_returns_serialized_rows_and_refresh_header(self, mock_fetch_ranked_data):
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
        self.assertIn("X-Ranked-Updated-At", response)
        payload = response.json()
        self.assertEqual(payload[0]["season_id"], 1025)
        self.assertEqual(payload[0]["highest_league_name"], "Gold")
        self.assertEqual(payload[0]["top_ship_name"], "Stalingrad")
        self.assertEqual(payload[0]["best_sprint"]["sprint_number"], 2)

    @patch("warships.views._fetch_player_id_by_name", return_value=None)
    def test_missing_player_lookup_uses_standard_drf_error_shape(self, _mock_lookup):
        response = self.client.get("/api/player/PlayerThatWillNeverExist/")

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertIn("detail", payload)
        self.assertEqual(payload.get("status_code"), 404)
