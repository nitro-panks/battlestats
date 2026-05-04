from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import BEST_CLAN_WR_MIN_CB_BATTLES, score_best_clans, summarize_clan_battle_activity_badge, warm_landing_best_entity_caches
from warships.landing import LANDING_CACHE_TTL, LANDING_CLAN_CACHE_TTL, LANDING_CLAN_FEATURED_COUNT, LANDING_CLAN_MIN_TOTAL_BATTLES, LANDING_CLANS_BEST_CACHE_KEY, LANDING_CLANS_BEST_CACHE_METADATA_KEY, LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY, LANDING_CLANS_BEST_PUBLISHED_METADATA_KEY, LANDING_CLANS_CACHE_KEY, LANDING_CLANS_CACHE_METADATA_KEY, LANDING_CLANS_DIRTY_KEY, LANDING_CLANS_PUBLISHED_CACHE_KEY, LANDING_CLANS_PUBLISHED_METADATA_KEY, LANDING_PLAYER_CACHE_TTL, LANDING_PLAYER_LIMIT, LANDING_PLAYERS_DIRTY_KEY, LANDING_RANDOM_CLAN_QUEUE_KEY, LANDING_RANDOM_PLAYER_QUEUE_KEY, LANDING_RECENT_CLANS_CACHE_KEY, LANDING_RECENT_CLANS_DIRTY_KEY, LANDING_RECENT_PLAYERS_CACHE_KEY, LANDING_RECENT_PLAYERS_LOOKBACK_DAYS, _calculate_landing_best_score, _ranked_quality_score, get_landing_best_clans_payload_with_cache_metadata, get_landing_clans_payload, get_landing_clans_payload_with_cache_metadata, get_landing_players_payload, get_landing_players_payload_with_cache_metadata, get_random_landing_clan_queue_payload, get_random_landing_player_queue_payload, invalidate_landing_clan_caches, invalidate_landing_player_caches, landing_best_clan_cache_key, landing_best_clan_cache_metadata_key, landing_best_clan_published_cache_key, landing_best_clan_published_metadata_key, landing_player_cache_key, landing_player_cache_metadata_key, landing_player_published_cache_key, landing_player_published_metadata_key, materialize_landing_player_best_snapshot, normalize_landing_clan_best_sort, normalize_landing_clan_limit, normalize_landing_clan_mode, normalize_landing_player_best_sort, normalize_landing_player_limit, normalize_landing_player_mode, refill_random_landing_clan_queue, refill_random_landing_player_queue
from warships.models import Clan, LandingPlayerBestSnapshot, Player, PlayerExplorerSummary, realm_cache_key


class LandingHelperTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_normalize_landing_player_mode_accepts_known_modes(self):
        self.assertEqual(normalize_landing_player_mode('random'), 'random')
        self.assertEqual(normalize_landing_player_mode(' BEST '), 'best')
        self.assertEqual(normalize_landing_player_mode(' sigma '), 'sigma')
        self.assertEqual(normalize_landing_player_mode(None), 'random')

    def test_normalize_landing_player_mode_rejects_unknown_mode(self):
        with self.assertRaisesMessage(ValueError, 'mode must be one of: random, best, sigma, popular'):
            normalize_landing_player_mode('hot')

    def test_normalize_landing_player_best_sort_accepts_known_modes(self):
        self.assertEqual(
            normalize_landing_player_best_sort('overall'), 'overall')
        self.assertEqual(
            normalize_landing_player_best_sort(' Ranked '), 'ranked')
        self.assertEqual(normalize_landing_player_best_sort(
            'efficiency'), 'efficiency')
        self.assertEqual(normalize_landing_player_best_sort(None), 'overall')

    def test_normalize_landing_player_best_sort_rejects_unknown_mode(self):
        with self.assertRaisesMessage(ValueError, 'sort must be one of: overall, ranked, efficiency, wr, cb'):
            normalize_landing_player_best_sort('sigma')

    def test_normalize_landing_player_best_sort_rejects_abs(self):
        with self.assertRaisesMessage(ValueError, 'sort must be one of: overall, ranked, efficiency, wr, cb'):
            normalize_landing_player_best_sort('abs')

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
        with self.assertRaisesMessage(ValueError, 'sort must be one of: overall, wr'):
            normalize_landing_clan_best_sort('activity')

    def test_normalize_landing_clan_best_sort_rejects_abs(self):
        with self.assertRaisesMessage(ValueError, 'sort must be one of: overall, wr'):
            normalize_landing_clan_best_sort('abs')

    def test_normalize_landing_clan_best_sort_rejects_cb(self):
        with self.assertRaisesMessage(ValueError, 'sort must be one of: overall, wr'):
            normalize_landing_clan_best_sort('cb')

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
        # Recent is now a 7-day rollup rebuilt by a dedicated 3h periodic
        # warmer, so per-row invalidations leave it untouched. The
        # `include_recent` kwarg is preserved as a no-op for callsite
        # compatibility.
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
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_invalidate_landing_player_caches_preserves_namespace_by_default(self, mock_delay):
        # Per-row invalidations must NOT bump the namespace; doing so orphans
        # the published fallback and forces inline rebuilds under load.
        # See runbook-landing-random-cold-queue-2026-04-07.md
        original_key = landing_player_cache_key('best', 5, sort='ranked')
        original_published_key = landing_player_published_cache_key(
            'best', 5, sort='ranked')

        invalidate_landing_player_caches()

        rebuilt_key = landing_player_cache_key('best', 5, sort='ranked')
        rebuilt_published_key = landing_player_published_cache_key(
            'best', 5, sort='ranked')
        self.assertEqual(original_key, rebuilt_key)
        self.assertEqual(original_published_key, rebuilt_published_key)
        self.assertIsNotNone(
            cache.get(realm_cache_key('na', LANDING_PLAYERS_DIRTY_KEY)))
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_invalidate_landing_player_caches_bumps_namespace_when_requested(self, mock_delay):
        original_key = landing_player_cache_key('best', 5, sort='ranked')
        invalidate_landing_player_caches(bump_namespace=True)
        rebuilt_key = landing_player_cache_key('best', 5, sort='ranked')
        self.assertNotEqual(original_key, rebuilt_key)

    def test_best_player_payload_uses_materialized_snapshot_without_recomputing(self):
        LandingPlayerBestSnapshot.objects.update_or_create(
            realm='na',
            sort='ranked',
            defaults={'payload_json': [
                {'name': 'SnapshotLeader', 'player_id': 4001, 'pvp_ratio': 62.1},
                {'name': 'SnapshotRunnerUp', 'player_id': 4002, 'pvp_ratio': 61.4},
            ]},
        )

        with patch('warships.landing.materialize_landing_player_best_snapshot') as mock_materialize:
            payload, _metadata = get_landing_players_payload_with_cache_metadata(
                mode='best',
                sort='ranked',
                limit=1,
                force_refresh=True,
            )

        self.assertEqual(payload, [
            {'name': 'SnapshotLeader', 'player_id': 4001, 'pvp_ratio': 62.1},
        ])
        mock_materialize.assert_not_called()

    def test_materialize_landing_player_best_snapshot_persists_ranked_order(self):
        now = timezone.now()
        last_battle_date = now.date()

        gold_leader = Player.objects.create(
            name='GoldLeader',
            player_id=5101,
            realm='na',
            is_hidden=False,
            total_battles=6200,
            pvp_battles=5400,
            pvp_ratio=58.0,
            days_since_last_battle=4,
            last_battle_date=last_battle_date,
            ranked_updated_at=now,
            ranked_json=[
                {
                    'highest_league_name': 'Gold',
                    'total_battles': 40,
                    'total_wins': 24,
                    'win_rate': 60.0,
                },
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=gold_leader,
            player_score=8.2,
            ranked_seasons_participated=1,
            latest_ranked_battles=40,
            highest_ranked_league_recent='Gold',
        )

        silver_volume = Player.objects.create(
            name='SilverVolume',
            player_id=5102,
            realm='na',
            is_hidden=False,
            total_battles=7000,
            pvp_battles=5600,
            pvp_ratio=63.0,
            days_since_last_battle=4,
            last_battle_date=last_battle_date,
            ranked_updated_at=now,
            ranked_json=[
                {
                    'highest_league_name': 'Silver',
                    'total_battles': 80,
                    'total_wins': 56,
                    'win_rate': 70.0,
                },
                {
                    'highest_league_name': 'Silver',
                    'total_battles': 60,
                    'total_wins': 42,
                    'win_rate': 70.0,
                },
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=silver_volume,
            player_score=8.9,
            ranked_seasons_participated=2,
            latest_ranked_battles=80,
            highest_ranked_league_recent='Silver',
        )

        result = materialize_landing_player_best_snapshot('ranked')

        snapshot = LandingPlayerBestSnapshot.objects.get(
            realm='na', sort='ranked')
        self.assertEqual(result['count'], 2)
        self.assertEqual(
            [row['name'] for row in snapshot.payload_json[:2]],
            ['GoldLeader', 'SilverVolume'],
        )

    def test_materialize_landing_player_best_snapshot_persists_cb_order(self):
        last_battle_date = timezone.now().date()

        durable_player = Player.objects.create(
            name='SnapshotCbDurable',
            player_id=5201,
            realm='na',
            is_hidden=False,
            total_battles=9000,
            pvp_battles=7500,
            pvp_ratio=58.0,
            days_since_last_battle=3,
            last_battle_date=last_battle_date,
            battles_json=[
                {'ship_tier': 8, 'pvp_battles': 7500, 'wins': 4350},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=durable_player,
            player_score=5.1,
            clan_battle_total_battles=2400,
            clan_battle_seasons_participated=8,
            clan_battle_overall_win_rate=60.0,
        )

        streaky_player = Player.objects.create(
            name='SnapshotCbStreaky',
            player_id=5202,
            realm='na',
            is_hidden=False,
            total_battles=9200,
            pvp_battles=7600,
            pvp_ratio=58.0,
            days_since_last_battle=3,
            last_battle_date=last_battle_date,
            battles_json=[
                {'ship_tier': 8, 'pvp_battles': 7600, 'wins': 4408},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=streaky_player,
            player_score=8.6,
            clan_battle_total_battles=240,
            clan_battle_seasons_participated=8,
            clan_battle_overall_win_rate=60.0,
        )

        result = materialize_landing_player_best_snapshot('cb')

        snapshot = LandingPlayerBestSnapshot.objects.get(
            realm='na', sort='cb')
        self.assertEqual(result['count'], 2)
        self.assertEqual(
            [row['name'] for row in snapshot.payload_json[:2]],
            ['SnapshotCbDurable', 'SnapshotCbStreaky'],
        )

    def test_ranked_quality_score_no_ranked_data(self):
        self.assertEqual(_ranked_quality_score({}), 0.0)
        self.assertEqual(_ranked_quality_score({
            'ranked_seasons_participated': 0,
            'latest_ranked_battles': 0,
        }), 0.0)

    def test_ranked_quality_score_gold_high_volume(self):
        row = {
            'ranked_seasons_participated': 28,
            'latest_ranked_battles': 135,
            'highest_ranked_league_recent': 'Gold',
            'ranked_overall_win_rate': 53.5,
        }
        score = _ranked_quality_score(row)
        # Gold=1.0 (35%), WR=(53.5-45)/20=0.425 (25%), depth=28/15→1.0 (25%), volume=saturated (15%)
        self.assertGreater(score, 0.8)
        self.assertLess(score, 0.9)

    def test_ranked_quality_score_bronze_minimal(self):
        row = {
            'ranked_seasons_participated': 1,
            'latest_ranked_battles': 2,
            'highest_ranked_league_recent': 'Bronze',
            'ranked_overall_win_rate': 50.0,
        }
        score = _ranked_quality_score(row)
        self.assertLess(score, 0.25)

    def test_best_score_no_ranked_multiplier_is_one(self):
        """Non-ranked player's score should not be boosted by ranked multiplier."""
        row = {
            'high_tier_pvp_ratio': 60.0,
            'player_score': 7.5,
            'efficiency_rank_percentile': 90.0,
            'shrunken_efficiency_strength': 0.8,
            'high_tier_pvp_battles': 3000,
            'is_clan_battle_player': False,
            'clan_battle_win_rate': None,
            'pvp_battles': 5000,
            'ranked_seasons_participated': 0,
            'latest_ranked_battles': 0,
            'highest_ranked_league_recent': None,
            'ranked_overall_win_rate': None,
        }
        score = _calculate_landing_best_score(row)
        self.assertGreater(score, 0.0)
        # Ranked multiplier is 1.0, so score equals base * comp_share
        self.assertEqual(_ranked_quality_score(row), 0.0)

    def test_best_score_gold_ranked_beats_non_ranked(self):
        """Gold ranked player with same base stats should score higher."""
        base_row = {
            'high_tier_pvp_ratio': 60.0,
            'player_score': 7.5,
            'efficiency_rank_percentile': 90.0,
            'shrunken_efficiency_strength': 0.8,
            'high_tier_pvp_battles': 3000,
            'is_clan_battle_player': False,
            'clan_battle_win_rate': None,
            'pvp_battles': 5000,
            'ranked_seasons_participated': 0,
            'latest_ranked_battles': 0,
            'highest_ranked_league_recent': None,
            'ranked_overall_win_rate': None,
        }
        gold_row = {
            **base_row,
            'ranked_seasons_participated': 20,
            'latest_ranked_battles': 100,
            'highest_ranked_league_recent': 'Gold',
            'ranked_overall_win_rate': 55.0,
        }
        non_ranked_score = _calculate_landing_best_score(base_row)
        gold_score = _calculate_landing_best_score(gold_row)
        self.assertGreater(gold_score, non_ranked_score)

    def test_materialize_landing_player_best_snapshot_ranked_multiplier_affects_overall_order(self):
        """Ranked Gold player should outrank non-ranked player with similar base stats."""
        last_battle_date = timezone.now().date()

        non_ranked = Player.objects.create(
            name='NoRank',
            player_id=5301,
            realm='na',
            is_hidden=False,
            total_battles=8000,
            pvp_battles=6000,
            pvp_ratio=59.0,
            days_since_last_battle=3,
            last_battle_date=last_battle_date,
            battles_json=[
                {'ship_tier': 8, 'pvp_battles': 4000, 'wins': 2360},
                {'ship_tier': 10, 'pvp_battles': 2000, 'wins': 1180},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=non_ranked,
            player_score=8.0,
            ranked_seasons_participated=0,
            latest_ranked_battles=0,
        )

        gold_ranked = Player.objects.create(
            name='GoldRanked',
            player_id=5302,
            realm='na',
            is_hidden=False,
            total_battles=8000,
            pvp_battles=6000,
            pvp_ratio=59.0,
            days_since_last_battle=3,
            last_battle_date=last_battle_date,
            ranked_json=[
                {
                    'highest_league_name': 'Gold',
                    'total_battles': 80,
                    'total_wins': 48,
                    'win_rate': 60.0,
                },
                {
                    'highest_league_name': 'Gold',
                    'total_battles': 50,
                    'total_wins': 28,
                    'win_rate': 56.0,
                },
            ],
            battles_json=[
                {'ship_tier': 8, 'pvp_battles': 4000, 'wins': 2360},
                {'ship_tier': 10, 'pvp_battles': 2000, 'wins': 1180},
            ],
        )
        PlayerExplorerSummary.objects.create(
            player=gold_ranked,
            player_score=8.0,
            ranked_seasons_participated=2,
            latest_ranked_battles=80,
            highest_ranked_league_recent='Gold',
        )

        result = materialize_landing_player_best_snapshot('overall')

        snapshot = LandingPlayerBestSnapshot.objects.get(
            realm='na', sort='overall')
        self.assertEqual(result['count'], 2)
        names = [row['name'] for row in snapshot.payload_json[:2]]
        self.assertEqual(names, ['GoldRanked', 'NoRank'])

    def test_landing_clans_use_twelve_hour_cache_ttl(self):
        _, metadata = get_landing_clans_payload_with_cache_metadata()
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        self.assertEqual(LANDING_CLAN_CACHE_TTL, 60 * 60 * 6)

    def test_best_clan_cache_keys_are_sort_specific(self):
        self.assertEqual(landing_best_clan_cache_key('overall'),
                         realm_cache_key('na', LANDING_CLANS_BEST_CACHE_KEY))
        self.assertEqual(landing_best_clan_published_cache_key(
            'overall'), realm_cache_key('na', LANDING_CLANS_BEST_PUBLISHED_CACHE_KEY))
        self.assertEqual(landing_best_clan_cache_key(
            'wr'), realm_cache_key('na', 'landing:clans:best:v2:wr'))

    def test_best_clan_sorts_rank_independent_top_results(self):
        cache.clear()
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

        overall_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
            force_refresh=True, sort='overall')
        wr_payload, _ = get_landing_best_clans_payload_with_cache_metadata(
            force_refresh=True, sort='wr')

        self.assertEqual(overall_payload[0]['name'], 'OverallLeader')
        self.assertEqual(wr_payload[0]['name'], 'WRLeader')
        self.assertIn('avg_cb_battles', wr_payload[0])

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

    def test_clan_battle_activity_badge_requires_recent_sustained_participation(self):
        today = timezone.now().date()

        def season_date(offset_days: int) -> str:
            return (today - timedelta(days=offset_days)).strftime('%Y-%m-%d')

        season_meta = {
            season_id: {
                'name': f'Season {season_id}',
                'label': f'S{season_id}',
                'start_date': season_date((12 - season_id) * 90 + 21),
                'end_date': season_date((12 - season_id) * 90),
            }
            for season_id in range(1, 13)
        }

        sustained_rows = [
            {'season_id': season_id, 'participants': 8, 'roster_battles': 28}
            for season_id in (12, 11, 10)
        ]
        low_share_rows = [
            {'season_id': season_id, 'participants': 2, 'roster_battles': 28}
            for season_id in (12, 11, 10)
        ]
        one_season_spike = [
            {'season_id': 12, 'participants': 12, 'roster_battles': 40}
        ]

        with patch('warships.data._get_clan_battle_seasons_metadata', return_value=season_meta):
            sustained = summarize_clan_battle_activity_badge(
                sustained_rows,
                total_members=40,
                reference_date=today,
            )
            low_share = summarize_clan_battle_activity_badge(
                low_share_rows,
                total_members=40,
                reference_date=today,
            )
            spike = summarize_clan_battle_activity_badge(
                one_season_spike,
                total_members=40,
                reference_date=today,
            )

        self.assertTrue(sustained['is_clan_battle_active'])
        self.assertFalse(low_share['is_clan_battle_active'])
        self.assertFalse(spike['is_clan_battle_active'])

    def test_best_clan_payload_marks_clan_battle_activity_badges(self):
        now = timezone.now()

        def create_candidate(clan_id: int, name: str):
            clan = Clan.objects.create(
                clan_id=clan_id,
                name=name,
                tag=name[:5].upper(),
                members_count=40,
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

        create_candidate(7701, 'CBBadgeLeader')
        create_candidate(7702, 'CBBadgeSleeper')

        today = timezone.now().date()

        def season_date(offset_days: int) -> str:
            return (today - timedelta(days=offset_days)).strftime('%Y-%m-%d')

        season_meta = {
            season_id: {
                'name': f'Season {season_id}',
                'label': f'S{season_id}',
                'start_date': season_date((12 - season_id) * 90 + 21),
                'end_date': season_date((12 - season_id) * 90),
            }
            for season_id in range(1, 13)
        }

        season_rows_by_clan = {
            '7701': [
                {'season_id': season_id, 'participants': 8,
                    'roster_battles': 28, 'roster_win_rate': 57.0}
                for season_id in (12, 11, 10)
            ],
            '7702': [
                {'season_id': 12, 'participants': 12,
                    'roster_battles': 40, 'roster_win_rate': 62.0}
            ],
        }

        # Pre-populate the clan battle summary cache so the cache-only badge
        # path resolves on hit (production: warmed by background refresh).
        from warships.data import _get_clan_battle_summary_cache_key
        for clan_id, rows in season_rows_by_clan.items():
            cache.set(_get_clan_battle_summary_cache_key(clan_id), rows, 600)

        with patch('warships.data._get_clan_battle_seasons_metadata', return_value=season_meta):
            payload, _ = get_landing_best_clans_payload_with_cache_metadata(
                force_refresh=True,
                sort='overall',
            )

        badge_by_name = {
            row['name']: row.get('is_clan_battle_active')
            for row in payload[:2]
        }
        self.assertTrue(badge_by_name['CBBadgeLeader'])
        self.assertFalse(badge_by_name['CBBadgeSleeper'])

    def test_landing_clan_badges_cache_miss_defers_to_async_refresh(self):
        """Cache miss on the hot path must NOT fire synchronous WG API calls.

        Regression guard for the gunicorn 30s timeout that hit
        /api/landing/clans?mode=random when the clan battle summary cache was
        cold. The fix routes misses through queue_clan_battle_summary_refresh
        instead of refresh_clan_battle_seasons_cache.
        """
        from warships.landing import _attach_clan_battle_activity_badges

        rows = [
            {'clan_id': 9001, 'name': 'ColdCacheClan', 'members_count': 30},
            {'clan_id': 9002, 'name': 'AlsoCold', 'members_count': 25},
        ]

        with patch('warships.data.refresh_clan_battle_seasons_cache') as sync_refresh, \
                patch('warships.tasks.queue_clan_battle_summary_refresh', return_value={'status': 'queued'}) as queue_refresh:
            result = _attach_clan_battle_activity_badges(rows, realm='na')

        # No synchronous WG API fan-out from the request thread.
        sync_refresh.assert_not_called()
        # Both cache misses scheduled async refreshes.
        self.assertEqual(queue_refresh.call_count, 2)
        # Default badge is False on miss; the page renders without blocking.
        self.assertFalse(result[0]['is_clan_battle_active'])
        self.assertFalse(result[1]['is_clan_battle_active'])

    def test_all_landing_player_modes_use_six_hour_cache_ttl(self):
        _, best_meta = get_landing_players_payload_with_cache_metadata(
            'best', LANDING_PLAYER_LIMIT)
        self.assertEqual(best_meta['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)

        _, sigma_meta = get_landing_players_payload_with_cache_metadata(
            'sigma', LANDING_PLAYER_LIMIT)
        self.assertEqual(sigma_meta['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)
        self.assertEqual(LANDING_PLAYER_CACHE_TTL, 60 * 60 * 6)

    def test_best_player_cache_keys_are_sort_specific(self):
        self.assertEqual(
            landing_player_cache_key('best', LANDING_PLAYER_LIMIT),
            landing_player_cache_key(
                'best', LANDING_PLAYER_LIMIT, sort='overall'),
        )
        self.assertEqual(
            landing_player_cache_key('sigma', LANDING_PLAYER_LIMIT),
            landing_player_cache_key(
                'best', LANDING_PLAYER_LIMIT, sort='efficiency'),
        )
        self.assertNotEqual(
            landing_player_cache_key(
                'best', LANDING_PLAYER_LIMIT, sort='ranked'),
            landing_player_cache_key('best', LANDING_PLAYER_LIMIT, sort='wr'),
        )

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

    @patch('warships.tasks.queue_landing_page_warm', return_value={'status': 'queued'})
    def test_best_landing_clans_use_published_fallback_while_dirty(self, mock_queue_warm):
        published_cache_key = landing_best_clan_published_cache_key('overall')
        published_metadata_key = landing_best_clan_published_metadata_key(
            'overall')
        cache.set(published_cache_key, [
                  {'name': 'published-best-clan'}], timeout=None)
        cache.set(published_metadata_key, {
            'ttl_seconds': LANDING_CLAN_CACHE_TTL,
            'cached_at': '2026-03-25T00:00:00+00:00',
            'expires_at': '2026-03-25T12:00:00+00:00',
        }, timeout=None)
        cache.set(realm_cache_key('na', LANDING_CLANS_DIRTY_KEY),
                  'dirty', timeout=None)

        with patch('warships.landing._build_best_landing_clans') as mock_builder:
            payload, metadata = get_landing_best_clans_payload_with_cache_metadata(
                sort='overall')

        self.assertEqual(payload, [{'name': 'published-best-clan'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        mock_builder.assert_not_called()
        mock_queue_warm.assert_called_once_with(realm='na')

    @patch('warships.tasks.queue_landing_page_warm', return_value={'status': 'queued'})
    def test_best_landing_clans_preserve_non_empty_published_payload_when_primary_is_empty(self, mock_queue_warm):
        primary_cache_key = landing_best_clan_cache_key('overall')
        primary_metadata_key = landing_best_clan_cache_metadata_key('overall')
        published_cache_key = landing_best_clan_published_cache_key('overall')
        published_metadata_key = landing_best_clan_published_metadata_key(
            'overall')
        cache.set(primary_cache_key, [], 60)
        cache.set(primary_metadata_key, {
            'ttl_seconds': LANDING_CLAN_CACHE_TTL,
            'cached_at': '2026-03-26T00:00:00+00:00',
            'expires_at': '2026-03-26T12:00:00+00:00',
        }, 60)
        cache.set(published_cache_key, [
                  {'name': 'durable-best-clan'}], timeout=None)
        cache.set(published_metadata_key, {
            'ttl_seconds': LANDING_CLAN_CACHE_TTL,
            'cached_at': '2026-03-25T00:00:00+00:00',
            'expires_at': '2026-03-25T12:00:00+00:00',
        }, timeout=None)

        payload, metadata = get_landing_best_clans_payload_with_cache_metadata(
            sort='overall')

        self.assertEqual(payload, [{'name': 'durable-best-clan'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_CLAN_CACHE_TTL)
        self.assertEqual(cache.get(published_cache_key),
                         [{'name': 'durable-best-clan'}])
        mock_queue_warm.assert_called_once_with(realm='na')

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

    def test_get_landing_recent_players_payload_serves_cache_without_rebuild(self):
        # The 7-day rollup is rebuilt out-of-band by
        # `warm_landing_recent_players_task`; reads must be pure cache
        # lookups so a rebuild never adds latency to a request.
        cache.set(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY), [
                  {'name': 'cached-player'}], None)

        with patch('warships.landing._build_recent_players') as mock_build:
            from warships.landing import get_landing_recent_players_payload
            payload = get_landing_recent_players_payload()

        self.assertEqual(payload, [{'name': 'cached-player'}])
        mock_build.assert_not_called()

    def test_get_landing_recent_players_payload_force_refresh_rebuilds_and_publishes(self):
        with patch('warships.landing._build_recent_players', return_value=[{'name': 'fresh-player'}]) as mock_build:
            from warships.landing import get_landing_recent_players_payload
            payload = get_landing_recent_players_payload(force_refresh=True)

        self.assertEqual(payload, [{'name': 'fresh-player'}])
        self.assertEqual(cache.get(realm_cache_key('na', LANDING_RECENT_PLAYERS_CACHE_KEY)), [
                         {'name': 'fresh-player'}])
        mock_build.assert_called_once_with(realm='na')

    def test_get_landing_recent_players_payload_cold_cache_builds_inline(self):
        # First-ever read after a Redis flush: no cache, no warmer tick yet.
        # Build inline so the surface isn't empty until the next 3h tick.
        with patch('warships.landing._build_recent_players', return_value=[{'name': 'cold-player'}]) as mock_build:
            from warships.landing import get_landing_recent_players_payload
            payload = get_landing_recent_players_payload()

        self.assertEqual(payload, [{'name': 'cold-player'}])
        mock_build.assert_called_once_with(realm='na')

    def test_get_landing_players_payload_rebuilds_when_dirty_instead_of_serving_published(self):
        cache_key = landing_player_cache_key('best', 5, sort='ranked')
        metadata_key = landing_player_cache_metadata_key(
            'best', 5, sort='ranked')
        published_key = landing_player_published_cache_key(
            'best', 5, sort='ranked')
        published_metadata_key = landing_player_published_metadata_key(
            'best', 5, sort='ranked')
        dirty_key = realm_cache_key('na', LANDING_PLAYERS_DIRTY_KEY)

        cache.set(cache_key, [{'name': 'old-current'}],
                  LANDING_PLAYER_CACHE_TTL)
        cache.set(metadata_key, {
            'cached_at': '2026-01-01T00:00:00',
            'expires_at': '2026-01-01T06:00:00',
            'ttl_seconds': LANDING_PLAYER_CACHE_TTL,
        }, LANDING_PLAYER_CACHE_TTL)
        cache.set(published_key, [{'name': 'old-published'}], timeout=None)
        cache.set(published_metadata_key, {
            'cached_at': '2026-01-01T00:00:00',
            'expires_at': '2026-01-01T06:00:00',
            'ttl_seconds': LANDING_PLAYER_CACHE_TTL,
        }, timeout=None)
        cache.set(dirty_key, 'dirty', timeout=None)

        with patch('warships.landing._build_best_landing_players', return_value=[{'name': 'fresh-player'}]) as mock_build_best:
            payload, metadata = get_landing_players_payload_with_cache_metadata(
                'best', 5, sort='ranked')

        self.assertEqual(payload, [{'name': 'fresh-player'}])
        self.assertEqual(cache.get(cache_key), [{'name': 'fresh-player'}])
        self.assertEqual(cache.get(published_key), [{'name': 'fresh-player'}])
        self.assertEqual(metadata['ttl_seconds'], LANDING_PLAYER_CACHE_TTL)
        self.assertIsNone(cache.get(dirty_key))
        mock_build_best.assert_called_once_with(5, realm='na', sort='ranked')

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
                'ttl_seconds': 21600,
                'cached_at': 'now',
                'expires_at': 'later',
            },
        )) as mock_cached_payload:
            response = self.client.get('/api/landing/clans/?mode=random')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{'name': 'CachedClan'}])
        self.assertEqual(response['X-Landing-Clans-Cache-Mode'], 'random')
        self.assertEqual(
            response['X-Landing-Clans-Cache-TTL-Seconds'], '21600')
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
                'recent_clans': 1,
                'players_random': 1,
                'players_best_overall': 1,
                'players_best_ranked': 1,
                'players_best_efficiency': 1,
                'players_best_wr': 1,
                'players_best_cb': 1,
                'players_popular': 1,
                'recent_players': 1,
            },
        })
        mock_random_clans.assert_called_once_with(
            force_refresh=True, realm='na')
        best_clan_sorts = [call.kwargs.get('sort')
                           for call in mock_best_clans.call_args_list]
        self.assertCountEqual(best_clan_sorts, ['overall', 'wr'])
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
        self.assertEqual(len(player_calls), 3)
        self.assertCountEqual(player_calls.keys(), [
                              'random', 'best', 'popular'])
        self.assertEqual(player_calls['random'].args,
                         ('random', LANDING_PLAYER_LIMIT))
        self.assertEqual(player_calls['popular'].args,
                         ('popular', LANDING_PLAYER_LIMIT))
        best_calls = [
            call for call in mock_players.call_args_list if call.args[0] == 'best']
        self.assertEqual(len(best_calls), 5)
        self.assertCountEqual(
            [call.kwargs.get('sort') for call in best_calls],
            ['overall', 'ranked', 'efficiency', 'wr', 'cb'],
        )
        for call in best_calls:
            self.assertEqual(call.args, ('best', LANDING_PLAYER_LIMIT))
            self.assertEqual(call.kwargs.get('force_refresh'), True)
            self.assertEqual(call.kwargs.get('realm'), 'na')
        self.assertEqual(player_calls['popular'].kwargs, {
            'force_refresh': True,
            'realm': 'na',
        })
        self.assertEqual(player_calls['random'].kwargs, {
            'force_refresh': True,
            'realm': 'na',
        })
        mock_recent_players.assert_called_once_with(
            force_refresh=True, realm='na')

    @patch('warships.data.warm_clan_entity_caches', return_value=4)
    @patch('warships.data.warm_player_entity_caches', return_value=7)
    def test_warm_landing_best_entity_caches_uses_current_best_cohorts(self, mock_warm_players, mock_warm_clans):
        player_payloads = {
            'overall': [{'player_id': 101}, {'player_id': 102}, {'player_id': 103}],
            'ranked': [{'player_id': 103}, {'player_id': 104}],
            'efficiency': [{'player_id': 105}],
            'wr': [{'player_id': 106}],
            'cb': [{'player_id': 107}],
        }
        with patch('warships.landing.get_landing_players_payload', side_effect=lambda mode, limit, sort=None, realm='na', force_refresh=False: player_payloads.get(sort or 'overall', [])) as mock_best_players, patch('warships.landing.get_landing_best_clans_payload', return_value=[
            {'clan_id': 201},
            {'clan_id': 202},
            {'clan_id': 203},
            {'clan_id': 204},
        ]) as mock_best_clans:
            result = warm_landing_best_entity_caches(
                player_limit=25, clan_limit=99, realm='eu')

        self.assertEqual(result, {
            'status': 'completed',
            'realm': 'eu',
            'warmed': {
                'players': 7,
                'clans': 4,
            },
            'candidate_counts': {
                'players': 7,
                'clans': 4,
            },
        })
        best_player_sorts = [call.kwargs.get(
            'sort') for call in mock_best_players.call_args_list]
        self.assertCountEqual(best_player_sorts, [
                              'overall', 'ranked', 'efficiency', 'wr', 'cb'])
        for call in mock_best_players.call_args_list:
            self.assertEqual(call.kwargs.get('realm'), 'eu')
        mock_best_clans.assert_called_once_with(realm='eu')
        mock_warm_players.assert_called_once_with(
            [101, 102, 103, 104, 105, 106, 107], force_refresh=False, realm='eu')
        mock_warm_clans.assert_called_once_with(
            [201, 202, 203, 204], force_refresh=False, realm='eu')


class LandingRecentPlayersRecencyFilterTests(TestCase):
    """Recent sub-sort surfaces the LANDING_RECENT_PLAYERS_LIMIT players who
    have played > MIN_WEEK_BATTLES random battles in the trailing
    LOOKBACK_DAYS-day window, ordered by `Player.last_random_battle_at` desc.
    Eligibility comes from PlayerDailyShipStats; ordering comes from the
    BattleEvent capture hook's `last_random_battle_at`. See
    `agents/runbooks/runbook-recent-players-recency-filter-2026-05-04.md`.
    """

    def setUp(self):
        cache.clear()

    def _make_player(self, *, name, player_id, realm='na', pvp_battles=500,
                     last_random_battle_at=None):
        return Player.objects.create(
            name=name,
            player_id=player_id,
            realm=realm,
            pvp_battles=pvp_battles,
            pvp_ratio=55.0,
            is_hidden=False,
            last_random_battle_at=last_random_battle_at,
        )

    def _add_daily(self, player, *, date, battles, ship_id=1, mode=None):
        from warships.models import PlayerDailyShipStats
        return PlayerDailyShipStats.objects.create(
            player=player,
            date=date,
            ship_id=ship_id,
            mode=mode or PlayerDailyShipStats.MODE_RANDOM,
            battles=battles,
        )

    def test_empty_when_no_daily_rollups_exist(self):
        from warships.landing import _build_recent_players

        self._make_player(name='no_rollups', player_id=1)
        self.assertEqual(_build_recent_players(realm='na'), [])

    def test_orders_by_last_random_battle_at_descending(self):
        from warships.landing import _build_recent_players

        today = timezone.now().date()
        now = timezone.now()
        oldest = self._make_player(name='oldest', player_id=10,
                                   last_random_battle_at=now - timedelta(hours=3))
        middle = self._make_player(name='middle', player_id=11,
                                   last_random_battle_at=now - timedelta(hours=1))
        newest = self._make_player(name='newest', player_id=12,
                                   last_random_battle_at=now)

        # All clear the >10-battles floor inside the window.
        self._add_daily(oldest, date=today, battles=15)
        self._add_daily(middle, date=today, battles=20)
        self._add_daily(newest, date=today, battles=25)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['newest', 'middle', 'oldest'])

    def test_excludes_players_at_or_below_min_battles_floor(self):
        from warships.landing import (
            LANDING_RECENT_PLAYERS_MIN_WEEK_BATTLES, _build_recent_players,
        )

        today = timezone.now().date()
        now = timezone.now()
        floor = LANDING_RECENT_PLAYERS_MIN_WEEK_BATTLES
        too_quiet = self._make_player(name='too_quiet', player_id=20,
                                      last_random_battle_at=now)
        on_floor = self._make_player(name='on_floor', player_id=21,
                                     last_random_battle_at=now - timedelta(minutes=1))
        passes = self._make_player(name='passes', player_id=22,
                                   last_random_battle_at=now - timedelta(minutes=2))

        self._add_daily(too_quiet, date=today, battles=floor - 5)
        self._add_daily(on_floor, date=today, battles=floor)  # strict-gt: dropped
        self._add_daily(passes, date=today, battles=floor + 1)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['passes'])

    def test_excludes_rollups_outside_lookback_window(self):
        from warships.landing import _build_recent_players

        today = timezone.now().date()
        now = timezone.now()
        stale = self._make_player(name='stale', player_id=30,
                                  last_random_battle_at=now)
        fresh = self._make_player(name='fresh', player_id=31,
                                  last_random_battle_at=now - timedelta(hours=2))

        # Stale's 50 battles fell outside the 7d window.
        self._add_daily(stale, date=today - timedelta(days=10), battles=50)
        self._add_daily(fresh, date=today - timedelta(days=1), battles=15)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['fresh'])

    def test_excludes_ranked_mode_rollups(self):
        from warships.landing import _build_recent_players
        from warships.models import PlayerDailyShipStats

        today = timezone.now().date()
        now = timezone.now()
        ranked_only = self._make_player(name='ranked_only', player_id=40,
                                        last_random_battle_at=now)
        random_player = self._make_player(name='random_player', player_id=41,
                                          last_random_battle_at=now - timedelta(minutes=5))

        self._add_daily(ranked_only, date=today, battles=50,
                        mode=PlayerDailyShipStats.MODE_RANKED)
        self._add_daily(random_player, date=today, battles=15)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['random_player'])

    def test_realm_isolation(self):
        from warships.landing import _build_recent_players

        today = timezone.now().date()
        now = timezone.now()
        na_player = self._make_player(name='na_player', player_id=50, realm='na',
                                      last_random_battle_at=now)
        eu_player = self._make_player(name='eu_player', player_id=51, realm='eu',
                                      last_random_battle_at=now)

        self._add_daily(na_player, date=today, battles=15)
        self._add_daily(eu_player, date=today, battles=20)

        self.assertEqual([r['name']
                          for r in _build_recent_players(realm='na')], ['na_player'])
        self.assertEqual([r['name']
                          for r in _build_recent_players(realm='eu')], ['eu_player'])

    def test_lookback_window_constant_is_one_week(self):
        self.assertEqual(LANDING_RECENT_PLAYERS_LOOKBACK_DAYS, 7)

    def test_excludes_players_without_last_random_battle_at(self):
        # If the capture hook never wrote `last_random_battle_at` (rare —
        # it's set on every random BattleEvent emission), the row can't be
        # ordered, so it drops out of the recency-anchored surface.
        from warships.landing import _build_recent_players

        today = timezone.now().date()
        now = timezone.now()
        ordered = self._make_player(name='ordered', player_id=60,
                                    last_random_battle_at=now)
        no_anchor = self._make_player(name='no_anchor', player_id=61,
                                      last_random_battle_at=None)

        self._add_daily(ordered, date=today, battles=15)
        self._add_daily(no_anchor, date=today, battles=15)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['ordered'])

    def test_excludes_rows_above_absolute_max_week_battles(self):
        # Phantom first-observation deltas can dump a player's lifetime
        # battles into a single day's PlayerDailyShipStats row. Any
        # week_battles above the max-plausible cap is dropped to keep the
        # surface honest.
        from warships.landing import (
            LANDING_RECENT_PLAYERS_MAX_WEEK_BATTLES,
            _build_recent_players,
        )

        today = timezone.now().date()
        now = timezone.now()
        phantom = self._make_player(
            name='phantom', player_id=50, pvp_battles=20000,
            last_random_battle_at=now)
        normal = self._make_player(
            name='normal', player_id=51, pvp_battles=20000,
            last_random_battle_at=now - timedelta(minutes=1))

        self._add_daily(phantom, date=today,
                        battles=LANDING_RECENT_PLAYERS_MAX_WEEK_BATTLES + 100)
        self._add_daily(normal, date=today, battles=40)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['normal'])

    def test_excludes_rows_exceeding_lifetime_pvp_battles(self):
        # Definitional bound: you can't play more random battles in a week
        # than you've ever played. Drop rows that violate this.
        from warships.landing import _build_recent_players

        today = timezone.now().date()
        now = timezone.now()
        impossible = self._make_player(
            name='impossible', player_id=60, pvp_battles=300,
            last_random_battle_at=now)
        plausible = self._make_player(
            name='plausible', player_id=61, pvp_battles=5000,
            last_random_battle_at=now - timedelta(minutes=1))

        # 1200 < absolute cap of 1500 so it survives that filter, but
        # exceeds the player's 300 lifetime battles → must be dropped.
        self._add_daily(impossible, date=today, battles=1200)
        self._add_daily(plausible, date=today, battles=80)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['plausible'])

    def test_excludes_hidden_players(self):
        # Players with hidden stats opt out of public discovery surfaces;
        # the recent-players list must skip them even when they topped the
        # 7-day battles ranking.
        from warships.landing import _build_recent_players

        today = timezone.now().date()
        now = timezone.now()
        hidden = self._make_player(name='hidden_top', player_id=80,
                                   last_random_battle_at=now)
        hidden.is_hidden = True
        hidden.save(update_fields=['is_hidden'])
        visible = self._make_player(name='visible_player', player_id=81,
                                    last_random_battle_at=now - timedelta(minutes=1))

        self._add_daily(hidden, date=today, battles=200)
        self._add_daily(visible, date=today, battles=20)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['visible_player'])

    def test_pvp_slack_keeps_borderline_rows_visible(self):
        # The cached pvp_battles can lag the rollup briefly. A small slack
        # window keeps real active players visible while still rejecting
        # the phantom first-observation case.
        from warships.landing import _build_recent_players

        today = timezone.now().date()
        now = timezone.now()
        borderline = self._make_player(
            name='borderline', player_id=70, pvp_battles=200,
            last_random_battle_at=now)

        # week_battles=210 vs pvp_battles=200 → 10 over, well within the
        # 50-battle slack.
        self._add_daily(borderline, date=today, battles=210)

        rows = _build_recent_players(realm='na')
        self.assertEqual([r['name'] for r in rows], ['borderline'])


class QueueLandingPageWarmGateTests(TestCase):
    """Regression coverage for the dispatch gate that prevents the
    self-fanout pileup observed on 2026-04-27 (4581 duplicate
    warm_landing_page_content_task messages in the background queue).
    """

    def setUp(self):
        cache.clear()

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_enqueues_when_no_lock_or_dispatch_key(self, mock_delay):
        from warships.tasks import queue_landing_page_warm

        result = queue_landing_page_warm(realm='na')

        self.assertEqual(result, {'status': 'queued'})
        mock_delay.assert_called_once_with(include_recent=True, realm='na')

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_skips_when_warm_lock_is_held(self, mock_delay):
        from warships.tasks import _landing_page_warm_lock_key, queue_landing_page_warm

        cache.set(_landing_page_warm_lock_key('na'), 'in-flight', 60)

        result = queue_landing_page_warm(realm='na')

        self.assertEqual(
            result, {'status': 'skipped', 'reason': 'already-running'})
        mock_delay.assert_not_called()

    @patch('warships.tasks.warm_landing_page_content_task.delay')
    def test_skips_when_dispatch_key_is_held(self, mock_delay):
        from warships.tasks import _landing_page_warm_dispatch_key, queue_landing_page_warm

        cache.set(_landing_page_warm_dispatch_key('na'), 'queued', 30)

        result = queue_landing_page_warm(realm='na')

        self.assertEqual(
            result, {'status': 'skipped', 'reason': 'already-queued'})
        mock_delay.assert_not_called()

    @patch('warships.tasks.warm_landing_page_content_task.delay',
           side_effect=RuntimeError('broker down'))
    def test_cleans_dispatch_key_on_enqueue_failure(self, mock_delay):
        from warships.tasks import _landing_page_warm_dispatch_key, queue_landing_page_warm

        result = queue_landing_page_warm(realm='na')

        self.assertEqual(
            result, {'status': 'skipped', 'reason': 'enqueue-failed'})
        self.assertIsNone(cache.get(_landing_page_warm_dispatch_key('na')))
        mock_delay.assert_called_once()


class QueueWarmPlayerCorrelationsGateTests(TestCase):
    """Regression coverage for the dispatch gate that prevents cold-cache
    fanout on the player-correlation path (server/warships/data.py:3400 →
    fetch_player_tier_type_correlation). Same bug class as the landing
    warmer fix in commit f0e51d8 — a request-driven .delay() with no
    dedup unbounded the queue under traffic.
    """

    def setUp(self):
        cache.clear()

    @patch('warships.tasks.warm_player_correlations_task.delay')
    def test_enqueues_when_no_lock_or_dispatch_key(self, mock_delay):
        from warships.tasks import queue_warm_player_correlations

        result = queue_warm_player_correlations(realm='na')

        self.assertEqual(result, {'status': 'queued'})
        mock_delay.assert_called_once_with(realm='na')

    @patch('warships.tasks.warm_player_correlations_task.delay')
    def test_skips_when_warm_lock_is_held(self, mock_delay):
        from warships.tasks import (
            _correlation_warm_lock_key, queue_warm_player_correlations,
        )

        cache.set(_correlation_warm_lock_key('na'), 'in-flight', 60)

        result = queue_warm_player_correlations(realm='na')

        self.assertEqual(
            result, {'status': 'skipped', 'reason': 'already-running'})
        mock_delay.assert_not_called()

    @patch('warships.tasks.warm_player_correlations_task.delay')
    def test_skips_when_dispatch_key_is_held(self, mock_delay):
        from warships.tasks import (
            _correlation_warm_dispatch_key, queue_warm_player_correlations,
        )

        cache.set(_correlation_warm_dispatch_key('na'), 'queued', 30)

        result = queue_warm_player_correlations(realm='na')

        self.assertEqual(
            result, {'status': 'skipped', 'reason': 'already-queued'})
        mock_delay.assert_not_called()

    @patch('warships.tasks.warm_player_correlations_task.delay',
           side_effect=RuntimeError('broker down'))
    def test_cleans_dispatch_key_on_enqueue_failure(self, mock_delay):
        from warships.tasks import (
            _correlation_warm_dispatch_key, queue_warm_player_correlations,
        )

        result = queue_warm_player_correlations(realm='na')

        self.assertEqual(
            result, {'status': 'skipped', 'reason': 'enqueue-failed'})
        self.assertIsNone(cache.get(_correlation_warm_dispatch_key('na')))
        mock_delay.assert_called_once()
