"""Current-season Ranked Enjoyer criteria + durable RankedSeason reference.

Spec: agents/work-items/ranked-enjoyer-current-season-spec.md

Covers: the "latest season persists" current-season resolution, the
RankedSeason upsert/DB-fallback behind `_get_ranked_seasons_metadata`, the
self-healing rollover in `update_ranked_data`, and the per-row qualification
helpers the clan-members view and player serializer share.
"""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from warships.data import (
    RANKED_SEASONS_CACHE_KEY,
    _get_ranked_seasons_metadata,
    _impute_ranked_season_from_activity,
    get_current_ranked_season_id,
    get_current_season_ranked_league,
    is_current_season_ranked_player,
    update_ranked_data,
)
from warships.models import Player, RankedSeason


class CurrentRankedSeasonResolutionTests(TestCase):
    def test_empty_reference_resolves_to_none(self):
        self.assertIsNone(get_current_ranked_season_id())

    def test_latest_started_season_wins_and_persists_past_its_end_date(self):
        today = date.today()
        RankedSeason.objects.create(
            season_id=1007, start_date=today - timedelta(days=300),
            end_date=today - timedelta(days=240))
        # Ended 10 days ago — off-season gap — but still the newest started
        # season, so it remains current until 1009 starts.
        RankedSeason.objects.create(
            season_id=1008, start_date=today - timedelta(days=70),
            end_date=today - timedelta(days=10))
        RankedSeason.objects.create(
            season_id=1009, start_date=today + timedelta(days=20))

        self.assertEqual(get_current_ranked_season_id(), 1008)

    def test_null_start_date_counts_as_started(self):
        RankedSeason.objects.create(season_id=1003, start_date=None)
        self.assertEqual(get_current_ranked_season_id(), 1003)


class RankedSeasonsMetadataDurabilityTests(TestCase):
    def setUp(self):
        cache.delete(RANKED_SEASONS_CACHE_KEY)

    @patch('warships.api.players._fetch_ranked_seasons_info')
    def test_fresh_fetch_upserts_durable_reference(self, mock_fetch):
        start = datetime(2026, 6, 1)
        close = datetime(2026, 7, 20)
        mock_fetch.return_value = {
            '1008': {
                'season_name': 'Season 8',
                'start_at': start.timestamp(),
                'close_at': close.timestamp(),
            },
        }

        result = _get_ranked_seasons_metadata()

        self.assertEqual(result[1008]['start_date'], '2026-06-01')
        row = RankedSeason.objects.get(season_id=1008)
        self.assertEqual(row.start_date, date(2026, 6, 1))
        self.assertEqual(row.end_date, date(2026, 7, 20))
        self.assertEqual(row.label, 'S8')

    @patch('warships.api.players._fetch_ranked_seasons_info', return_value={})
    def test_wg_failure_falls_back_to_durable_reference(self, _mock_fetch):
        RankedSeason.objects.create(
            season_id=1005, name='Season 5', label='S5',
            start_date=date(2025, 1, 10), end_date=date(2025, 2, 20))

        result = _get_ranked_seasons_metadata()

        self.assertEqual(result[1005]['name'], 'Season 5')
        self.assertEqual(result[1005]['start_date'], '2025-01-10')
        self.assertEqual(result[1005]['end_date'], '2025-02-20')
        # The fallback is not re-cached: the next call retries WG.
        self.assertIsNone(cache.get(RANKED_SEASONS_CACHE_KEY))

    @patch('warships.api.players._fetch_ranked_seasons_info')
    def test_force_refresh_skips_redis_read(self, mock_fetch):
        cache.set(RANKED_SEASONS_CACHE_KEY, {1001: {'name': 'stale'}}, 60)
        mock_fetch.return_value = {
            '1002': {'season_name': 'Season 2', 'start_at': None, 'close_at': None},
        }

        result = _get_ranked_seasons_metadata(force_refresh=True)

        self.assertIn(1002, result)
        mock_fetch.assert_called_once()


class SelfHealingRolloverTests(TestCase):
    PID = 4242

    def setUp(self):
        Player.objects.create(name='RolloverPlayer', player_id=self.PID, realm='na')

    @patch('warships.data.refresh_player_explorer_summary')
    @patch('warships.data._build_top_ranked_ship_names_by_season', return_value={})
    @patch('warships.data._fetch_ranked_ship_stats_for_player', return_value=[])
    @patch('warships.data._get_ranked_seasons_metadata')
    @patch('warships.data._fetch_ranked_account_info')
    def test_unknown_season_id_triggers_metadata_refetch(
        self, mock_acct, mock_meta, _ships, _top, _refresh,
    ):
        # The 24h-cached reference knows only up to 1008; the player already
        # has battles in 1009 → one force_refresh=True refetch.
        mock_meta.side_effect = [
            {1008: {'name': 'Season 8', 'label': 'S8', 'start_date': None, 'end_date': None}},
            {1009: {'name': 'Season 9', 'label': 'S9', 'start_date': None, 'end_date': None}},
        ]
        mock_acct.return_value = {
            'rank_info': {'1009': {'1': {'1': {'battles': 5, 'victories': 3, 'rank': 5}}}},
        }

        update_ranked_data(self.PID, realm='na')

        self.assertEqual(mock_meta.call_count, 2)
        self.assertEqual(mock_meta.call_args_list[1].kwargs, {'force_refresh': True})

    @patch('warships.data.refresh_player_explorer_summary')
    @patch('warships.data._build_top_ranked_ship_names_by_season', return_value={})
    @patch('warships.data._fetch_ranked_ship_stats_for_player', return_value=[])
    @patch('warships.data._get_ranked_seasons_metadata')
    @patch('warships.data._fetch_ranked_account_info')
    def test_known_seasons_do_not_refetch_metadata(
        self, mock_acct, mock_meta, _ships, _top, _refresh,
    ):
        mock_meta.return_value = {
            1009: {'name': 'Season 9', 'label': 'S9', 'start_date': None, 'end_date': None},
        }
        mock_acct.return_value = {
            'rank_info': {'1009': {'1': {'1': {'battles': 5, 'victories': 3, 'rank': 5}}}},
        }

        update_ranked_data(self.PID, realm='na')

        self.assertEqual(mock_meta.call_count, 1)

    @patch('warships.data.refresh_player_explorer_summary')
    @patch('warships.data._build_top_ranked_ship_names_by_season', return_value={})
    @patch('warships.data._fetch_ranked_ship_stats_for_player', return_value=[])
    @patch('warships.data._get_ranked_seasons_metadata')
    @patch('warships.data._fetch_ranked_account_info')
    def test_already_imputed_season_skips_metadata_refetch(
        self, mock_acct, mock_meta, _ships, _top, _refresh,
    ):
        # Dedup: once the live season is in the durable reference (imputed here),
        # a player with battles there must not re-hit seasons/info every refresh.
        RankedSeason.objects.create(
            season_id=1009, label='S9', start_date=date.today())
        mock_meta.return_value = {
            1008: {'name': 'Season 8', 'label': 'S8', 'start_date': None, 'end_date': None},
        }
        mock_acct.return_value = {
            'rank_info': {'1009': {'1': {'1': {'battles': 5, 'victories': 3, 'rank': 5}}}},
        }

        update_ranked_data(self.PID, realm='na')

        self.assertEqual(mock_meta.call_count, 1)


class CurrentSeasonQualificationTests(TestCase):
    ROWS = [
        {'season_id': 1008, 'total_battles': 12, 'highest_league': 2,
         'highest_league_name': 'Silver'},
        {'season_id': 1006, 'total_battles': 300, 'highest_league': 1,
         'highest_league_name': 'Gold'},
        {'season_id': 1007, 'total_battles': 0, 'highest_league': 1,
         'highest_league_name': 'Gold'},
    ]

    def test_qualifies_on_any_current_season_battles(self):
        self.assertTrue(is_current_season_ranked_player(self.ROWS, 1008))

    def test_zero_battle_current_season_row_does_not_qualify(self):
        self.assertFalse(is_current_season_ranked_player(self.ROWS, 1007))

    def test_career_battles_outside_current_season_do_not_qualify(self):
        self.assertFalse(is_current_season_ranked_player(self.ROWS, 1009))

    def test_unknown_current_season_never_qualifies(self):
        self.assertFalse(is_current_season_ranked_player(self.ROWS, None))

    def test_league_is_scoped_to_the_current_season(self):
        self.assertEqual(
            get_current_season_ranked_league(self.ROWS, 1008), 'Silver')
        self.assertIsNone(get_current_season_ranked_league(self.ROWS, 1009))

    def test_league_falls_back_to_name_when_numeric_value_missing(self):
        rows = [{'season_id': 1008, 'total_battles': 3,
                 'highest_league_name': 'Bronze'}]
        self.assertEqual(
            get_current_season_ranked_league(rows, 1008), 'Bronze')


class RankedSeasonActivityImputationTests(TestCase):
    """Bridge WG's seasons/info publish lag by imputing the live season's start
    from observed play. Spec: ranked-enjoyer-current-season-spec.md.
    """

    def _ended_prior_meta(self):
        # 1008 is the newest season WG has dated, and it ended yesterday.
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        return {1008: {'name': 'Season 8', 'label': 'S8',
                       'start_date': '2026-01-01', 'end_date': yesterday}}

    def test_imputes_next_season_start_from_observed_play(self):
        meta = self._ended_prior_meta()
        result = [{'season_id': 1009, 'total_battles': 3}]

        _impute_ranked_season_from_activity(result, meta)

        row = RankedSeason.objects.get(season_id=1009)
        self.assertEqual(row.start_date, date.today())
        self.assertEqual(row.label, 'S9')
        self.assertIsNone(row.end_date)
        # The resolver now rolls over to the live season.
        self.assertEqual(get_current_ranked_season_id(), 1009)
        # And the aggregated row carries the imputed date so the season table
        # shows it instead of "Start date unavailable".
        self.assertEqual(result[0]['start_date'], date.today().isoformat())

    def test_stamps_earliest_stored_date_onto_a_null_row(self):
        # A later-observed player whose aggregated row is null still shows the
        # earliest fleet-wide imputed date, and does not drift the DB forward.
        earlier = date.today() - timedelta(days=3)
        RankedSeason.objects.create(
            season_id=1009, label='S9', start_date=earlier)
        meta = self._ended_prior_meta()
        result = [{'season_id': 1009, 'total_battles': 3, 'start_date': None}]

        _impute_ranked_season_from_activity(result, meta)

        self.assertEqual(result[0]['start_date'], earlier.isoformat())
        self.assertEqual(RankedSeason.objects.get(season_id=1009).start_date, earlier)

    def test_no_imputation_once_wg_publishes_the_season(self):
        # Self-correction: once seasons/info lists 1009, we stop imputing so
        # WG's real dates (written by _upsert_ranked_seasons_reference) stand.
        meta = self._ended_prior_meta()
        meta[1009] = {'name': 'Season 9', 'label': 'S9',
                      'start_date': None, 'end_date': None}
        result = [{'season_id': 1009, 'total_battles': 3}]

        _impute_ranked_season_from_activity(result, meta)

        self.assertFalse(RankedSeason.objects.filter(season_id=1009).exists())

    def test_phantom_far_future_season_id_does_not_impute(self):
        # Only max_known + 1 rolls over; a stray 1011 in one player's rank_info
        # cannot leap the fleet forward.
        meta = self._ended_prior_meta()
        result = [{'season_id': 1011, 'total_battles': 3}]

        _impute_ranked_season_from_activity(result, meta)

        self.assertFalse(RankedSeason.objects.filter(season_id=1011).exists())
        self.assertFalse(RankedSeason.objects.filter(season_id=1009).exists())

    def test_no_imputation_while_prior_season_still_running(self):
        # Overlap edge: WG says 1008 runs through next week, so early 1009
        # pre-season battles must not kill the still-live 1008 stars.
        next_week = (date.today() + timedelta(days=7)).isoformat()
        meta = {1008: {'name': 'Season 8', 'label': 'S8',
                       'start_date': '2026-01-01', 'end_date': next_week}}
        result = [{'season_id': 1009, 'total_battles': 3}]

        _impute_ranked_season_from_activity(result, meta)

        self.assertFalse(RankedSeason.objects.filter(season_id=1009).exists())

    def test_zero_battle_next_season_row_does_not_impute(self):
        meta = self._ended_prior_meta()
        result = [{'season_id': 1009, 'total_battles': 0}]

        _impute_ranked_season_from_activity(result, meta)

        self.assertFalse(RankedSeason.objects.filter(season_id=1009).exists())

    def test_empty_reference_does_not_bootstrap(self):
        result = [{'season_id': 1001, 'total_battles': 3}]

        _impute_ranked_season_from_activity(result, {})

        self.assertEqual(RankedSeason.objects.count(), 0)

    def test_prior_null_end_date_still_imputes(self):
        # A prior season WG never dated an end for is treated as ended once the
        # next season shows real play.
        meta = {1008: {'name': 'Season 8', 'label': 'S8',
                       'start_date': '2026-01-01', 'end_date': None}}
        result = [{'season_id': 1009, 'total_battles': 3}]

        _impute_ranked_season_from_activity(result, meta)

        self.assertTrue(RankedSeason.objects.filter(season_id=1009).exists())

    def test_steady_state_reimputation_is_idempotent(self):
        meta = self._ended_prior_meta()
        result = [{'season_id': 1009, 'total_battles': 3}]

        _impute_ranked_season_from_activity(result, meta)
        first = RankedSeason.objects.get(season_id=1009)
        # A later refresh the same gap must not drift the start_date forward.
        _impute_ranked_season_from_activity(result, meta)

        rows = RankedSeason.objects.filter(season_id=1009)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().start_date, first.start_date)

    @patch('warships.data.refresh_player_explorer_summary')
    @patch('warships.data._build_top_ranked_ship_names_by_season', return_value={})
    @patch('warships.data._fetch_ranked_ship_stats_for_player', return_value=[])
    @patch('warships.data._get_ranked_seasons_metadata')
    @patch('warships.data._fetch_ranked_account_info')
    def test_update_ranked_data_imputes_end_to_end(
        self, mock_acct, mock_meta, _ships, _top, _refresh,
    ):
        # WG's seasons/info tops out at an ended 1008; the player already has
        # 1009 battles → the icon's current season rolls to 1009 after refresh.
        Player.objects.create(name='RollPlayer', player_id=9911, realm='na')
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        ended = {1008: {'name': 'Season 8', 'label': 'S8',
                        'start_date': '2026-01-01', 'end_date': yesterday}}
        # Both the initial read and the self-heal force_refresh see only 1008.
        mock_meta.side_effect = [ended, ended]
        mock_acct.return_value = {
            'rank_info': {'1009': {'1': {'1': {'battles': 4, 'victories': 2, 'rank': 5}}}},
        }

        update_ranked_data(9911, realm='na')

        self.assertEqual(get_current_ranked_season_id(), 1009)
        self.assertEqual(RankedSeason.objects.get(season_id=1009).start_date, date.today())
