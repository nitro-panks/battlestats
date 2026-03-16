from unittest.mock import patch
from datetime import datetime, timedelta
from kombu.exceptions import OperationalError as KombuOperationalError

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from warships.landing import LANDING_CLANS_CACHE_KEY, LANDING_RECENT_CLANS_CACHE_KEY, LANDING_RECENT_PLAYERS_CACHE_KEY, landing_player_cache_key, warm_landing_page_content
from warships.models import Player, Clan, PlayerExplorerSummary
from warships.views import PUBLIC_API_THROTTLES, landing_players


class PlayerViewSetTests(TestCase):
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
        self.assertIsNotNone(player.last_lookup)
        self.assertLess(
            abs((timezone.now() - player.last_lookup).total_seconds()), 5)

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
        self.assertIsNone(cache.get(LANDING_RECENT_PLAYERS_CACHE_KEY))

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
    def test_player_lookup_force_refreshes_when_efficiency_data_is_missing(
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
            battles_json=[{"ship_name": "Stub Ship", "pvp_battles": 25}],
            efficiency_json=None,
        )

        response = self.client.get("/api/player/EfficiencyGapPlayer/")

        self.assertEqual(response.status_code, 200)
        mock_update_player_data.assert_called_once_with(
            player=player, force_refresh=True)
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
        mock_update_player_task.assert_called_once_with(player_id=9002)
        mock_update_clan_task.assert_called_once_with(clan_id=901)
        mock_update_clan_members_task.assert_called_once_with(clan_id=901)


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
        self.queue_clan_battle_hydration_patcher = patch(
            "warships.data.queue_clan_battle_hydration",
            return_value={
                "pending_player_ids": set(),
                "queued_player_ids": set(),
                "deferred_player_ids": set(),
                "eligible_player_ids": set(),
                "max_in_flight": 8,
            },
        )
        self.mock_queue_clan_battle_hydration = self.queue_clan_battle_hydration_patcher.start()

    def tearDown(self):
        self.queue_clan_battle_hydration_patcher.stop()
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
                             "clan_battle_hydration_pending": False,
                             "highest_ranked_league": None,
                             "ranked_hydration_pending": False,
                             "ranked_updated_at": None,
                             "activity_bucket": "active_7d",
                         }])
        self.assertEqual(response["X-Ranked-Hydration-Queued"], "0")
        self.assertEqual(response["X-Ranked-Hydration-Deferred"], "0")
        self.assertEqual(response["X-Ranked-Hydration-Pending"], "0")
        self.assertEqual(response["X-Ranked-Hydration-Max-In-Flight"], "8")
        self.assertEqual(response["X-Clan-Battle-Hydration-Queued"], "0")
        self.assertEqual(response["X-Clan-Battle-Hydration-Deferred"], "0")
        self.assertEqual(response["X-Clan-Battle-Hydration-Pending"], "0")
        self.assertEqual(
            response["X-Clan-Battle-Hydration-Max-In-Flight"], "8")
        mock_update_clan_data.assert_not_called()
        mock_update_clan_members.assert_not_called()

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

    def test_clan_members_exposes_clan_battle_hydration_metadata(self):
        self.mock_queue_clan_battle_hydration.return_value = {
            "pending_player_ids": {7931},
            "queued_player_ids": {7931},
            "deferred_player_ids": set(),
            "eligible_player_ids": {7931, 7932},
            "max_in_flight": 8,
        }
        clan = Clan.objects.create(
            clan_id=793,
            name="Clan Battle Hydration Clan",
            members_count=2,
        )
        Player.objects.create(
            name="PendingClanBattleHydration",
            player_id=7931,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="FreshClanBattleHydration",
            player_id=7932,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )
        cache.set(
            "clan_battles:player:7932",
            [{"season_id": 34, "battles": 12, "wins": 7, "losses": 5}],
            300,
        )

        response = self.client.get("/api/fetch/clan_members/793/")

        self.assertEqual(response.status_code, 200)
        payload = {row["name"]: row for row in response.json()}
        self.assertTrue(payload["PendingClanBattleHydration"]
                        ["clan_battle_hydration_pending"])
        self.assertFalse(payload["FreshClanBattleHydration"]
                         ["clan_battle_hydration_pending"])
        self.assertEqual(response["X-Clan-Battle-Hydration-Queued"], "1")
        self.assertEqual(response["X-Clan-Battle-Hydration-Deferred"], "0")
        self.assertEqual(response["X-Clan-Battle-Hydration-Pending"], "1")
        self.assertEqual(
            response["X-Clan-Battle-Hydration-Max-In-Flight"], "8")

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

    def test_clan_members_marks_pve_players_from_updated_thresholds(self):
        clan = Clan.objects.create(
            clan_id=80,
            name="PvE Clan",
            members_count=5,
        )
        Player.objects.create(
            name="AboveSeventyFivePercent",
            player_id=8001,
            clan=clan,
            total_battles=1200,
            pvp_battles=600,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="BelowSeventyFivePercent",
            player_id=8002,
            clan=clan,
            total_battles=1200,
            pvp_battles=800,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="TooSmallSample",
            player_id=8003,
            clan=clan,
            total_battles=500,
            pvp_battles=40,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="HighAbsolutePvE",
            player_id=8004,
            clan=clan,
            total_battles=10000,
            pvp_battles=5500,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="ExactlySeventyFivePercent",
            player_id=8005,
            clan=clan,
            total_battles=1750,
            pvp_battles=1000,
            last_battle_date=timezone.now().date(),
        )

        response = self.client.get("/api/fetch/clan_members/80/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["name"]: row["is_pve_player"] for row in response.json()},
            {
                "AboveSeventyFivePercent": True,
                "BelowSeventyFivePercent": False,
                "TooSmallSample": False,
                "HighAbsolutePvE": True,
                "ExactlySeventyFivePercent": False,
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
        Player.objects.create(
            name="ClanBattleMain",
            player_id=8201,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )
        Player.objects.create(
            name="ClanBattleDabbler",
            player_id=8202,
            clan=clan,
            last_battle_date=timezone.now().date(),
        )
        cache.set(
            "clan_battles:player:8201",
            [
                {"season_id": 31, "battles": 24, "wins": 14, "losses": 10},
                {"season_id": 32, "battles": 20, "wins": 11, "losses": 9},
            ],
            300,
        )
        cache.set(
            "clan_battles:player:8202",
            [
                {"season_id": 33, "battles": 18, "wins": 9, "losses": 9},
            ],
            300,
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

    def test_landing_players_best_mode_orders_by_high_tier_wr_desc(self):
        cache.clear()
        today = timezone.now().date()
        high = Player.objects.create(
            name="LandingHighScore",
            player_id=4301,
            is_hidden=False,
            pvp_ratio=55.0,
            pvp_battles=3200,
            last_battle_date=today - timedelta(days=3),
            battles_json=[
                {"ship_tier": 10, "pvp_battles": 3200, "wins": 1760},
            ],
        )
        low = Player.objects.create(
            name="LandingLowScore",
            player_id=4302,
            is_hidden=False,
            pvp_ratio=53.0,
            pvp_battles=3100,
            last_battle_date=today,
            battles_json=[
                {"ship_tier": 8, "pvp_battles": 3100, "wins": 1550},
            ],
        )
        no_score = Player.objects.create(
            name="LandingNoScore",
            player_id=4303,
            is_hidden=False,
            pvp_ratio=57.0,
            pvp_battles=3300,
            last_battle_date=today - timedelta(days=1),
            battles_json=[
                {"ship_tier": 9, "pvp_battles": 3300, "wins": 1914},
            ],
        )
        PlayerExplorerSummary.objects.create(player=high, player_score=9.1)
        PlayerExplorerSummary.objects.create(player=low, player_score=4.2)

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.json()[:3]],
            ["LandingNoScore", "LandingHighScore", "LandingLowScore"],
        )

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

    def test_landing_players_best_mode_falls_back_to_overall_wr_when_high_tier_history_is_missing(self):
        cache.clear()
        today = timezone.now().date()
        Player.objects.create(
            name="LandingOverallFallback",
            player_id=4314,
            is_hidden=False,
            pvp_ratio=68.4,
            pvp_battles=4100,
            last_battle_date=today - timedelta(days=2),
            battles_json=None,
        )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        row = next(
            item for item in payload if item["name"] == "LandingOverallFallback")
        self.assertEqual(row["pvp_ratio"], 68.4)
        self.assertEqual(row["high_tier_pvp_battles"], 0)
        self.assertIsNone(row["high_tier_pvp_ratio"])

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
                battles_json=None,
            )

        Player.objects.create(
            name="LandingBestTooSmall",
            player_id=4330,
            is_hidden=False,
            pvp_ratio=72.0,
            pvp_battles=2200,
            days_since_last_battle=4,
            last_battle_date=today - timedelta(days=4),
        )
        Player.objects.create(
            name="LandingBestHidden",
            player_id=4331,
            is_hidden=True,
            pvp_ratio=75.0,
            pvp_battles=4800,
            days_since_last_battle=3,
            last_battle_date=today - timedelta(days=3),
        )
        Player.objects.create(
            name="LandingBestInactive",
            player_id=4332,
            is_hidden=False,
            pvp_ratio=77.0,
            pvp_battles=4900,
            days_since_last_battle=240,
            last_battle_date=today - timedelta(days=240),
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
                battles_json=None,
            )

        response = self.client.get("/api/landing/players/?mode=best&limit=40")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 40)

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
        PlayerExplorerSummary.objects.create(player=player, player_score=7.4)
        cache.set(
            "clan_battles:player:4410",
            [
                {"season_id": 40, "battles": 26, "wins": 16, "losses": 10},
                {"season_id": 41, "battles": 18, "wins": 9, "losses": 9},
            ],
            300,
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

        result = warm_landing_page_content()

        self.assertEqual(result['status'], 'completed')
        self.assertIsNotNone(cache.get(LANDING_CLANS_CACHE_KEY))
        self.assertIsNotNone(cache.get(LANDING_RECENT_CLANS_CACHE_KEY))
        self.assertIsNotNone(cache.get(landing_player_cache_key('random', 40)))
        self.assertIsNotNone(cache.get(landing_player_cache_key('best', 40)))
        self.assertIsNotNone(cache.get(LANDING_RECENT_PLAYERS_CACHE_KEY))

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

    def test_player_correlation_distribution_returns_ranked_wr_battles_payload(self):
        cache.clear()

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

        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/8841/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metric"], "ranked_wr_battles")
        self.assertEqual(payload["label"], "Ranked Games vs Win Rate")
        self.assertEqual(payload["x_label"], "Total Ranked Games")
        self.assertEqual(payload["y_label"], "Ranked Win Rate")
        self.assertEqual(payload["x_scale"], "log")
        self.assertEqual(payload["x_domain"]["min"], 50.0)
        self.assertEqual(payload["x_ticks"][0], 50.0)
        self.assertEqual(payload["x_ticks"][1], 100.0)
        self.assertEqual(payload["tracked_population"], 2)
        self.assertEqual(payload["player_point"]["x"], 60.0)
        self.assertEqual(payload["player_point"]["y"], 56.67)
        self.assertTrue(any(tile["count"] > 0 for tile in payload["tiles"]))
        self.assertTrue(any(
            tile["x_min"] == 59.0 and tile["x_max"] == 71.0 and tile["y_min"] == 56.0 and tile["y_max"] == 56.75 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(
            tile["x_min"] == 119.0 and tile["x_max"] == 141.0 and tile["y_min"] == 59.75 and tile["y_max"] == 60.5 and tile["count"] == 1
            for tile in payload["tiles"]
        ))
        self.assertTrue(any(point["count"] > 0 for point in payload["trend"]))

    def test_player_correlation_distribution_returns_404_for_missing_ranked_wr_battles_player(self):
        response = self.client.get(
            "/api/fetch/player_correlation/ranked_wr_battles/999999/")

        self.assertEqual(response.status_code, 404)

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
        cache.delete(LANDING_RECENT_PLAYERS_CACHE_KEY)
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

    @override_settings(SECRET_KEY="test-secret")
    @patch("warships.views.get_agentic_trace_dashboard")
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
            },
            "learning": {
                "recurring_issues": [],
                "common_guidance_paths": [{"label": "agents/runbooks/runbook-langgraph-opinionated-workflow.md", "count": 1}],
                "chart_tuning_notes": [{"slug": "ranked_wr_battles_heatmap"}],
            },
        }

        response = self.client.get("/api/agentic/traces/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_name"], "trace-lab")
        self.assertEqual(response.json()["diagnostics"]["total_runs"], 1)
        self.assertEqual(
            response.json()["diagnostics"]["runs_with_guidance"], 1)
        self.assertTrue(
            response.json()["recent_runs"][0]["design_review_passed"])
        self.assertEqual(response.json()[
                         "learning"]["chart_tuning_notes"][0]["slug"], "ranked_wr_battles_heatmap")
        mock_get_agentic_trace_dashboard.assert_called_once_with(limit=12)

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
