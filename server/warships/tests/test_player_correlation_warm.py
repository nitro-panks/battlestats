from django.test import SimpleTestCase
from unittest.mock import patch

from warships import data


class TierTypeWarmGateTests(SimpleTestCase):
    """The tier-type population rebuild is a ~8 min full scan, so the periodic
    warmer must only force it when the cache is stale or empty — and it MUST
    force it when empty (the asia `tracked_population=0` freeze), since the
    durable `published` fallback otherwise serves the empty payload forever."""

    @patch('warships.data._fetch_player_tier_type_population_correlation')
    @patch('warships.data.cache')
    def test_skips_rebuild_when_fresh_cache_is_nonempty(self, mock_cache, mock_fetch):
        mock_cache.get.return_value = {'tracked_population': 12345, 'tiles': [{}]}

        result = data.warm_player_tier_type_population_correlation(realm='na')

        self.assertEqual(result['tracked_population'], 12345)
        mock_fetch.assert_not_called()  # no 8-min scan when fresh + populated

    @patch('warships.data._fetch_player_tier_type_population_correlation')
    @patch('warships.data.cache')
    def test_forces_rebuild_when_cache_is_empty_population(self, mock_cache, mock_fetch):
        # A realm frozen at tracked_population=0 must trigger a real rebuild.
        mock_cache.get.return_value = {'tracked_population': 0, 'tiles': []}
        mock_fetch.return_value = {'tracked_population': 9000}

        result = data.warm_player_tier_type_population_correlation(realm='asia')

        self.assertEqual(result['tracked_population'], 9000)
        mock_fetch.assert_called_once_with(realm='asia', force_rebuild=True)

    @patch('warships.data._fetch_player_tier_type_population_correlation')
    @patch('warships.data.cache')
    def test_forces_rebuild_when_cache_missing(self, mock_cache, mock_fetch):
        mock_cache.get.return_value = None  # TTL expired
        mock_fetch.return_value = {'tracked_population': 7}

        data.warm_player_tier_type_population_correlation(realm='eu')

        mock_fetch.assert_called_once_with(realm='eu', force_rebuild=True)


class CorrelationForceRebuildBypassTests(SimpleTestCase):
    """`force_rebuild=True` must skip the cached/published read short-circuit so
    a stale or empty durable `published` payload can actually be replaced."""

    @patch('warships.data._build_tier_type_y_values', return_value=[10])
    @patch('warships.data._build_tier_type_x_labels', return_value=[])
    @patch('warships.data.Player')
    @patch('warships.data.cache')
    def test_tier_type_force_rebuild_ignores_published_fallback(
        self, mock_cache, mock_player, _mock_x, _mock_y,
    ):
        # Published holds a stale payload; without the bypass this would be
        # returned verbatim. force_rebuild must recompute instead.
        mock_cache.get.return_value = {'tracked_population': 999, 'stale': True}
        mock_player.objects.filter.return_value.values_list.return_value.iterator.return_value = iter([])

        with patch('warships.data.transaction.atomic'), patch('warships.data._elevated_work_mem'):
            result = data._fetch_player_tier_type_population_correlation(
                realm='asia', force_rebuild=True)

        # Recomputed from an empty population, NOT the stale published payload.
        self.assertNotIn('stale', result)
        self.assertEqual(result['tracked_population'], 0)
        self.assertEqual(result['tiles'], [])
