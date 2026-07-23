"""Current-season ClanBattleShieldIcon criteria + durable ClanBattleSeason reference.

Runbook: agents/runbooks/runbook-cb-icon-current-season-2026-07-15.md

Covers: the "latest season persists" current-season resolution (with the
brawl-id guard the ranked heuristic doesn't need), the ClanBattleSeason
upsert/DB-fallback behind `_get_clan_battle_seasons_metadata`, the persist-time
current-season extraction on PlayerExplorerSummary, the self-healing rollover
in `fetch_player_clan_battle_seasons`, the per-row `is_current` flag, and the
read-side qualification helpers the clan-members view and player serializer
share.
"""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone as django_timezone

from warships.data import (
    CLAN_BATTLE_SEASONS_CACHE_KEY,
    _get_clan_battle_seasons_metadata,
    _impute_clan_battle_season_from_activity,
    clan_battle_summary_is_stale,
    fetch_player_clan_battle_seasons,
    get_current_clan_battle_season_id,
    get_current_season_clan_battle_win_rate,
    is_current_season_clan_battle_player,
)
from warships.models import ClanBattleSeason, Player, PlayerExplorerSummary


class CurrentClanBattleSeasonResolutionTests(TestCase):
    def test_empty_reference_resolves_to_none(self):
        self.assertIsNone(get_current_clan_battle_season_id())

    def test_latest_started_season_wins_and_persists_past_its_end_date(self):
        today = date.today()
        ClanBattleSeason.objects.create(
            season_id=33, start_date=today - timedelta(days=120),
            end_date=today - timedelta(days=60))
        # Ended — off-season gap — but still the newest started season, so it
        # remains current until 34 starts.
        ClanBattleSeason.objects.create(
            season_id=34, start_date=today + timedelta(days=15))

        self.assertEqual(get_current_clan_battle_season_id(), 33)

    def test_brawl_ids_never_win_even_with_higher_ids(self):
        # Live WG payload (verified 2026-07-15): brawls/specials occupy ids
        # 101+, 201+, 301+ with 2018-2021 dates. max(season_id) would pick a
        # museum piece; max-start-date with the <100 guard picks the ladder.
        today = date.today()
        ClanBattleSeason.objects.create(
            season_id=34, start_date=today - timedelta(days=20),
            end_date=today + timedelta(days=25))
        ClanBattleSeason.objects.create(
            season_id=301, start_date=today - timedelta(days=2000),
            end_date=today - timedelta(days=1995))

        self.assertEqual(get_current_clan_battle_season_id(), 34)

    def test_brawl_id_with_current_dates_is_still_excluded(self):
        today = date.today()
        ClanBattleSeason.objects.create(
            season_id=34, start_date=today - timedelta(days=20))
        ClanBattleSeason.objects.create(
            season_id=216, start_date=today - timedelta(days=1))

        self.assertEqual(get_current_clan_battle_season_id(), 34)

    def test_start_date_beats_higher_id(self):
        today = date.today()
        # Defensive: if WG ever lists a lower-id regular season with a newer
        # start date, chronology wins.
        ClanBattleSeason.objects.create(
            season_id=35, start_date=today - timedelta(days=200))
        ClanBattleSeason.objects.create(
            season_id=34, start_date=today - timedelta(days=10))

        self.assertEqual(get_current_clan_battle_season_id(), 34)

    def test_null_start_date_counts_as_started_but_loses_to_dated_rows(self):
        ClanBattleSeason.objects.create(season_id=5, start_date=None)
        self.assertEqual(get_current_clan_battle_season_id(), 5)

        ClanBattleSeason.objects.create(
            season_id=4, start_date=date.today() - timedelta(days=30))
        self.assertEqual(get_current_clan_battle_season_id(), 4)


class ClanBattleSeasonsMetadataDurabilityTests(TestCase):
    def setUp(self):
        cache.delete(CLAN_BATTLE_SEASONS_CACHE_KEY)

    @patch('warships.data._fetch_clan_battle_seasons_info')
    def test_fresh_fetch_upserts_durable_reference(self, mock_fetch):
        start = datetime(2026, 6, 22)
        finish = datetime(2026, 8, 10)
        mock_fetch.return_value = {
            '34': {
                'name': 'Hammerhead',
                'start_time': start.timestamp(),
                'finish_time': finish.timestamp(),
                'ship_tier_min': 10,
                'ship_tier_max': 10,
            },
        }

        result = _get_clan_battle_seasons_metadata()

        self.assertEqual(result[34]['start_date'], '2026-06-22')
        row = ClanBattleSeason.objects.get(season_id=34)
        self.assertEqual(row.name, 'Hammerhead')
        self.assertEqual(row.start_date, date(2026, 6, 22))
        self.assertEqual(row.end_date, date(2026, 8, 10))
        self.assertEqual(row.ship_tier_min, 10)
        self.assertEqual(row.label, 'S34')

    @patch('warships.data._fetch_clan_battle_seasons_info', return_value={})
    def test_wg_failure_falls_back_to_durable_reference(self, _mock_fetch):
        ClanBattleSeason.objects.create(
            season_id=33, name='Blue Marlin', label='S33',
            start_date=date(2026, 3, 16), end_date=date(2026, 5, 18),
            ship_tier_min=10, ship_tier_max=10)

        result = _get_clan_battle_seasons_metadata()

        self.assertEqual(result[33]['name'], 'Blue Marlin')
        self.assertEqual(result[33]['start_date'], '2026-03-16')
        self.assertEqual(result[33]['ship_tier_max'], 10)
        # The fallback is not re-cached: the next call retries WG.
        self.assertIsNone(cache.get(CLAN_BATTLE_SEASONS_CACHE_KEY))

    @patch('warships.data._fetch_clan_battle_seasons_info')
    def test_force_refresh_skips_redis_read(self, mock_fetch):
        cache.set(CLAN_BATTLE_SEASONS_CACHE_KEY, {33: {'name': 'stale'}}, 60)
        mock_fetch.return_value = {
            '34': {'name': 'Hammerhead', 'start_time': None, 'finish_time': None},
        }

        result = _get_clan_battle_seasons_metadata(force_refresh=True)

        self.assertIn(34, result)
        mock_fetch.assert_called_once()


class PersistCurrentSeasonTests(TestCase):
    PID = 5150

    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name='CbPlayer', player_id=self.PID, realm='na')
        today = date.today()
        ClanBattleSeason.objects.create(
            season_id=34, name='Hammerhead', label='S34',
            start_date=today - timedelta(days=20),
            end_date=today + timedelta(days=25))
        ClanBattleSeason.objects.create(
            season_id=33, name='Blue Marlin', label='S33',
            start_date=today - timedelta(days=120),
            end_date=today - timedelta(days=60))
        # Keep the metadata fresh key warm so fetch_player_clan_battle_seasons
        # doesn't hit the (unmocked) WG metadata endpoint.
        cache.set(CLAN_BATTLE_SEASONS_CACHE_KEY, {
            34: {'name': 'Hammerhead', 'label': 'S34',
                 'start_date': None, 'end_date': None,
                 'ship_tier_min': 10, 'ship_tier_max': 10},
            33: {'name': 'Blue Marlin', 'label': 'S33',
                 'start_date': None, 'end_date': None,
                 'ship_tier_min': 10, 'ship_tier_max': 10},
        }, 60)

    def _summary(self):
        return PlayerExplorerSummary.objects.get(player=self.player)

    @patch('warships.data._fetch_clan_battle_season_stats')
    def test_participant_persists_current_season_fields(self, mock_stats):
        mock_stats.return_value = {'seasons': [
            {'season_id': 34, 'battles': 8, 'wins': 5, 'losses': 3},
            {'season_id': 33, 'battles': 40, 'wins': 18, 'losses': 22},
        ]}

        rows = fetch_player_clan_battle_seasons(self.PID, realm='na')

        summary = self._summary()
        self.assertEqual(summary.clan_battle_current_season_id, 34)
        self.assertEqual(summary.clan_battle_current_season_battles, 8)
        self.assertEqual(summary.clan_battle_current_season_win_rate, 62.5)
        # Career aggregates keep their existing semantics.
        self.assertEqual(summary.clan_battle_total_battles, 48)
        self.assertEqual(summary.clan_battle_seasons_participated, 2)
        # Per-row currency flag for the live frontend path.
        by_sid = {row['season_id']: row for row in rows}
        self.assertTrue(by_sid[34]['is_current'])
        self.assertFalse(by_sid[33]['is_current'])

    @patch('warships.data._fetch_clan_battle_season_stats')
    def test_season_sitout_persists_zero_battles_and_null_wr(self, mock_stats):
        mock_stats.return_value = {'seasons': [
            {'season_id': 33, 'battles': 40, 'wins': 18, 'losses': 22},
        ]}

        fetch_player_clan_battle_seasons(self.PID, realm='na')

        summary = self._summary()
        self.assertEqual(summary.clan_battle_current_season_id, 34)
        self.assertEqual(summary.clan_battle_current_season_battles, 0)
        self.assertIsNone(summary.clan_battle_current_season_win_rate)

    @patch('warships.data._fetch_clan_battle_season_stats')
    def test_unresolvable_current_season_persists_nulls(self, mock_stats):
        ClanBattleSeason.objects.all().delete()
        mock_stats.return_value = {'seasons': [
            {'season_id': 33, 'battles': 40, 'wins': 18, 'losses': 22},
        ]}

        fetch_player_clan_battle_seasons(self.PID, realm='na')

        summary = self._summary()
        self.assertIsNone(summary.clan_battle_current_season_id)
        self.assertIsNone(summary.clan_battle_current_season_battles)
        self.assertIsNone(summary.clan_battle_current_season_win_rate)

    @patch('warships.data._fetch_clan_battle_season_stats')
    def test_cold_request_path_still_never_persists(self, mock_stats):
        result = fetch_player_clan_battle_seasons(
            self.PID, realm='na', allow_remote_fetch=False)

        self.assertEqual(result, [])
        mock_stats.assert_not_called()
        self.assertFalse(
            PlayerExplorerSummary.objects.filter(player=self.player).exists())


class SelfHealingRolloverTests(TestCase):
    PID = 5151

    def setUp(self):
        cache.clear()
        Player.objects.create(name='Rollover', player_id=self.PID, realm='na')

    @patch('warships.data._get_clan_battle_seasons_metadata')
    @patch('warships.data._fetch_clan_battle_season_stats')
    def test_unknown_regular_season_id_triggers_metadata_refetch(
        self, mock_stats, mock_meta,
    ):
        # The 24h-cached reference knows only up to 34; the player already has
        # battles in 35 → one force_refresh=True refetch.
        mock_meta.side_effect = [
            {34: {'name': 'Hammerhead', 'label': 'S34',
                  'start_date': None, 'end_date': None}},
            {35: {'name': 'Next', 'label': 'S35',
                  'start_date': None, 'end_date': None}},
        ]
        mock_stats.return_value = {'seasons': [
            {'season_id': 35, 'battles': 3, 'wins': 2, 'losses': 1},
        ]}

        fetch_player_clan_battle_seasons(self.PID, realm='na')

        self.assertEqual(mock_meta.call_count, 2)
        self.assertEqual(
            mock_meta.call_args_list[1].kwargs, {'force_refresh': True})

    @patch('warships.data._get_clan_battle_seasons_metadata')
    @patch('warships.data._fetch_clan_battle_season_stats')
    def test_brawl_row_does_not_trigger_metadata_refetch(
        self, mock_stats, mock_meta,
    ):
        mock_meta.return_value = {
            34: {'name': 'Hammerhead', 'label': 'S34',
                 'start_date': None, 'end_date': None}}
        mock_stats.return_value = {'seasons': [
            {'season_id': 34, 'battles': 3, 'wins': 2, 'losses': 1},
            {'season_id': 305, 'battles': 6, 'wins': 3, 'losses': 3},
        ]}

        fetch_player_clan_battle_seasons(self.PID, realm='na')

        self.assertEqual(mock_meta.call_count, 1)

    @patch('warships.data._get_clan_battle_seasons_metadata')
    @patch('warships.data._fetch_clan_battle_season_stats')
    def test_already_imputed_season_skips_metadata_refetch(
        self, mock_stats, mock_meta,
    ):
        # Dedup: once the live season is in the durable reference (published OR
        # imputed), a player with battles there must not re-hit clans/season.
        ClanBattleSeason.objects.create(
            season_id=35, label='S35', start_date=date.today())
        mock_meta.return_value = {
            34: {'name': 'Hammerhead', 'label': 'S34',
                 'start_date': None, 'end_date': None}}
        mock_stats.return_value = {'seasons': [
            {'season_id': 35, 'battles': 3, 'wins': 2, 'losses': 1},
        ]}

        fetch_player_clan_battle_seasons(self.PID, realm='na')

        self.assertEqual(mock_meta.call_count, 1)


class ClanBattleSeasonActivityImputationTests(TestCase):
    """Bridge WG's clans/season publish lag by imputing the live regular
    season's start from observed play. Runbook: runbook-cb-icon-current-season.
    """

    def _ended_prior_meta(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        return {34: {'name': 'Hammerhead', 'label': 'S34',
                     'start_date': '2026-01-01', 'end_date': yesterday}}

    def test_imputes_next_regular_season_from_observed_play(self):
        meta = self._ended_prior_meta()
        seasons = [{'season_id': 35, 'battles': 3, 'wins': 2, 'losses': 1}]

        _impute_clan_battle_season_from_activity(seasons, meta)

        row = ClanBattleSeason.objects.get(season_id=35)
        self.assertEqual(row.start_date, date.today())
        self.assertEqual(row.label, 'S35')
        self.assertEqual(get_current_clan_battle_season_id(), 35)
        # The imputed season is injected into season_meta so the caller's per-row
        # start_date/label reflect it instead of nulls.
        self.assertEqual(meta[35]['start_date'], date.today().isoformat())
        self.assertEqual(meta[35]['label'], 'S35')

    def test_fetch_stamps_imputed_start_onto_result_row(self):
        # End-to-end: the per-season result row shows the imputed date, not null.
        cache.clear()
        Player.objects.create(name='CBImpute', player_id=6262, realm='na')
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with patch('warships.data._get_clan_battle_seasons_metadata') as mock_meta, \
                patch('warships.data._fetch_clan_battle_season_stats') as mock_stats:
            mock_meta.return_value = {
                34: {'name': 'Hammerhead', 'label': 'S34',
                     'start_date': '2026-01-01', 'end_date': yesterday}}
            mock_stats.return_value = {'seasons': [
                {'season_id': 35, 'battles': 4, 'wins': 3, 'losses': 1}]}

            rows = fetch_player_clan_battle_seasons(6262, realm='na')

        row35 = next(r for r in rows if r['season_id'] == 35)
        self.assertEqual(row35['start_date'], date.today().isoformat())
        self.assertTrue(row35['is_current'])

    def test_brawl_season_id_never_imputes(self):
        # A brawl/special id (101+) must never become current, even with battles.
        meta = {33: {'name': 'S33', 'label': 'S33',
                     'start_date': '2026-01-01', 'end_date': '2026-02-01'},
                # A brawl already on record must not shift max_known off regular.
                305: {'name': 'Brawl', 'label': 'S305',
                      'start_date': '2020-01-01', 'end_date': '2020-02-01'}}
        seasons = [{'season_id': 305, 'battles': 9, 'wins': 5, 'losses': 4}]

        _impute_clan_battle_season_from_activity(seasons, meta)

        # Only regular 34 (max_known 33 + 1) could impute, and there is no play
        # there; the brawl row triggers nothing.
        self.assertFalse(ClanBattleSeason.objects.filter(season_id=306).exists())
        self.assertFalse(ClanBattleSeason.objects.filter(season_id=34).exists())

    def test_no_imputation_once_wg_publishes(self):
        meta = self._ended_prior_meta()
        meta[35] = {'name': 'Next', 'label': 'S35',
                    'start_date': None, 'end_date': None}
        seasons = [{'season_id': 35, 'battles': 3, 'wins': 2, 'losses': 1}]

        _impute_clan_battle_season_from_activity(seasons, meta)

        self.assertFalse(ClanBattleSeason.objects.filter(season_id=35).exists())

    def test_no_imputation_while_prior_season_running(self):
        next_week = (date.today() + timedelta(days=7)).isoformat()
        meta = {34: {'name': 'Hammerhead', 'label': 'S34',
                     'start_date': '2026-01-01', 'end_date': next_week}}
        seasons = [{'season_id': 35, 'battles': 3, 'wins': 2, 'losses': 1}]

        _impute_clan_battle_season_from_activity(seasons, meta)

        self.assertFalse(ClanBattleSeason.objects.filter(season_id=35).exists())

    def test_zero_battle_next_season_does_not_impute(self):
        meta = self._ended_prior_meta()
        seasons = [{'season_id': 35, 'battles': 0, 'wins': 0, 'losses': 0}]

        _impute_clan_battle_season_from_activity(seasons, meta)

        self.assertFalse(ClanBattleSeason.objects.filter(season_id=35).exists())

    def test_idempotent_reimputation(self):
        meta = self._ended_prior_meta()
        seasons = [{'season_id': 35, 'battles': 3, 'wins': 2, 'losses': 1}]

        _impute_clan_battle_season_from_activity(seasons, meta)
        first = ClanBattleSeason.objects.get(season_id=35).start_date
        _impute_clan_battle_season_from_activity(seasons, meta)

        rows = ClanBattleSeason.objects.filter(season_id=35)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().start_date, first)


class CurrentSeasonQualificationTests(TestCase):
    _next_player_id = 7000

    def _summary(self, **overrides):
        type(self)._next_player_id += 1
        player = Player.objects.create(
            name=f'Qualifier{type(self)._next_player_id}',
            player_id=type(self)._next_player_id, realm='na')
        defaults = {
            'realm': 'na',
            'clan_battle_current_season_id': 34,
            'clan_battle_current_season_battles': 8,
            'clan_battle_current_season_win_rate': 62.5,
        }
        defaults.update(overrides)
        return PlayerExplorerSummary.objects.create(player=player, **defaults)

    def test_qualifies_on_any_current_season_battles(self):
        self.assertTrue(
            is_current_season_clan_battle_player(self._summary(), 34))

    def test_zero_battle_current_season_does_not_qualify(self):
        summary = self._summary(
            clan_battle_current_season_battles=0,
            clan_battle_current_season_win_rate=None)
        self.assertFalse(is_current_season_clan_battle_player(summary, 34))

    def test_stored_season_older_than_current_does_not_qualify(self):
        # Rollover self-correction: the stored row points at a finished
        # season, so the gate goes false the moment 35 becomes current —
        # no write required.
        self.assertTrue(
            is_current_season_clan_battle_player(self._summary(), 34))
        self.assertFalse(
            is_current_season_clan_battle_player(self._summary(), 35))

    def test_unknown_current_season_never_qualifies(self):
        self.assertFalse(
            is_current_season_clan_battle_player(self._summary(), None))

    def test_missing_summary_never_qualifies(self):
        self.assertFalse(is_current_season_clan_battle_player(None, 34))

    def test_win_rate_is_scoped_to_the_current_season(self):
        summary = self._summary()
        self.assertEqual(
            get_current_season_clan_battle_win_rate(summary, 34), 62.5)
        self.assertIsNone(
            get_current_season_clan_battle_win_rate(summary, 35))
        self.assertIsNone(get_current_season_clan_battle_win_rate(None, 34))


class StaleCheckBackfillTests(TestCase):
    def test_summary_without_current_season_fields_is_stale(self):
        # Pre-0081 rows: career aggregates present, current-season fields
        # never computed → stale, so the existing clan-view hydration
        # machinery backfills them organically.
        player = Player.objects.create(
            name='PreMigration', player_id=6161, realm='na')
        PlayerExplorerSummary.objects.create(
            player=player, realm='na',
            clan_battle_total_battles=120,
            clan_battle_seasons_participated=5,
            clan_battle_overall_win_rate=51.0,
            clan_battle_summary_updated_at=django_timezone.now())

        self.assertTrue(clan_battle_summary_is_stale(player))

    def test_fresh_summary_with_current_season_fields_is_not_stale(self):
        player = Player.objects.create(
            name='PostMigration', player_id=6162, realm='na')
        PlayerExplorerSummary.objects.create(
            player=player, realm='na',
            clan_battle_total_battles=120,
            clan_battle_seasons_participated=5,
            clan_battle_overall_win_rate=51.0,
            clan_battle_current_season_id=34,
            clan_battle_current_season_battles=0,
            clan_battle_summary_updated_at=django_timezone.now())

        self.assertFalse(clan_battle_summary_is_stale(player))
