from unittest.mock import patch
from datetime import datetime, timedelta
from kombu.exceptions import OperationalError as KombuOperationalError

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from warships.landing import LANDING_CLANS_BEST_CACHE_KEY, LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY, LANDING_CLANS_CACHE_KEY, LANDING_CLANS_PUBLISHED_CACHE_KEY, LANDING_PLAYER_LIMIT, LANDING_RECENT_CLANS_CACHE_KEY, LANDING_RECENT_PLAYERS_CACHE_KEY, LANDING_RECENT_PLAYERS_DIRTY_KEY, landing_best_clan_cache_key, landing_best_clan_published_cache_key, landing_player_cache_key, landing_player_published_cache_key, warm_landing_page_content
from warships.models import Player, Clan, PlayerExplorerSummary, realm_cache_key
from warships.views import PUBLIC_API_THROTTLES, landing_players, _missing_player_lookup_cache_key


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
    def test_player_detail_suppresses_stale_efficiency_rank_fields(
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
        self.assertIsNone(payload["efficiency_rank_percentile"])
        self.assertIsNone(payload["efficiency_rank_tier"])
        self.assertFalse(payload["has_efficiency_rank_icon"])
        self.assertIsNone(payload["efficiency_rank_population_size"])
        self.assertIsNone(payload["efficiency_rank_updated_at"])
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
    @patch("warships.views.queue_landing_best_entity_warm")
    def test_landing_best_warmup_queues_background_warm(self, mock_queue):
        mock_queue.return_value = {"status": "queued"}

        response = self.client.get("/api/landing/warm-best/")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "queued"})
        mock_queue.assert_called_once_with(
            player_limit=LANDING_PLAYER_LIMIT,
            clan_limit=LANDING_PLAYER_LIMIT,
            realm='na',
        )

    @patch("warships.views.queue_landing_best_entity_warm")
    def test_landing_best_warmup_returns_skip_status_without_error(self, mock_queue):
        mock_queue.return_value = {
            "status": "skipped", "reason": "already-queued"}

        response = self.client.get("/api/landing/warm-best/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
                         "status": "skipped", "reason": "already-queued"})

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

    @patch("warships.views.update_clan_members_task.delay")
    @patch("warships.views.update_clan_data_task.delay")
    @patch("warships.views.update_player_data_task.delay")
    def test_player_lookup_invalidates_recent_players_cache(
        self,
        _mock_update_player_task,
        _mock_update_clan_task,
        _mock_update_clan_members_task,
    ):
        now = timezone.now()
        clan = Clan.objects.create(
            clan_id=953,
            name="CacheClan",
            members_count=1,
            last_fetch=now,
        )
        Player.objects.create(
            name="CacheLookupPlayer",
            player_id=9053,
            clan=clan,
            last_fetch=now,
            last_lookup=None,
        )
        cache.set(LANDING_RECENT_PLAYERS_CACHE_KEY,
                  [{'name': 'stale'}], 60)

        response = self.client.get("/api/player/CacheLookupPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache.get(LANDING_RECENT_PLAYERS_CACHE_KEY), [
            {'name': 'stale'}])
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY)))

    def test_player_cache_hit_still_updates_last_lookup_and_invalidates_recent(self):
        """When the player detail cache is pre-populated, the cache-hit path
        should still bump last_lookup and mark the recent-players cache dirty
        so newly viewed players propagate to the landing page."""
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

        cache.set(LANDING_RECENT_PLAYERS_CACHE_KEY, [{'name': 'stale'}], 60)

        request_started_at = timezone.now()
        response = self.client.get("/api/player/CacheHitPlayer/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get('X-Player-Cache'), 'hit')
        # last_lookup should have been bumped even on cache hit
        player.refresh_from_db()
        self.assertIsNotNone(player.last_lookup)
        self.assertGreaterEqual(player.last_lookup, request_started_at)
        # Dirty flag should be set
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY)))

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

    @patch("warships.data.update_ranked_data")
    def test_ranked_endpoint_sync_hydration_uses_request_realm(
        self,
        mock_update_ranked_data,
    ):
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
        mock_update_ranked_data.assert_called_once_with(
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
                             "is_hidden": False,
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

    def test_clan_members_marks_ranked_players_with_over_100_ranked_battles(self):
        clan = Clan.objects.create(
            clan_id=81,
            name="Ranked Clan",
            members_count=3,
        )
        Player.objects.create(
            name="RankedMain",
            player_id=8101,
            clan=clan,
            ranked_json=[
                {"season_id": 1, "total_battles": 65,
                    "total_wins": 35, "win_rate": 53.85, "highest_league": 1, "highest_league_name": "Gold"},
                {"season_id": 2, "total_battles": 45,
                    "total_wins": 20, "win_rate": 44.44, "highest_league": 2, "highest_league_name": "Silver"},
            ],
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="RankedDabbler",
            player_id=8102,
            clan=clan,
            ranked_json=[
                {"season_id": 3, "total_battles": 100,
                    "total_wins": 55, "win_rate": 55.0, "highest_league": 2, "highest_league_name": "Silver"},
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
                "RankedMain": True,
                "RankedDabbler": False,
                "NoRanked": False,
            },
        )
        self.assertEqual(
            {row["name"]: row["highest_ranked_league"]
                for row in response.json()},
            {
                "RankedMain": "Gold",
                "RankedDabbler": "Silver",
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
        cache.clear()
        today = timezone.now().date()
        Player.objects.create(
            name="HiddenLandingPlayer",
            player_id=4242,
            is_hidden=True,
            pvp_ratio=61.5,
            pvp_battles=900,
            days_since_last_battle=2,
            last_battle_date=today,
        )
        Player.objects.create(
            name="VisibleLandingPlayer",
            player_id=4243,
            is_hidden=False,
            pvp_ratio=58.2,
            pvp_battles=950,
            days_since_last_battle=1,
            last_battle_date=today,
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
            shifted = datetime(absolute_month // 12,
                               (absolute_month % 12) + 1, 15)
            if settings.USE_TZ:
                return timezone.make_aware(shifted)
            return shifted

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
                    player_id=4800 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=55,
                    last_battle_date=now.date() - timedelta(days=55),
                )
                Player.objects.create(
                    name=f"PriorDormantA-{month_delta}",
                    player_id=4801 + (month_delta + 11) * 10,
                    is_hidden=False,
                    creation_date=creation_date,
                    days_since_last_battle=130,
                    last_battle_date=now.date() - timedelta(days=130),
                )
                Player.objects.create(
                    name=f"PriorDormantB-{month_delta}",
                    player_id=4802 + (month_delta + 11) * 10,
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
            creation_date=(
                timezone.make_aware(
                    datetime(current_month_start.year,
                             current_month_start.month, 15)
                )
                if settings.USE_TZ
                else datetime(current_month_start.year, current_month_start.month, 15)
            ),
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

    def test_landing_players_best_mode_uses_composite_strength_not_raw_wr(self):
        cache.clear()
        today = timezone.now().date()
        composite_leader = Player.objects.create(
            name="LandingCompositeLeader",
            player_id=4301,
            is_hidden=False,
            pvp_ratio=63.0,
            pvp_battles=3200,
            last_battle_date=today - timedelta(days=3),
            battles_json=[
                {"ship_tier": 10, "pvp_battles": 3200, "wins": 2016},
            ],
        )
        raw_wr_leader = Player.objects.create(
            name="LandingRawWrLeader",
            player_id=4302,
            is_hidden=False,
            pvp_ratio=69.0,
            pvp_battles=3300,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 3300, "wins": 2277},
            ],
        )
        trailing_player = Player.objects.create(
            name="LandingTrailingPlayer",
            player_id=4303,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=3400,
            last_battle_date=today - timedelta(days=1),
            battles_json=[
                {"ship_tier": 9, "pvp_battles": 3400, "wins": 1972},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=composite_leader,
            player_score=8.8,
            shrunken_efficiency_strength=0.95,
            latest_ranked_battles=28,
            highest_ranked_league_recent="Gold",
        )
        PlayerExplorerSummary.objects.create(
            player=raw_wr_leader,
            player_score=3.1,
        )
        PlayerExplorerSummary.objects.create(
            player=trailing_player,
            player_score=5.2,
            shrunken_efficiency_strength=0.44,
        )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:3]],
            ["LandingCompositeLeader", "LandingRawWrLeader", "LandingTrailingPlayer"],
        )

    def test_landing_players_best_mode_excludes_low_tier_specialists(self):
        cache.clear()
        today = timezone.now().date()
        Player.objects.create(
            name="LandingLowTierFarmer",
            player_id=4304,
            is_hidden=False,
            pvp_ratio=92.0,
            pvp_battles=3100,
            last_battle_date=today - timedelta(days=2),
            battles_json=[
                {"ship_tier": 3, "pvp_battles": 3100, "wins": 2852},
            ],
        )
        competitive_player = Player.objects.create(
            name="LandingCompetitivePlayer",
            player_id=4305,
            is_hidden=False,
            pvp_ratio=64.5,
            pvp_battles=3200,
            last_battle_date=today - timedelta(days=1),
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 3200, "wins": 2064},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=competitive_player,
            player_score=8.1,
        )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()]
        self.assertIn("LandingCompetitivePlayer", names)
        self.assertNotIn("LandingLowTierFarmer", names)

    def test_landing_players_exposes_high_tier_record_excluding_low_tiers(self):
        cache.clear()
        today = timezone.now().date()
        Player.objects.create(
            name="LandingTierFilter",
            player_id=4311,
            is_hidden=False,
            pvp_ratio=70.0,
            pvp_battles=3500,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 2, "pvp_battles": 1800, "wins": 1500},
                {"ship_tier": 4, "pvp_battles": 900, "wins": 650},
                {"ship_tier": 5, "pvp_battles": 1200, "wins": 630},
                {"ship_tier": 10, "pvp_battles": 600, "wins": 330},
            ],
        )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        row = next(
            item for item in payload if item["name"] == "LandingTierFilter")
        self.assertEqual(row["high_tier_pvp_battles"], 1800)
        self.assertEqual(row["high_tier_pvp_ratio"], 53.33)

    def test_landing_players_best_mode_excludes_players_without_enough_high_tier_history(self):
        cache.clear()
        today = timezone.now().date()
        Player.objects.create(
            name="LandingInsufficientHighTier",
            player_id=4314,
            is_hidden=False,
            pvp_ratio=68.4,
            pvp_battles=4100,
            last_battle_date=today - timedelta(days=2),
            battles_json=[
                {"ship_tier": 3, "pvp_battles": 3700, "wins": 2664},
                {"ship_tier": 8, "pvp_battles": 400, "wins": 240},
            ],
        )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "LandingInsufficientHighTier",
            [row["name"] for row in response.json()],
        )

    def test_landing_players_best_mode_returns_all_eligible_players_up_to_limit(self):
        cache.clear()
        today = timezone.now().date()

        eligible_names = []
        for index in range(5):
            name = f"LandingBestCount{index}"
            eligible_names.append(name)
            Player.objects.create(
                name=name,
                player_id=4320 + index,
                is_hidden=False,
                pvp_ratio=60.0 + index,
                pvp_battles=3000 + (index * 100),
                days_since_last_battle=10 + index,
                last_battle_date=today - timedelta(days=10 + index),
                battles_json=[
                    {"ship_tier": 8, "pvp_battles": 3000 +
                        (index * 100), "wins": 1650 + (index * 70)},
                ],
            )

        Player.objects.create(
            name="LandingBestTooSmall",
            player_id=4330,
            is_hidden=False,
            pvp_ratio=72.0,
            pvp_battles=2200,
            days_since_last_battle=4,
            last_battle_date=today - timedelta(days=4),
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 2200, "wins": 1584},
            ],
        )
        Player.objects.create(
            name="LandingBestHidden",
            player_id=4331,
            is_hidden=True,
            pvp_ratio=75.0,
            pvp_battles=4800,
            days_since_last_battle=3,
            last_battle_date=today - timedelta(days=3),
            battles_json=[
                {"ship_tier": 9, "pvp_battles": 4800, "wins": 3600},
            ],
        )
        Player.objects.create(
            name="LandingBestInactive",
            player_id=4332,
            is_hidden=False,
            pvp_ratio=77.0,
            pvp_battles=4900,
            days_since_last_battle=240,
            last_battle_date=today - timedelta(days=240),
            battles_json=[
                {"ship_tier": 10, "pvp_battles": 4900, "wins": 3773},
            ],
        )
        Player.objects.create(
            name="LandingBestLowTierOnly",
            player_id=4333,
            is_hidden=False,
            pvp_ratio=88.0,
            pvp_battles=4000,
            days_since_last_battle=2,
            last_battle_date=today - timedelta(days=2),
            battles_json=[
                {"ship_tier": 4, "pvp_battles": 4000, "wins": 3520},
            ],
        )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 5)
        self.assertEqual({row["name"] for row in payload}, set(eligible_names))

    def test_landing_players_best_mode_caps_results_to_requested_limit(self):
        cache.clear()
        today = timezone.now().date()

        for index in range(45):
            Player.objects.create(
                name=f"LandingBestLimit{index:02d}",
                player_id=4340 + index,
                is_hidden=False,
                pvp_ratio=70.0 - (index * 0.1),
                pvp_battles=4000 + index,
                days_since_last_battle=5,
                last_battle_date=today - timedelta(days=(index % 7)),
                battles_json=[
                    {"ship_tier": 8, "pvp_battles": 4000 +
                        index, "wins": 2600 - (index * 4)},
                ],
            )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), LANDING_PLAYER_LIMIT)

    def test_landing_players_expose_cache_expiry_headers(self):
        cache.clear()
        today = timezone.now().date()
        Player.objects.create(
            name="LandingCacheHeaders",
            player_id=4389,
            is_hidden=False,
            pvp_ratio=61.5,
            pvp_battles=3600,
            days_since_last_battle=2,
            last_battle_date=today - timedelta(days=2),
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 3600, "wins": 2214},
            ],
        )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Landing-Players-Cache-Mode"], "best")
        self.assertEqual(
            response["X-Landing-Players-Cache-TTL-Seconds"],
            "21600",
        )
        self.assertTrue(response["X-Landing-Players-Cache-Cached-At"])
        self.assertTrue(response["X-Landing-Players-Cache-Expires-At"])

    @patch("warships.views.get_landing_players_payload_with_cache_metadata")
    def test_landing_best_players_passes_sort_through_to_backend(self, mock_cached_payload):
        mock_cached_payload.return_value = (
            [{"name": "SortedPlayer"}],
            {
                "ttl_seconds": 21600,
                "cached_at": "now",
                "expires_at": "later",
            },
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=wr&limit=25")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Landing-Players-Cache-Sort"], "wr")
        mock_cached_payload.assert_called_once_with(
            mode='best',
            limit=25,
            realm='na',
            sort='wr',
        )

    def test_landing_best_players_reject_invalid_sort(self):
        response = self.client.get(
            "/api/landing/players/?mode=best&sort=invalid")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'detail': 'sort must be one of: overall, ranked, efficiency, wr, cb'})

    def test_landing_players_best_ranked_sort_orders_by_ranked_strength(self):
        cache.clear()
        today = timezone.now().date()
        gold_leader = Player.objects.create(
            name="LandingRankedGoldLeader",
            player_id=4391,
            is_hidden=False,
            pvp_ratio=55.0,
            pvp_battles=4200,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 4200, "wins": 2310},
            ],
            ranked_json=[
                {"season_id": 3, "total_battles": 38, "total_wins": 21,
                    "win_rate": 55.26, "highest_league": 1, "highest_league_name": "Gold"},
                {"season_id": 2, "total_battles": 30, "total_wins": 18,
                    "win_rate": 60.0, "highest_league": 1, "highest_league_name": "Gold"},
                {"season_id": 1, "total_battles": 24, "total_wins": 14,
                    "win_rate": 58.33, "highest_league": 2, "highest_league_name": "Silver"},
            ],
        )
        wr_runner = Player.objects.create(
            name="LandingRankedWRRunner",
            player_id=4392,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=4100,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 9, "pvp_battles": 4100, "wins": 2419},
            ],
            ranked_json=[
                {"season_id": 2, "total_battles": 20, "total_wins": 14, "win_rate": 70.0,
                    "highest_league": 1, "highest_league_name": "Gold"},
                {"season_id": 1, "total_battles": 16, "total_wins": 11, "win_rate": 68.75,
                    "highest_league": 2, "highest_league_name": "Silver"},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=gold_leader,
            player_score=6.0,
            latest_ranked_battles=38,
            highest_ranked_league_recent="Gold",
            ranked_seasons_participated=3,
        )
        PlayerExplorerSummary.objects.create(
            player=wr_runner,
            player_score=5.0,
            latest_ranked_battles=20,
            highest_ranked_league_recent="Gold",
            ranked_seasons_participated=2,
        )
        bronze_grinder = Player.objects.create(
            name="LandingRankedBronzeGrinder",
            player_id=4395,
            is_hidden=False,
            pvp_ratio=67.0,
            pvp_battles=5200,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 10, "pvp_battles": 5200, "wins": 3484},
            ],
            ranked_json=[
                {"season_id": 1, "total_battles": 40, "total_wins": 27,
                    "win_rate": 67.5, "highest_league": 3, "highest_league_name": "Bronze"},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=bronze_grinder,
            player_score=9.8,
            latest_ranked_battles=40,
            highest_ranked_league_recent="Bronze",
            ranked_seasons_participated=1,
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=ranked&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:3]],
            [
                "LandingRankedGoldLeader",
                "LandingRankedWRRunner",
                "LandingRankedBronzeGrinder",
            ],
        )

    def test_landing_players_best_ranked_sort_breaks_gold_ties_by_ranked_wr(self):
        cache.clear()
        today = timezone.now().date()
        gold_grinder = Player.objects.create(
            name="LandingGoldGrinder",
            player_id=4396,
            is_hidden=False,
            pvp_ratio=55.0,
            pvp_battles=4500,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 4500, "wins": 2475},
            ],
            ranked_json=[
                {"season_id": 2, "total_battles": 40, "total_wins": 22,
                    "win_rate": 55.0, "highest_league": 1, "highest_league_name": "Gold"},
                {"season_id": 1, "total_battles": 18, "total_wins": 10,
                    "win_rate": 55.56, "highest_league": 2, "highest_league_name": "Silver"},
            ],
        )
        gold_ace = Player.objects.create(
            name="LandingGoldAce",
            player_id=4397,
            is_hidden=False,
            pvp_ratio=62.0,
            pvp_battles=4200,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 9, "pvp_battles": 4200, "wins": 2604},
            ],
            ranked_json=[
                {"season_id": 2, "total_battles": 14, "total_wins": 9,
                    "win_rate": 64.29, "highest_league": 1, "highest_league_name": "Gold"},
                {"season_id": 1, "total_battles": 12, "total_wins": 8,
                    "win_rate": 66.67, "highest_league": 2, "highest_league_name": "Silver"},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=gold_grinder,
            player_score=4.0,
            latest_ranked_battles=40,
            highest_ranked_league_recent="Gold",
            ranked_seasons_participated=2,
        )
        PlayerExplorerSummary.objects.create(
            player=gold_ace,
            player_score=8.0,
            latest_ranked_battles=14,
            highest_ranked_league_recent="Gold",
            ranked_seasons_participated=2,
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=ranked&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:2]],
            ["LandingGoldAce", "LandingGoldGrinder"],
        )

    def test_landing_players_best_ranked_sort_uses_silver_after_gold_and_wr_ties(self):
        cache.clear()
        today = timezone.now().date()
        silver_stacker = Player.objects.create(
            name="LandingSilverStacker",
            player_id=4398,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=4600,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 4600, "wins": 2668},
            ],
            ranked_json=[
                {"season_id": 3, "total_battles": 10, "total_wins": 6,
                    "win_rate": 60.0, "highest_league": 2, "highest_league_name": "Silver"},
                {"season_id": 2, "total_battles": 10, "total_wins": 6,
                    "win_rate": 60.0, "highest_league": 2, "highest_league_name": "Silver"},
                {"season_id": 1, "total_battles": 10, "total_wins": 6,
                    "win_rate": 60.0, "highest_league": 2, "highest_league_name": "Silver"},
            ],
        )
        bronze_stacker = Player.objects.create(
            name="LandingBronzeStacker",
            player_id=4399,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=4300,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 9, "pvp_battles": 4300, "wins": 2494},
            ],
            ranked_json=[
                {"season_id": 3, "total_battles": 10, "total_wins": 6,
                    "win_rate": 60.0, "highest_league": 3, "highest_league_name": "Bronze"},
                {"season_id": 2, "total_battles": 10, "total_wins": 6,
                    "win_rate": 60.0, "highest_league": 3, "highest_league_name": "Bronze"},
                {"season_id": 1, "total_battles": 10, "total_wins": 6,
                    "win_rate": 60.0, "highest_league": 3, "highest_league_name": "Bronze"},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=silver_stacker,
            player_score=6.5,
            latest_ranked_battles=10,
            highest_ranked_league_recent="Silver",
            ranked_seasons_participated=3,
        )
        PlayerExplorerSummary.objects.create(
            player=bronze_stacker,
            player_score=6.5,
            latest_ranked_battles=10,
            highest_ranked_league_recent="Bronze",
            ranked_seasons_participated=3,
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=ranked&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:2]],
            ["LandingSilverStacker", "LandingBronzeStacker"],
        )

    def test_landing_players_best_wr_sort_orders_by_high_tier_wr(self):
        cache.clear()
        today = timezone.now().date()
        wr_leader = Player.objects.create(
            name="LandingWrLeader",
            player_id=4393,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=4300,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 3000, "wins": 1980},
                {"ship_tier": 4, "pvp_battles": 1300, "wins": 845},
            ],
        )
        wr_runner = Player.objects.create(
            name="LandingWrRunner",
            player_id=4394,
            is_hidden=False,
            pvp_ratio=61.0,
            pvp_battles=4300,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 9, "pvp_battles": 3000, "wins": 1890},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=wr_leader, player_score=5.5)
        PlayerExplorerSummary.objects.create(
            player=wr_runner, player_score=8.2)

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=wr&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:2]],
            ["LandingWrLeader", "LandingWrRunner"],
        )
        self.assertEqual(response.json()[0]["pvp_ratio"], 66.0)

    def test_landing_players_best_cb_sort_orders_by_cb_profile(self):
        cache.clear()
        today = timezone.now().date()
        cb_leader = Player.objects.create(
            name="LandingCbLeader",
            player_id=4395,
            is_hidden=False,
            pvp_ratio=57.0,
            pvp_battles=5000,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 5000, "wins": 2850},
            ],
        )
        cb_runner = Player.objects.create(
            name="LandingCbRunner",
            player_id=4396,
            is_hidden=False,
            pvp_ratio=60.0,
            pvp_battles=5000,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 5000, "wins": 2900},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=cb_leader,
            player_score=4.5,
            clan_battle_total_battles=180,
            clan_battle_seasons_participated=6,
            clan_battle_overall_win_rate=64.0,
        )
        PlayerExplorerSummary.objects.create(
            player=cb_runner,
            player_score=8.0,
            clan_battle_total_battles=80,
            clan_battle_seasons_participated=2,
            clan_battle_overall_win_rate=58.0,
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=cb&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:2]],
            ["LandingCbLeader", "LandingCbRunner"],
        )

    def test_landing_players_best_cb_sort_prefers_larger_sample_for_same_wr(self):
        cache.clear()
        today = timezone.now().date()

        durable_player = Player.objects.create(
            name="LandingCbDurable",
            player_id=4397,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=7000,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 7000, "wins": 4060},
            ],
        )
        streaky_player = Player.objects.create(
            name="LandingCbStreaky",
            player_id=4398,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=7000,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 7000, "wins": 4060},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=durable_player,
            player_score=5.0,
            clan_battle_total_battles=2000,
            clan_battle_seasons_participated=8,
            clan_battle_overall_win_rate=60.0,
        )
        PlayerExplorerSummary.objects.create(
            player=streaky_player,
            player_score=8.0,
            clan_battle_total_battles=200,
            clan_battle_seasons_participated=8,
            clan_battle_overall_win_rate=60.0,
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=cb&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:2]],
            ["LandingCbDurable", "LandingCbStreaky"],
        )

    def test_landing_players_best_cb_sort_penalizes_tiny_sample_outlier(self):
        cache.clear()
        today = timezone.now().date()

        durable_player = Player.objects.create(
            name="LandingCbHugeSample",
            player_id=4399,
            is_hidden=False,
            pvp_ratio=57.0,
            pvp_battles=8000,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 8000, "wins": 4560},
            ],
        )
        outlier_player = Player.objects.create(
            name="LandingCbTinyOutlier",
            player_id=4400,
            is_hidden=False,
            pvp_ratio=62.0,
            pvp_battles=8000,
            days_since_last_battle=0,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 8000, "wins": 4960},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=durable_player,
            player_score=5.5,
            clan_battle_total_battles=4000,
            clan_battle_seasons_participated=9,
            clan_battle_overall_win_rate=59.0,
        )
        PlayerExplorerSummary.objects.create(
            player=outlier_player,
            player_score=9.0,
            clan_battle_total_battles=40,
            clan_battle_seasons_participated=2,
            clan_battle_overall_win_rate=65.0,
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=cb&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:2]],
            ["LandingCbHugeSample", "LandingCbTinyOutlier"],
        )

    def test_landing_clans_expose_cache_expiry_headers(self):
        cache.clear()
        clan = Clan.objects.create(
            clan_id=4390,
            name="LandingClanCacheHeaders",
            tag="LCH",
            members_count=4,
        )
        for index in range(4):
            Player.objects.create(
                name=f"LandingClanCachePlayer{index}",
                player_id=439000 + index,
                clan=clan,
                pvp_battles=30000,
                pvp_wins=16000,
                days_since_last_battle=2,
            )

        response = self.client.get("/api/landing/clans/?mode=best")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Landing-Clans-Cache-Mode"], "best")
        self.assertEqual(response["X-Landing-Clans-Cache-Sort"], "overall")
        self.assertEqual(
            response["X-Landing-Clans-Cache-TTL-Seconds"],
            "21600",
        )
        self.assertTrue(response["X-Landing-Clans-Cache-Cached-At"])
        self.assertTrue(response["X-Landing-Clans-Cache-Expires-At"])

    @patch("warships.views.get_landing_best_clans_payload_with_cache_metadata")
    def test_landing_best_clans_passes_sort_through_to_backend(self, mock_cached_payload):
        mock_cached_payload.return_value = (
            [{"name": "SortedClan"}],
            {
                "ttl_seconds": 21600,
                "cached_at": "now",
                "expires_at": "later",
            },
        )

        response = self.client.get(
            "/api/landing/clans/?mode=best&sort=wr&limit=25")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Landing-Clans-Cache-Sort"], "wr")
        mock_cached_payload.assert_called_once_with(realm='na', sort='wr')

    def test_landing_best_clans_reject_invalid_sort(self):
        response = self.client.get(
            "/api/landing/clans/?mode=best&sort=invalid")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
                         'detail': 'sort must be one of: overall, wr, cb'})

    @patch("warships.views.get_landing_clans_payload_with_cache_metadata")
    def test_landing_random_clans_use_cached_headers(self, mock_cached_payload):
        mock_cached_payload.return_value = (
            [{"name": "CachedClan"}],
            {
                "ttl_seconds": 21600,
                "cached_at": "now",
                "expires_at": "later",
            },
        )

        response = self.client.get("/api/landing/clans/?mode=random&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Landing-Clans-Cache-Mode"], "random")
        self.assertEqual(
            response["X-Landing-Clans-Cache-TTL-Seconds"], "21600")
        self.assertEqual(response["X-Landing-Clans-Cache-Cached-At"], "now")
        self.assertEqual(response["X-Landing-Clans-Cache-Expires-At"], "later")
        self.assertNotIn("X-Landing-Queue-Type", response)

    def test_landing_clans_support_gzip_for_large_json_payloads(self):
        cache.clear()
        for index in range(120):
            clan = Clan.objects.create(
                clan_id=5000 + index,
                name=f"LandingGzipClan{index}",
                tag=f"G{index}",
                members_count=12,
                cached_clan_wr=53.0,
                cached_total_battles=120000,
                cached_active_member_count=10,
            )
            for p in range(12):
                Player.objects.create(
                    name=f"LandingGzipPlayer{index}_{p}",
                    player_id=700000 + index * 100 + p,
                    clan=clan,
                    pvp_battles=120000,
                    pvp_wins=62000,
                    days_since_last_battle=3,
                )

        response = self.client.get(
            "/api/landing/clans/?mode=best",
            HTTP_ACCEPT_ENCODING="gzip",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Encoding"), "gzip")

    def test_landing_players_best_efficiency_sort_orders_by_efficiency_percentile(self):
        cache.clear()
        now = timezone.now()
        today = now.date()
        lower_tiebreak = Player.objects.create(
            name="LandingSigmaTieLow",
            player_id=4360,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=3200,
            days_since_last_battle=5,
            last_battle_date=today - timedelta(days=5),
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        leader = Player.objects.create(
            name="LandingSigmaLeader",
            player_id=4361,
            is_hidden=False,
            pvp_ratio=57.0,
            pvp_battles=3300,
            days_since_last_battle=2,
            last_battle_date=today - timedelta(days=2),
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        higher_tiebreak = Player.objects.create(
            name="LandingSigmaTieHigh",
            player_id=4362,
            is_hidden=False,
            pvp_ratio=60.0,
            pvp_battles=3400,
            days_since_last_battle=3,
            last_battle_date=today - timedelta(days=3),
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        PlayerExplorerSummary.objects.create(
            player=lower_tiebreak,
            player_score=6.1,
            efficiency_rank_percentile=0.91,
            efficiency_rank_tier='I',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=367,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )
        PlayerExplorerSummary.objects.create(
            player=leader,
            player_score=4.2,
            efficiency_rank_percentile=0.97,
            efficiency_rank_tier='E',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=367,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )
        PlayerExplorerSummary.objects.create(
            player=higher_tiebreak,
            player_score=8.9,
            efficiency_rank_percentile=0.91,
            efficiency_rank_tier='I',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=367,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=efficiency&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:3]],
            ["LandingSigmaLeader", "LandingSigmaTieHigh", "LandingSigmaTieLow"],
        )

    def test_landing_players_best_efficiency_sort_excludes_hidden_and_unpublished_players(self):
        cache.clear()
        now = timezone.now()
        today = now.date()
        visible = Player.objects.create(
            name="LandingSigmaVisible",
            player_id=4363,
            is_hidden=False,
            pvp_ratio=59.0,
            pvp_battles=3200,
            days_since_last_battle=3,
            last_battle_date=today - timedelta(days=3),
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        hidden = Player.objects.create(
            name="LandingSigmaHidden",
            player_id=4364,
            is_hidden=True,
            pvp_ratio=61.0,
            pvp_battles=3300,
            days_since_last_battle=2,
            last_battle_date=today - timedelta(days=2),
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        stale = Player.objects.create(
            name="LandingSigmaStale",
            player_id=4365,
            is_hidden=False,
            pvp_ratio=58.0,
            pvp_battles=3400,
            days_since_last_battle=4,
            last_battle_date=today - timedelta(days=4),
            efficiency_updated_at=now,
            battles_updated_at=now,
        )
        unpublished = Player.objects.create(
            name="LandingSigmaUnpublished",
            player_id=4366,
            is_hidden=False,
            pvp_ratio=57.0,
            pvp_battles=3500,
            days_since_last_battle=1,
            last_battle_date=today - timedelta(days=1),
        )
        PlayerExplorerSummary.objects.create(
            player=visible,
            player_score=7.7,
            efficiency_rank_percentile=0.95,
            efficiency_rank_tier='I',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=367,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )
        PlayerExplorerSummary.objects.create(
            player=hidden,
            player_score=9.1,
            efficiency_rank_percentile=0.98,
            efficiency_rank_tier='E',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=367,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )
        PlayerExplorerSummary.objects.create(
            player=stale,
            player_score=8.4,
            efficiency_rank_percentile=0.96,
            efficiency_rank_tier='E',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=367,
            efficiency_rank_updated_at=now - timedelta(hours=3),
        )
        PlayerExplorerSummary.objects.create(
            player=unpublished,
            player_score=8.0,
            efficiency_rank_percentile=None,
            efficiency_rank_tier=None,
            has_efficiency_rank_icon=False,
            efficiency_rank_population_size=None,
            efficiency_rank_updated_at=None,
        )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=efficiency&limit=40")

        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()]
        # Visible and "stale" both have valid percentiles — landing surfaces
        # tolerate input-data drift. Hidden and unpublished are excluded.
        self.assertIn("LandingSigmaVisible", names)
        self.assertIn("LandingSigmaStale", names)
        self.assertNotIn("LandingSigmaHidden", names)
        self.assertNotIn("LandingSigmaUnpublished", names)

    def test_landing_players_best_efficiency_sort_caps_results_to_requested_limit(self):
        cache.clear()
        now = timezone.now()
        today = now.date()

        for index in range(45):
            player = Player.objects.create(
                name=f"LandingSigmaLimit{index:02d}",
                player_id=4370 + index,
                is_hidden=False,
                pvp_ratio=60.0 - (index * 0.1),
                pvp_battles=3200 + index,
                days_since_last_battle=5,
                last_battle_date=today - timedelta(days=(index % 7)),
                efficiency_updated_at=now - timedelta(hours=3),
                battles_updated_at=now - timedelta(hours=3),
            )
            PlayerExplorerSummary.objects.create(
                player=player,
                player_score=9.0 - (index * 0.05),
                efficiency_rank_percentile=0.99 - (index * 0.001),
                efficiency_rank_tier='E' if index < 11 else 'I',
                has_efficiency_rank_icon=True,
                efficiency_rank_population_size=367,
                efficiency_rank_updated_at=now - timedelta(hours=1),
            )

        response = self.client.get(
            "/api/landing/players/?mode=best&sort=efficiency&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), LANDING_PLAYER_LIMIT)

    def test_landing_players_only_include_recently_active_players(self):
        cache.clear()
        today = timezone.now().date()
        Player.objects.create(
            name="LandingActivePlayer",
            player_id=4312,
            is_hidden=False,
            pvp_ratio=54.0,
            pvp_battles=3200,
            days_since_last_battle=90,
            last_battle_date=today - timedelta(days=90),
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 3200, "wins": 1664},
            ],
        )
        Player.objects.create(
            name="LandingInactivePlayer",
            player_id=4313,
            is_hidden=False,
            pvp_ratio=61.0,
            pvp_battles=6400,
            days_since_last_battle=220,
            last_battle_date=today - timedelta(days=220),
            battles_json=[
                {"ship_tier": 10, "pvp_battles": 6400, "wins": 3904},
            ],
        )

        response = self.client.get(
            "/api/landing/players/?mode=random&limit=40")

        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()]
        self.assertIn("LandingActivePlayer", names)
        self.assertNotIn("LandingInactivePlayer", names)

    def test_landing_recent_players_orders_by_last_lookup_desc(self):
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
            ["RecentNoScore", "RecentLowScore", "RecentHighScore"],
        )

    def test_landing_players_and_recent_players_expose_clan_battle_enjoyer_from_cache(self):
        cache.clear()
        today = timezone.now().date()
        looked_up_at = timezone.now() - timedelta(minutes=1)
        player = Player.objects.create(
            name="LandingClanBattleMain",
            player_id=4410,
            is_hidden=False,
            pvp_ratio=57.0,
            total_battles=4200,
            pvp_battles=3800,
            last_battle_date=today,
            last_lookup=looked_up_at,
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            player_score=7.4,
            clan_battle_total_battles=44,
            clan_battle_seasons_participated=2,
            clan_battle_overall_win_rate=56.8,
            clan_battle_summary_updated_at=timezone.now(),
        )

        landing_response = self.client.get(
            "/api/landing/players/?mode=random&limit=40")
        recent_response = self.client.get("/api/landing/recent/")

        self.assertEqual(landing_response.status_code, 200)
        self.assertEqual(recent_response.status_code, 200)

        landing_row = next(
            row for row in landing_response.json() if row["name"] == "LandingClanBattleMain"
        )
        recent_row = next(
            row for row in recent_response.json() if row["name"] == "LandingClanBattleMain"
        )

        self.assertTrue(landing_row["is_clan_battle_player"])
        self.assertEqual(landing_row["clan_battle_win_rate"], 56.8)
        self.assertTrue(recent_row["is_clan_battle_player"])
        self.assertEqual(recent_row["clan_battle_win_rate"], 56.8)

    def test_landing_players_and_recent_players_prefer_durable_clan_battle_summary_when_cache_missing(self):
        cache.clear()
        today = timezone.now().date()
        looked_up_at = timezone.now() - timedelta(minutes=1)
        player = Player.objects.create(
            name="LandingClanBattleDurable",
            player_id=4411,
            is_hidden=False,
            pvp_ratio=57.0,
            total_battles=4200,
            pvp_battles=3800,
            last_battle_date=today,
            last_lookup=looked_up_at,
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            player_score=7.4,
            clan_battle_total_battles=44,
            clan_battle_seasons_participated=2,
            clan_battle_overall_win_rate=56.8,
            clan_battle_summary_updated_at=timezone.now() - timedelta(minutes=5),
        )

        landing_response = self.client.get(
            "/api/landing/players/?mode=random&limit=40")
        recent_response = self.client.get("/api/landing/recent/")

        self.assertEqual(landing_response.status_code, 200)
        self.assertEqual(recent_response.status_code, 200)

        landing_row = next(
            row for row in landing_response.json() if row["name"] == "LandingClanBattleDurable"
        )
        recent_row = next(
            row for row in recent_response.json() if row["name"] == "LandingClanBattleDurable"
        )

        self.assertTrue(landing_row["is_clan_battle_player"])
        self.assertEqual(landing_row["clan_battle_win_rate"], 56.8)
        self.assertTrue(recent_row["is_clan_battle_player"])
        self.assertEqual(recent_row["clan_battle_win_rate"], 56.8)

    def test_landing_players_and_recent_players_use_streamlined_pve_rule(self):
        cache.clear()
        today = timezone.now().date()
        looked_up_at = timezone.now() - timedelta(minutes=1)
        Player.objects.create(
            name="LandingPveYes",
            player_id=4412,
            is_hidden=False,
            pvp_ratio=56.0,
            total_battles=9576,
            pvp_battles=3111,
            days_since_last_battle=7,
            last_battle_date=today,
            last_lookup=looked_up_at,
        )
        Player.objects.create(
            name="LandingPveNoHighAbsolute",
            player_id=4413,
            is_hidden=False,
            pvp_ratio=67.0,
            total_battles=23851,
            pvp_battles=19629,
            days_since_last_battle=3,
            last_battle_date=today,
            last_lookup=looked_up_at - timedelta(minutes=1),
        )

        landing_response = self.client.get(
            "/api/landing/players/?mode=random&limit=40")
        recent_response = self.client.get("/api/landing/recent/")

        self.assertEqual(landing_response.status_code, 200)
        self.assertEqual(recent_response.status_code, 200)

        landing_rows = {row["name"]: row["is_pve_player"] for row in landing_response.json(
        ) if row["name"] in {"LandingPveYes", "LandingPveNoHighAbsolute"}}
        recent_rows = {row["name"]: row["is_pve_player"] for row in recent_response.json(
        ) if row["name"] in {"LandingPveYes", "LandingPveNoHighAbsolute"}}

        self.assertEqual(landing_rows, {
            "LandingPveYes": True,
            "LandingPveNoHighAbsolute": False,
        })
        self.assertEqual(recent_rows, {
            "LandingPveYes": True,
            "LandingPveNoHighAbsolute": False,
        })

    def test_landing_players_and_recent_players_expose_published_efficiency_fields(self):
        cache.clear()
        now = timezone.now()
        today = now.date()
        expert = Player.objects.create(
            name="LandingEfficiencyExpert",
            player_id=4414,
            is_hidden=False,
            pvp_ratio=59.0,
            total_battles=6200,
            pvp_battles=5400,
            days_since_last_battle=4,
            last_battle_date=today,
            last_lookup=now - timedelta(minutes=2),
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        grade_two = Player.objects.create(
            name="LandingEfficiencyGradeTwo",
            player_id=4415,
            is_hidden=False,
            pvp_ratio=56.0,
            total_battles=5400,
            pvp_battles=4700,
            days_since_last_battle=2,
            last_battle_date=today,
            last_lookup=now - timedelta(minutes=1),
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        hidden_recent = Player.objects.create(
            name="LandingEfficiencyHidden",
            player_id=4416,
            is_hidden=True,
            pvp_ratio=55.0,
            total_battles=5100,
            pvp_battles=4300,
            days_since_last_battle=1,
            last_battle_date=today,
            last_lookup=now,
            efficiency_updated_at=now - timedelta(hours=3),
            battles_updated_at=now - timedelta(hours=3),
        )
        PlayerExplorerSummary.objects.create(
            player=expert,
            efficiency_rank_percentile=0.97,
            efficiency_rank_tier='E',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=367,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )
        PlayerExplorerSummary.objects.create(
            player=grade_two,
            efficiency_rank_percentile=0.81,
            efficiency_rank_tier='II',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=124,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )
        PlayerExplorerSummary.objects.create(
            player=hidden_recent,
            efficiency_rank_percentile=0.99,
            efficiency_rank_tier='E',
            has_efficiency_rank_icon=True,
            efficiency_rank_population_size=500,
            efficiency_rank_updated_at=now - timedelta(hours=1),
        )

        landing_response = self.client.get(
            "/api/landing/players/?mode=random&limit=40")
        recent_response = self.client.get("/api/landing/recent/")

        self.assertEqual(landing_response.status_code, 200)
        self.assertEqual(recent_response.status_code, 200)

        landing_rows = {
            row["name"]: row
            for row in landing_response.json()
            if row["name"] in {"LandingEfficiencyExpert", "LandingEfficiencyGradeTwo"}
        }
        recent_rows = {
            row["name"]: row
            for row in recent_response.json()
            if row["name"] in {"LandingEfficiencyExpert", "LandingEfficiencyGradeTwo", "LandingEfficiencyHidden"}
        }

        self.assertEqual(
            landing_rows["LandingEfficiencyExpert"]["efficiency_rank_tier"], "E")
        self.assertEqual(
            landing_rows["LandingEfficiencyExpert"]["efficiency_rank_percentile"], 0.97)
        self.assertTrue(
            landing_rows["LandingEfficiencyExpert"]["has_efficiency_rank_icon"])
        self.assertEqual(
            landing_rows["LandingEfficiencyExpert"]["efficiency_rank_population_size"], 367)
        self.assertIsNotNone(
            landing_rows["LandingEfficiencyExpert"]["efficiency_rank_updated_at"])

        self.assertEqual(
            landing_rows["LandingEfficiencyGradeTwo"]["efficiency_rank_tier"], "II")
        self.assertEqual(
            landing_rows["LandingEfficiencyGradeTwo"]["efficiency_rank_percentile"], 0.81)
        self.assertTrue(
            landing_rows["LandingEfficiencyGradeTwo"]["has_efficiency_rank_icon"])

        self.assertEqual(
            recent_rows["LandingEfficiencyExpert"]["efficiency_rank_tier"], "E")
        self.assertEqual(
            recent_rows["LandingEfficiencyGradeTwo"]["efficiency_rank_tier"], "II")
        self.assertIsNone(
            recent_rows["LandingEfficiencyHidden"]["efficiency_rank_percentile"])
        self.assertIsNone(
            recent_rows["LandingEfficiencyHidden"]["efficiency_rank_tier"])
        self.assertFalse(
            recent_rows["LandingEfficiencyHidden"]["has_efficiency_rank_icon"])
        self.assertIsNone(
            recent_rows["LandingEfficiencyHidden"]["efficiency_rank_population_size"])
        self.assertIsNone(
            recent_rows["LandingEfficiencyHidden"]["efficiency_rank_updated_at"])

    def test_landing_players_reject_invalid_mode(self):
        response = self.client.get("/api/landing/players/?mode=invalid")

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

    def test_landing_recent_players_expose_sleepy_player_flag(self):
        cache.clear()
        now = timezone.now()
        Player.objects.create(
            name="RecentSleeper",
            player_id=4411,
            pvp_ratio=49.0,
            days_since_last_battle=500,
            last_lookup=now - timedelta(minutes=2),
        )

        response = self.client.get("/api/landing/recent/")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()
                   if item["name"] == "RecentSleeper")
        self.assertTrue(row["is_sleepy_player"])

    def test_warm_landing_page_content_populates_current_landing_cache_keys(self):
        cache.clear()

        with patch('warships.landing._build_best_landing_clans', return_value=[{
            'clan_id': 93251,
            'name': 'WarmLandingBestClan',
            'tag': 'WLBC',
            'members_count': 12,
            'clan_wr': 58.4,
            'total_battles': 180000,
            'active_members': 8,
            'is_clan_battle_active': False,
        }]):
            result = warm_landing_page_content()

        self.assertEqual(result['status'], 'completed')
        self.assertIsNotNone(
            cache.get(realm_cache_key('na', LANDING_CLANS_CACHE_KEY)))
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_CLANS_PUBLISHED_CACHE_KEY)))
        self.assertIsNotNone(
            cache.get(landing_best_clan_cache_key('overall')))
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY)))
        self.assertIsNotNone(cache.get(landing_best_clan_cache_key('wr')))
        self.assertIsNotNone(cache.get(landing_best_clan_cache_key('cb')))
        self.assertIsNotNone(
            cache.get(landing_best_clan_published_cache_key('wr')))
        self.assertIsNotNone(
            cache.get(landing_best_clan_published_cache_key('cb')))
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_CLANS_CACHE_KEY)))
        self.assertIsNotNone(
            cache.get(landing_player_cache_key('random', LANDING_PLAYER_LIMIT)))
        self.assertIsNotNone(
            cache.get(landing_player_published_cache_key('random', LANDING_PLAYER_LIMIT)))
        self.assertIsNotNone(
            cache.get(landing_player_cache_key('best', LANDING_PLAYER_LIMIT, sort='overall')))
        self.assertIsNotNone(
            cache.get(landing_player_published_cache_key('best', LANDING_PLAYER_LIMIT, sort='overall')))
        self.assertIsNotNone(
            cache.get(landing_player_cache_key('best', LANDING_PLAYER_LIMIT, sort='ranked')))
        self.assertIsNotNone(
            cache.get(landing_player_published_cache_key('best', LANDING_PLAYER_LIMIT, sort='ranked')))
        self.assertIsNotNone(
            cache.get(landing_player_cache_key('best', LANDING_PLAYER_LIMIT, sort='efficiency')))
        self.assertIsNotNone(
            cache.get(landing_player_published_cache_key('best', LANDING_PLAYER_LIMIT, sort='efficiency')))
        self.assertIsNotNone(
            cache.get(landing_player_cache_key('best', LANDING_PLAYER_LIMIT, sort='wr')))
        self.assertIsNotNone(
            cache.get(landing_player_published_cache_key('best', LANDING_PLAYER_LIMIT, sort='wr')))
        self.assertIsNotNone(
            cache.get(landing_player_cache_key('best', LANDING_PLAYER_LIMIT, sort='cb')))
        self.assertIsNotNone(
            cache.get(landing_player_published_cache_key('best', LANDING_PLAYER_LIMIT, sort='cb')))
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_CACHE_KEY)))

    def test_landing_recent_clans_orders_by_last_lookup_desc(self):
        cache.clear()
        now = timezone.now()
        old_clan = Clan.objects.create(
            clan_id=5401,
            name="OlderClan",
            tag="OLD",
            members_count=40,
            last_lookup=now - timedelta(hours=2),
        )
        mid_clan = Clan.objects.create(
            clan_id=5402,
            name="MidClan",
            tag="MID",
            members_count=35,
            last_lookup=now - timedelta(minutes=30),
        )
        new_clan = Clan.objects.create(
            clan_id=5403,
            name="NewestClan",
            tag="NEW",
            members_count=32,
            last_lookup=now - timedelta(minutes=5),
        )
        Player.objects.create(name="OldClanPlayer", player_id=6401,
                              clan=old_clan, pvp_wins=55, pvp_battles=100)
        Player.objects.create(name="MidClanPlayer", player_id=6402,
                              clan=mid_clan, pvp_wins=60, pvp_battles=100)
        Player.objects.create(name="NewClanPlayer", player_id=6403,
                              clan=new_clan, pvp_wins=65, pvp_battles=100)

        response = self.client.get("/api/landing/recent-clans/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:3]],
            ["NewestClan", "MidClan", "OlderClan"],
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
        self.assertEqual(response.json()["player_score"], 3.22)

    def test_player_detail_exposes_highest_ranked_league_from_history(self):
        now = timezone.now()
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
        self.assertEqual(response.json()["highest_ranked_league"], "Gold")

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
        self.assertEqual(response.json()["player_score"], 3.22)

        player.refresh_from_db()
        self.assertEqual(player.explorer_summary.kill_ratio, 0.78)
        self.assertEqual(player.explorer_summary.player_score, 3.22)

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
        self.assertEqual(payload["x_label"], "Win Rate")
        self.assertEqual(payload["y_label"], "Survival Rate")
        self.assertEqual(payload["tracked_population"], 2)
        self.assertTrue(payload["correlation"]
                        is None or payload["correlation"] > 0)
        self.assertEqual(payload["x_domain"], {
            "min": 35.0,
            "max": 75.0,
            "bin_width": 1.0,
        })
        self.assertEqual(payload["y_domain"], {
            "min": 15.0,
            "max": 75.0,
            "bin_width": 1.5,
        })
        self.assertTrue(any(
            tile["x_index"] == 17 and tile["y_index"] == 12 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(
            tile["x_index"] == 23 and tile["y_index"] == 18 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(all("x_min" not in tile for tile in payload["tiles"]))
        self.assertTrue(
            any(point["x_index"] == 17 and point["count"] == 1 for point in payload["trend"]))
        self.assertTrue(
            any(point["x_index"] == 23 and point["count"] == 1 for point in payload["trend"]))
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

    def test_players_explorer_defaults_to_player_score_desc(self):
        now = timezone.now()
        Player.objects.create(
            name="ExplorerDefaultAlpha",
            player_id=9110,
            is_hidden=False,
            total_battles=3000,
            pvp_ratio=53.0,
            pvp_battles=1700,
            pvp_survival_rate=36.0,
            creation_date=now - timedelta(days=250),
            days_since_last_battle=5,
            activity_json=[
                {"date": "2026-03-06", "battles": 2, "wins": 1},
            ],
            battles_json=[
                {"ship_name": "Ship A", "ship_type": "Destroyer",
                    "ship_tier": 10, "pvp_battles": 18, "kdr": 1.2},
            ],
            ranked_json=[],
        )
        Player.objects.create(
            name="ExplorerDefaultBravo",
            player_id=9111,
            is_hidden=False,
            total_battles=4000,
            pvp_ratio=57.0,
            pvp_battles=1600,
            pvp_survival_rate=45.0,
            creation_date=now - timedelta(days=320),
            days_since_last_battle=2,
            activity_json=[
                {"date": "2026-03-08", "battles": 7, "wins": 5},
                {"date": "2026-03-09", "battles": 5, "wins": 4},
            ],
            battles_json=[
                {"ship_name": "Ship B", "ship_type": "Cruiser",
                    "ship_tier": 9, "pvp_battles": 20, "kdr": 1.4},
            ],
            ranked_json=[],
        )

        response = self.client.get("/api/players/explorer/?q=ExplorerDefault")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["results"][0]["name"], "ExplorerDefaultBravo")
        self.assertEqual(payload["results"][1]["name"], "ExplorerDefaultAlpha")

    def test_players_explorer_builds_only_requested_page_rows(self):
        from warships.data import build_player_summary as real_build_player_summary

        now = timezone.now()
        players = [
            Player.objects.create(
                name=f"ExplorerPaged{name}",
                player_id=player_id,
                is_hidden=False,
                pvp_ratio=50.0 + index,
                pvp_battles=1000 + index,
                pvp_survival_rate=35.0 + index,
                creation_date=now - timedelta(days=100 + index),
                days_since_last_battle=index,
            )
            for index, (name, player_id) in enumerate([
                ("Alpha", 9120),
                ("Bravo", 9121),
                ("Charlie", 9122),
            ], start=1)
        ]
        for index, player in enumerate(players, start=1):
            PlayerExplorerSummary.objects.create(
                player=player,
                player_score=float(index),
                ships_played_total=index,
                ranked_seasons_participated=0,
            )

        with patch("warships.data.build_player_summary", wraps=real_build_player_summary) as mock_build_player_summary:
            response = self.client.get(
                "/api/players/explorer/?q=ExplorerPaged&sort=name&direction=asc&page=2&page_size=1"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 3)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["name"], "ExplorerPagedBravo")
        self.assertEqual(mock_build_player_summary.call_count, 1)

    @patch("warships.views.fetch_player_explorer_page")
    def test_players_explorer_reuses_cached_response_payload(self, mock_fetch_player_explorer_page):
        cache.clear()
        mock_fetch_player_explorer_page.return_value = (
            1,
            [{
                "name": "ExplorerCacheCaptain",
                "player_id": 9801,
                "is_hidden": False,
                "days_since_last_battle": 2,
                "pvp_ratio": 55.2,
                "pvp_battles": 4000,
                "pvp_survival_rate": 41.5,
                "account_age_days": 365,
                "kill_ratio": 1.22,
                "player_score": 6.3,
                "battles_last_29_days": 12,
                "active_days_last_29_days": 4,
                "ships_played_total": 8,
                "ranked_seasons_participated": 2,
            }],
        )

        first = self.client.get(
            "/api/players/explorer/?q=ExplorerCacheCaptain&page_size=5")
        second = self.client.get(
            "/api/players/explorer/?q=ExplorerCacheCaptain&page_size=5")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first["X-Players-Explorer-Cache"], "miss")
        self.assertEqual(second["X-Players-Explorer-Cache"], "hit")
        self.assertEqual(first.json(), second.json())
        mock_fetch_player_explorer_page.assert_called_once()

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
        cache.delete(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY))
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

    def test_landing_recent_clans_orders_by_last_lookup_desc_and_limits_to_40(self):
        cache.delete(LANDING_RECENT_CLANS_CACHE_KEY)
        now = timezone.now()

        for index in range(45):
            Clan.objects.create(
                clan_id=20000 + index,
                name=f"RecentClan{index}",
                tag=f"R{index}",
                last_lookup=now - timedelta(minutes=index),
            )

        response = self.client.get("/api/landing/recent-clans/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 40)
        self.assertEqual(payload[0]["name"], "RecentClan0")
        self.assertEqual(payload[39]["name"], "RecentClan39")

    def test_clan_data_rejects_invalid_filter_type(self):
        response = self.client.get("/api/fetch/clan_data/42:invalid")

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

    def test_randoms_data_for_missing_player_returns_empty_list(self):
        response = self.client.get("/api/fetch/randoms_data/999999999/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @override_settings(SECRET_KEY="test-secret", ENABLE_AGENTIC_RUNTIME=True)
    @patch("warships.views._get_agentic_trace_dashboard_data")
    def test_agentic_trace_dashboard_endpoint_returns_summary(self, mock_get_agentic_trace_dashboard):
        cache.delete('agentic:trace_dashboard:v2')
        mock_get_agentic_trace_dashboard.return_value = {
            "project_name": "trace-lab",
            "tracing_enabled": True,
            "api_key_configured": True,
            "api_host": None,
            "recent_runs": [{
                "workflow_id": "run-1",
                "status": "completed",
                "design_review_passed": True,
                "api_review_required": True,
                "api_review_passed": True,
                "guidance_match_count": 2,
                "doctrine_note_count": 1,
            }],
            "diagnostics": {
                "total_runs": 1,
                "runs_with_doctrine": 1,
                "runs_with_guidance": 1,
                "design_review_fail_count": 0,
                "api_review_fail_count": 0,
                "reviewed_memory_total": 1,
            },
            "learning": {
                "recurring_issues": [],
                "common_guidance_paths": [{"label": "agents/runbooks/runbook-langgraph-opinionated-workflow.md", "count": 1}],
                "reviewed_store_paths": [{"label": "logs/agentic/memory/reviewed/battlestats__local__procedural.json", "count": 1}],
                "chart_tuning_notes": [{"slug": "ranked_wr_battles_heatmap"}],
            },
            "memory_store": {
                "backend": "file",
                "reviewed_total": 1,
                "pending_review_total": 0,
                "superseded_total": 0,
                "recent_reviewed": [{"memory_id": "mem-1", "summary": "Reuse trace validation commands."}],
                "recent_candidates": [],
            },
        }

        response = self.client.get("/api/agentic/traces/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_name"], "trace-lab")
        self.assertEqual(response.json()["diagnostics"]["total_runs"], 1)
        self.assertEqual(
            response.json()["diagnostics"]["runs_with_guidance"], 1)
        self.assertEqual(
            response.json()["diagnostics"]["reviewed_memory_total"], 1)
        self.assertTrue(
            response.json()["recent_runs"][0]["design_review_passed"])
        self.assertEqual(
            response.json()["memory_store"]["recent_reviewed"][0]["memory_id"], "mem-1")
        self.assertEqual(response.json()[
                         "learning"]["chart_tuning_notes"][0]["slug"], "ranked_wr_battles_heatmap")
        mock_get_agentic_trace_dashboard.assert_called_once_with(limit=12)

    def test_agentic_trace_dashboard_endpoint_returns_404_when_disabled(self):
        cache.delete('agentic:trace_dashboard:v2')

        response = self.client.get("/api/agentic/traces/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            "Agentic runtime is not enabled.",
        )

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
        mock_fetch.assert_called_once_with("777")

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
