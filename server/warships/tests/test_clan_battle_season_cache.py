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
