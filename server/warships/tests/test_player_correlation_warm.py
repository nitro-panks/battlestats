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


class TierTypePopulationRebuildTests(SimpleTestCase):
    """The rebuild aggregates in Postgres (`_aggregate_tier_type_population_sql`),
    builds tiles/trend from the result, and must bypass the durable `published`
    fallback under force_rebuild. trend is derived from the raw tile_counts."""

    @patch('warships.data._aggregate_tier_type_population_sql')
    @patch('warships.data.cache')
    def test_force_rebuild_builds_from_sql_and_ignores_published(self, mock_cache, mock_sql):
        # Published holds a stale payload; without the bypass it'd be returned verbatim.
        mock_cache.get.return_value = {'tracked_population': 999, 'stale': True}
        # Two ship types, Cruiser spanning two tiers.
        mock_sql.return_value = (
            {('Cruiser', 10): 300, ('Cruiser', 8): 100, ('Destroyer', 9): 200},
            42,
        )

        with patch('warships.data.transaction.atomic'), patch('warships.data._elevated_work_mem'):
            result = data._fetch_player_tier_type_population_correlation(
                realm='asia', force_rebuild=True)

        self.assertNotIn('stale', result)  # not the published payload
        self.assertEqual(result['tracked_population'], 42)
        # tile sums preserved
        tile_counts = {(result['x_labels'][t['x_index']], result['y_values'][t['y_index']]): t['count']
                       for t in result['tiles']}
        self.assertEqual(tile_counts, {('Cruiser', 10): 300, ('Cruiser', 8): 100, ('Destroyer', 9): 200})
        # trend = battle-weighted avg tier per type, derived from raw tiles
        trend = {result['x_labels'][p['x_index']]: (p['avg_tier'], p['count']) for p in result['trend']}
        self.assertEqual(trend['Destroyer'], (9.0, 200))
        # Cruiser: (10*300 + 8*100) / 400 = 3800/400 = 9.5
        self.assertEqual(trend['Cruiser'], (9.5, 400))

    @patch('warships.data._aggregate_tier_type_population_python')
    @patch('warships.data._aggregate_tier_type_population_sql', side_effect=RuntimeError('boom'))
    @patch('warships.data.cache')
    def test_sql_failure_falls_back_to_python_scan(self, mock_cache, _mock_sql, mock_py):
        mock_cache.get.return_value = None
        mock_py.return_value = ({('Battleship', 7): 75}, 3)

        with patch('warships.data.transaction.atomic'), patch('warships.data._elevated_work_mem'):
            result = data._fetch_player_tier_type_population_correlation(
                realm='na', force_rebuild=True)

        mock_py.assert_called_once()  # fell back to the Python scan
        self.assertEqual(result['tracked_population'], 3)


class TierTypeRebuildIntervalFloorTests(SimpleTestCase):
    """F9.4: the full jsonb CROSS JOIN LATERAL scan runs ~400 s/realm on prod
    and the daily Beat always outlives the 12 h fresh-key TTL, so without a
    floor every daily warm re-runs the scan. A rebuild marker (TTL =
    TIER_TYPE_POPULATION_REBUILD_HOURS) bounds the scan to at most one run per
    interval per realm; between rebuilds the warmer serves the durable
    `published` payload. The empty-population rescue (asia freeze) still
    forces a rebuild straight through the marker."""

    def _keyed_cache(self, fresh=None, marker=None, published=None):
        cache_key = data._player_correlation_cache_key(
            data.PLAYER_TIER_TYPE_CACHE_VERSION, realm='na')
        published_key = data._player_correlation_published_cache_key(
            data.PLAYER_TIER_TYPE_CACHE_VERSION, realm='na')
        marker_key = data._tier_type_rebuild_marker_key('na')
        values = {cache_key: fresh, marker_key: marker, published_key: published}
        return lambda key, default=None: values.get(key, default)

    @patch('warships.data._fetch_player_tier_type_population_correlation')
    @patch('warships.data.cache')
    def test_serves_published_within_rebuild_interval(self, mock_cache, mock_fetch):
        published = {'tracked_population': 5555, 'tiles': [{}]}
        mock_cache.get.side_effect = self._keyed_cache(
            fresh=None, marker='2026-07-20T00:00:00', published=published)

        result = data.warm_player_tier_type_population_correlation(realm='na')

        self.assertEqual(result['tracked_population'], 5555)
        mock_fetch.assert_not_called()  # no ~400 s scan inside the interval

    @patch('warships.data._fetch_player_tier_type_population_correlation')
    @patch('warships.data.cache')
    def test_rebuilds_when_marker_present_but_published_empty(self, mock_cache, mock_fetch):
        # Marker alone must not trap a realm at tracked_population=0.
        mock_cache.get.side_effect = self._keyed_cache(
            fresh=None, marker='2026-07-20T00:00:00',
            published={'tracked_population': 0, 'tiles': []})
        mock_fetch.return_value = {'tracked_population': 8}

        result = data.warm_player_tier_type_population_correlation(realm='na')

        self.assertEqual(result['tracked_population'], 8)
        mock_fetch.assert_called_once_with(realm='na', force_rebuild=True)

    @patch('warships.data._fetch_player_tier_type_population_correlation')
    @patch('warships.data.cache')
    def test_marker_set_after_successful_rebuild(self, mock_cache, mock_fetch):
        mock_cache.get.side_effect = self._keyed_cache()
        mock_fetch.return_value = {'tracked_population': 9000}

        data.warm_player_tier_type_population_correlation(realm='na')

        mock_fetch.assert_called_once_with(realm='na', force_rebuild=True)
        marker_key = data._tier_type_rebuild_marker_key('na')
        marker_sets = [c for c in mock_cache.set.call_args_list
                       if c.args[0] == marker_key]
        self.assertEqual(len(marker_sets), 1)
        self.assertEqual(
            marker_sets[0].kwargs.get('timeout'),
            data.TIER_TYPE_POPULATION_REBUILD_HOURS * 3600)

    @patch('warships.data._fetch_player_tier_type_population_correlation')
    @patch('warships.data.cache')
    def test_marker_not_set_when_rebuild_yields_empty_population(self, mock_cache, mock_fetch):
        # An empty result must not start the interval clock — the next daily
        # warm should retry the scan.
        mock_cache.get.side_effect = self._keyed_cache()
        mock_fetch.return_value = {'tracked_population': 0}

        data.warm_player_tier_type_population_correlation(realm='na')

        marker_key = data._tier_type_rebuild_marker_key('na')
        marker_sets = [c for c in mock_cache.set.call_args_list
                       if c.args[0] == marker_key]
        self.assertEqual(marker_sets, [])
