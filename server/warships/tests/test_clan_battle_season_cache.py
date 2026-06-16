from django.test import SimpleTestCase
from unittest.mock import patch

from warships.data import (
    CLAN_BATTLE_PLAYER_STATS_CACHE_TTL,
    CLAN_BATTLE_PLAYER_STATS_ERROR_TTL,
    _get_player_clan_battle_season_stats,
)


class PlayerClanBattleSeasonCacheTtlTests(SimpleTestCase):
    """A failed upstream fetch (e.g. REQUEST_LIMIT_EXCEEDED during a warm
    burst) must be cached only briefly so we retry soon, while a genuine empty
    or a successful result is cached for the full TTL. This stops a transient
    rate-limit from poisoning the 6h cache — and the clan summary — with a
    wrong "0 clan-battle battles"."""

    @patch('warships.data._fetch_clan_battle_season_stats')
    @patch('warships.data.cache')
    def test_upstream_error_caches_empty_with_short_ttl(self, mock_cache, mock_fetch):
        mock_cache.get.return_value = None
        mock_fetch.return_value = None  # upstream failure

        result = _get_player_clan_battle_season_stats(123, realm='na')

        self.assertEqual(result, [])
        mock_cache.set.assert_called_once()
        _key, value, ttl = mock_cache.set.call_args.args
        self.assertEqual(value, [])
        self.assertEqual(ttl, CLAN_BATTLE_PLAYER_STATS_ERROR_TTL)

    @patch('warships.data._fetch_clan_battle_season_stats')
    @patch('warships.data.cache')
    def test_genuine_empty_caches_with_full_ttl(self, mock_cache, mock_fetch):
        mock_cache.get.return_value = None
        mock_fetch.return_value = {}  # success, player has no CB history

        result = _get_player_clan_battle_season_stats(123, realm='na')

        self.assertEqual(result, [])
        _key, value, ttl = mock_cache.set.call_args.args
        self.assertEqual(value, [])
        self.assertEqual(ttl, CLAN_BATTLE_PLAYER_STATS_CACHE_TTL)

    @patch('warships.data._fetch_clan_battle_season_stats')
    @patch('warships.data.cache')
    def test_success_caches_seasons_with_full_ttl(self, mock_cache, mock_fetch):
        mock_cache.get.return_value = None
        seasons = [{'season_id': 22, 'battles': 10}]
        mock_fetch.return_value = {'seasons': seasons}

        result = _get_player_clan_battle_season_stats(123, realm='na')

        self.assertEqual(result, seasons)
        _key, value, ttl = mock_cache.set.call_args.args
        self.assertEqual(value, seasons)
        self.assertEqual(ttl, CLAN_BATTLE_PLAYER_STATS_CACHE_TTL)

    @patch('warships.data._fetch_clan_battle_season_stats')
    @patch('warships.data.cache')
    def test_cold_cache_without_remote_fetch_returns_none_and_skips_wg(self, mock_cache, mock_fetch):
        # Request path: cold cache + allow_remote_fetch=False must NOT call WG
        # and must signal "no fetch" via None so the caller skips persistence.
        mock_cache.get.return_value = None

        result = _get_player_clan_battle_season_stats(
            123, realm='na', allow_remote_fetch=False)

        self.assertIsNone(result)
        mock_fetch.assert_not_called()
        mock_cache.set.assert_not_called()

    @patch('warships.data._fetch_clan_battle_season_stats')
    @patch('warships.data.cache')
    def test_warm_cache_without_remote_fetch_returns_cached(self, mock_cache, mock_fetch):
        seasons = [{'season_id': 22, 'battles': 10}]
        mock_cache.get.return_value = seasons

        result = _get_player_clan_battle_season_stats(
            123, realm='na', allow_remote_fetch=False)

        self.assertEqual(result, seasons)
        mock_fetch.assert_not_called()


class FetchPlayerClanBattleSeasonsColdPathTests(SimpleTestCase):
    """The request path (allow_remote_fetch=False) must not persist on a cold
    miss — persisting zeros would clobber the stored summary and fire a
    landing-cache invalidation storm."""

    @patch('warships.data._persist_player_clan_battle_summary')
    @patch('warships.data._get_clan_battle_seasons_metadata')
    @patch('warships.data._fetch_clan_battle_season_stats')
    @patch('warships.data.cache')
    def test_cold_request_path_returns_empty_without_fetch_or_persist(
        self, mock_cache, mock_fetch, mock_meta, mock_persist,
    ):
        from warships.data import fetch_player_clan_battle_seasons
        mock_cache.get.return_value = None

        result = fetch_player_clan_battle_seasons(
            123, realm='na', allow_remote_fetch=False)

        self.assertEqual(result, [])
        mock_fetch.assert_not_called()
        mock_persist.assert_not_called()
        # Metadata is a separate cold WG call — must also be skipped.
        mock_meta.assert_not_called()
