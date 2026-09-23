"""The materialised ranked record (Player.ranked_total_battles / ranked_win_rate).

Runbook: agents/runbooks/runbook-ranked-correlation-materialized-record-2026-09-23.md

The ranked correlation warm read every ranked_json payload through TOAST and
outgrew its 1080s soft limit on eu. These tests pin the three parts of the
fix: every ranked_json writer derives the two columns, the warm reads them
only once the realm's backfill marker is stamped, and the backfill command
stamps only after a full pass.
"""

from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    _build_player_ranked_wr_battles_population_correlation_payload,
    ranked_record_backfill_marker_key,
    ranked_record_from_json,
    update_ranked_data,
)
from warships.models import Player


SEASONS = [
    {"season_id": 9, "total_battles": 40, "total_wins": 24, "win_rate": 0.6},
    {"season_id": 8, "total_battles": 20, "total_wins": 10, "win_rate": 0.5},
]


class RankedRecordFromJsonTests(TestCase):
    def test_sums_seasons_and_rounds_the_win_rate(self):
        self.assertEqual(ranked_record_from_json(SEASONS), (60, 56.67))

    def test_fetched_but_no_ranked_play_is_zero_not_null(self):
        # Distinguishes a backfilled row from a never-backfilled NULL.
        self.assertEqual(ranked_record_from_json([]), (0, None))

    def test_no_payload_at_all_is_null(self):
        self.assertEqual(ranked_record_from_json(None), (None, None))


class WritersDeriveTheRecordTests(TestCase):
    PID = 5150

    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="RecordWriter", player_id=self.PID, realm="na", is_hidden=False)

    @patch('warships.data.refresh_player_explorer_summary')
    @patch('warships.data._build_top_ranked_ship_names_by_season', return_value={})
    @patch('warships.data._fetch_ranked_ship_stats_for_player', return_value=[])
    @patch('warships.data._get_ranked_seasons_metadata')
    @patch('warships.data._fetch_ranked_account_info')
    def test_update_ranked_data_stamps_the_record(
            self, mock_acct, mock_meta, _ships, _top, _refresh):
        mock_meta.return_value = {
            1009: {'name': 'Season 9', 'label': 'S9',
                   'start_date': None, 'end_date': None},
        }
        mock_acct.return_value = {
            'rank_info': {'1009': {'1': {'1': {
                'battles': 80, 'victories': 48, 'rank': 5}}}},
        }

        update_ranked_data(self.PID, realm='na')

        self.player.refresh_from_db()
        self.assertEqual(self.player.ranked_total_battles, 80)
        self.assertEqual(self.player.ranked_win_rate, 60.0)

    @patch('warships.data._fetch_ranked_account_info', return_value={'rank_info': {}})
    def test_update_ranked_data_with_no_ranked_play_writes_zero(self, _acct):
        self.player.ranked_total_battles = 33
        self.player.ranked_win_rate = 50.0
        self.player.save()

        update_ranked_data(self.PID, realm='na')

        self.player.refresh_from_db()
        self.assertEqual(self.player.ranked_json, [])
        self.assertEqual(self.player.ranked_total_battles, 0)
        self.assertIsNone(self.player.ranked_win_rate)

    def test_enrichment_command_stamps_the_record(self):
        from warships.management.commands.enrich_player_data import (
            _process_player_ranked_data)

        with patch('warships.api.ships._fetch_ranked_ship_stats_for_player',
                   return_value=[]), \
                patch('warships.data._get_ranked_seasons_metadata',
                      return_value={1009: {'name': 'S9', 'label': 'S9',
                                           'start_date': None, 'end_date': None}}), \
                patch('warships.data._build_top_ranked_ship_names_by_season',
                      return_value={}):
            _process_player_ranked_data(
                self.player,
                {'1009': {'1': {'1': {'battles': 10, 'victories': 7, 'rank': 3}}}},
                'na')

        self.player.refresh_from_db()
        self.assertEqual(self.player.ranked_total_battles, 10)
        self.assertEqual(self.player.ranked_win_rate, 70.0)


class WarmReadsTheRecordOnlyAfterBackfillTests(TestCase):
    def setUp(self):
        cache.clear()
        # Materialised only: no ranked_json. Visible to the warm solely
        # through the columns.
        Player.objects.create(
            name="ColumnsOnly", player_id=7001, realm="eu", is_hidden=False,
            ranked_json=None, ranked_total_battles=120, ranked_win_rate=55.0)
        # JSON only: a row the backfill has not reached yet.
        Player.objects.create(
            name="JsonOnly", player_id=7002, realm="eu", is_hidden=False,
            ranked_json=SEASONS)
        # Below the 50-battle floor on the columns.
        Player.objects.create(
            name="TooSmall", player_id=7003, realm="eu", is_hidden=False,
            ranked_json=None, ranked_total_battles=30, ranked_win_rate=60.0)
        # Hidden rows never count on either path.
        Player.objects.create(
            name="Hidden", player_id=7004, realm="eu", is_hidden=True,
            ranked_json=None, ranked_total_battles=200, ranked_win_rate=70.0)
        # Wrong realm.
        Player.objects.create(
            name="OtherRealm", player_id=7005, realm="na", is_hidden=False,
            ranked_json=None, ranked_total_battles=200, ranked_win_rate=70.0)

    def test_without_the_marker_the_warm_reads_ranked_json(self):
        payload = _build_player_ranked_wr_battles_population_correlation_payload(
            realm="eu")
        # Only JsonOnly (60 battles) qualifies on the legacy path.
        self.assertEqual(payload["tracked_population"], 1)

    def test_with_the_marker_the_warm_reads_the_columns(self):
        cache.set(ranked_record_backfill_marker_key(realm="eu"), "stamped",
                  timeout=None)
        payload = _build_player_ranked_wr_battles_population_correlation_payload(
            realm="eu")
        # Only ColumnsOnly qualifies: JsonOnly has no columns yet, TooSmall is
        # under the floor, Hidden and OtherRealm are excluded.
        self.assertEqual(payload["tracked_population"], 1)
        self.assertEqual(payload["trend"][0]["y"], 55.0)

    def test_the_marker_is_per_realm(self):
        cache.set(ranked_record_backfill_marker_key(realm="na"), "stamped",
                  timeout=None)
        payload = _build_player_ranked_wr_battles_population_correlation_payload(
            realm="eu")
        self.assertEqual(payload["tracked_population"], 1)  # legacy path on eu


class BackfillRankedRecordCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        self.eu = Player.objects.create(
            name="BackfillEU", player_id=8001, realm="eu", is_hidden=False,
            ranked_json=SEASONS)
        self.eu_empty = Player.objects.create(
            name="BackfillEUEmpty", player_id=8002, realm="eu", is_hidden=False,
            ranked_json=[])
        self.na = Player.objects.create(
            name="BackfillNA", player_id=8003, realm="na", is_hidden=False,
            ranked_json=SEASONS)
        self.never = Player.objects.create(
            name="NoRankedJson", player_id=8004, realm="eu", is_hidden=False,
            ranked_json=None)

    def test_full_pass_fills_the_columns_and_stamps_the_realm(self):
        out = StringIO()
        call_command("backfill_ranked_record", realm="eu", delay=0, stdout=out)

        self.eu.refresh_from_db()
        self.eu_empty.refresh_from_db()
        self.na.refresh_from_db()
        self.never.refresh_from_db()
        self.assertEqual(
            (self.eu.ranked_total_battles, self.eu.ranked_win_rate), (60, 56.67))
        self.assertEqual(
            (self.eu_empty.ranked_total_battles, self.eu_empty.ranked_win_rate),
            (0, None))
        self.assertIsNone(self.na.ranked_total_battles)      # other realm untouched
        self.assertIsNone(self.never.ranked_total_battles)   # nothing to derive
        self.assertTrue(cache.get(ranked_record_backfill_marker_key(realm="eu")))
        self.assertIsNone(cache.get(ranked_record_backfill_marker_key(realm="na")))
        self.assertIn("marker stamped", out.getvalue())

    def test_all_realms_stamps_each(self):
        call_command("backfill_ranked_record", delay=0, stdout=StringIO())
        for realm in ("na", "eu", "asia"):
            self.assertTrue(
                cache.get(ranked_record_backfill_marker_key(realm=realm)), realm)

    def test_dry_run_writes_nothing_and_stamps_nothing(self):
        out = StringIO()
        call_command("backfill_ranked_record", dry_run=True, delay=0, stdout=out)
        self.eu.refresh_from_db()
        self.assertIsNone(self.eu.ranked_total_battles)
        self.assertIsNone(cache.get(ranked_record_backfill_marker_key(realm="eu")))
        self.assertIn("no marker", out.getvalue())

    def test_partial_pass_never_stamps(self):
        self.eu.last_battle_date = timezone.now().date()
        self.eu.save()
        out = StringIO()
        call_command("backfill_ranked_record", realm="eu", active_days=7,
                     delay=0, stdout=out)
        self.eu.refresh_from_db()
        self.assertEqual(self.eu.ranked_total_battles, 60)
        self.assertIsNone(cache.get(ranked_record_backfill_marker_key(realm="eu")))
        self.assertIn("NOT stamped", out.getvalue())

    def test_idempotent_second_pass_changes_nothing(self):
        call_command("backfill_ranked_record", realm="eu", delay=0, stdout=StringIO())
        out = StringIO()
        call_command("backfill_ranked_record", realm="eu", delay=0, stdout=out)
        self.assertIn("changed=0", out.getvalue())
