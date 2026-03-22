from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from warships.models import Player, Clan, PlayerExplorerSummary, Ship


LOCMEM_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'TIMEOUT': 60,
    }
}


@override_settings(CACHES=LOCMEM_CACHES)
class LandingPlayersCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _create_best_player(self, player_id: int, name: str):
        return Player.objects.create(
            name=name,
            player_id=player_id,
            pvp_battles=3200,
            total_battles=3300,
            days_since_last_battle=0,
            last_battle_date=timezone.now().date(),
            battles_json=[
                {"ship_tier": 10, "pvp_battles": 3200, "wins": 1760},
            ],
        )

    def test_landing_players_cache_miss_then_hit(self):
        self._create_best_player(1001, "CachePlayer")

        # First request: cache miss → hits DB
        resp1 = self.client.get("/api/landing/players/?mode=best")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(len(resp1.json()), 1)

        # Add another player — but cache should still return stale data
        self._create_best_player(1002, "NewPlayer")

        resp2 = self.client.get("/api/landing/players/?mode=best")
        self.assertEqual(resp2.status_code, 200)
        # Still 1 because the cached result is served
        self.assertEqual(len(resp2.json()), 1)

    def test_landing_players_cache_clear_returns_fresh_data(self):
        self._create_best_player(1001, "CachePlayer")
        self.client.get("/api/landing/players/?mode=best")

        self._create_best_player(1002, "NewPlayer")
        cache.clear()

        resp = self.client.get("/api/landing/players/?mode=best")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)


@override_settings(CACHES=LOCMEM_CACHES)
class LandingClansCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _create_best_clan(self, clan_id: int, name: str, tag: str):
        clan = Clan.objects.create(
            clan_id=clan_id,
            name=name,
            tag=tag,
            members_count=4,
        )
        for index in range(4):
            Player.objects.create(
                name=f"{name}Player{index}",
                player_id=(clan_id * 100) + index,
                clan=clan,
                pvp_battles=30000,
                pvp_wins=16000,
                days_since_last_battle=3,
            )
        return clan

    def test_landing_clans_cache_miss_then_hit(self):
        self._create_best_clan(100, "TestClan", "TC")

        resp1 = self.client.get("/api/landing/clans/?mode=best")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(len(resp1.json()), 1)

        self._create_best_clan(101, "AnotherClan", "AC")

        resp2 = self.client.get("/api/landing/clans/?mode=best")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(len(resp2.json()), 1)  # cached

    def test_landing_clans_cache_clear_returns_fresh_data(self):
        self._create_best_clan(100, "TestClan", "TC")
        self.client.get("/api/landing/clans/?mode=best")

        self._create_best_clan(101, "AnotherClan", "AC")
        cache.clear()

        resp = self.client.get("/api/landing/clans/?mode=best")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)

    @patch("warships.views.random.sample")
    def test_prioritize_landing_clans_frontloads_random_high_volume_sample_sorted_by_wr(self, mock_sample):
        from warships.views import _prioritize_landing_clans

        rows = [
            {"clan_id": 101, "name": "Alpha",
                "clan_wr": 54.2, "total_battles": 180000},
            {"clan_id": 102, "name": "Bravo",
                "clan_wr": 51.0, "total_battles": 210000},
            {"clan_id": 103, "name": "Charlie",
                "clan_wr": 47.5, "total_battles": 125000},
            {"clan_id": 104, "name": "Delta",
                "clan_wr": 58.0, "total_battles": 90000},
        ]
        mock_sample.return_value = [rows[0], rows[2]]

        ranked = _prioritize_landing_clans(rows, sample_size=2)

        self.assertEqual([row["clan_id"] for row in ranked[:2]], [103, 101])
        self.assertEqual([row["clan_id"] for row in ranked[2:]], [102, 104])
        mock_sample.assert_called_once_with(rows[:3], k=2)

    @patch("warships.views.random.sample")
    def test_prioritize_landing_clans_returns_original_order_when_no_clan_meets_threshold(self, mock_sample):
        from warships.views import _prioritize_landing_clans

        rows = [
            {"clan_id": 201, "name": "Echo", "clan_wr": 49.2, "total_battles": 99000},
            {"clan_id": 202, "name": "Foxtrot",
                "clan_wr": None, "total_battles": 130000},
        ]

        ranked = _prioritize_landing_clans(rows, sample_size=2)

        self.assertEqual(ranked, rows)
        mock_sample.assert_not_called()


@override_settings(CACHES=LOCMEM_CACHES)
class ShipInfoCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_ship_info_cached_after_first_lookup(self):
        from warships.api.ships import _fetch_ship_info

        Ship.objects.create(
            ship_id=12345, name="Shimakaze", nation="japan",
            ship_type="Destroyer", tier=10,
        )

        ship1 = _fetch_ship_info("12345")
        self.assertIsNotNone(ship1)
        self.assertEqual(ship1.name, "Shimakaze")

        # Verify it's cached
        cached = cache.get("ship:12345")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.name, "Shimakaze")

        # Second call should return cached value (no DB needed)
        ship2 = _fetch_ship_info("12345")
        self.assertEqual(ship2.name, "Shimakaze")

    def test_ship_info_cache_miss_fetches_from_db(self):
        from warships.api.ships import _fetch_ship_info

        Ship.objects.create(
            ship_id=67890, name="Yamato", nation="japan",
            ship_type="Battleship", tier=10,
        )

        # No cache entry yet
        self.assertIsNone(cache.get("ship:67890"))

        ship = _fetch_ship_info("67890")
        self.assertIsNotNone(ship)
        self.assertEqual(ship.name, "Yamato")

        # Now it should be cached
        self.assertIsNotNone(cache.get("ship:67890"))

    def test_ship_info_invalid_id_returns_none(self):
        from warships.api.ships import _fetch_ship_info

        result = _fetch_ship_info("not_a_number")
        self.assertIsNone(result)

        result = _fetch_ship_info("-1")
        self.assertIsNone(result)


@override_settings(CACHES=LOCMEM_CACHES)
class RankedSeasonsMetadataCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("warships.data._get_ranked_seasons_metadata.__wrapped__" if hasattr(
        __import__('warships.data', fromlist=[
                   '_get_ranked_seasons_metadata'])._get_ranked_seasons_metadata, '__wrapped__'
    ) else "warships.api.players._fetch_ranked_seasons_info")
    def test_ranked_seasons_cached_after_first_call(self, mock_fetch):
        from warships.data import _get_ranked_seasons_metadata, RANKED_SEASONS_CACHE_KEY

        mock_fetch.return_value = {
            "1024": {
                "season_name": "Season 24",
                "start_at": 1700000000,
                "close_at": 1703000000,
            }
        }

        result1 = _get_ranked_seasons_metadata()
        self.assertIn(1024, result1)
        self.assertEqual(result1[1024]["label"], "S24")
        mock_fetch.assert_called_once()

        # Second call should use cache — mock should not be called again
        mock_fetch.reset_mock()
        result2 = _get_ranked_seasons_metadata()
        self.assertEqual(result2[1024]["label"], "S24")
        mock_fetch.assert_not_called()

    @patch("warships.api.players._fetch_ranked_seasons_info")
    def test_ranked_seasons_cache_clear_refetches(self, mock_fetch):
        from warships.data import _get_ranked_seasons_metadata

        mock_fetch.return_value = {
            "1024": {
                "season_name": "Season 24",
                "start_at": 1700000000,
                "close_at": 1703000000,
            }
        }

        _get_ranked_seasons_metadata()
        cache.clear()

        mock_fetch.return_value = {
            "1025": {
                "season_name": "Season 25",
                "start_at": 1706000000,
                "close_at": 1709000000,
            }
        }

        result = _get_ranked_seasons_metadata()
        self.assertIn(1025, result)
        self.assertNotIn(1024, result)

    @patch("warships.api.players._fetch_ranked_seasons_info")
    def test_ranked_seasons_empty_response_not_cached(self, mock_fetch):
        from warships.data import _get_ranked_seasons_metadata, RANKED_SEASONS_CACHE_KEY

        mock_fetch.return_value = None

        result = _get_ranked_seasons_metadata()
        self.assertEqual(result, {})
        self.assertIsNone(cache.get(RANKED_SEASONS_CACHE_KEY))


@override_settings(CACHES=LOCMEM_CACHES)
class ClanBattleSeasonsMetadataCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("warships.data._fetch_clan_battle_seasons_info")
    def test_clan_battle_seasons_cached_after_first_call(self, mock_fetch):
        from warships.data import _get_clan_battle_seasons_metadata, CLAN_BATTLE_SEASONS_CACHE_KEY

        mock_fetch.return_value = {
            "50": {
                "name": "Valhalla",
                "start_time": 1700000000,
                "finish_time": 1703000000,
                "ship_tier_min": 10,
                "ship_tier_max": 10,
            }
        }

        result1 = _get_clan_battle_seasons_metadata()
        self.assertIn(50, result1)
        self.assertEqual(result1[50]["name"], "Valhalla")
        self.assertEqual(cache.get(CLAN_BATTLE_SEASONS_CACHE_KEY)[
                         50]["ship_tier_max"], 10)
        mock_fetch.assert_called_once()

        mock_fetch.reset_mock()
        result2 = _get_clan_battle_seasons_metadata()
        self.assertEqual(result2[50]["name"], "Valhalla")
        mock_fetch.assert_not_called()

    @patch("warships.data._fetch_clan_battle_seasons_info")
    def test_clan_battle_seasons_empty_response_not_cached(self, mock_fetch):
        from warships.data import _get_clan_battle_seasons_metadata, CLAN_BATTLE_SEASONS_CACHE_KEY

        mock_fetch.return_value = None

        result = _get_clan_battle_seasons_metadata()
        self.assertEqual(result, {})
        self.assertIsNone(cache.get(CLAN_BATTLE_SEASONS_CACHE_KEY))


@override_settings(CACHES=LOCMEM_CACHES)
class ClanBattlePlayerStatsCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("warships.data._fetch_clan_battle_season_stats")
    def test_player_clan_battle_stats_cached_after_first_call(self, mock_fetch):
        from warships.data import _get_player_clan_battle_season_stats

        mock_fetch.return_value = {
            "seasons": [
                {"season_id": 50, "battles": 12, "wins": 7, "losses": 5}
            ]
        }

        result1 = _get_player_clan_battle_season_stats(12345)
        self.assertEqual(result1[0]["season_id"], 50)
        mock_fetch.assert_called_once_with(12345)

        mock_fetch.reset_mock()
        result2 = _get_player_clan_battle_season_stats(12345)
        self.assertEqual(result2[0]["battles"], 12)
        mock_fetch.assert_not_called()

    @patch("warships.data._get_player_clan_battle_season_stats")
    @patch("warships.data._get_clan_battle_seasons_metadata")
    def test_fetch_player_clan_battle_seasons_enriches_and_sorts_rows(self, mock_meta, mock_player_stats):
        from warships.data import fetch_player_clan_battle_seasons

        mock_meta.return_value = {
            31: {
                "name": "Earlier Season",
                "label": "S31",
                "start_date": "2025-08-01",
                "end_date": "2025-09-01",
                "ship_tier_min": 8,
                "ship_tier_max": 8,
            },
            209: {
                "name": "Later Legacy Season",
                "label": "S209",
                "start_date": "2025-10-01",
                "end_date": "2025-11-01",
                "ship_tier_min": 10,
                "ship_tier_max": 10,
            },
        }
        mock_player_stats.return_value = [
            {"season_id": 31, "battles": 20, "wins": 11, "losses": 9},
            {"season_id": 209, "battles": 12, "wins": 8, "losses": 4},
            {"season_id": 999, "battles": 0, "wins": 0, "losses": 0},
        ]

        result = fetch_player_clan_battle_seasons(12345)

        self.assertEqual([row["season_id"] for row in result], [209, 31])
        self.assertEqual(result[0]["season_name"], "Later Legacy Season")
        self.assertEqual(result[0]["win_rate"], 66.7)
        self.assertEqual(result[1]["ship_tier_min"], 8)

    @patch("warships.data._get_player_clan_battle_season_stats")
    @patch("warships.data._get_clan_battle_seasons_metadata")
    def test_fetch_player_clan_battle_seasons_persists_durable_summary_and_invalidates_landing_caches(self, mock_meta, mock_player_stats):
        from warships.data import fetch_player_clan_battle_seasons
        from warships.landing import LANDING_PLAYERS_DIRTY_KEY, LANDING_RECENT_PLAYERS_CACHE_KEY, LANDING_RECENT_PLAYERS_DIRTY_KEY, landing_player_cache_key

        player = Player.objects.create(
            name="DurableClanBattlePlayer",
            player_id=5510,
            is_hidden=False,
            pvp_battles=700,
            last_battle_date=timezone.now().date(),
        )
        PlayerExplorerSummary.objects.create(
            player=player,
            clan_battle_total_battles=0,
            clan_battle_seasons_participated=0,
        )
        mock_meta.return_value = {
            31: {
                "name": "Earlier Season",
                "label": "S31",
                "start_date": "2025-08-01",
                "end_date": "2025-09-01",
                "ship_tier_min": 8,
                "ship_tier_max": 8,
            },
        }
        mock_player_stats.return_value = [
            {"season_id": 31, "battles": 42, "wins": 23, "losses": 19},
            {"season_id": 32, "battles": 18, "wins": 9, "losses": 9},
        ]
        original_random_key = landing_player_cache_key('random', 40)
        cache.set(original_random_key, [{'name': 'stale'}], 60)
        cache.set(LANDING_RECENT_PLAYERS_CACHE_KEY,
                  [{'name': 'recent-stale'}], 60)

        result = fetch_player_clan_battle_seasons(5510)

        self.assertEqual(len(result), 2)
        player.refresh_from_db()
        self.assertEqual(player.explorer_summary.clan_battle_total_battles, 60)
        self.assertEqual(
            player.explorer_summary.clan_battle_seasons_participated, 2)
        self.assertEqual(
            player.explorer_summary.clan_battle_overall_win_rate, 53.3)
        self.assertIsNotNone(
            player.explorer_summary.clan_battle_summary_updated_at)
        self.assertEqual(cache.get(LANDING_RECENT_PLAYERS_CACHE_KEY), [
                         {'name': 'recent-stale'}])
        self.assertEqual(cache.get(original_random_key), [{'name': 'stale'}])
        self.assertEqual(original_random_key,
                         landing_player_cache_key('random', 40))
        self.assertIsNotNone(cache.get(LANDING_PLAYERS_DIRTY_KEY))
        self.assertIsNotNone(cache.get(LANDING_RECENT_PLAYERS_DIRTY_KEY))


@override_settings(CACHES=LOCMEM_CACHES)
class ClanBattleSummaryCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("warships.data._get_player_clan_battle_season_stats")
    @patch("warships.data._get_clan_battle_seasons_metadata")
    def test_clan_battle_summary_cached_after_first_call(self, mock_meta, mock_player_stats):
        from warships.data import refresh_clan_battle_seasons_cache, _get_clan_battle_summary_cache_key

        clan = Clan.objects.create(
            clan_id=77, name="CacheClan", tag="CC", members_count=2)
        Player.objects.create(name="One", player_id=1001, clan=clan)
        Player.objects.create(name="Two", player_id=1002, clan=clan)

        mock_meta.return_value = {
            50: {
                "name": "Valhalla",
                "label": "S50",
                "start_date": "2026-01-01",
                "end_date": "2026-02-01",
                "ship_tier_min": 10,
                "ship_tier_max": 10,
            }
        }
        mock_player_stats.side_effect = [
            [{"season_id": 50, "battles": 10, "wins": 6, "losses": 4}],
            [{"season_id": 50, "battles": 20, "wins": 9, "losses": 11}],
        ]

        result1 = refresh_clan_battle_seasons_cache("77")
        self.assertEqual(result1[0]["participants"], 2)
        self.assertEqual(result1[0]["roster_battles"], 30)
        self.assertEqual(result1[0]["roster_wins"], 15)
        self.assertEqual(result1[0]["roster_win_rate"], 50.0)
        self.assertIsNotNone(
            cache.get(_get_clan_battle_summary_cache_key("77")))
        self.assertEqual(mock_player_stats.call_count, 2)

        mock_player_stats.reset_mock()
        result2 = cache.get(_get_clan_battle_summary_cache_key("77"))
        self.assertEqual(result2[0]["roster_battles"], 30)
        mock_player_stats.assert_not_called()

    @patch("warships.data._get_player_clan_battle_season_stats")
    @patch("warships.data._get_clan_battle_seasons_metadata")
    def test_clan_battle_summary_orders_by_season_dates_and_keeps_all_rows(self, mock_meta, mock_player_stats):
        from warships.data import refresh_clan_battle_seasons_cache

        clan = Clan.objects.create(
            clan_id=78, name="SortClan", tag="SC", members_count=1)
        Player.objects.create(name="One", player_id=2001, clan=clan)

        mock_meta.return_value = {
            301: {
                "name": "Legacy",
                "label": "S301",
                "start_date": "2020-11-27",
                "end_date": "2020-11-30",
                "ship_tier_min": 8,
                "ship_tier_max": 8,
            },
            32: {
                "name": "Pelican",
                "label": "S32",
                "start_date": "2025-12-01",
                "end_date": "2026-02-09",
                "ship_tier_min": 10,
                "ship_tier_max": 10,
            },
            31: {
                "name": "Mahi-Mahi",
                "label": "S31",
                "start_date": "2025-09-08",
                "end_date": "2025-10-27",
                "ship_tier_min": 10,
                "ship_tier_max": 10,
            },
        }
        mock_player_stats.return_value = [
            {"season_id": 301, "battles": 7, "wins": 3, "losses": 4},
            {"season_id": 32, "battles": 12, "wins": 8, "losses": 4},
            {"season_id": 31, "battles": 10, "wins": 6, "losses": 4},
        ]

        result = refresh_clan_battle_seasons_cache("78")

        self.assertEqual([row["season_id"] for row in result], [32, 31, 301])
        self.assertEqual(len(result), 3)

    def test_clan_battle_summary_invalidation_helper_clears_cache(self):
        from warships.data import _get_clan_battle_summary_cache_key, _invalidate_clan_battle_summary_cache

        cache_key = _get_clan_battle_summary_cache_key("88")
        cache.set(cache_key, [{"season_id": 50}], 3600)
        self.assertIsNotNone(cache.get(cache_key))

        _invalidate_clan_battle_summary_cache("88")

        self.assertIsNone(cache.get(cache_key))

    @patch("warships.tasks.update_clan_battle_summary_task.delay")
    def test_fetch_clan_battle_seasons_enqueues_refresh_on_cache_miss(self, mock_delay):
        from warships.data import fetch_clan_battle_seasons

        result = fetch_clan_battle_seasons("91")

        self.assertEqual(result, [])
        mock_delay.assert_called_once_with(clan_id="91")

    @patch("warships.tasks.queue_clan_battle_summary_refresh")
    def test_fetch_clan_battle_seasons_keeps_empty_cache_and_queues_refresh_for_populated_clan(self, mock_queue_refresh):
        from warships.data import fetch_clan_battle_seasons, _get_clan_battle_summary_cache_key

        clan = Clan.objects.create(
            clan_id=92, name="PopulatedClan", tag="PC", members_count=2)
        Player.objects.create(name="One", player_id=9201, clan=clan)
        Player.objects.create(name="Two", player_id=9202, clan=clan)
        cache.set(_get_clan_battle_summary_cache_key("92"), [], 3600)

        result = fetch_clan_battle_seasons("92")

        self.assertEqual(result, [])
        mock_queue_refresh.assert_called_once_with("92")
