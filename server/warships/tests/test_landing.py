from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from warships.landing import LANDING_CLAN_CACHE_TTL, LANDING_CLANS_CACHE_KEY, LANDING_PLAYER_CACHE_TTL, LANDING_PLAYER_LIMIT, LANDING_RECENT_CLANS_CACHE_KEY, LANDING_RECENT_PLAYERS_CACHE_KEY, get_landing_clans_payload, get_landing_clans_payload_with_cache_metadata, get_landing_players_payload, get_landing_players_payload_with_cache_metadata, invalidate_landing_clan_caches, invalidate_landing_player_caches, landing_player_cache_key, normalize_landing_player_limit, normalize_landing_player_mode


class LandingHelperTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_normalize_landing_player_mode_accepts_known_modes(self):
        self.assertEqual(normalize_landing_player_mode('random'), 'random')
        self.assertEqual(normalize_landing_player_mode(' BEST '), 'best')
        self.assertEqual(normalize_landing_player_mode(' sigma '), 'sigma')
        self.assertEqual(normalize_landing_player_mode(None), 'random')

    def test_normalize_landing_player_mode_rejects_unknown_mode(self):
        with self.assertRaisesMessage(ValueError, 'mode must be one of: random, best, sigma'):
            normalize_landing_player_mode('hot')

    def test_normalize_landing_player_limit_clamps_requested_values(self):
        self.assertEqual(normalize_landing_player_limit(
            None), LANDING_PLAYER_LIMIT)
        self.assertEqual(normalize_landing_player_limit('5'), 5)
        self.assertEqual(normalize_landing_player_limit('0'), 1)
        self.assertEqual(normalize_landing_player_limit(
            '999'), LANDING_PLAYER_LIMIT)
        self.assertEqual(normalize_landing_player_limit(
            'not-a-number'), LANDING_PLAYER_LIMIT)

    def test_invalidate_landing_clan_caches_clears_current_keys(self):
        cache.set(LANDING_CLANS_CACHE_KEY, ['current'], 60)
        cache.set(LANDING_RECENT_CLANS_CACHE_KEY, ['recent'], 60)

        invalidate_landing_clan_caches()

        self.assertIsNone(cache.get(LANDING_CLANS_CACHE_KEY))
        self.assertIsNone(cache.get(LANDING_RECENT_CLANS_CACHE_KEY))

    def test_invalidate_landing_player_caches_bumps_namespace_and_clears_recent_key(self):
        original_random_key = landing_player_cache_key('random', 40)
        original_best_key = landing_player_cache_key('best', 40)
        cache.set(original_random_key, ['random'], 60)
        cache.set(original_best_key, ['best'], 60)
        cache.set(LANDING_RECENT_PLAYERS_CACHE_KEY, ['recent'], 60)

        invalidate_landing_player_caches(include_recent=True)

        refreshed_random_key = landing_player_cache_key('random', 40)
        refreshed_best_key = landing_player_cache_key('best', 40)

        self.assertNotEqual(original_random_key, refreshed_random_key)
        self.assertNotEqual(original_best_key, refreshed_best_key)
        self.assertEqual(cache.get(original_random_key), ['random'])
        self.assertEqual(cache.get(original_best_key), ['best'])
        self.assertIsNone(cache.get(refreshed_random_key))
        self.assertIsNone(cache.get(refreshed_best_key))
        self.assertIsNone(cache.get(LANDING_RECENT_PLAYERS_CACHE_KEY))

    def test_invalidate_landing_player_caches_preserves_recent_key_by_default(self):
        original_random_key = landing_player_cache_key('random', 40)
        cache.set(original_random_key, ['random'], 60)
        cache.set(LANDING_RECENT_PLAYERS_CACHE_KEY, ['recent'], 60)

        invalidate_landing_player_caches()

        refreshed_random_key = landing_player_cache_key('random', 40)
        self.assertNotEqual(original_random_key, refreshed_random_key)
        self.assertEqual(cache.get(original_random_key), ['random'])
        self.assertEqual(
            cache.get(LANDING_RECENT_PLAYERS_CACHE_KEY), ['recent'])

    def test_landing_clans_use_one_hour_cache_ttl(self):
        _, metadata = get_landing_clans_payload_with_cache_metadata()
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)

    def test_all_landing_player_modes_use_one_hour_cache_ttl(self):
        _, random_meta = get_landing_players_payload_with_cache_metadata(
            'random', 40)
        self.assertEqual(random_meta['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)

        _, best_meta = get_landing_players_payload_with_cache_metadata(
            'best', 40)
        self.assertEqual(best_meta['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)

        _, sigma_meta = get_landing_players_payload_with_cache_metadata(
            'sigma', 40)
        self.assertEqual(sigma_meta['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)

    def test_landing_clan_metadata_is_rebuilt_when_payload_exists_without_metadata(self):
        cache.set(LANDING_CLANS_CACHE_KEY, [{'name': 'cached'}], 60)

        payload, metadata = get_landing_clans_payload_with_cache_metadata()

        self.assertEqual(payload, [{'name': 'cached'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        self.assertIsNotNone(cache.get('landing:clans:v3:meta'))

    def test_landing_players_metadata_is_rebuilt_when_payload_exists_without_metadata(self):
        player_cache_key = landing_player_cache_key('random', 40)
        cache.set(player_cache_key, [{'name': 'cached-player'}], 60)

        payload, metadata = get_landing_players_payload_with_cache_metadata(
            'random', 40)

        self.assertEqual(payload, [{'name': 'cached-player'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)
        metadata_key = player_cache_key.replace(':40', ':40:meta')
        self.assertIsNotNone(cache.get(metadata_key))

    def test_force_refresh_rebuilds_cached_landing_clans_payload(self):
        with patch('warships.landing._build_landing_clans', side_effect=[[{'name': 'old'}], [{'name': 'new'}]]) as mock_builder:
            first_payload = get_landing_clans_payload()
            refreshed_payload = get_landing_clans_payload(force_refresh=True)

        self.assertEqual(first_payload, [{'name': 'old'}])
        self.assertEqual(refreshed_payload, [{'name': 'new'}])
        self.assertEqual(mock_builder.call_count, 2)
        self.assertEqual(cache.get(LANDING_CLANS_CACHE_KEY), [{'name': 'new'}])

    def test_force_refresh_rebuilds_cached_landing_players_payload(self):
        with patch('warships.landing._build_random_landing_players', side_effect=[[{'name': 'old'}], [{'name': 'new'}]]) as mock_builder:
            first_payload = get_landing_players_payload('random', 40)
            refreshed_payload = get_landing_players_payload(
                'random', 40, force_refresh=True)

        self.assertEqual(first_payload, [{'name': 'old'}])
        self.assertEqual(refreshed_payload, [{'name': 'new'}])
        self.assertEqual(mock_builder.call_count, 2)
        self.assertEqual(cache.get(landing_player_cache_key(
            'random', 40)), [{'name': 'new'}])

    def test_warm_landing_page_content_warms_each_surface_once(self):
        with patch('warships.landing.get_landing_clans_payload', return_value=[{'name': 'Clan'}]) as mock_clans, \
                patch('warships.landing.get_landing_recent_clans_payload', return_value=[{'name': 'Recent Clan'}]) as mock_recent_clans, \
                patch('warships.landing.get_landing_players_payload', side_effect=[
                    [{'name': 'Random'}],
                    [{'name': 'Best'}],
                    [{'name': 'Sigma'}],
                ]) as mock_players, \
                patch('warships.landing.get_landing_recent_players_payload', return_value=[{'name': 'Recent Player'}]) as mock_recent_players:
            from warships.landing import warm_landing_page_content

            result = warm_landing_page_content(force_refresh=True)

        self.assertEqual(result, {
            'status': 'completed',
            'warmed': {
                'clans': 1,
                'recent_clans': 1,
                'players_random': 1,
                'players_best': 1,
                'players_sigma': 1,
                'recent_players': 1,
            },
        })
        mock_clans.assert_called_once_with(force_refresh=True)
        mock_recent_clans.assert_called_once_with()
        self.assertEqual(mock_players.call_args_list[0].args, ('random', 40))
        self.assertEqual(mock_players.call_args_list[0].kwargs, {
                         'force_refresh': True})
        self.assertEqual(mock_players.call_args_list[1].args, ('best', 40))
        self.assertEqual(mock_players.call_args_list[1].kwargs, {
                         'force_refresh': True})
        self.assertEqual(mock_players.call_args_list[2].args, ('sigma', 40))
        self.assertEqual(mock_players.call_args_list[2].kwargs, {
                         'force_refresh': True})
        mock_recent_players.assert_called_once_with()
