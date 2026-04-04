from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import BEST_CLAN_WR_MIN_CB_BATTLES, warm_landing_best_entity_caches
from warships.landing import LANDING_CACHE_TTL, LANDING_CLAN_CACHE_TTL, LANDING_CLAN_FEATURED_COUNT, LANDING_CLAN_MIN_TOTAL_BATTLES, LANDING_CLANS_BEST_CACHE_KEY, LANDING_CLANS_BEST_CACHE_METADATA_KEY, LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY, LANDING_CLANS_BEST_PUBLISHED_METADATA_KEY, LANDING_CLANS_CACHE_KEY, LANDING_CLANS_CACHE_METADATA_KEY, LANDING_CLANS_DIRTY_KEY, LANDING_CLANS_PUBLISHED_CACHE_KEY, LANDING_CLANS_PUBLISHED_METADATA_KEY, LANDING_PLAYER_CACHE_TTL, LANDING_PLAYER_LIMIT, LANDING_PLAYERS_DIRTY_KEY, LANDING_RANDOM_CLAN_QUEUE_KEY, LANDING_RANDOM_PLAYER_QUEUE_KEY, LANDING_RECENT_CLANS_CACHE_KEY, LANDING_RECENT_CLANS_DIRTY_KEY, LANDING_RECENT_PLAYERS_CACHE_KEY, LANDING_RECENT_PLAYERS_DIRTY_KEY, get_landing_best_clans_payload_with_cache_metadata, get_landing_clans_payload, get_landing_clans_payload_with_cache_metadata, get_landing_players_payload, get_landing_players_payload_with_cache_metadata, get_random_landing_clan_queue_payload, get_random_landing_player_queue_payload, invalidate_landing_clan_caches, invalidate_landing_player_caches, landing_best_clan_cache_key, landing_best_clan_published_cache_key, landing_player_cache_key, landing_player_published_cache_key, landing_player_published_metadata_key, normalize_landing_clan_best_sort, normalize_landing_clan_limit, normalize_landing_clan_mode, normalize_landing_player_limit, normalize_landing_player_mode, refill_random_landing_clan_queue, refill_random_landing_player_queue
from warships.models import Clan, Player, PlayerExplorerSummary, realm_cache_key


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

    def test_normalize_landing_clan_limit_clamps_requested_values(self):
        self.assertEqual(normalize_landing_clan_limit(
            None), LANDING_CLAN_FEATURED_COUNT)
        self.assertEqual(normalize_landing_clan_limit('5'), 5)
        self.assertEqual(normalize_landing_clan_limit('0'), 1)
        self.assertEqual(normalize_landing_clan_limit(
            '999'), LANDING_CLAN_FEATURED_COUNT)
        self.assertEqual(normalize_landing_clan_limit(
            'not-a-number'), LANDING_CLAN_FEATURED_COUNT)

    def test_normalize_landing_clan_best_sort_accepts_known_modes(self):
        self.assertEqual(
            normalize_landing_clan_best_sort('overall'), 'overall')
        self.assertEqual(normalize_landing_clan_best_sort(' WR '), 'wr')
        self.assertEqual(normalize_landing_clan_best_sort(None), 'overall')

    def test_normalize_landing_clan_best_sort_rejects_unknown_mode(self):
        with self.assertRaisesMessage(ValueError, 'sort must be one of: overall, wr, cb'):
            normalize_landing_clan_best_sort('activity')

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_invalidate_landing_clan_caches_marks_dirty_and_preserves_current_keys(self, mock_delay):
        cache.set(realm_cache_key(
            'na', LANDING_CLANS_CACHE_KEY), ['current'], 60)
        cache.set(realm_cache_key('na', LANDING_CLANS_CACHE_METADATA_KEY), {
                  'ttl_seconds': 60}, 60)
        cache.set(realm_cache_key(
            'na', LANDING_CLANS_BEST_CACHE_KEY), ['best'], 60)
        cache.set(realm_cache_key('na', LANDING_CLANS_BEST_CACHE_METADATA_KEY),
                  {'ttl_seconds': 60}, 60)
        cache.set(realm_cache_key(
            'na', LANDING_RECENT_CLANS_CACHE_KEY), ['recent'], 60)

        invalidate_landing_clan_caches()

        self.assertEqual(cache.get(realm_cache_key(
            'na', LANDING_CLANS_CACHE_KEY)), ['current'])
        self.assertEqual(cache.get(realm_cache_key(
            'na', LANDING_CLANS_BEST_CACHE_KEY)), ['best'])
        self.assertEqual(cache.get(realm_cache_key(
            'na', LANDING_RECENT_CLANS_CACHE_KEY)), ['recent'])
        self.assertIsNotNone(
            cache.get(realm_cache_key('na', LANDING_CLANS_DIRTY_KEY)))
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_CLANS_DIRTY_KEY)))
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_invalidate_landing_player_caches_marks_dirty_and_preserves_recent_key(self, mock_delay):
        original_random_key = landing_player_cache_key(
            'random', LANDING_PLAYER_LIMIT)
        original_best_key = landing_player_cache_key(
            'best', LANDING_PLAYER_LIMIT)
        cache.set(original_random_key, ['random'], 60)
        cache.set(original_best_key, ['best'], 60)
        cache.set(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_CACHE_KEY), ['recent'], 60)

        invalidate_landing_player_caches(include_recent=True)

        self.assertEqual(cache.get(original_random_key), ['random'])
        self.assertEqual(cache.get(original_best_key), ['best'])
        self.assertEqual(
            cache.get(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY)), ['recent'])
        self.assertIsNotNone(
            cache.get(realm_cache_key('na', LANDING_PLAYERS_DIRTY_KEY)))
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY)))
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_invalidate_landing_player_caches_preserves_recent_key_by_default(self, mock_delay):
        original_random_key = landing_player_cache_key(
            'random', LANDING_PLAYER_LIMIT)
        cache.set(original_random_key, ['random'], 60)
        cache.set(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_CACHE_KEY), ['recent'], 60)

        invalidate_landing_player_caches()

        self.assertEqual(cache.get(original_random_key), ['random'])
        self.assertEqual(
            cache.get(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY)), ['recent'])
        self.assertIsNotNone(
            cache.get(realm_cache_key('na', LANDING_PLAYERS_DIRTY_KEY)))
        self.assertIsNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY)))
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_invalidate_landing_recent_player_cache_still_marks_dirty_during_cooldown(self, mock_delay):
        from warships.landing import invalidate_landing_recent_player_cache
        dirty_key = realm_cache_key('na', LANDING_RECENT_PLAYERS_DIRTY_KEY)

        invalidate_landing_recent_player_cache()
        self.assertIsNotNone(cache.get(dirty_key))
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

        cache.delete(dirty_key)

        invalidate_landing_recent_player_cache()

        self.assertIsNotNone(cache.get(dirty_key))
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

    def test_landing_clans_use_twelve_hour_cache_ttl(self):
        _, metadata = get_landing_clans_payload_with_cache_metadata()
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        self.assertEqual(LANDING_CLAN_CACHE_TTL, 60 * 60 * 12)

    def test_best_clan_cache_keys_are_sort_specific(self):
        self.assertEqual(landing_best_clan_cache_key('overall'),
                         realm_cache_key('na', LANDING_CLANS_BEST_CACHE_KEY))
        self.assertEqual(landing_best_clan_published_cache_key(
            'overall'), realm_cache_key('na', LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY))
        self.assertEqual(landing_best_clan_cache_key(
            'wr'), realm_cache_key('na', 'landing:clans:best:v2:wr'))
        self.assertEqual(landing_best_clan_cache_key(
            'cb'), realm_cache_key('na', 'landing:clans:best:v2:cb'))

    def test_best_clan_sorts_rank_independent_top_results(self):
        now = timezone.now()

        def create_candidate(clan_id: int, name: str, clan_wr: float, active_members: int, total_battles: int, member_score: float, cb_battles: int, cb_wr: float, cb_days_ago: int):
            clan = Clan.objects.create(
                clan_id=clan_id,
                name=name,
                tag=name[:5].upper(),
                members_count=12,
                cached_clan_wr=clan_wr,
                cached_total_battles=total_battles,
                cached_active_member_count=active_members,
            )
            for index in range(5):
                player = Player.objects.create(
                    name=f'{name}Player{index}',
                    player_id=clan_id * 100 + index,
                    clan=clan,
                    pvp_battles=5000,
                    pvp_wins=2600,
                    days_since_last_battle=3,
                )
                PlayerExplorerSummary.objects.create(
                    player=player,
                    player_score=member_score,
                    clan_battle_total_battles=cb_battles,
                    clan_battle_overall_win_rate=cb_wr,
                    clan_battle_summary_updated_at=now -
                    timedelta(days=cb_days_ago),
                )

        create_candidate(7101, 'OverallLeader', 55.0,
                         11, 450000, 9.8, 12, 52.0, 60)
        create_candidate(7102, 'WRLeader', 62.0, 7, 150000, 4.0, 24, 70.0, 30)
        create_candidate(7103, 'CBLeader', 53.0, 9, 120000, 5.0, 300, 68.0, 10)

        today = timezone.now().date()

        def season_date(offset_days: int) -> str:
            return (today - timedelta(days=offset_days)).strftime('%Y-%m-%d')

        season_meta = {
            season_id: {
                'name': f'Season {season_id}',
                'label': f'S{season_id}',
                'start_date': season_date((12 - season_id) * 14 + 7),
                'end_date': season_date((12 - season_id) * 14),
            }
            for season_id in range(1, 13)
        }
        season_meta[12]['start_date'] = today.strftime('%Y-%m-%d')
        season_meta[12]['end_date'] = (
            today + timedelta(days=7)).strftime('%Y-%m-%d')

        season_rows_by_clan = {
            '7101': [
                {'season_id': season_id, 'roster_battles': 120,
                    'roster_win_rate': 51.0}
                for season_id in range(2, 12)
            ],
            '7102': [
                {'season_id': season_id, 'roster_battles': 80, 'roster_win_rate': 57.0}
                for season_id in range(2, 12)
            ],
            '7103': [
                {'season_id': season_id, 'roster_battles': 160,
                    'roster_win_rate': 64.0}
                for season_id in range(2, 12)
            ],
        }

        overall_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
            force_refresh=True, sort='overall')
        wr_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
            force_refresh=True, sort='wr')
        with patch('warships.data._get_clan_battle_seasons_metadata', return_value=season_meta), \
                patch('warships.data.refresh_clan_battle_seasons_cache', side_effect=lambda clan_id, realm='na': season_rows_by_clan.get(str(clan_id), [])):
            cb_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
                force_refresh=True, sort='cb')

        self.assertEqual(overall_payload[0]['name'], 'OverallLeader')
        self.assertEqual(wr_payload[0]['name'], 'WRLeader')
        self.assertEqual(cb_payload[0]['name'], 'CBLeader')
        self.assertIn('avg_cb_battles', wr_payload[0])
        self.assertIn('avg_cb_wr', cb_payload[0])

    def test_best_clan_wr_sort_ignores_tiny_cb_samples(self):
        now = timezone.now()

        def create_candidate(clan_id: int, name: str, clan_wr: float, member_score: float, cb_battles: float, cb_wr: float):
            clan = Clan.objects.create(
                clan_id=clan_id,
                name=name,
                tag=name[:5].upper(),
                members_count=12,
                cached_clan_wr=clan_wr,
                cached_total_battles=180000,
                cached_active_member_count=9,
            )
            for index in range(5):
                player = Player.objects.create(
                    name=f'{name}Player{index}',
                    player_id=clan_id * 100 + index,
                    clan=clan,
                    pvp_battles=5000,
                    pvp_wins=2600,
                    days_since_last_battle=3,
                )
                PlayerExplorerSummary.objects.create(
                    player=player,
                    player_score=member_score,
                    clan_battle_total_battles=cb_battles,
                    clan_battle_overall_win_rate=cb_wr,
                    clan_battle_summary_updated_at=now - timedelta(days=2),
                )

        create_candidate(7201, 'TinySampleTrap', 49.0, 6.0, 1.0, 100.0)
        create_candidate(7202, 'QualifiedBlendLeader', 58.0,
                         6.0, BEST_CLAN_WR_MIN_CB_BATTLES, 80.0)
        create_candidate(7203, 'PureWRLeader', 63.0, 6.0, 0.0, 0.0)

        wr_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
            force_refresh=True, sort='wr')
        wr_names = [row['name'] for row in wr_payload[:3]]

        self.assertEqual(wr_names[0], 'PureWRLeader')
        self.assertEqual(wr_names[1], 'QualifiedBlendLeader')
        self.assertEqual(wr_names[2], 'TinySampleTrap')

    def test_best_clan_wr_sort_rewards_depth_backing_cb_results(self):
        now = timezone.now()

        def create_candidate(
            clan_id: int,
            name: str,
            clan_wr: float,
            members_count: int,
            active_members: int,
            member_score: float,
            cb_battles: float,
            cb_wr: float,
        ):
            clan = Clan.objects.create(
                clan_id=clan_id,
                name=name,
                tag=name[:5].upper(),
                members_count=members_count,
                cached_clan_wr=clan_wr,
                cached_total_battles=280000,
                cached_active_member_count=active_members,
            )
            for index in range(5):
                player = Player.objects.create(
                    name=f'{name}Player{index}',
                    player_id=clan_id * 100 + index,
                    clan=clan,
                    pvp_battles=6000,
                    pvp_wins=3200,
                    days_since_last_battle=3,
                )
                PlayerExplorerSummary.objects.create(
                    player=player,
                    player_score=member_score,
                    clan_battle_total_battles=cb_battles,
                    clan_battle_overall_win_rate=cb_wr,
                    clan_battle_summary_updated_at=now - timedelta(days=1),
                )

        create_candidate(7301, 'ShallowSpike', 58.0, 27, 14, 3.8, 450.0, 79.0)
        create_candidate(7302, 'DeepRoster', 63.2, 34, 30, 6.0, 1850.0, 56.8)
        create_candidate(7303, 'BalancedRunnerUp',
                         64.0, 44, 29, 5.8, 825.0, 60.9)

        wr_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
            force_refresh=True, sort='wr')
        wr_names = [row['name'] for row in wr_payload[:3]]

        self.assertEqual(wr_names[0], 'BalancedRunnerUp')
        self.assertEqual(wr_names[1], 'DeepRoster')
        self.assertEqual(wr_names[2], 'ShallowSpike')

    def test_best_clan_cb_sort_requires_successful_cb_results(self):
        now = timezone.now()

        def create_candidate(
            clan_id: int,
            name: str,
            clan_wr: float,
            members_count: int,
            active_members: int,
            member_score: float,
            cb_battles: float,
            cb_wr: float,
        ):
            clan = Clan.objects.create(
                clan_id=clan_id,
                name=name,
                tag=name[:5].upper(),
                members_count=members_count,
                cached_clan_wr=clan_wr,
                cached_total_battles=320000,
                cached_active_member_count=active_members,
            )
            for index in range(5):
                player = Player.objects.create(
                    name=f'{name}Player{index}',
                    player_id=clan_id * 100 + index,
                    clan=clan,
                    pvp_battles=7000,
                    pvp_wins=3900,
                    days_since_last_battle=3,
                )
                PlayerExplorerSummary.objects.create(
                    player=player,
                    player_score=member_score,
                    clan_battle_total_battles=cb_battles,
                    clan_battle_overall_win_rate=cb_wr,
                    clan_battle_summary_updated_at=now - timedelta(days=1),
                )

        create_candidate(7401, 'FullWindowLeader', 62.5,
                         42, 30, 5.4, 2600.0, 60.5)
        create_candidate(7402, 'HalfWindowSpike', 60.8,
                         46, 29, 5.1, 1900.0, 58.0)
        create_candidate(7403, 'CurrentSeasonTrap', 61.0,
                         44, 28, 5.0, 2100.0, 59.0)

        today = timezone.now().date()
        season_ids = list(range(1, 13))

        def season_date(offset_days: int) -> str:
            return (today - timedelta(days=offset_days)).strftime('%Y-%m-%d')

        season_meta = {
            season_id: {
                'name': f'Season {season_id}',
                'label': f'S{season_id}',
                'start_date': season_date((12 - season_id) * 14 + 7),
                'end_date': season_date((12 - season_id) * 14),
            }
            for season_id in season_ids
        }
        season_meta[12]['start_date'] = today.strftime('%Y-%m-%d')
        season_meta[12]['end_date'] = (
            today + timedelta(days=7)).strftime('%Y-%m-%d')

        season_rows_by_clan = {
            '7401': [
                {'season_id': season_id, 'roster_battles': 120,
                    'roster_win_rate': 60.0}
                for season_id in range(2, 12)
            ],
            '7402': [
                {'season_id': season_id, 'roster_battles': 80,
                    'roster_win_rate': 100.0}
                for season_id in range(7, 12)
            ],
            '7403': [
                {'season_id': season_id, 'roster_battles': 90, 'roster_win_rate': 49.0}
                for season_id in range(2, 12)
            ] + [
                {'season_id': 1, 'roster_battles': 90, 'roster_win_rate': 100.0},
                {'season_id': 12, 'roster_battles': 90, 'roster_win_rate': 100.0},
            ],
        }

        with patch('warships.data._get_clan_battle_seasons_metadata', return_value=season_meta), \
                patch('warships.data.refresh_clan_battle_seasons_cache', side_effect=lambda clan_id, realm='na': season_rows_by_clan.get(str(clan_id), [])):
            cb_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
                force_refresh=True, sort='cb')

        cb_names = [row['name'] for row in cb_payload[:3]]

        self.assertEqual(cb_names[0], 'FullWindowLeader')
        self.assertEqual(cb_names[1], 'HalfWindowSpike')
        self.assertEqual(cb_names[2], 'CurrentSeasonTrap')

    def test_best_clan_cb_sort_weights_same_wr_by_season_battles(self):
        now = timezone.now()

        def create_candidate(clan_id: int, name: str):
            clan = Clan.objects.create(
                clan_id=clan_id,
                name=name,
                tag=name[:5].upper(),
                members_count=42,
                cached_clan_wr=58.0,
                cached_total_battles=240000,
                cached_active_member_count=28,
            )
            for index in range(5):
                player = Player.objects.create(
                    name=f'{name}Player{index}',
                    player_id=clan_id * 100 + index,
                    clan=clan,
                    pvp_battles=7000,
                    pvp_wins=3900,
                    days_since_last_battle=3,
                )
                PlayerExplorerSummary.objects.create(
                    player=player,
                    player_score=5.2,
                    clan_battle_total_battles=1400.0,
                    clan_battle_overall_win_rate=60.0,
                    clan_battle_summary_updated_at=now - timedelta(days=1),
                )

        create_candidate(7501, 'ThirtyBattleSeason')
        create_candidate(7502, 'TwoBattleSeason')
        create_candidate(7503, 'FiftyFiveAnchor')

        today = timezone.now().date()

        def season_date(offset_days: int) -> str:
            return (today - timedelta(days=offset_days)).strftime('%Y-%m-%d')

        season_meta = {
            season_id: {
                'name': f'Season {season_id}',
                'label': f'S{season_id}',
                'start_date': season_date((12 - season_id) * 14 + 7),
                'end_date': season_date((12 - season_id) * 14),
            }
            for season_id in range(1, 13)
        }
        season_meta[12]['start_date'] = today.strftime('%Y-%m-%d')
        season_meta[12]['end_date'] = (
            today + timedelta(days=7)).strftime('%Y-%m-%d')

        season_rows_by_clan = {
            '7501': [
                {'season_id': season_id, 'roster_battles': 30, 'roster_win_rate': 60.0}
                for season_id in range(2, 12)
            ],
            '7502': [
                {'season_id': season_id, 'roster_battles': 2, 'roster_win_rate': 60.0}
                for season_id in range(2, 12)
            ],
            '7503': [
                {'season_id': season_id, 'roster_battles': 30, 'roster_win_rate': 55.0}
                for season_id in range(2, 12)
            ],
        }

        with patch('warships.data._get_clan_battle_seasons_metadata', return_value=season_meta), \
                patch('warships.data.refresh_clan_battle_seasons_cache', side_effect=lambda clan_id, realm='na': season_rows_by_clan.get(str(clan_id), [])):
            cb_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
                force_refresh=True, sort='cb')

        cb_names = [row['name'] for row in cb_payload[:3]]

        self.assertEqual(cb_names[0], 'ThirtyBattleSeason')
        self.assertEqual(cb_names[1], 'FiftyFiveAnchor')
        self.assertEqual(cb_names[2], 'TwoBattleSeason')

    def test_all_landing_player_modes_use_twelve_hour_cache_ttl(self):
        _, best_meta = get_landing_players_payload_with_cache_metadata(
            'best', LANDING_PLAYER_LIMIT)
        self.assertEqual(best_meta['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)

        _, sigma_meta = get_landing_players_payload_with_cache_metadata(
            'sigma', LANDING_PLAYER_LIMIT)
        self.assertEqual(sigma_meta['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)
        self.assertEqual(LANDING_PLAYER_CACHE_TTL, 60 * 60 * 12)

    def test_random_landing_player_queue_payload_uses_zero_ttl_metadata(self):
        with patch('warships.landing.peek_random_landing_player_ids', return_value=([11, 12], 55)), patch('warships.landing.resolve_landing_players_by_id_order', return_value=[{'name': 'Player A'}, {'name': 'Player B'}]), patch('warships.tasks.queue_random_landing_player_queue_refill', return_value={'status': 'queued'}):
            payload, metadata = get_random_landing_player_queue_payload(
                LANDING_PLAYER_LIMIT,
                pop=False,
                schedule_refill=True,
            )

        self.assertEqual(payload, [{'name': 'Player A'}, {'name': 'Player B'}])
        self.assertEqual(metadata['ttl_seconds'], 0)
        self.assertEqual(metadata['queue_remaining'], 55)
        self.assertEqual(metadata['served_count'], 2)
        self.assertTrue(metadata['refill_scheduled'])

    def test_landing_clan_metadata_is_rebuilt_when_payload_exists_without_metadata(self):
        cache.set(realm_cache_key('na', LANDING_CLANS_CACHE_KEY),
                  [{'name': 'cached'}], 60)

        payload, metadata = get_landing_clans_payload_with_cache_metadata()

        self.assertEqual(payload, [{'name': 'cached'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_CLANS_CACHE_METADATA_KEY)))

    def test_landing_clans_payload_is_capped_to_featured_count(self):
        clans = []
        for index in range(LANDING_CLAN_FEATURED_COUNT + 5):
            clan = Clan.objects.create(
                clan_id=9000 + index,
                name=f'FeaturedClan{index}',
                tag=f'FC{index}',
                members_count=1,
            )
            Player.objects.create(
                name=f'FeaturedClanPlayer{index}',
                player_id=99000 + index,
                clan=clan,
                pvp_battles=LANDING_CLAN_MIN_TOTAL_BATTLES,
                pvp_wins=LANDING_CLAN_MIN_TOTAL_BATTLES // 2,
                days_since_last_battle=1,
            )
            clans.append(clan)

        payload = get_landing_clans_payload(force_refresh=True)

        self.assertEqual(len(payload), LANDING_CLAN_FEATURED_COUNT)

    def test_landing_players_metadata_is_rebuilt_when_payload_exists_without_metadata(self):
        player_cache_key = landing_player_cache_key(
            'random', LANDING_PLAYER_LIMIT)
        cache.set(player_cache_key, [{'name': 'cached-player'}], 60)

        payload, metadata = get_landing_players_payload_with_cache_metadata(
            'random', LANDING_PLAYER_LIMIT)

        self.assertEqual(payload, [{'name': 'cached-player'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)
        metadata_key = f'{player_cache_key}:meta'
        self.assertIsNotNone(cache.get(metadata_key))

    @patch('warships.tasks.queue_landing_page_warm', return_value={'status': 'queued'})
    def test_landing_clans_use_published_fallback_when_primary_cache_is_missing(self, mock_queue_warm):
        cache.set(realm_cache_key('na', LANDING_CLANS_PUBLISHED_CACHE_KEY), [
                  {'name': 'published-clan'}], timeout=None)
        cache.set(realm_cache_key('na', LANDING_CLANS_PUBLISHED_METADATA_KEY), {
            'ttl_seconds': LANDING_CLAN_CACHE_TTL,
            'cached_at': '2026-03-25T00:00:00+00:00',
            'expires_at': '2026-03-25T12:00:00+00:00',
        }, timeout=None)

        with patch('warships.landing._build_landing_clans') as mock_builder:
            payload, metadata = get_landing_clans_payload_with_cache_metadata()

        self.assertEqual(payload, [{'name': 'published-clan'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        mock_builder.assert_not_called()
        mock_queue_warm.assert_called_once_with(realm='na')

    @patch('warships.tasks.queue_landing_page_warm', return_value={'status': 'queued'})
    def test_landing_players_use_published_fallback_when_primary_cache_is_missing(self, mock_queue_warm):
        cache.set(landing_player_published_cache_key('random', LANDING_PLAYER_LIMIT), [
            {'name': 'published-player'}
        ], timeout=None)
        cache.set(landing_player_published_metadata_key('random', LANDING_PLAYER_LIMIT), {
            'ttl_seconds': LANDING_PLAYER_CACHE_TTL,
            'cached_at': '2026-03-25T00:00:00+00:00',
            'expires_at': '2026-03-25T12:00:00+00:00',
        }, timeout=None)

        with patch('warships.landing._build_random_landing_players') as mock_builder:
            payload, metadata = get_landing_players_payload_with_cache_metadata(
                'random', LANDING_PLAYER_LIMIT)

        self.assertEqual(payload, [{'name': 'published-player'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)
        mock_builder.assert_not_called()
        mock_queue_warm.assert_called_once_with(realm='na')

    def test_landing_clan_primary_cache_hit_backfills_published_fallback(self):
        cache.set(realm_cache_key('na', LANDING_CLANS_CACHE_KEY),
                  [{'name': 'cached'}], 60)
        cache.set(realm_cache_key('na', LANDING_CLANS_CACHE_METADATA_KEY), {
            'ttl_seconds': LANDING_CLAN_CACHE_TTL,
            'cached_at': '2026-03-25T00:00:00+00:00',
            'expires_at': '2026-03-25T12:00:00+00:00',
        }, 60)

        payload, metadata = get_landing_clans_payload_with_cache_metadata()

        self.assertEqual(payload, [{'name': 'cached'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        self.assertEqual(cache.get(realm_cache_key('na', LANDING_CLANS_PUBLISHED_CACHE_KEY)), [
                         {'name': 'cached'}])
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_CLANS_PUBLISHED_METADATA_KEY)))

    def test_landing_player_primary_cache_hit_backfills_published_fallback(self):
        player_cache_key = landing_player_cache_key(
            'random', LANDING_PLAYER_LIMIT)
        player_metadata_key = f'{player_cache_key}:meta'
        cache.set(player_cache_key, [{'name': 'cached-player'}], 60)
        cache.set(player_metadata_key, {
            'ttl_seconds': LANDING_PLAYER_CACHE_TTL,
            'cached_at': '2026-03-25T00:00:00+00:00',
            'expires_at': '2026-03-25T12:00:00+00:00',
        }, 60)

        payload, metadata = get_landing_players_payload_with_cache_metadata(
            'random', LANDING_PLAYER_LIMIT)

        self.assertEqual(payload, [{'name': 'cached-player'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)
        self.assertEqual(cache.get(landing_player_published_cache_key(
            'random', LANDING_PLAYER_LIMIT)), [{'name': 'cached-player'}])
        self.assertIsNotNone(cache.get(
            landing_player_published_metadata_key('random', LANDING_PLAYER_LIMIT)))

    def test_warm_landing_page_content_force_refresh_republishes_recent_surfaces_without_deleting(self):
        cache.set(realm_cache_key('na', LANDING_RECENT_CLANS_CACHE_KEY), [
                  {'name': 'old-clan'}], LANDING_CACHE_TTL)
        cache.set(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY), [
                  {'name': 'old-player'}], LANDING_CACHE_TTL)
        cache.set(realm_cache_key('na', LANDING_CLANS_DIRTY_KEY),
                  'dirty', timeout=None)
        cache.set(realm_cache_key('na', LANDING_PLAYERS_DIRTY_KEY),
                  'dirty', timeout=None)
        cache.set(realm_cache_key(
            'na', LANDING_RECENT_CLANS_DIRTY_KEY), 'dirty', timeout=None)
        cache.set(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY), 'dirty', timeout=None)

        with patch('warships.landing.get_landing_clans_payload', return_value=[]), patch('warships.landing.get_landing_best_clans_payload', return_value=[]), patch('warships.landing.get_landing_players_payload', return_value=[]), patch('warships.landing._build_recent_clans', return_value=[{'name': 'new-clan'}]), patch('warships.landing._build_recent_players', return_value=[{'name': 'new-player'}]):
            from warships.landing import warm_landing_page_content

            result = warm_landing_page_content(
                force_refresh=True, include_recent=True)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(cache.get(realm_cache_key('na', LANDING_RECENT_CLANS_CACHE_KEY)), [
                         {'name': 'new-clan'}])
        self.assertEqual(cache.get(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY)), [
                         {'name': 'new-player'}])
        self.assertIsNone(
            cache.get(realm_cache_key('na', LANDING_CLANS_DIRTY_KEY)))
        self.assertIsNone(
            cache.get(realm_cache_key('na', LANDING_PLAYERS_DIRTY_KEY)))
        self.assertIsNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_CLANS_DIRTY_KEY)))
        self.assertIsNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY)))

    def test_get_landing_recent_players_payload_rebuilds_when_dirty(self):
        cache.set(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY), [
                  {'name': 'old-player'}], LANDING_CACHE_TTL)
        cache.set(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY), 'dirty', timeout=None)

        with patch('warships.landing._build_recent_players', return_value=[{'name': 'new-player'}]) as mock_build_recent_players:
            from warships.landing import get_landing_recent_players_payload

            payload = get_landing_recent_players_payload()

        self.assertEqual(payload, [{'name': 'new-player'}])
        self.assertEqual(cache.get(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY)), [
                         {'name': 'new-player'}])
        self.assertIsNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY)))
        mock_build_recent_players.assert_called_once_with(realm='na')

    def test_get_landing_recent_clans_payload_rebuilds_when_dirty(self):
        cache.set(realm_cache_key('na', LANDING_RECENT_CLANS_CACHE_KEY), [
                  {'name': 'old-clan'}], LANDING_CACHE_TTL)
        cache.set(realm_cache_key(
            'na', LANDING_RECENT_CLANS_DIRTY_KEY), 'dirty', timeout=None)

        with patch('warships.landing._build_recent_clans', return_value=[{'name': 'new-clan'}]) as mock_build_recent_clans:
            from warships.landing import get_landing_recent_clans_payload

            payload = get_landing_recent_clans_payload()

        self.assertEqual(payload, [{'name': 'new-clan'}])
        self.assertEqual(cache.get(realm_cache_key('na', LANDING_RECENT_CLANS_CACHE_KEY)), [
                         {'name': 'new-clan'}])
        self.assertIsNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_CLANS_DIRTY_KEY)))
        mock_build_recent_clans.assert_called_once_with(realm='na')

    def test_normalize_landing_clan_mode_accepts_known_modes(self):
        self.assertEqual(normalize_landing_clan_mode('random'), 'random')
        self.assertEqual(normalize_landing_clan_mode(' BEST '), 'best')

    def test_normalize_landing_clan_mode_rejects_unknown_mode(self):
        with self.assertRaisesMessage(ValueError, 'mode must be one of: random, best'):
            normalize_landing_clan_mode('sigma')

    def test_force_refresh_rebuilds_cached_landing_clans_payload(self):
        with patch('warships.landing._build_landing_clans', side_effect=[[{'name': 'old'}], [{'name': 'new'}]]) as mock_builder:
            first_payload = get_landing_clans_payload()
            refreshed_payload = get_landing_clans_payload(force_refresh=True)

        self.assertEqual(first_payload, [{'name': 'old'}])
        self.assertEqual(refreshed_payload, [{'name': 'new'}])
        self.assertEqual(mock_builder.call_count, 2)
        self.assertEqual(cache.get(realm_cache_key(
            'na', LANDING_CLANS_CACHE_KEY)), [{'name': 'new'}])

    def test_force_refresh_rebuilds_cached_landing_players_payload(self):
        with patch('warships.landing._build_random_landing_players', side_effect=[[{'name': 'old'}], [{'name': 'new'}]]) as mock_builder:
            first_payload = get_landing_players_payload(
                'random', LANDING_PLAYER_LIMIT)
            refreshed_payload = get_landing_players_payload(
                'random', LANDING_PLAYER_LIMIT, force_refresh=True)

        self.assertEqual(first_payload, [{'name': 'old'}])
        self.assertEqual(refreshed_payload, [{'name': 'new'}])
        self.assertEqual(mock_builder.call_count, 2)
        self.assertEqual(cache.get(landing_player_cache_key(
            'random', LANDING_PLAYER_LIMIT)), [{'name': 'new'}])

    def test_random_landing_player_queue_payload_pops_ids_in_order(self):
        cache.set(realm_cache_key('na', LANDING_RANDOM_PLAYER_QUEUE_KEY),
                  [101, 102, 103], timeout=None)

        with patch('warships.landing.resolve_landing_players_by_id_order', return_value=[{'name': 'P1'}, {'name': 'P2'}]), patch('warships.tasks.queue_random_landing_player_queue_refill', return_value={'status': 'queued'}):
            payload, metadata = get_random_landing_player_queue_payload(
                2,
                pop=True,
                schedule_refill=True,
            )

        self.assertEqual(payload, [{'name': 'P1'}, {'name': 'P2'}])
        self.assertEqual(cache.get(realm_cache_key(
            'na', LANDING_RANDOM_PLAYER_QUEUE_KEY)), [103])
        self.assertEqual(metadata['queue_remaining'], 1)
        self.assertTrue(metadata['refill_scheduled'])

    def test_random_landing_clan_queue_payload_uses_zero_ttl_metadata(self):
        with patch('warships.landing.peek_random_landing_clan_ids', return_value=([21, 22], 55)), patch('warships.landing.resolve_landing_clans_by_id_order', return_value=[{'name': 'Clan A'}, {'name': 'Clan B'}]), patch('warships.tasks.queue_random_landing_clan_queue_refill', return_value={'status': 'queued'}):
            payload, metadata = get_random_landing_clan_queue_payload(
                LANDING_CLAN_FEATURED_COUNT,
                pop=False,
                schedule_refill=True,
            )

        self.assertEqual(payload, [{'name': 'Clan A'}, {'name': 'Clan B'}])
        self.assertEqual(metadata['ttl_seconds'], 0)
        self.assertEqual(metadata['queue_remaining'], 55)
        self.assertEqual(metadata['served_count'], 2)
        self.assertTrue(metadata['refill_scheduled'])

    def test_random_landing_clan_queue_payload_pops_ids_in_order(self):
        cache.set(realm_cache_key('na', LANDING_RANDOM_CLAN_QUEUE_KEY),
                  [201, 202, 203], timeout=None)

        with patch('warships.landing.resolve_landing_clans_by_id_order', return_value=[{'name': 'C1'}, {'name': 'C2'}]), patch('warships.tasks.queue_random_landing_clan_queue_refill', return_value={'status': 'queued'}):
            payload, metadata = get_random_landing_clan_queue_payload(
                2,
                pop=True,
                schedule_refill=True,
            )

        self.assertEqual(payload, [{'name': 'C1'}, {'name': 'C2'}])
        self.assertEqual(cache.get(realm_cache_key(
            'na', LANDING_RANDOM_CLAN_QUEUE_KEY)), [203])
        self.assertEqual(metadata['queue_remaining'], 1)
        self.assertTrue(metadata['refill_scheduled'])

    def test_landing_players_endpoint_uses_cached_payload_for_random_mode(self):
        with patch('warships.views.get_landing_players_payload_with_cache_metadata', return_value=(
            [{'name': 'QueuePlayer'}],
            {
                'ttl_seconds': LANDING_PLAYER_CACHE_TTL,
                'cached_at': 'now',
                'expires_at': 'later',
            },
        )) as mock_cached_payload:
            response = self.client.get('/api/landing/players/?mode=random')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{'name': 'QueuePlayer'}])
        self.assertEqual(response['X-Landing-Players-Cache-Mode'], 'random')
        self.assertEqual(
            response['X-Landing-Players-Cache-TTL-Seconds'], str(LANDING_PLAYER_CACHE_TTL))
        self.assertEqual(response['X-Landing-Players-Cache-Cached-At'], 'now')
        self.assertEqual(
            response['X-Landing-Players-Cache-Expires-At'], 'later')
        self.assertNotIn('X-Landing-Queue-Type', response)
        mock_cached_payload.assert_called_once_with(
            mode='random',
            limit=LANDING_PLAYER_LIMIT,
            realm='na',
        )

    def test_landing_clans_endpoint_uses_cached_payload_for_random_mode(self):
        with patch('warships.views.get_landing_clans_payload_with_cache_metadata', return_value=(
            [{'name': 'CachedClan'}],
            {
                'ttl_seconds': 43200,
                'cached_at': 'now',
                'expires_at': 'later',
            },
        )) as mock_cached_payload:
            response = self.client.get('/api/landing/clans/?mode=random')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{'name': 'CachedClan'}])
        self.assertEqual(response['X-Landing-Clans-Cache-Mode'], 'random')
        self.assertEqual(
            response['X-Landing-Clans-Cache-TTL-Seconds'], '43200')
        self.assertEqual(response['X-Landing-Clans-Cache-Cached-At'], 'now')
        self.assertEqual(response['X-Landing-Clans-Cache-Expires-At'], 'later')
        self.assertNotIn('X-Landing-Queue-Type', response)
        mock_cached_payload.assert_called_once_with(realm='na')

    def test_landing_recent_clans_endpoint_accepts_no_trailing_slash(self):
        with patch('warships.views.get_landing_recent_clans_payload', return_value=[{'name': 'Recent Clan'}]) as mock_recent_clans:
            response = self.client.get('/api/landing/recent-clans')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{'name': 'Recent Clan'}])
        mock_recent_clans.assert_called_once_with(realm='na')

    def test_refill_random_landing_player_queue_appends_unique_ids(self):
        cache.set(realm_cache_key('na', LANDING_RANDOM_PLAYER_QUEUE_KEY), [
                  101, 102], timeout=None)

        with patch('warships.landing._get_cached_random_landing_player_eligible_ids', return_value=[101, 102, 103, 104, 105]):
            result = refill_random_landing_player_queue(
                batch_size=2, target_size=5)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['added'], 2)
        queue_ids = cache.get(realm_cache_key(
            'na', LANDING_RANDOM_PLAYER_QUEUE_KEY))
        self.assertEqual(queue_ids[:2], [101, 102])
        self.assertEqual(len(queue_ids), 4)
        self.assertEqual(len(set(queue_ids)), 4)
        self.assertTrue(set(queue_ids[2:]).issubset({103, 104, 105}))

    def test_refill_random_landing_clan_queue_appends_unique_ids(self):
        cache.set(realm_cache_key('na', LANDING_RANDOM_CLAN_QUEUE_KEY), [
                  301, 302], timeout=None)

        with patch('warships.landing._get_cached_random_landing_clan_eligible_ids', return_value=[301, 302, 303, 304, 305]):
            result = refill_random_landing_clan_queue(
                batch_size=2, target_size=5)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['added'], 2)
        queue_ids = cache.get(realm_cache_key(
            'na', LANDING_RANDOM_CLAN_QUEUE_KEY))
        self.assertEqual(queue_ids[:2], [301, 302])
        self.assertEqual(len(queue_ids), 4)
        self.assertEqual(len(set(queue_ids)), 4)
        self.assertTrue(set(queue_ids[2:]).issubset({303, 304, 305}))

    def test_warm_landing_page_content_warms_each_surface_once(self):
        with patch('warships.landing.get_landing_clans_payload', return_value=[
            {'name': 'Random Clan'}
        ]) as mock_random_clans, \
                patch('warships.landing.get_landing_best_clans_payload', return_value=[{'name': 'Best Clan'}]) as mock_best_clans, \
                patch('warships.landing.get_landing_recent_clans_payload', return_value=[{'name': 'Recent Clan'}]) as mock_recent_clans, \
                patch('warships.landing.get_landing_players_payload', side_effect=lambda mode, *a, **kw: [
                    {'name': mode.capitalize()}
                ]) as mock_players, \
                patch('warships.landing.get_landing_recent_players_payload', return_value=[{'name': 'Recent Player'}]) as mock_recent_players:
            from warships.landing import warm_landing_page_content

            result = warm_landing_page_content(force_refresh=True)

        self.assertEqual(result, {
            'status': 'completed',
            'warmed': {
                'clans': 1,
                'clans_best_overall': 1,
                'clans_best_wr': 1,
                'clans_best_cb': 1,
                'recent_clans': 1,
                'players_random': 1,
                'players_best': 1,
                'players_sigma': 1,
                'players_popular': 1,
                'recent_players': 1,
            },
        })
        mock_random_clans.assert_called_once_with(
            force_refresh=True, realm='na')
        best_clan_sorts = [call.kwargs.get('sort')
                           for call in mock_best_clans.call_args_list]
        self.assertCountEqual(best_clan_sorts, ['overall', 'wr', 'cb'])
        for call in mock_best_clans.call_args_list:
            self.assertEqual(call.kwargs.get('force_refresh'), True)
            self.assertEqual(call.kwargs.get('realm'), 'na')
        mock_recent_clans.assert_called_once_with(
            force_refresh=True, realm='na')
        # Surfaces are warmed concurrently so call order is non-deterministic
        player_calls = {
            call.args[0]: call
            for call in mock_players.call_args_list
        }
        self.assertEqual(len(player_calls), 4)
        for mode in ('random', 'best', 'sigma', 'popular'):
            self.assertIn(mode, player_calls)
            self.assertEqual(
                player_calls[mode].args, (mode, LANDING_PLAYER_LIMIT))
            self.assertEqual(player_calls[mode].kwargs, {
                             'force_refresh': True, 'realm': 'na'})
        mock_recent_players.assert_called_once_with(
            force_refresh=True, realm='na')

    @patch('warships.data.warm_clan_entity_caches', return_value=4)
    @patch('warships.data.warm_player_entity_caches', return_value=3)
    def test_warm_landing_best_entity_caches_uses_current_best_cohorts(self, mock_warm_players, mock_warm_clans):
        with patch('warships.landing.get_landing_players_payload', return_value=[
            {'player_id': 101},
            {'player_id': 102},
            {'player_id': 103},
        ]) as mock_best_players, patch('warships.landing.get_landing_best_clans_payload', return_value=[
            {'clan_id': 201},
            {'clan_id': 202},
            {'clan_id': 203},
            {'clan_id': 204},
        ]) as mock_best_clans:
            result = warm_landing_best_entity_caches(
                player_limit=25, clan_limit=99)

        self.assertEqual(result, {
            'status': 'completed',
            'warmed': {
                'players': 3,
                'clans': 4,
            },
            'candidate_counts': {
                'players': 3,
                'clans': 4,
            },
        })
        mock_best_players.assert_called_once_with('best', LANDING_PLAYER_LIMIT)
        mock_best_clans.assert_called_once_with()
        mock_warm_players.assert_called_once_with(
            [101, 102, 103], force_refresh=False)
        mock_warm_clans.assert_called_once_with(
            [201, 202, 203, 204], force_refresh=False)
