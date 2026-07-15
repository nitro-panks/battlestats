from unittest.mock import patch
from datetime import datetime, timedelta
from kombu.exceptions import OperationalError as KombuOperationalError

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from warships.models import Player, Clan, PlayerExplorerSummary, RankedSeason, realm_cache_key, Ship, ShipTopPlayerSnapshot, EntityVisitDaily
from warships.views import PUBLIC_API_THROTTLES, _missing_player_lookup_cache_key


class PlayerViewSetTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_accepts_no_trailing_slash(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=952,
            name="NoSlashClan",
            members_count=1,
            last_fetch=now,
        )
        Player.objects.create(
            name="NoSlashPlayer",
            player_id=9052,
            clan=clan,
            last_fetch=now,
            pvp_battles=0,
            is_hidden=False,
        )

        response = self.client.get("/api/player/NoSlashPlayer")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "NoSlashPlayer")
        mock_update_player_task.assert_not_called()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay", side_effect=KombuOperationalError("broker-down"))
    def test_player_detail_returns_200_when_background_enqueue_fails(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=953,
            name="BrokerFailureClan",
            members_count=1,
            last_fetch=now,
        )
        Player.objects.create(
            name="BrokerFailurePlayer",
            player_id=9053,
            clan=clan,
            last_fetch=now - timedelta(hours=1),
            pvp_battles=0,
            is_hidden=False,
        )

        with self.assertLogs(level="WARNING") as captured_logs:
            response = self.client.get("/api/player/BrokerFailurePlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "BrokerFailurePlayer")
        self.assertTrue(
            any("Skipping async task enqueue" in line for line in captured_logs.output))
        mock_update_player_task.assert_called_once()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_exposes_ship_badges(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        from warships.data import SHIP_LEADERBOARD_WINDOW_DAYS
        now = timezone.now()
        player = Player.objects.create(
            name="BadgeHolder", player_id=9077, realm="na", last_fetch=now,
        )
        Ship.objects.create(ship_id=10, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        ShipTopPlayerSnapshot.objects.create(
            captured_on=now.date(), realm="na", ship_id=10,
            ship_name="Shimakaze", rank=1, player=player,
            win_rate=64.0, battles=312, damage=19_344_000, frags=400,
            survived=200,
        )

        response = self.client.get("/api/player/BadgeHolder/")

        self.assertEqual(response.status_code, 200)
        badges = response.json()["ship_badges"]
        self.assertEqual(len(badges), 1)
        self.assertEqual(badges[0]["ship_id"], 10)
        self.assertEqual(badges[0]["ship_name"], "Shimakaze")
        self.assertEqual(badges[0]["rank"], 1)
        self.assertEqual(badges[0]["win_rate"], 64.0)
        self.assertEqual(badges[0]["battles"], 312)
        self.assertEqual(badges[0]["avg_damage"], 62_000)        # 19_344_000/312
        self.assertEqual(badges[0]["window_days"], SHIP_LEADERBOARD_WINDOW_DAYS)

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_ship_badges_empty_when_none(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        Player.objects.create(
            name="NoBadges", player_id=9078, realm="na",
            last_fetch=timezone.now(),
        )

        response = self.client.get("/api/player/NoBadges/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ship_badges"], [])
        self.assertNotIn("ship_awards", response.json())

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_exposes_clan_leader_flag(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=954,
            name="Leader Flag Clan",
            members_count=1,
            last_fetch=now,
            leader_id=9054,
        )
        Player.objects.create(
            name="LeaderFlagPlayer",
            player_id=9054,
            clan=clan,
            last_fetch=now,
        )

        response = self.client.get("/api/player/LeaderFlagPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_clan_leader"])
        mock_update_player_task.assert_not_called()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_exposes_efficiency_and_randoms_rows(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=955,
            name="Badge View Clan",
            members_count=1,
            last_fetch=now,
        )
        Player.objects.create(
            name="BadgeViewPlayer",
            player_id=9055,
            clan=clan,
            last_fetch=now,
            pvp_battles=40,
            pvp_frags=30,
            pvp_survived_battles=16,
            pvp_deaths=24,
            actual_kdr=1.25,
            pvp_ratio=55.0,
            pvp_survival_rate=40.0,
            battles_json=[{
                "ship_name": "Badge Ship",
                "ship_type": "Cruiser",
                "ship_tier": 8,
                "pvp_battles": 40,
                "wins": 22,
                "kdr": 1.1,
            }],
            randoms_json=[{
                "ship_name": "Badge Ship",
                "ship_chart_name": "Badge Ship",
                "ship_type": "Cruiser",
                "ship_tier": 8,
                "pvp_battles": 40,
                "wins": 22,
                "win_ratio": 0.55,
            }],
            efficiency_json=[{
                "ship_id": 111,
                "top_grade_class": 1,
                "top_grade_label": "Expert",
                "badge_label": "Expert",
                "ship_name": "Badge Ship",
                "ship_chart_name": "Badge Ship",
                "ship_type": "Cruiser",
                "ship_tier": 8,
                "nation": "usa",
            }],
        )

        response = self.client.get("/api/player/BadgeViewPlayer/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["efficiency_json"][0]
                         ["top_grade_label"], "Expert")
        self.assertEqual(payload["efficiency_json"]
                         [0]["badge_label"], "Expert")
        self.assertEqual(payload["randoms_json"][0]["ship_name"], "Badge Ship")
        mock_update_player_task.assert_not_called()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_exposes_fresh_efficiency_rank_fields(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        player = Player.objects.create(
            name="EfficiencyRankPlayer",
            player_id=9056,
            last_fetch=now,
            is_hidden=False,
            pvp_battles=500,
            efficiency_updated_at=now - timedelta(hours=2),
            battles_updated_at=now - timedelta(hours=2),
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            efficiency_rank_percentile=0.81,
            efficiency_rank_tier='II',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=124,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )

        response = self.client.get("/api/player/EfficiencyRankPlayer/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["efficiency_rank_percentile"], 0.81)
        self.assertEqual(payload["efficiency_rank_tier"], "II")
        self.assertTrue(payload["has_efficiency_rank_icon"])
        self.assertEqual(payload["efficiency_rank_population_size"], 124)
        self.assertIsNotNone(payload["efficiency_rank_updated_at"])
        mock_update_player_task.assert_called_once()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_serves_stored_efficiency_rank_when_inputs_advanced(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        player = Player.objects.create(
            name="EfficiencyRankStalePlayer",
            player_id=9057,
            last_fetch=now,
            is_hidden=False,
            pvp_battles=500,
            efficiency_updated_at=now,
            battles_updated_at=now - timedelta(hours=2),
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            efficiency_rank_percentile=0.81,
            efficiency_rank_tier='II',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=124,
            efficiency_rank_updated_at=now - timedelta(hours=3),
        )

        response = self.client.get("/api/player/EfficiencyRankStalePlayer/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["efficiency_rank_percentile"], 0.81)
        self.assertEqual(payload["efficiency_rank_tier"], "II")
        self.assertTrue(payload["has_efficiency_rank_icon"])
        self.assertEqual(payload["efficiency_rank_population_size"], 124)
        self.assertIsNotNone(payload["efficiency_rank_updated_at"])
        mock_update_player_task.assert_called_once()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_exposes_shared_pve_player_flag(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        Player.objects.create(
            name="SharedPvePlayer",
            player_id=9060,
            last_fetch=now,
            is_hidden=False,
            total_battles=14344,
            pvp_battles=9549,
        )
        Player.objects.create(
            name="NotSharedPvePlayer",
            player_id=9061,
            last_fetch=now,
            is_hidden=False,
            total_battles=23851,
            pvp_battles=19629,
        )

        pve_response = self.client.get("/api/player/SharedPvePlayer/")
        non_pve_response = self.client.get("/api/player/NotSharedPvePlayer/")

        self.assertEqual(pve_response.status_code, 200)
        self.assertEqual(non_pve_response.status_code, 200)
        self.assertTrue(pve_response.json()["is_pve_player"])
        self.assertFalse(non_pve_response.json()["is_pve_player"])
        self.assertEqual(mock_update_player_task.call_count, 2)
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_exposes_streamer_flag(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        Player.objects.create(
            name="StreamerFlaggedPlayer",
            player_id=9062,
            last_fetch=now,
            is_hidden=False,
            is_streamer=True,
        )
        Player.objects.create(
            name="StreamerUnflaggedPlayer",
            player_id=9063,
            last_fetch=now,
            is_hidden=False,
            is_streamer=False,
        )

        flagged_response = self.client.get(
            "/api/player/StreamerFlaggedPlayer/")
        unflagged_response = self.client.get(
            "/api/player/StreamerUnflaggedPlayer/")

        self.assertEqual(flagged_response.status_code, 200)
        self.assertEqual(unflagged_response.status_code, 200)
        self.assertTrue(flagged_response.json()["is_streamer"])
        self.assertFalse(unflagged_response.json()["is_streamer"])
        self.assertEqual(mock_update_player_task.call_count, 2)
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.data._fetch_clan_battle_season_stats")
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_exposes_cached_clan_battle_header_fields(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
        mock_fetch_clan_battle_season_stats,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=9580, name="CBHeaderClan", members_count=1, last_fetch=now)
        player = Player.objects.create(
            name="ClanBattleHeaderPlayer",
            player_id=9058,
            clan=clan,
            last_fetch=now,
            is_hidden=False,
            pvp_battles=0,
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_seasons_participated=2,
            clan_battle_total_battles=43,
            clan_battle_overall_win_rate=55.8,
            clan_battle_summary_updated_at=now,
        )

        with patch('warships.tasks.queue_clan_battle_data_refresh'):
            response = self.client.get("/api/player/ClanBattleHeaderPlayer/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["clan_battle_header_eligible"])
        self.assertEqual(payload["clan_battle_header_total_battles"], 43)
        self.assertEqual(payload["clan_battle_header_seasons_played"], 2)
        self.assertEqual(payload["clan_battle_header_overall_win_rate"], 55.8)
        self.assertIsNotNone(payload["clan_battle_header_updated_at"])
        mock_fetch_clan_battle_season_stats.assert_not_called()

    @patch("warships.data._fetch_clan_battle_season_stats")
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_defaults_clan_battle_header_fields_when_cache_missing(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
        mock_fetch_clan_battle_season_stats,
    ):
        now = timezone.now()
        Player.objects.create(
            name="ClanBattleHeaderCacheMiss",
            player_id=9059,
            last_fetch=now,
            is_hidden=False,
            pvp_battles=500,
        )

        response = self.client.get("/api/player/ClanBattleHeaderCacheMiss/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["clan_battle_header_eligible"])
        self.assertEqual(payload["clan_battle_header_total_battles"], 0)
        self.assertEqual(payload["clan_battle_header_seasons_played"], 0)
        self.assertIsNone(payload["clan_battle_header_overall_win_rate"])
        self.assertIsNone(payload["clan_battle_header_updated_at"])
        mock_fetch_clan_battle_season_stats.assert_not_called()
        mock_update_player_task.assert_called_once()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.data._fetch_clan_battle_season_stats")
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_prefers_durable_clan_battle_header_fields_when_cache_missing(
        self,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
        mock_fetch_clan_battle_season_stats,
    ):
        now = timezone.now()
        player = Player.objects.create(
            name="ClanBattleHeaderDurable",
            player_id=9060,
            last_fetch=now,
            is_hidden=False,
            pvp_battles=500,
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_total_battles=43,
            clan_battle_seasons_participated=2,
            clan_battle_overall_win_rate=55.8,
            clan_battle_summary_updated_at=now - timedelta(minutes=10),
        )

        response = self.client.get("/api/player/ClanBattleHeaderDurable/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["clan_battle_header_eligible"])
        self.assertEqual(payload["clan_battle_header_total_battles"], 43)
        self.assertEqual(payload["clan_battle_header_seasons_played"], 2)
        self.assertEqual(payload["clan_battle_header_overall_win_rate"], 55.8)
        self.assertIsNotNone(payload["clan_battle_header_updated_at"])
        mock_fetch_clan_battle_season_stats.assert_not_called()
        mock_update_player_task.assert_called_once()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()


class LandingWarmupViewTests(TestCase):
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_lookup_updates_last_lookup_timestamp(
        self,
        _mock_update_player_task,
        _mock_update_clan_task,
        _mock_update_clan_members_task,
    ):
        request_started_at = timezone.now()
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
        self.assertIsNotNone(player.last_lookup)
        self.assertGreaterEqual(player.last_lookup, request_started_at)
        self.assertLessEqual(player.last_lookup, timezone.now())

    def test_player_cache_hit_still_updates_last_lookup(self):
        """Cache-hit path bumps last_lookup (analytics, hot-entity warmer)."""
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=954, name="CacheHitClan", members_count=1, last_fetch=now,
        )
        player = Player.objects.create(
            name="CacheHitPlayer", player_id=9054, clan=clan,
            last_fetch=now, last_lookup=None,
        )

        # Pre-populate the player detail cache so the retrieve path hits it
        from warships.data import _bulk_cache_key_player
        from warships.serializers import PlayerSerializer
        serialized = PlayerSerializer(player).data
        cache.set(_bulk_cache_key_player(player.player_id), serialized, 300)

        request_started_at = timezone.now()
        response = self.client.get("/api/player/CacheHitPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get('X-Player-Cache'), 'hit')
        # last_lookup should have been bumped even on cache hit
        player.refresh_from_db()
        self.assertIsNotNone(player.last_lookup)
        self.assertGreaterEqual(player.last_lookup, request_started_at)

    def test_clan_members_lookup_updates_clan_last_lookup_timestamp(self):
        cache.clear()
        request_started_at = timezone.now()
        clan = Clan.objects.create(
            clan_id=952,
            name="RecentClanLookup",
            tag="RCL",
            members_count=1,
            last_lookup=None,
        )
        Player.objects.create(
            name="RecentClanMember",
            player_id=9052,
            clan=clan,
            pvp_ratio=55.0,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/952/")

        self.assertEqual(response.status_code, 200)
        clan.refresh_from_db()
        self.assertIsNotNone(clan.last_lookup)
        self.assertGreaterEqual(clan.last_lookup, request_started_at)
        self.assertLessEqual(clan.last_lookup, timezone.now())

    def test_clan_members_accepts_no_trailing_slash(self):
        clan = Clan.objects.create(
            clan_id=953,
            name="NoSlashClanMembers",
            tag="NSCM",
            members_count=1,
        )
        Player.objects.create(
            name="NoSlashClanMate",
            player_id=9053,
            clan=clan,
            pvp_ratio=54.2,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/953")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["name"], "NoSlashClanMate")

    def test_clan_detail_accepts_no_trailing_slash(self):
        clan = Clan.objects.create(
            clan_id=1000067803,
            name="NoSlashClanDetail",
            tag="NSCD",
        )

        response = self.client.get("/api/clan/1000067803")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["clan_id"], clan.clan_id)

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.tasks.update_battle_data_task.delay")
    @patch("warships.data.update_battle_data")
    def test_player_lookup_keeps_missing_kill_ratio_without_sync_battle_hydration(
        self,
        mock_update_battle_data,
        mock_update_battle_data_task,
        _mock_update_player_task,
        _mock_update_clan_task,
        _mock_update_clan_members_task,
    ):
        request_started_at = timezone.now()
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
        self.assertIsNone(response.json()["kill_ratio"])
        mock_update_battle_data.assert_not_called()
        mock_update_battle_data_task.assert_called_once_with(
            player_id=player.player_id,
            realm='na',
        )
        player.refresh_from_db()
        self.assertIsNotNone(player.last_lookup)
        self.assertGreaterEqual(player.last_lookup, request_started_at)
        self.assertLessEqual(player.last_lookup, timezone.now())

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

        def hydrate_player(player, force_refresh=False, realm=None):
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
            realm='na',
        )
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.data.update_clan_data")
    @patch("warships.data.update_player_data")
    def test_player_lookup_without_clan_serves_stored_payload_and_enqueues_refresh(
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

        response = self.client.get("/api/player/NoClanYet/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["clan_id"])
        self.assertIsNone(payload["clan_name"])
        self.assertIsNone(payload["clan_tag"])
        mock_update_player_data.assert_not_called()
        mock_update_clan_data.assert_not_called()
        mock_update_player_task.assert_called_once_with(
            player_id=player.player_id,
            force_refresh=True,
            realm='na',
        )
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.tasks.queue_ranked_data_refresh")
    def test_ranked_endpoint_cold_cache_queues_async_refresh_with_request_realm(
        self,
        mock_queue_ranked_data_refresh,
    ):
        # Cold cache must NOT block on the WG API: serve [] now and queue an
        # async refresh keyed to the request realm (so the per-realm pending
        # dispatch key + X-Ranked-Pending header are correct for EU/ASIA).
        player = Player.objects.create(
            name="ColdRankedEU",
            player_id=7002,
            realm="eu",
            last_fetch=timezone.now(),
            ranked_json=None,
        )

        response = self.client.get(
            f"/api/fetch/ranked_data/{player.player_id}/?realm=eu")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        mock_queue_ranked_data_refresh.assert_called_once_with(
            str(player.player_id),
            realm='eu',
        )

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.data.update_player_data")
    def test_player_lookup_does_not_enqueue_when_data_is_fresh(
        self,
        mock_update_player_data,
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
        mock_update_player_data.assert_not_called()
        mock_update_player_task.assert_not_called()
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.data.update_player_data")
    def test_player_lookup_enqueues_force_refresh_when_efficiency_data_is_missing(
        self,
        mock_update_player_data,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=903,
            name="EfficiencyClan",
            members_count=1,
            last_fetch=now,
        )
        player = Player.objects.create(
            name="EfficiencyGapPlayer",
            player_id=9004,
            clan=clan,
            last_fetch=now,
            pvp_battles=25,
            pvp_ratio=52.0,
            pvp_survival_rate=38.0,
            actual_kdr=1.1,
            battles_json=[{"ship_name": "Stub Ship", "pvp_battles": 25}],
            efficiency_json=None,
        )

        response = self.client.get("/api/player/EfficiencyGapPlayer/")

        self.assertEqual(response.status_code, 200)
        mock_update_player_data.assert_not_called()
        mock_update_player_task.assert_called_once_with(
            player_id=player.player_id,
            force_refresh=True,
            realm='na',
        )
        mock_update_clan_task.assert_not_called()
        mock_update_clan_members_task.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    @patch("warships.data.update_player_data")
    def test_player_lookup_enqueues_refresh_for_efficiency_gap_when_actual_kdr_present(
        self,
        mock_update_player_data,
        mock_update_player_task,
        mock_update_clan_task,
        mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=904,
            name="EfficiencyAsyncClan",
            members_count=1,
            last_fetch=now,
        )
        Player.objects.create(
            name="EfficiencyAsyncPlayer",
            player_id=9005,
            clan=clan,
            last_fetch=now,
            pvp_battles=25,
            pvp_ratio=52.0,
            pvp_survival_rate=38.0,
            battles_json=[{"ship_name": "Stub Ship", "pvp_battles": 25}],
            efficiency_json=None,
            actual_kdr=1.8,
        )

        response = self.client.get("/api/player/EfficiencyAsyncPlayer/")

        self.assertEqual(response.status_code, 200)
        mock_update_player_data.assert_not_called()
        mock_update_player_task.assert_called_once_with(
            player_id=9005,
            force_refresh=True,
            realm='na',
        )
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
            pvp_frags=800,
            pvp_survived_battles=350,
            pvp_deaths=650,
            actual_kdr=1.23,
            pvp_ratio=50.0,
            pvp_survival_rate=35.0,
            efficiency_json=[],
            verdict=None,
        )

        response = self.client.get("/api/player/VerdictGapPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "Flotsam")
        player.refresh_from_db()
        self.assertEqual(player.verdict, "Flotsam")
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
        mock_update_player_task.assert_called_once_with(
            player_id=9002,
            realm='na',
        )
        mock_update_clan_task.assert_called_once_with(clan_id=901, realm='na')
        mock_update_clan_members_task.assert_called_once_with(
            clan_id=901, realm='na')


class ClanMembersEndpointTests(TestCase):
    def setUp(self):
        super().setUp()
        self.queue_clan_ranked_hydration_patcher = patch(
            "warships.data.queue_clan_ranked_hydration",
            return_value={
                "pending_player_ids": set(),
                "queued_player_ids": set(),
                "deferred_player_ids": set(),
                "eligible_player_ids": set(),
                "max_in_flight": 8,
            },
        )
        self.mock_queue_clan_ranked_hydration = self.queue_clan_ranked_hydration_patcher.start()
        self.queue_clan_efficiency_hydration_patcher = patch(
            "warships.data.queue_clan_efficiency_hydration",
            return_value={
                "pending_player_ids": set(),
                "queued_player_ids": set(),
                "deferred_player_ids": set(),
                "eligible_player_ids": set(),
                "max_in_flight": 8,
            },
        )
        self.mock_queue_clan_efficiency_hydration = self.queue_clan_efficiency_hydration_patcher.start()
        self.queue_clan_battle_data_refresh_patcher = patch(
            "warships.tasks.queue_clan_battle_data_refresh",
            return_value={"status": "queued"},
        )
        self.mock_queue_clan_battle_data_refresh = self.queue_clan_battle_data_refresh_patcher.start()

    def tearDown(self):
        self.queue_clan_battle_data_refresh_patcher.stop()
        self.queue_clan_efficiency_hydration_patcher.stop()
        self.queue_clan_ranked_hydration_patcher.stop()
        super().tearDown()

    def test_clan_members_null_returns_empty_list(self):
        response = self.client.get("/api/fetch/clan_members/null/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_orders_by_recent_battle_with_hidden_at_bottom(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
    ):
        # Recent-battle ordering with hidden players sinking to the bottom.
        # Visible/recent first → visible/older next → hidden last,
        # hidden block also internally sorted by recency.
        today = timezone.now().date()
        clan = Clan.objects.create(
            clan_id=4242, name="Order Clan", members_count=4)
        Player.objects.create(
            name="OldVisible", player_id=10, clan=clan, is_hidden=False,
            last_battle_date=today - timedelta(days=14),
        )
        Player.objects.create(
            name="HiddenRecent", player_id=11, clan=clan, is_hidden=True,
            last_battle_date=today - timedelta(days=1),
        )
        Player.objects.create(
            name="VisibleRecent", player_id=12, clan=clan, is_hidden=False,
            last_battle_date=today - timedelta(days=2),
        )
        Player.objects.create(
            name="HiddenAncient", player_id=13, clan=clan, is_hidden=True,
            last_battle_date=today - timedelta(days=90),
        )

        response = self.client.get("/api/fetch/clan_members/4242/")

        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()]
        self.assertEqual(
            names,
            ["VisibleRecent", "OldVisible", "HiddenRecent", "HiddenAncient"],
        )

    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_include_current_ship_badges(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
    ):
        # A member who currently holds a top spot gets `ship_badges` (+ realm)
        # in the clan-member payload, so the roster tray can render the medal.
        today = timezone.now().date()
        clan = Clan.objects.create(clan_id=4244, name="Badge Clan", members_count=2)
        holder = Player.objects.create(
            name="Holder", player_id=20, clan=clan, realm="na", is_hidden=False,
            last_battle_date=today,
        )
        Player.objects.create(
            name="Plain", player_id=21, clan=clan, realm="na", is_hidden=False,
            last_battle_date=today - timedelta(days=1),
        )
        Ship.objects.create(ship_id=10, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        ShipTopPlayerSnapshot.objects.create(
            captured_on=today, realm="na", ship_id=10, ship_name="Shimakaze",
            rank=1, player=holder, win_rate=64.0, battles=312,
            damage=19_344_000, frags=400, survived=200,
        )

        response = self.client.get("/api/fetch/clan_members/4244/")

        self.assertEqual(response.status_code, 200)
        by_name = {row["name"]: row for row in response.json()}
        self.assertEqual(by_name["Holder"]["realm"], "na")
        holder_badges = by_name["Holder"]["ship_badges"]
        self.assertEqual(len(holder_badges), 1)
        self.assertEqual(holder_badges[0]["ship_id"], 10)
        self.assertEqual(holder_badges[0]["rank"], 1)
        self.assertEqual(by_name["Plain"]["ship_badges"], [])

    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_days_since_last_battle_derives_from_last_battle_date(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
    ):
        # Regression: the stored `days_since_last_battle` column is a
        # snapshot taken at refresh time and drifts by 1 day per day
        # without a refresh. The clan_members endpoint must surface a
        # value derived from `last_battle_date` so the displayed
        # "X days idle" label tracks the actual gap (and matches the
        # row ordering).
        today = timezone.now().date()
        clan = Clan.objects.create(
            clan_id=4243, name="Drift Clan", members_count=1)
        Player.objects.create(
            name="StaleSnapshot",
            player_id=14,
            clan=clan,
            is_hidden=False,
            last_battle_date=today - timedelta(days=5),
            # Intentionally stale stored value — the response must NOT
            # surface this; it should compute 5 from last_battle_date.
            days_since_last_battle=1,
        )

        response = self.client.get("/api/fetch/clan_members/4243/")

        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "StaleSnapshot")
        self.assertEqual(rows[0]["days_since_last_battle"], 5)

    @patch("warships.tasks.refresh_clan_member_idle_task.delay")
    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_cold_cache_queues_idle_refresh_and_flags_pending(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
        mock_idle_delay,
    ):
        # Cold cache: queue a bulk roster idle refresh + signal pending so the
        # FE re-polls for corrected "X days idle"; serve stored values now.
        today = timezone.now().date()
        clan = Clan.objects.create(
            clan_id=4244, name="Idle Clan", members_count=1)
        Player.objects.create(
            name="StaleIdle",
            player_id=144,
            clan=clan,
            is_hidden=False,
            last_battle_date=today - timedelta(days=73),
        )

        response = self.client.get("/api/fetch/clan_members/4244/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Clan-Idle-Pending"], "true")
        mock_idle_delay.assert_called_once_with(clan_id="4244", realm="na")

    @patch("warships.tasks.refresh_clan_member_idle_task.delay")
    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_idle_refresh_respects_cooldown(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
        mock_idle_delay,
    ):
        from warships.tasks import _clan_member_idle_refresh_cooldown_key
        cache.set(_clan_member_idle_refresh_cooldown_key("4245", realm="na"), "1")

        clan = Clan.objects.create(
            clan_id=4245, name="Cooldown Clan", members_count=1)
        Player.objects.create(
            name="CooldownMember", player_id=145, clan=clan, is_hidden=False,
            last_battle_date=timezone.now().date() - timedelta(days=10))

        response = self.client.get("/api/fetch/clan_members/4245/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Clan-Idle-Pending", response)
        mock_idle_delay.assert_not_called()

    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_returns_data_when_members_exist(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
    ):
        clan = Clan.objects.create(
            clan_id=42, name="Test Clan", members_count=1, leader_id=1, leader_name="MemberOne")
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
                             "realm": "na",
                             "ship_badges": [],
                             "is_hidden": False,
                             "is_streamer": False,
                             "pvp_ratio": None,
                             "days_since_last_battle": 6,
                             "is_leader": True,
                             "is_pve_player": False,
                             "is_sleepy_player": False,
                             "is_ranked_player": False,
                             "is_clan_battle_player": False,
                             "clan_battle_win_rate": None,
                             "efficiency_hydration_pending": False,
                             "highest_ranked_league": None,
                             "ranked_hydration_pending": False,
                             "ranked_updated_at": None,
                             "efficiency_rank_percentile": None,
                             "efficiency_rank_tier": None,
                             "has_efficiency_rank_icon": False,
                             "efficiency_rank_population_size": None,
                             "efficiency_rank_updated_at": None,
                             "activity_bucket": "active_7d",
                         }])
        self.assertEqual(response["X-Ranked-Hydration-Queued"], "0")
        self.assertEqual(response["X-Ranked-Hydration-Deferred"], "0")
        self.assertEqual(response["X-Ranked-Hydration-Pending"], "0")
        self.assertEqual(response["X-Ranked-Hydration-Max-In-Flight"], "8")
        self.assertEqual(response["X-Efficiency-Hydration-Queued"], "0")
        self.assertEqual(response["X-Efficiency-Hydration-Deferred"], "0")
        self.assertEqual(response["X-Efficiency-Hydration-Pending"], "0")
        self.assertEqual(response["X-Efficiency-Hydration-Max-In-Flight"], "8")
        mock_update_clan_data.assert_not_called()
        mock_update_clan_members.assert_not_called()

    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_exposes_streamer_flag(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
    ):
        clan = Clan.objects.create(
            clan_id=43, name="Streamer Clan", members_count=1, leader_id=10, leader_name="StreamerMate")
        Player.objects.create(
            name="StreamerMate",
            player_id=10,
            clan=clan,
            is_streamer=True,
            days_since_last_battle=2,
            last_battle_date=timezone.now().date() - timedelta(days=2),
        )

        response = self.client.get("/api/fetch/clan_members/43/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["name"], "StreamerMate")
        self.assertTrue(response.json()[0]["is_streamer"])
        mock_update_clan_data.assert_not_called()
        mock_update_clan_members.assert_not_called()

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.data.update_clan_members")
    @patch("warships.data.update_clan_data")
    def test_clan_members_returns_partial_rows_and_queues_refresh_for_incomplete_clan(
        self,
        mock_update_clan_data,
        mock_update_clan_members,
        mock_update_clan_data_task,
        mock_update_clan_members_task,
    ):
        clan = Clan.objects.create(
            clan_id=420,
            name="Incomplete Clan",
            members_count=2,
            leader_id=None,
            leader_name="",
        )
        Player.objects.create(
            name="ExistingMember",
            player_id=4201,
            clan=clan,
            days_since_last_battle=4,
            last_battle_date=timezone.now().date() - timedelta(days=4),
        )

        response = self.client.get("/api/fetch/clan_members/420/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["name"], "ExistingMember")
        mock_update_clan_data.assert_not_called()
        mock_update_clan_members.assert_not_called()
        mock_update_clan_data_task.assert_called_once_with(
            clan_id="420", realm='na')
        mock_update_clan_members_task.assert_called_once_with(
            clan_id="420", realm='na')

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    def test_clan_members_enqueues_realm_aware_refresh_for_eu_clan(
        self,
        mock_update_clan_data_task,
        mock_update_clan_members_task,
    ):
        clan = Clan.objects.create(
            clan_id=421,
            name="EU Incomplete Clan",
            realm='eu',
            members_count=2,
            leader_id=None,
            leader_name="",
        )
        Player.objects.create(
            name="EuExistingMember",
            player_id=4211,
            clan=clan,
            realm='eu',
            days_since_last_battle=4,
            last_battle_date=timezone.now().date() - timedelta(days=4),
        )

        response = self.client.get("/api/fetch/clan_members/421/?realm=eu")

        self.assertEqual(response.status_code, 200)
        mock_update_clan_data_task.assert_called_once_with(
            clan_id="421", realm='eu')
        mock_update_clan_members_task.assert_called_once_with(
            clan_id="421", realm='eu')

    def test_clan_members_exposes_ranked_hydration_metadata(self):
        self.mock_queue_clan_ranked_hydration.return_value = {
            "pending_player_ids": {7901},
            "queued_player_ids": {7901},
            "deferred_player_ids": set(),
            "eligible_player_ids": {7901, 7902},
            "max_in_flight": 8,
        }
        ranked_updated_at = timezone.now() - timedelta(hours=3)
        clan = Clan.objects.create(
            clan_id=791,
            name="Ranked Hydration Clan",
            members_count=2,
        )
        Player.objects.create(
            name="PendingRankedHydration",
            player_id=7901,
            clan=clan,
            ranked_updated_at=None,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="FreshRankedHydration",
            player_id=7902,
            clan=clan,
            ranked_updated_at=ranked_updated_at,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/791/")

        self.assertEqual(response.status_code, 200)
        payload = {row["name"]: row for row in response.json()}
        self.assertTrue(payload["PendingRankedHydration"]
                        ["ranked_hydration_pending"])
        self.assertIsNone(
            payload["PendingRankedHydration"]["ranked_updated_at"])
        self.assertFalse(payload["FreshRankedHydration"]
                         ["ranked_hydration_pending"])
        self.assertEqual(response["X-Ranked-Hydration-Queued"], "1")
        self.assertEqual(response["X-Ranked-Hydration-Deferred"], "0")
        self.assertEqual(response["X-Ranked-Hydration-Pending"], "1")
        self.assertEqual(response["X-Ranked-Hydration-Max-In-Flight"], "8")
        self.assertEqual(
            payload["FreshRankedHydration"]["ranked_updated_at"],
            ranked_updated_at.isoformat().replace(
                "+00:00", "Z") if ranked_updated_at.tzinfo else ranked_updated_at.isoformat(),
        )

    def test_clan_members_exposes_efficiency_hydration_metadata(self):
        self.mock_queue_clan_efficiency_hydration.return_value = {
            "pending_player_ids": {7941},
            "queued_player_ids": {7941},
            "deferred_player_ids": set(),
            "eligible_player_ids": {7941, 7942},
            "max_in_flight": 8,
        }
        clan = Clan.objects.create(
            clan_id=794,
            name="Efficiency Hydration Clan",
            members_count=2,
        )
        Player.objects.create(
            name="PendingEfficiencyHydration",
            player_id=7941,
            clan=clan,
            pvp_battles=500,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="FreshEfficiencyHydration",
            player_id=7942,
            clan=clan,
            pvp_battles=500,
            efficiency_json=[],
            efficiency_updated_at=timezone.now(),
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/794/")

        self.assertEqual(response.status_code, 200)
        payload = {row["name"]: row for row in response.json()}
        self.assertTrue(payload["PendingEfficiencyHydration"]
                        ["efficiency_hydration_pending"])
        self.assertFalse(payload["FreshEfficiencyHydration"]
                         ["efficiency_hydration_pending"])
        self.assertEqual(response["X-Efficiency-Hydration-Queued"], "1")
        self.assertEqual(response["X-Efficiency-Hydration-Deferred"], "0")
        self.assertEqual(response["X-Efficiency-Hydration-Pending"], "1")
        self.assertEqual(
            response["X-Efficiency-Hydration-Max-In-Flight"], "8")

    def test_clan_members_exposes_deferred_ranked_hydration_count_in_headers(self):
        self.mock_queue_clan_ranked_hydration.return_value = {
            "pending_player_ids": {7921, 7922},
            "queued_player_ids": {7921},
            "deferred_player_ids": {7922},
            "eligible_player_ids": {7921, 7922},
            "max_in_flight": 1,
        }
        clan = Clan.objects.create(
            clan_id=792,
            name="Deferred Ranked Hydration Clan",
            members_count=2,
        )
        Player.objects.create(
            name="QueuedMember",
            player_id=7921,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="DeferredMember",
            player_id=7922,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/792/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Ranked-Hydration-Queued"], "1")
        self.assertEqual(response["X-Ranked-Hydration-Deferred"], "1")
        self.assertEqual(response["X-Ranked-Hydration-Pending"], "2")
        self.assertEqual(response["X-Ranked-Hydration-Max-In-Flight"], "1")

    def test_clan_members_marks_leader_by_name_when_leader_id_missing(self):
        clan = Clan.objects.create(
            clan_id=79,
            name="Leader Name Clan",
            members_count=2,
            leader_name="NamedLeader",
        )
        Player.objects.create(
            name="NamedLeader",
            player_id=7901,
            clan=clan,
            days_since_last_battle=1,
            last_battle_date=timezone.now().date() - timedelta(days=1),
        )
        Player.objects.create(
            name="OtherMember",
            player_id=7902,
            clan=clan,
            days_since_last_battle=3,
            last_battle_date=timezone.now().date() - timedelta(days=3),
        )

        response = self.client.get("/api/fetch/clan_members/79/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["name"]: row["is_leader"] for row in response.json()},
            {
                "NamedLeader": True,
                "OtherMember": False,
            },
        )

    def test_clan_members_marks_pve_players_from_streamlined_thresholds(self):
        clan = Clan.objects.create(
            clan_id=80,
            name="PvE Clan",
            members_count=5,
        )
        Player.objects.create(
            name="HighShareHighVolume",
            player_id=8001,
            clan=clan,
            total_battles=7000,
            pvp_battles=4000,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="LowShareHighVolume",
            player_id=8002,
            clan=clan,
            total_battles=10000,
            pvp_battles=8300,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="TooSmallTotal",
            player_id=8003,
            clan=clan,
            total_battles=500,
            pvp_battles=40,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="HighAbsoluteButLowShare",
            player_id=8004,
            clan=clan,
            total_battles=23851,
            pvp_battles=19629,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="ExactlyThreshold",
            player_id=8005,
            clan=clan,
            total_battles=5000,
            pvp_battles=3500,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="HighShareLowVolume",
            player_id=8006,
            clan=clan,
            total_battles=1400,
            pvp_battles=200,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/80/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["name"]: row["is_pve_player"] for row in response.json()},
            {
                "HighShareHighVolume": True,
                "LowShareHighVolume": False,
                "TooSmallTotal": False,
                "HighAbsoluteButLowShare": False,
                "ExactlyThreshold": True,
                "HighShareLowVolume": False,
            },
        )

    def test_clan_members_marks_ranked_players_by_current_season_participation(self):
        # Current season = newest started season (4 is future-dated, so 3).
        today = timezone.now().date()
        RankedSeason.objects.create(
            season_id=2, label="S2",
            start_date=today - timedelta(days=200),
            end_date=today - timedelta(days=140))
        RankedSeason.objects.create(
            season_id=3, label="S3",
            start_date=today - timedelta(days=30), end_date=None)
        RankedSeason.objects.create(
            season_id=4, label="S4",
            start_date=today + timedelta(days=30), end_date=None)

        clan = Clan.objects.create(
            clan_id=81,
            name="Ranked Clan",
            members_count=3,
        )
        # A handful of current-season battles qualifies; the color is the
        # league reached THIS season (Silver), not the career-best Gold.
        Player.objects.create(
            name="CurrentSeason",
            player_id=8101,
            clan=clan,
            ranked_json=[
                {"season_id": 2, "total_battles": 300,
                    "total_wins": 170, "win_rate": 56.7, "highest_league": 1, "highest_league_name": "Gold"},
                {"season_id": 3, "total_battles": 12,
                    "total_wins": 6, "win_rate": 50.0, "highest_league": 2, "highest_league_name": "Silver"},
            ],
            last_battle_date=timezone.now().date(),
        )
        # Career volume alone no longer qualifies.
        Player.objects.create(
            name="CareerOnly",
            player_id=8102,
            clan=clan,
            ranked_json=[
                {"season_id": 2, "total_battles": 500,
                    "total_wins": 280, "win_rate": 56.0, "highest_league": 1, "highest_league_name": "Gold"},
            ],
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="NoRanked",
            player_id=8103,
            clan=clan,
            ranked_json=[],
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/81/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["name"]: row["is_ranked_player"] for row in response.json()},
            {
                "CurrentSeason": True,
                "CareerOnly": False,
                "NoRanked": False,
            },
        )
        self.assertEqual(
            {row["name"]: row["highest_ranked_league"]
                for row in response.json()},
            {
                "CurrentSeason": "Silver",
                "CareerOnly": None,
                "NoRanked": None,
            },
        )

    def test_clan_members_marks_clan_battle_players_from_cached_seasons(self):
        cache.clear()
        clan = Clan.objects.create(
            clan_id=82,
            name="Clan Battle Clan",
            members_count=2,
        )
        main = Player.objects.create(
            name="ClanBattleMain",
            player_id=8201,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )
        dabbler = Player.objects.create(
            name="ClanBattleDabbler",
            player_id=8202,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )
        PlayerExplorerSummary.objects.create(
            player=main,
            clan_battle_seasons_participated=2,
            clan_battle_total_battles=44,
            clan_battle_overall_win_rate=56.8,
            clan_battle_summary_updated_at=timezone.now(),
        )
        PlayerExplorerSummary.objects.create(
            player=dabbler,
            clan_battle_seasons_participated=1,
            clan_battle_total_battles=18,
            clan_battle_overall_win_rate=50.0,
            clan_battle_summary_updated_at=timezone.now(),
        )

        response = self.client.get("/api/fetch/clan_members/82/")

        self.assertEqual(response.status_code, 200)
        payload = {row["name"]: row for row in response.json()}
        self.assertTrue(payload["ClanBattleMain"]["is_clan_battle_player"])
        self.assertEqual(payload["ClanBattleMain"]
                         ["clan_battle_win_rate"], 56.8)
        self.assertFalse(payload["ClanBattleDabbler"]["is_clan_battle_player"])
        self.assertEqual(payload["ClanBattleDabbler"]
                         ["clan_battle_win_rate"], 50.0)

    def test_clan_members_marks_sleepy_players_after_one_year(self):
        clan = Clan.objects.create(
            clan_id=83,
            name="Sleepy Clan",
            members_count=2,
        )
        Player.objects.create(
            name="FreshSleeper",
            player_id=8301,
            clan=clan,
            days_since_last_battle=365,
            last_battle_date=timezone.now().date() - timedelta(days=365),
        )
        Player.objects.create(
            name="LongSleeper",
            player_id=8302,
            clan=clan,
            days_since_last_battle=430,
            last_battle_date=timezone.now().date() - timedelta(days=430),
        )

        response = self.client.get("/api/fetch/clan_members/83/")

        self.assertEqual(response.status_code, 200)
        payload = {row["name"]: row["is_sleepy_player"]
                   for row in response.json()}
        self.assertEqual(payload, {
            "FreshSleeper": False,
            "LongSleeper": True,
        })

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

    def test_clan_members_orders_by_last_battle_date_desc(self):
        # Ordering contract: most-recent battle first, NULL last_battle_date
        # last, name as deterministic tiebreak. player_score is no longer
        # part of the ordering (simplified 2026-04-29).
        clan = Clan.objects.create(
            clan_id=77, name="Recency Clan", members_count=3)
        Player.objects.create(
            name="OneDayIdle",
            player_id=7701,
            clan=clan,
            pvp_ratio=52.0,
            last_battle_date=timezone.now().date() - timedelta(days=1),
        )
        Player.objects.create(
            name="ThreeDaysIdle",
            player_id=7702,
            clan=clan,
            pvp_ratio=55.0,
            last_battle_date=timezone.now().date() - timedelta(days=3),
        )
        Player.objects.create(
            name="PlayedToday",
            player_id=7703,
            clan=clan,
            pvp_ratio=57.0,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/77/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()],
            ["PlayedToday", "OneDayIdle", "ThreeDaysIdle"],
        )

    def test_clan_members_exposes_fresh_efficiency_rank_fields(self):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=84,
            name="Efficiency Rank Clan",
            members_count=2,
        )
        ranked_member = Player.objects.create(
            name="RankedRosterMember",
            player_id=8401,
            clan=clan,
            is_hidden=False,
            pvp_battles=700,
            efficiency_updated_at=now - timedelta(hours=2),
            battles_updated_at=now - timedelta(hours=2),
            last_battle_date=now.date(),
        )
        PlayerExplorerSummary.objects.create(
            player=ranked_member,
            efficiency_rank_percentile=0.81,
            efficiency_rank_tier='II',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=124,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )
        Player.objects.create(
            name="UnrankedRosterMember",
            player_id=8402,
            clan=clan,
            is_hidden=False,
            pvp_battles=100,
            last_battle_date=now.date(),
        )

        response = self.client.get("/api/fetch/clan_members/84/")

        self.assertEqual(response.status_code, 200)
        payload = {row["name"]: row for row in response.json()}
        self.assertEqual(
            payload["RankedRosterMember"]["efficiency_rank_percentile"],
            0.81,
        )
        self.assertEqual(
            payload["RankedRosterMember"]["efficiency_rank_tier"],
            "II",
        )
        self.assertTrue(
            payload["RankedRosterMember"]["has_efficiency_rank_icon"]
        )
        self.assertEqual(
            payload["RankedRosterMember"]["efficiency_rank_population_size"],
            124,
        )
        self.assertIsNotNone(
            payload["RankedRosterMember"]["efficiency_rank_updated_at"]
        )
        self.assertIsNone(
            payload["UnrankedRosterMember"]["efficiency_rank_percentile"]
        )
        self.assertIsNone(
            payload["UnrankedRosterMember"]["efficiency_rank_tier"]
        )
        self.assertFalse(
            payload["UnrankedRosterMember"]["has_efficiency_rank_icon"]
        )


class ApiContractTests(TestCase):
    def setUp(self):
        cache.clear()

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

    def test_player_name_suggestions_null_byte_does_not_crash(self):
        response = self.client.get(
            "/api/landing/player-suggestions/?q=test\x00")
        self.assertIn(response.status_code, [200, 400])

    def test_clan_name_suggestions_returns_matching_clans(self):
        Clan.objects.create(clan_id=9001, name="Storm Fleet",
                            tag="STORM", members_count=40)
        Clan.objects.create(clan_id=9002, name="Thunderstorm",
                            tag="THDR", members_count=20)
        Clan.objects.create(clan_id=9003, name="Calm Seas",
                            tag="CALM", members_count=30)

        response = self.client.get("/api/landing/clan-suggestions/?q=storm")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["name"], "Storm Fleet")
        self.assertEqual(payload[1]["name"], "Thunderstorm")
        for entry in payload:
            self.assertIn("clan_id", entry)
            self.assertIn("tag", entry)
            self.assertIn("name", entry)
            self.assertIn("members_count", entry)

    def test_clan_name_suggestions_matches_tag(self):
        Clan.objects.create(clan_id=9010, name="Alpha Fleet",
                            tag="ALFA", members_count=25)
        Clan.objects.create(clan_id=9011, name="Bravo Crew",
                            tag="BRV", members_count=15)

        response = self.client.get("/api/landing/clan-suggestions/?q=alfa")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["tag"], "ALFA")

    def test_clan_name_suggestions_short_query_returns_empty(self):
        response = self.client.get("/api/landing/clan-suggestions/?q=ab")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_clan_name_suggestions_respects_realm(self):
        Clan.objects.create(clan_id=9020, name="Euro Fleet",
                            tag="EURO", members_count=30, realm="eu")
        Clan.objects.create(clan_id=9021, name="Euro Corps",
                            tag="EURC", members_count=20, realm="na")

        response = self.client.get(
            "/api/landing/clan-suggestions/?q=euro&realm=eu")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["clan_id"], 9020)

    def test_clan_name_suggestions_null_byte_does_not_crash(self):
        response = self.client.get(
            "/api/landing/clan-suggestions/?q=test\x00")
        self.assertIn(response.status_code, [200, 400])

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

    def test_player_summary_days_since_last_battle_is_derived_at_read_time(self):
        # Simulates the real bug: stored days_since_last_battle is stale
        # (snapshotted at last refresh), but last_battle_date is current.
        # The API should always return the freshly-computed value derived
        # from last_battle_date — never the stored stale snapshot.
        now = timezone.now()
        Player.objects.create(
            name="StaleDaysPlayer",
            player_id=8183,
            is_hidden=False,
            pvp_battles=100,
            last_battle_date=now.date() - timedelta(days=2),
            # Stored value is stale (e.g. computed 5 days ago when player
            # was 7 days idle and 5 more days have passed without refresh).
            days_since_last_battle=7,
        )
        response = self.client.get("/api/fetch/player_summary/8183/")
        self.assertEqual(response.status_code, 200)
        # Derived from last_battle_date (now - 2 days) — not the stored 7.
        self.assertEqual(response.json()["days_since_last_battle"], 2)

    def test_player_detail_includes_kill_ratio(self):
        now = timezone.now()
        Player.objects.create(
            name="DetailKillRatioPlayer",
            player_id=8182,
            is_hidden=False,
            pvp_ratio=53.0,
            pvp_battles=30,
            pvp_frags=45,
            pvp_survived_battles=10,
            pvp_deaths=20,
            actual_kdr=2.25,
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
        self.assertEqual(response.json()["actual_kdr"], 2.25)
        self.assertEqual(response.json()["player_score"], 3.87)

    def test_player_detail_exposes_current_season_ranked_fields(self):
        now = timezone.now()
        today = now.date()
        # Season 8 is the newest started season → current. The payload league is
        # the season-8 Silver, not the career-best season-6 Gold, and the icon
        # flag rides current-season participation.
        RankedSeason.objects.create(
            season_id=6, label="S6",
            start_date=today - timedelta(days=300),
            end_date=today - timedelta(days=240))
        RankedSeason.objects.create(
            season_id=8, label="S8",
            start_date=today - timedelta(days=20), end_date=None)
        Player.objects.create(
            name="DetailRankedLeaguePlayer",
            player_id=8185,
            is_hidden=False,
            pvp_ratio=54.0,
            pvp_battles=400,
            pvp_survival_rate=39.0,
            creation_date=now - timedelta(days=180),
            ranked_json=[
                {"season_id": 8, "highest_league_name": "Silver", "total_battles": 34},
                {"season_id": 6, "highest_league": 1,
                    "highest_league_name": "Gold", "total_battles": 12},
            ],
        )

        response = self.client.get("/api/player/DetailRankedLeaguePlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["highest_ranked_league"], "Silver")
        self.assertTrue(response.json()["is_ranked_player"])

    def test_player_detail_ranked_fields_dark_without_current_season_battles(self):
        now = timezone.now()
        today = now.date()
        RankedSeason.objects.create(
            season_id=9, label="S9",
            start_date=today - timedelta(days=10), end_date=None)
        Player.objects.create(
            name="DetailCareerRankedPlayer",
            player_id=8186,
            is_hidden=False,
            pvp_ratio=51.0,
            pvp_battles=300,
            pvp_survival_rate=41.0,
            creation_date=now - timedelta(days=400),
            ranked_json=[
                {"season_id": 6, "highest_league": 1,
                    "highest_league_name": "Gold", "total_battles": 250},
            ],
        )

        response = self.client.get("/api/player/DetailCareerRankedPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["highest_ranked_league"])
        self.assertFalse(response.json()["is_ranked_player"])

    def test_player_detail_backfills_missing_kill_ratio_from_stale_summary(self):
        now = timezone.now()
        player = Player.objects.create(
            name="DetailKillRatioBackfill",
            player_id=8183,
            is_hidden=False,
            pvp_ratio=53.0,
            pvp_battles=30,
            pvp_frags=45,
            pvp_survived_battles=10,
            pvp_deaths=20,
            actual_kdr=2.25,
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
        self.assertEqual(response.json()["actual_kdr"], 2.25)
        self.assertEqual(response.json()["player_score"], 3.87)

        player.refresh_from_db()
        self.assertEqual(player.explorer_summary.kill_ratio, 0.78)
        self.assertEqual(player.explorer_summary.player_score, 3.87)

    @patch("warships.views.update_player_data_task.delay")
    def test_player_detail_keeps_missing_actual_kdr_and_queues_refresh(self, mock_update_player_data_task):
        now = timezone.now()
        player = Player.objects.create(
            name="DetailActualKdrBackfill",
            player_id=8184,
            is_hidden=False,
            pvp_ratio=53.0,
            pvp_battles=30,
            pvp_survival_rate=40.0,
            creation_date=now - timedelta(days=180),
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 8, "pvp_battles": 30, "kdr": 1.0},
            ],
            efficiency_json=[],
            actual_kdr=None,
            last_fetch=now - timedelta(days=2),
        )

        response = self.client.get("/api/player/DetailActualKdrBackfill/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["actual_kdr"])
        mock_update_player_data_task.assert_called_once_with(
            player_id=player.player_id,
            force_refresh=True,
            realm='na',
        )

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
            realm='na',
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
        self.assertEqual(payload["x_label"], "Survival Rate")
        self.assertEqual(payload["y_label"], "Win Rate")
        self.assertEqual(payload["tracked_population"], 2)
        self.assertTrue(payload["correlation"]
                        is None or payload["correlation"] > 0)
        self.assertEqual(payload["x_domain"], {
            "min": 15.0,
            "max": 75.0,
            "bin_width": 1.5,
        })
        self.assertEqual(payload["y_domain"], {
            "min": 35.0,
            "max": 75.0,
            "bin_width": 1.0,
        })
        self.assertTrue(any(
            tile["x_index"] == 12 and tile["y_index"] == 17 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(
            tile["x_index"] == 18 and tile["y_index"] == 23 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(all("x_min" not in tile for tile in payload["tiles"]))
        self.assertTrue(
            any(point["x_index"] == 12 and point["count"] == 1 for point in payload["trend"]))
        self.assertTrue(
            any(point["x_index"] == 18 and point["count"] == 1 for point in payload["trend"]))
        self.assertTrue(all("x" not in point for point in payload["trend"]))

    def test_warm_player_correlations_populates_all_heatmap_caches(self):
        from warships.data import (
            PLAYER_TIER_TYPE_CACHE_VERSION,
            _player_correlation_cache_key,
            _player_correlation_published_cache_key,
            warm_player_correlations,
        )

        cache.clear()

        Player.objects.create(
            name="WarmCorrelationOne",
            player_id=8826,
            is_hidden=False,
            pvp_battles=1600,
            pvp_ratio=52.0,
            pvp_survival_rate=34.0,
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 40, "wins": 24},
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 20, "wins": 10},
            ],
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 12, "total_battles": 80,
                    "total_wins": 45, "win_rate": 0.5625, "top_ship_name": None},
            ],
        )
        Player.objects.create(
            name="WarmCorrelationTwo",
            player_id=8827,
            is_hidden=False,
            pvp_battles=2200,
            pvp_ratio=58.0,
            pvp_survival_rate=42.0,
            battles_json=[
                {"ship_name": "Ship C", "ship_type": "Battleship",
                    "ship_tier": 9, "pvp_battles": 25, "wins": 14},
            ],
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 11, "total_battles": 120,
                    "total_wins": 72, "win_rate": 0.6, "top_ship_name": None},
            ],
        )

        result = warm_player_correlations()

        self.assertEqual(result["tier_type"]["tracked_population"], 2)
        self.assertEqual(result["win_rate_survival"]["tracked_population"], 2)
        self.assertEqual(result["ranked_wr_battles"]["tracked_population"], 2)
        self.assertIsNotNone(
            cache.get(_player_correlation_cache_key(PLAYER_TIER_TYPE_CACHE_VERSION)))
        self.assertIsNotNone(
            cache.get(_player_correlation_published_cache_key(PLAYER_TIER_TYPE_CACHE_VERSION)))
        self.assertIsNotNone(
            cache.get(_player_correlation_cache_key("win_rate_survival")))
        self.assertIsNotNone(
            cache.get(_player_correlation_published_cache_key("win_rate_survival")))
        self.assertIsNotNone(
            cache.get(_player_correlation_cache_key("ranked_wr_battles:v6")))
        self.assertIsNotNone(
            cache.get(_player_correlation_published_cache_key("ranked_wr_battles:v6")))

    def test_player_correlation_distribution_returns_tier_type_payload(self):
        cache.clear()
        from warships.data import warm_player_tier_type_population_correlation

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

        # Pre-warm the population correlation cache so this request exercises
        # the populated response path rather than the cold-cache pending path.
        warm_player_tier_type_population_correlation()

        response = self.client.get(
            "/api/fetch/player_correlation/tier_type/8831/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "tier_type")
        self.assertEqual(payload["label"], "Tier vs Ship Type")
        self.assertEqual(payload["x_label"], "Ship Type")
        self.assertEqual(payload["y_label"], "Tier")
        self.assertEqual(payload["tracked_population"], 2)
        self.assertEqual(
            payload["x_labels"][:5],
            ["Destroyer", "Cruiser", "Battleship",
                "Aircraft Carrier", "Submarine"],
        )
        self.assertEqual(payload["y_values"][:3], [11, 10, 9])
        self.assertEqual(payload["player_cells"][0]["ship_type"], "Destroyer")
        self.assertEqual(payload["player_cells"][0]["ship_tier"], 10)
        self.assertEqual(payload["player_cells"][0]["pvp_battles"], 40)
        self.assertAlmostEqual(payload["player_cells"][0]["win_ratio"], 0.6)
        self.assertTrue(any(
            tile["x_index"] == 0
            and tile["y_index"] == 1
            and tile["count"] == 55
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(
            point["x_index"] == 2
            and point["count"] == 35
            and point["avg_tier"] > 8.7
            for point in payload["trend"]
        ))

    @patch("warships.data.update_battle_data_task.delay")
    def test_player_correlation_distribution_flags_pending_tier_type_refresh_when_player_battles_are_missing(self, mock_update_battle_data_task):
        cache.clear()

        Player.objects.create(
            name="TierTypePending",
            player_id=8834,
            is_hidden=False,
            pvp_battles=1400,
            battles_json=None,
        )
        Player.objects.create(
            name="TierTypePopulation",
            player_id=8835,
            is_hidden=False,
            pvp_battles=2100,
            battles_json=[
                {"ship_name": "Ship D", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 15, "wins": 8},
                {"ship_name": "Ship E", "ship_type": "Cruiser",
                    "ship_tier": 8, "pvp_battles": 30, "wins": 18},
            ],
        )

        # Pre-warm the population correlation cache so the request path
        # doesn't need to rebuild it inline (matches production behavior).
        from warships.data import warm_player_tier_type_population_correlation
        warm_player_tier_type_population_correlation()

        response = self.client.get(
            "/api/fetch/player_correlation/tier_type/8834/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Tier-Type-Pending"], "true")
        payload = response.json()
        self.assertEqual(payload["metric"], "tier_type")
        self.assertEqual(payload["player_cells"], [])
        self.assertTrue(payload["tiles"])
        mock_update_battle_data_task.assert_called_once_with(
            player_id='8834', realm='na')

    def test_player_correlation_distribution_returns_ranked_wr_battles_payload(self):
        cache.clear()
        from warships.data import warm_player_ranked_wr_battles_population_correlation

        Player.objects.create(
            name="RankedHeatmapOne",
            player_id=8841,
            is_hidden=False,
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 9, "total_battles": 40,
                    "total_wins": 24, "win_rate": 0.6, "top_ship_name": None},
                {"season_id": 8, "total_battles": 20,
                    "total_wins": 10, "win_rate": 0.5, "top_ship_name": None},
            ],
        )
        Player.objects.create(
            name="RankedHeatmapTwo",
            player_id=8842,
            is_hidden=False,
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 9, "total_battles": 140,
                    "total_wins": 84, "win_rate": 0.6, "top_ship_name": None},
            ],
        )
        Player.objects.create(
            name="RankedHeatmapTooSmall",
            player_id=8844,
            is_hidden=False,
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 9, "total_battles": 30,
                    "total_wins": 18, "win_rate": 0.6, "top_ship_name": None},
            ],
        )
        Player.objects.create(
            name="RankedHeatmapHidden",
            player_id=8843,
            is_hidden=True,
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 9, "total_battles": 60,
                    "total_wins": 54, "win_rate": 0.9, "top_ship_name": None},
            ],
        )

        warm_player_ranked_wr_battles_population_correlation()

        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/8841/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "ranked_wr_battles")
        self.assertEqual(payload["label"], "Ranked Games vs Win Rate")
        self.assertEqual(payload["x_label"], "Total Ranked Games")
        self.assertEqual(payload["y_label"], "Ranked Win Rate")
        self.assertEqual(payload["x_scale"], "log")
        self.assertEqual(payload["x_edges"][0], 50.0)
        self.assertEqual(payload["x_edges"][1], 59.0)
        self.assertEqual(payload["x_ticks"][0], 50.0)
        self.assertEqual(payload["x_ticks"][1], 100.0)
        self.assertEqual(payload["tracked_population"], 2)
        self.assertEqual(payload["player_point"]["x"], 60.0)
        self.assertEqual(payload["player_point"]["y"], 56.67)
        self.assertTrue(any(tile["count"] > 0 for tile in payload["tiles"]))
        self.assertTrue(any(
            tile["x_index"] == 1 and tile["y_index"] == 28 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(
            tile["x_index"] == 5 and tile["y_index"] == 33 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(point["count"] > 0 for point in payload["trend"]))
        self.assertTrue(all("x_index" in point for point in payload["trend"]))

    def test_player_correlation_distribution_returns_404_for_missing_ranked_wr_battles_player(self):
        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/999999/")

        self.assertEqual(response.status_code, 404)

    @patch("warships.tasks.queue_player_ranked_wr_battles_correlation_refresh")
    def test_player_correlation_distribution_queues_refresh_when_cache_is_cold(self, mock_queue_refresh):
        cache.clear()

        Player.objects.create(
            name="ColdRankedHeatmap",
            player_id=8845,
            is_hidden=False,
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 10, "total_battles": 90,
                    "total_wins": 54, "win_rate": 0.6, "top_ship_name": None},
            ],
        )

        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/8845/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Ranked-WR-Battles-Pending"], "true")
        payload = response.json()
        self.assertEqual(payload["metric"], "ranked_wr_battles")
        self.assertEqual(payload["tracked_population"], 0)
        self.assertEqual(payload["tiles"], [])
        self.assertEqual(payload["player_point"]["x"], 90.0)
        self.assertEqual(payload["player_point"]["y"], 60.0)
        mock_queue_refresh.assert_called()

    @patch("warships.tasks.queue_player_ranked_wr_battles_correlation_refresh")
    def test_player_correlation_distribution_uses_published_ranked_population_fallback(self, mock_queue_refresh):
        from warships.data import _player_correlation_cache_key, _player_correlation_published_cache_key, warm_player_ranked_wr_battles_population_correlation

        cache.clear()

        Player.objects.create(
            name="PublishedFallbackRankedHeatmap",
            player_id=8846,
            is_hidden=False,
            ranked_updated_at=timezone.now(),
            ranked_json=[
                {"season_id": 10, "total_battles": 120,
                    "total_wins": 72, "win_rate": 0.6, "top_ship_name": None},
            ],
        )

        warm_player_ranked_wr_battles_population_correlation()
        cache.delete(_player_correlation_cache_key("ranked_wr_battles:v6"))

        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/8846/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "ranked_wr_battles")
        self.assertEqual(payload["tracked_population"], 1)
        self.assertTrue(payload["tiles"])
        self.assertEqual(payload["player_point"]["x"], 120.0)
        self.assertEqual(payload["player_point"]["y"], 60.0)
        self.assertIsNotNone(
            cache.get(_player_correlation_published_cache_key("ranked_wr_battles:v6")))
        mock_queue_refresh.assert_called_once_with(realm='na')

    @patch("warships.tasks.queue_player_ranked_wr_battles_correlation_refresh")
    def test_ranked_wr_battles_cold_cache_returns_200_with_pending_header(self, mock_queue_refresh):
        cache.clear()

        Player.objects.create(
            name="ColdCachePendingPlayer",
            player_id=8850,
            is_hidden=False,
            ranked_updated_at=timezone.now(),
            ranked_json=[],
        )

        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/8850/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Ranked-WR-Battles-Pending"], "true")
        payload = response.json()
        self.assertEqual(payload["metric"], "ranked_wr_battles")
        self.assertEqual(payload["tracked_population"], 0)
        self.assertEqual(payload["tiles"], [])
        self.assertIsNone(payload["player_point"])
        mock_queue_refresh.assert_called()

    @patch("warships.data.fetch_player_ranked_wr_battles_correlation", side_effect=RuntimeError("db timeout"))
    def test_ranked_wr_battles_exception_returns_200_with_pending_header(self, mock_fetch):
        Player.objects.create(
            name="ErrorPlayer",
            player_id=8851,
            is_hidden=False,
            ranked_updated_at=timezone.now(),
            ranked_json=[],
        )

        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/8851/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Ranked-WR-Battles-Pending"], "true")
        payload = response.json()
        self.assertEqual(payload["tracked_population"], 0)
        self.assertEqual(payload["tiles"], [])

    def test_player_correlation_distribution_returns_404_for_missing_tier_type_player(self):
        response = self.client.get(
            "/api/fetch/player_correlation/tier_type/999999/")

        self.assertEqual(response.status_code, 404)

    def test_player_correlation_distribution_rejects_unknown_metric(self):
        response = self.client.get("/api/fetch/player_correlation/not-real/")

        self.assertEqual(response.status_code, 404)

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
    def setUp(self):
        # Redis (the CI cache) is not transactional — clear it between tests.
        cache.clear()

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

    @patch("warships.views.fetch_randoms_data")
    def test_randoms_data_all_uses_cached_randoms_rows_when_battles_json_missing(self, mock_fetch_randoms_data):
        now = timezone.now()
        Player.objects.create(
            name="RandomsFallback",
            player_id=654,
            battles_json=None,
            randoms_json=[
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
            randoms_updated_at=now,
        )
        mock_fetch_randoms_data.return_value = [
            {
                "ship_name": "Fallback Ship",
                "ship_chart_name": "Fallback Ship",
                "ship_type": "Cruiser",
                "ship_tier": 8,
                "pvp_battles": 31,
                "win_ratio": 0.58,
                "wins": 18,
            }
        ]

        response = self.client.get("/api/fetch/randoms_data/654/?all=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["ship_name"], "Fallback Ship")
        self.assertIn("X-Randoms-Updated-At", response)
        mock_fetch_randoms_data.assert_called_once_with("654", realm='na')

    @patch("warships.views.is_clan_battle_summary_refresh_pending", return_value=True)
    @patch("warships.tasks.queue_clan_battle_summary_refresh")
    def test_clan_battle_seasons_flags_pending_refresh_on_empty_cache(self, mock_queue_refresh, _mock_pending):
        Clan.objects.create(clan_id=42, name="PendingClan",
                            tag="PC", members_count=0)

        response = self.client.get("/api/fetch/clan_battle_seasons/42/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response["X-Clan-Battles-Pending"], "true")
        mock_queue_refresh.assert_called_once_with("42", realm='na')

    @patch("warships.views.fetch_clan_plot_data", return_value=[])
    def test_clan_data_flags_pending_refresh_on_empty_plot_warmup(self, _mock_fetch_plot):
        clan = Clan.objects.create(
            clan_id=43,
            name="PendingPlotClan",
            tag="PPC",
            members_count=2,
            last_fetch=None,
        )
        Player.objects.create(
            name="PlotMember",
            player_id=4301,
            clan=clan,
            pvp_battles=120,
            pvp_ratio=54.0,
        )

        response = self.client.get("/api/fetch/clan_data/43:active")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response["X-Clan-Plot-Pending"], "true")

    @patch("warships.views.fetch_player_clan_battle_seasons")
    def test_player_clan_battle_seasons_returns_serialized_rows(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "season_id": 32,
                "season_name": "Northern Waters",
                "season_label": "S32",
                "start_date": "2025-11-01",
                "end_date": "2025-12-15",
                "ship_tier_min": 10,
                "ship_tier_max": 10,
                "battles": 48,
                "wins": 27,
                "losses": 21,
                "win_rate": 56.3,
            }
        ]

        response = self.client.get(
            "/api/fetch/player_clan_battle_seasons/777/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["season_label"], "S32")
        self.assertEqual(payload[0]["battles"], 48)
        self.assertEqual(payload[0]["win_rate"], 56.3)

    @patch("warships.views.fetch_player_clan_battle_seasons")
    def test_player_clan_battle_seasons_accepts_no_trailing_slash(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "season_id": 33,
                "season_name": "Storm Front",
                "season_label": "S33",
                "start_date": "2026-01-01",
                "end_date": "2026-02-15",
                "ship_tier_min": 10,
                "ship_tier_max": 10,
                "battles": 12,
                "wins": 7,
                "losses": 5,
                "win_rate": 58.3,
            }
        ]

        response = self.client.get(
            "/api/fetch/player_clan_battle_seasons/777")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["season_label"], "S33")
        mock_fetch.assert_called_once_with(
            "777", realm='na', allow_remote_fetch=False)

    @patch("warships.views.logger.exception")
    @patch("warships.views.fetch_player_clan_battle_seasons", side_effect=RuntimeError("boom"))
    def test_player_clan_battle_seasons_logs_player_context_on_failure(self, _mock_fetch, mock_logger_exception):
        clan = Clan.objects.create(
            clan_id=778, name="LogClan", tag="LOG", members_count=1)
        Player.objects.create(name="LogPlayer", player_id=7781, clan=clan)

        with self.assertRaises(RuntimeError):
            self.client.get("/api/fetch/player_clan_battle_seasons/7781/")

        mock_logger_exception.assert_called_once_with(
            'Player clan battle seasons endpoint failed for player_id=%s player_name=%s clan_id=%s clan_name=%s',
            '7781',
            'LogPlayer',
            clan.clan_id,
            'LogClan',
        )

    @patch("warships.tasks.queue_clan_battle_data_refresh")
    @patch("warships.data._fetch_clan_battle_season_stats")
    def test_player_clan_battle_seasons_cold_cache_queues_async_and_flags_pending(
        self, mock_remote_fetch, mock_queue_refresh,
    ):
        # Cold cache must serve [] without a synchronous WG call, queue an
        # async refresh, and flag X-Clan-Battle-Seasons-Pending so the FE polls.
        Player.objects.create(name="ColdCBPlayer", player_id=9001, realm='na')

        response = self.client.get(
            "/api/fetch/player_clan_battle_seasons/9001/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response["X-Clan-Battle-Seasons-Pending"], "true")
        # No synchronous WG fetch on the request path.
        mock_remote_fetch.assert_not_called()
        mock_queue_refresh.assert_called_once_with("9001", realm='na')

    @patch("warships.tasks.queue_clan_battle_data_refresh")
    @patch("warships.data._fetch_clan_battle_season_stats")
    def test_player_clan_battle_seasons_genuinely_empty_does_not_flag_pending(
        self, mock_remote_fetch, mock_queue_refresh,
    ):
        # A player already fetched and genuinely empty (warm cache holding [])
        # must not poll/re-queue forever.
        from warships.data import _player_clan_battle_season_cache_key
        cache.set(_player_clan_battle_season_cache_key(9002, realm='na'), [])
        Player.objects.create(name="EmptyCBPlayer", player_id=9002, realm='na')

        response = self.client.get(
            "/api/fetch/player_clan_battle_seasons/9002/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertNotIn("X-Clan-Battle-Seasons-Pending", response)
        mock_remote_fetch.assert_not_called()
        mock_queue_refresh.assert_not_called()

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

    @patch("warships.views.is_ranked_data_refresh_pending", return_value=True)
    @patch("warships.views.fetch_ranked_data", return_value=[])
    def test_ranked_data_flags_pending_when_empty_refresh_is_in_flight(self, mock_fetch_ranked_data, _mock_pending):
        Player.objects.create(
            name="PendingRankedPlayer",
            player_id=778,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(days=2),
        )

        response = self.client.get("/api/fetch/ranked_data/778/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response["X-Ranked-Pending"], "true")
        self.assertIn("X-Ranked-Updated-At", response)
        mock_fetch_ranked_data.assert_called_once_with("778", realm='na')

    @patch("warships.views.is_ranked_data_refresh_pending", return_value=True)
    @patch("warships.views.fetch_ranked_data")
    def test_ranked_data_flags_pending_even_when_stale_payload_returned(self, mock_fetch_ranked_data, _mock_pending):
        # Regression: when cache is stale but populated, fetch_ranked_data serves the
        # stale payload and queues a background refresh. The header must still flag
        # the pending refresh so the client polls for the updated payload — otherwise
        # users only see fresh numbers after a hard reload.
        mock_fetch_ranked_data.return_value = [
            {
                "season_id": 1025,
                "season_name": "Season 25",
                "season_label": "Season 25",
                "start_date": "2025-12-01",
                "end_date": "2026-01-15",
                "highest_league": 1,
                "highest_league_name": "Gold",
                "total_battles": 100,
                "total_wins": 55,
                "win_rate": 0.55,
                "top_ship_name": "Stalingrad",
                "best_sprint": None,
                "sprints": [],
            }
        ]
        Player.objects.create(
            name="StaleRankedPlayer",
            player_id=779,
            ranked_json=mock_fetch_ranked_data.return_value,
            ranked_updated_at=timezone.now() - timedelta(hours=2),
        )

        response = self.client.get("/api/fetch/ranked_data/779/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response["X-Ranked-Pending"], "true")

    @patch("warships.views._fetch_player_id_by_name", return_value=None)
    def test_missing_player_lookup_uses_standard_drf_error_shape(self, _mock_lookup):
        response = self.client.get("/api/player/PlayerThatWillNeverExist/")

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertIn("detail", payload)
        self.assertEqual(payload.get("status_code"), 404)

    @patch("warships.views._fetch_player_id_by_name", return_value=None)
    def test_missing_player_lookup_uses_negative_cache_after_first_miss(self, mock_lookup):
        cache_key = _missing_player_lookup_cache_key(
            "PlayerThatWillNeverExist")
        cache.delete(cache_key)

        first_response = self.client.get(
            "/api/player/PlayerThatWillNeverExist/")
        second_response = self.client.get(
            "/api/player/PlayerThatWillNeverExist/")

        self.assertEqual(first_response.status_code, 404)
        self.assertEqual(second_response.status_code, 404)
        self.assertTrue(cache.get(cache_key))
        mock_lookup.assert_called_once_with(
            "PlayerThatWillNeverExist", realm='na')


class StreamerSubmissionViewTests(TestCase):
    URL = '/api/streamer-submissions/'

    def setUp(self):
        cache.clear()

    def _payload(self, **overrides):
        payload = {
            'ign': 'bfk_ferlyfe',
            'realm': 'na',
            'twitch_handle': 'bfk_fer1yfe',
            'twitch_url': 'https://www.twitch.tv/bfk_fer1yfe',
            'website': '',
            'form_loaded_at': 1,  # ancient timestamp passes the > 2s gate
        }
        payload.update(overrides)
        return payload

    def test_happy_path_creates_pending_submission(self):
        from warships.models import StreamerSubmission
        response = self.client.post(
            self.URL, data=self._payload(), content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(StreamerSubmission.objects.count(), 1)
        sub = StreamerSubmission.objects.first()
        self.assertEqual(sub.status, StreamerSubmission.STATUS_PENDING)
        self.assertEqual(sub.ign, 'bfk_ferlyfe')
        self.assertEqual(sub.twitch_handle, 'bfk_fer1yfe')

    def test_honeypot_trips(self):
        from warships.models import StreamerSubmission
        response = self.client.post(
            self.URL,
            data=self._payload(website='spamspam'),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StreamerSubmission.objects.count(), 0)

    def test_url_handle_mismatch_rejected(self):
        from warships.models import StreamerSubmission
        response = self.client.post(
            self.URL,
            data=self._payload(twitch_url='https://www.twitch.tv/someoneelse'),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StreamerSubmission.objects.count(), 0)

    def test_too_fast_submission_rejected(self):
        import time as _time
        from warships.models import StreamerSubmission
        now_ms = int(_time.time() * 1000)
        response = self.client.post(
            self.URL,
            data=self._payload(form_loaded_at=now_ms),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StreamerSubmission.objects.count(), 0)


class PlayerLiveRefreshSignalTests(TestCase):
    """Live-update contract: the player-detail response advertises whether a
    visit-driven refresh is pending and when the next one is allowed, so the
    client can poll-to-rehydrate and render the cooldown countdown.
    See runbook-live-update-cooldown-2026-05-27.md.
    """

    def setUp(self):
        cache.clear()

    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.tasks.update_battle_data_task.delay")
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_fresh_player_reports_not_pending_with_next_refresh(self, *_mocks):
        now = timezone.now()
        Player.objects.create(
            name="FreshLivePlayer", player_id=77001, realm="na",
            last_fetch=now, battles_updated_at=now,
            pvp_battles=1000, is_hidden=False,
        )
        response = self.client.get("/api/player/FreshLivePlayer/?realm=na")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Player-Refresh-Pending"], "false")
        next_refresh = int(response["X-Player-Next-Refresh"])
        expected = int((now + timedelta(minutes=15)).timestamp())
        self.assertAlmostEqual(next_refresh, expected, delta=5)

    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.tasks.update_battle_data_task.delay")
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_stale_player_reports_pending(self, *_mocks):
        stale = timezone.now() - timedelta(minutes=20)
        Player.objects.create(
            name="StaleLivePlayer", player_id=77002, realm="na",
            last_fetch=stale, battles_updated_at=stale,
            pvp_battles=1000, is_hidden=False,
        )
        response = self.client.get("/api/player/StaleLivePlayer/?realm=na")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Player-Refresh-Pending"], "true")

    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.tasks.update_battle_data_task.delay")
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_never_fetched_player_reports_pending(self, *_mocks):
        Player.objects.create(
            name="NeverLivePlayer", player_id=77003, realm="na",
            battles_updated_at=None, pvp_battles=1000, is_hidden=False,
        )
        response = self.client.get("/api/player/NeverLivePlayer/?realm=na")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Player-Refresh-Pending"], "true")

    @patch.dict("os.environ", {"BATTLESTATS_DISABLE_LIVE_REFRESH": "1"})
    @patch("warships.tasks.queue_ranked_data_refresh")
    @patch("warships.tasks.update_battle_data_task.delay")
    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_live_refresh_can_be_disabled_for_local_stale_snapshots(self, *_mocks):
        stale = timezone.now() - timedelta(days=30)
        Player.objects.create(
            name="LocalStaleLivePlayer", player_id=77005, realm="na",
            last_fetch=stale, battles_updated_at=stale,
            pvp_battles=1000, is_hidden=False,
        )
        response = self.client.get(
            "/api/player/LocalStaleLivePlayer/?realm=na")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Player-Refresh-Pending"], "false")

    def test_invalidate_player_detail_cache_clears_bulk_cache(self):
        # The rehydrate-on-poll loop depends on update_battle_data busting the
        # player-detail bulk cache so the next poll serves fresh stats.
        from warships.data import (
            _bulk_cache_key_player, get_cached_player_detail,
            invalidate_player_detail_cache,
        )
        Player.objects.create(
            name="CacheBustPlayer", player_id=77004, realm="na", pvp_battles=10)
        cache.set(_bulk_cache_key_player(77004, realm="na"),
                  {"name": "CacheBustPlayer"})
        self.assertIsNotNone(get_cached_player_detail(77004, realm="na"))
        invalidate_player_detail_cache(77004, realm="na")
        self.assertIsNone(get_cached_player_detail(77004, realm="na"))


class SitemapEntitiesTests(TestCase):
    """The sitemap must not emit hidden / dead-end player URLs (thin content)."""

    def setUp(self):
        cache.clear()

    def _visit(self, pid, name, views):
        EntityVisitDaily.objects.create(
            date=timezone.now().date(),
            entity_type='player',
            entity_id=pid,
            realm='na',
            entity_name_snapshot=name,
            views_deduped=views,
            views_raw=views,
            unique_visitors=1,
        )

    def test_sitemap_excludes_hidden_and_missing_players(self):
        now = timezone.now()
        # Visible player — should appear.
        Player.objects.create(
            name="VisiblePlayer", player_id=88001, realm="na",
            last_fetch=now, pvp_battles=1000, is_hidden=False)
        # Hidden player ranked ABOVE the visible one by views — must be dropped
        # despite outranking, proving the filter isn't just a tail trim.
        Player.objects.create(
            name="HiddenPlayer", player_id=88002, realm="na",
            last_fetch=now, pvp_battles=1000, is_hidden=True)
        # Visited id with no Player row (deleted / never ingested) — must drop.

        self._visit(88002, "HiddenPlayer", views=50)   # top by views
        self._visit(88001, "VisiblePlayer", views=20)
        self._visit(88003, "GhostPlayer", views=10)     # no Player row

        response = self.client.get("/api/sitemap-entities/")
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.json()["players"]]

        self.assertIn("VisiblePlayer", names)
        self.assertNotIn("HiddenPlayer", names)
        self.assertNotIn("GhostPlayer", names)

    def test_sitemap_drops_low_view_and_old_visits(self):
        now = timezone.now()
        Player.objects.create(
            name="OnlyOnce", player_id=88010, realm="na",
            last_fetch=now, pvp_battles=10, is_hidden=False)
        Player.objects.create(
            name="StaleVisit", player_id=88011, realm="na",
            last_fetch=now, pvp_battles=10, is_hidden=False)
        # Below the views_deduped>=2 floor.
        self._visit(88010, "OnlyOnce", views=1)
        # Visited outside the 30-day window.
        EntityVisitDaily.objects.create(
            date=(now - timedelta(days=45)).date(),
            entity_type='player', entity_id=88011, realm='na',
            entity_name_snapshot="StaleVisit", views_deduped=99, views_raw=99,
            unique_visitors=1)

        response = self.client.get("/api/sitemap-entities/")
        names = [p["name"] for p in response.json()["players"]]
        self.assertNotIn("OnlyOnce", names)
        self.assertNotIn("StaleVisit", names)


class RecordClanLookupDebounceTests(TestCase):
    """`_record_clan_lookup` runs on every clan read (incl. each hydration poll
    and each response-cache hit), so it must not re-write `last_lookup` unless it
    has actually gone stale past the debounce interval."""

    def setUp(self):
        cache.clear()

    def test_records_when_never_looked_up(self):
        from warships.views import _record_clan_lookup
        clan = Clan.objects.create(
            clan_id=9701, name="NeverLookedUp", members_count=1,
            last_fetch=timezone.now(), last_lookup=None)
        _record_clan_lookup(clan, realm="na")
        self.assertIsNotNone(clan.last_lookup)

    def test_skips_within_interval(self):
        from warships.views import _record_clan_lookup
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=9702, name="RecentlyLookedUp", members_count=1,
            last_fetch=now, last_lookup=now)
        with patch.object(clan, "save") as mock_save:
            _record_clan_lookup(clan, realm="na")
        # Within the debounce window: no write.
        mock_save.assert_not_called()
        self.assertEqual(clan.last_lookup, now)

    def test_records_again_once_stale(self):
        from warships.views import _record_clan_lookup, CLAN_LOOKUP_RECORD_INTERVAL
        stale = timezone.now() - CLAN_LOOKUP_RECORD_INTERVAL - timedelta(minutes=1)
        clan = Clan.objects.create(
            clan_id=9703, name="StaleLookup", members_count=1,
            last_fetch=timezone.now(), last_lookup=stale)
        _record_clan_lookup(clan, realm="na")
        self.assertGreater(clan.last_lookup, stale)
        # An immediate repeat is now debounced (no new write).
        recorded = clan.last_lookup
        with patch.object(clan, "save") as mock_save:
            _record_clan_lookup(clan, realm="na")
        mock_save.assert_not_called()
        self.assertEqual(clan.last_lookup, recorded)


class ClanMemberBadgesCacheTests(TestCase):
    """Badges are recomputed only nightly, so the hydration poll loop must not
    re-run the 3-query bulk fetch on every poll — `_clan_member_badges_cached`
    caches per clan, keyed on the member-pk set so a roster change rotates it."""

    def setUp(self):
        cache.clear()

    @patch("warships.data.get_players_ship_badges_bulk")
    def test_cached_across_calls_same_roster(self, mock_bulk):
        from warships.views import _clan_member_badges_cached
        mock_bulk.return_value = {101: [{"ship_id": 1, "rank": 1}]}

        first = _clan_member_badges_cached("4242", "na", [101, 102, 103])
        second = _clan_member_badges_cached("4242", "na", [103, 101, 102])  # reordered

        # Underlying bulk fetch ran once; the second (poll) call hit cache.
        mock_bulk.assert_called_once()
        self.assertEqual(first, second)

    @patch("warships.data.get_players_ship_badges_bulk")
    def test_roster_change_rotates_key(self, mock_bulk):
        from warships.views import _clan_member_badges_cached
        mock_bulk.return_value = {}

        _clan_member_badges_cached("4242", "na", [101, 102])
        _clan_member_badges_cached("4242", "na", [101, 102, 104])  # new member joined

        # Different roster -> different cache key -> recompute.
        self.assertEqual(mock_bulk.call_count, 2)
