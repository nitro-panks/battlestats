from io import StringIO
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import EntityVisitDaily, EntityVisitEvent, Player


class WarmLandingPageContentCommandTests(TestCase):
    @patch('warships.management.commands.warm_landing_page_content.warm_landing_page_content')
    def test_command_outputs_warm_summary(self, mock_warm_landing_page_content):
        mock_warm_landing_page_content.return_value = {
            'status': 'completed',
            'warmed': {'clans': 4, 'recent_clans': 3, 'players_best': 1, 'players_random': 40, 'recent_players': 12},
        }
        stdout = StringIO()

        call_command('warm_landing_page_content', stdout=stdout)

        self.assertIn('"status": "completed"', stdout.getvalue())
        self.assertIn('"players_random": 40', stdout.getvalue())


class BackfillPlayerEfficiencyBadgesCommandTests(TestCase):
    @patch('warships.management.commands.backfill_player_efficiency_badges.update_player_efficiency_data')
    def test_command_targets_only_visible_missing_players_with_pvp_by_default(self, mock_update_player_efficiency_data):
        eligible = Player.objects.create(
            name='EligibleCaptain',
            player_id=101,
            is_hidden=False,
            pvp_battles=50,
            efficiency_json=None,
            efficiency_updated_at=None,
        )
        Player.objects.create(
            name='StampedCaptain',
            player_id=102,
            is_hidden=False,
            pvp_battles=60,
            efficiency_json=[],
            efficiency_updated_at=timezone.now(),
        )
        Player.objects.create(
            name='ZeroPvpCaptain',
            player_id=103,
            is_hidden=False,
            pvp_battles=0,
            efficiency_json=None,
            efficiency_updated_at=None,
        )
        Player.objects.create(
            name='HiddenCaptain',
            player_id=104,
            is_hidden=True,
            pvp_battles=75,
            efficiency_json=None,
            efficiency_updated_at=None,
        )

        stdout = StringIO()
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'efficiency-state.json'
            call_command(
                'backfill_player_efficiency_badges',
                batch_size=1,
                state_file=str(state_file),
                stdout=stdout,
            )

            state = json.loads(state_file.read_text())

        mock_update_player_efficiency_data.assert_called_once()
        processed_player = mock_update_player_efficiency_data.call_args.args[0]
        self.assertEqual(processed_player.id, eligible.id)
        self.assertEqual(mock_update_player_efficiency_data.call_args.kwargs, {
                         'force_refresh': True})
        self.assertIn('attempted=1', stdout.getvalue())
        self.assertEqual(state['last_player_id'], eligible.id)
        self.assertEqual(state['failed_player_ids'], [])
        self.assertIsNotNone(state['completed_at'])

    def test_command_retries_failed_players_from_state_file_before_resuming(self):
        first_player = Player.objects.create(
            name='RetryCaptain',
            player_id=201,
            is_hidden=False,
            pvp_battles=30,
            efficiency_json=None,
            efficiency_updated_at=None,
        )
        second_player = Player.objects.create(
            name='ResumeCaptain',
            player_id=202,
            is_hidden=False,
            pvp_battles=40,
            efficiency_json=None,
            efficiency_updated_at=None,
        )

        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'efficiency-state.json'

            with patch('warships.management.commands.backfill_player_efficiency_badges.update_player_efficiency_data', side_effect=[RuntimeError('boom')]):
                first_stdout = StringIO()
                first_stderr = StringIO()
                call_command(
                    'backfill_player_efficiency_badges',
                    batch_size=1,
                    max_errors=1,
                    state_file=str(state_file),
                    stdout=first_stdout,
                    stderr=first_stderr,
                )

            failed_state = json.loads(state_file.read_text())
            self.assertEqual(failed_state['failed_player_ids'], [
                             first_player.id])
            self.assertEqual(failed_state['last_player_id'], first_player.id)

            with patch('warships.management.commands.backfill_player_efficiency_badges.update_player_efficiency_data') as retry_mock:
                second_stdout = StringIO()
                call_command(
                    'backfill_player_efficiency_badges',
                    batch_size=1,
                    state_file=str(state_file),
                    stdout=second_stdout,
                )

            retried_ids = [
                call.args[0].id for call in retry_mock.call_args_list]
            self.assertEqual(retried_ids, [first_player.id, second_player.id])

            resumed_state = json.loads(state_file.read_text())
            self.assertEqual(resumed_state['failed_player_ids'], [])
            self.assertEqual(resumed_state['last_player_id'], second_player.id)
            self.assertIsNotNone(resumed_state['completed_at'])
            self.assertIn('Retrying 1 previously failed player(s)',
                          second_stdout.getvalue())


class BackfillAchievementsDataCommandTests(TestCase):
    @patch('warships.management.commands.backfill_achievements_data.update_achievements_data')
    def test_command_targets_only_missing_players_by_default(self, mock_update_achievements_data):
        eligible = Player.objects.create(
            name='AchievementEligible',
            player_id=301,
            is_hidden=False,
            achievements_json=None,
            achievements_updated_at=None,
        )
        Player.objects.create(
            name='AchievementFresh',
            player_id=302,
            is_hidden=False,
            achievements_json={'battle': {'PCH016_FirstBlood': 3}},
            achievements_updated_at=timezone.now(),
        )
        Player.objects.create(
            name='AchievementHidden',
            player_id=303,
            is_hidden=True,
            achievements_json=None,
            achievements_updated_at=None,
        )

        stdout = StringIO()
        call_command(
            'backfill_achievements_data',
            batch_size=1,
            stdout=stdout,
        )

        mock_update_achievements_data.assert_called_once_with(
            eligible.player_id,
            force_refresh=True,
        )
        self.assertIn('processed=1', stdout.getvalue())

    @patch('warships.management.commands.backfill_achievements_data.update_achievements_data')
    def test_command_force_mode_refreshes_existing_rows(self, mock_update_achievements_data):
        player = Player.objects.create(
            name='AchievementForce',
            player_id=311,
            is_hidden=False,
            achievements_json={'battle': {'PCH016_FirstBlood': 1}},
            achievements_updated_at=timezone.now(),
        )

        call_command(
            'backfill_achievements_data',
            player_id=player.player_id,
            force=True,
        )

        mock_update_achievements_data.assert_called_once_with(
            player.player_id,
            force_refresh=True,
        )

    def test_command_only_missing_mode_is_idempotent_across_overlapping_runs(self):
        player = Player.objects.create(
            name='AchievementOverlap',
            player_id=321,
            is_hidden=False,
            achievements_json=None,
            achievements_updated_at=None,
        )

        def stamp_achievements(player_id: int, force_refresh: bool = False):
            refreshed_player = Player.objects.get(player_id=player_id)
            refreshed_player.achievements_json = {
                'battle': {'PCH016_FirstBlood': 4}}
            refreshed_player.achievements_updated_at = timezone.now()
            refreshed_player.save(
                update_fields=['achievements_json', 'achievements_updated_at'])

        with patch('warships.management.commands.backfill_achievements_data.update_achievements_data', side_effect=stamp_achievements) as first_run:
            call_command('backfill_achievements_data', only_missing=True)
        self.assertEqual(first_run.call_count, 1)

        with patch('warships.management.commands.backfill_achievements_data.update_achievements_data') as second_run:
            call_command('backfill_achievements_data', only_missing=True)
        second_run.assert_not_called()
        player.refresh_from_db()
        self.assertEqual(player.achievements_json, {
                         'battle': {'PCH016_FirstBlood': 4}})


class BackfillPlayerEfficiencyRanksCommandTests(TestCase):
    @patch('warships.management.commands.backfill_player_efficiency_ranks.recompute_efficiency_rank_snapshot')
    def test_command_outputs_and_writes_rank_report(self, mock_recompute_efficiency_rank_snapshot):
        mock_recompute_efficiency_rank_snapshot.return_value = {
            'publish_applied': False,
            'partial_population': True,
            'population_size': 120,
            'qualifying_count': 41,
            'qualifying_share': 0.341667,
            'field_mean_strength': 0.203,
            'tier_thresholds': {'III': 0.5, 'II': 0.75, 'I': 0.9, 'E': 0.97},
            'tier_counts': {'III': 25, 'II': 10, 'I': 4, 'E': 2},
            'suppressed_counts': {'no_badge_rows': 7},
            'distribution': {'p50': 0.18, 'p67': 0.24, 'p75': 0.31, 'p90': 0.49},
        }

        stdout = StringIO()
        with TemporaryDirectory() as temp_dir:
            report_file = Path(temp_dir) / 'efficiency-rank-report.json'
            call_command(
                'backfill_player_efficiency_ranks',
                limit=50,
                report_file=str(report_file),
                stdout=stdout,
            )

            payload = json.loads(report_file.read_text())
        mock_recompute_efficiency_rank_snapshot.assert_called_once_with(
            player_limit=50,
            skip_refresh=False,
            publish_partial=False,
        )
        self.assertEqual(payload['population_size'], 120)
        self.assertIn('"qualifying_count": 41', stdout.getvalue())

    @patch('warships.management.commands.backfill_player_efficiency_ranks.recompute_efficiency_rank_snapshot')
    def test_command_allows_explicit_partial_publication(self, mock_recompute_efficiency_rank_snapshot):
        mock_recompute_efficiency_rank_snapshot.return_value = {
            'publish_applied': True,
            'partial_population': True,
            'population_size': 10,
            'qualifying_count': 3,
            'qualifying_share': 0.3,
            'field_mean_strength': 0.18,
            'tier_thresholds': {'III': 0.5, 'II': 0.75, 'I': 0.9, 'E': 0.97},
            'tier_counts': {'III': 2, 'II': 1},
            'suppressed_counts': {},
            'distribution': {'p50': 0.12, 'p67': 0.19, 'p75': 0.22, 'p90': 0.4},
        }

        call_command(
            'backfill_player_efficiency_ranks',
            limit=10,
            publish_partial=True,
        )

        mock_recompute_efficiency_rank_snapshot.assert_called_once_with(
            player_limit=10,
            skip_refresh=False,
            publish_partial=True,
        )


class EntityVisitMaintenanceCommandTests(TestCase):
    def test_rebuild_entity_visit_daily_recomputes_aggregate_rows(self):
        event_date = timezone.now().date()
        EntityVisitEvent.objects.create(
            event_uuid='5cedd17d-1ea2-4a54-8aad-8ac22be25a01',
            occurred_at=timezone.now(),
            event_date=event_date,
            entity_type='player',
            entity_id=701,
            entity_name_snapshot='Player Seven',
            entity_slug_snapshot='player-seven',
            route_path='/player/player-seven',
            referrer_path='/',
            visitor_key_hash='visitor-a',
            session_key_hash='session-a',
            counted_in_deduped_views=True,
        )
        EntityVisitEvent.objects.create(
            event_uuid='5cedd17d-1ea2-4a54-8aad-8ac22be25a02',
            occurred_at=timezone.now(),
            event_date=event_date,
            entity_type='player',
            entity_id=701,
            entity_name_snapshot='Player Seven',
            entity_slug_snapshot='player-seven',
            route_path='/player/player-seven',
            referrer_path='/',
            visitor_key_hash='visitor-a',
            session_key_hash='session-b',
            counted_in_deduped_views=False,
        )
        EntityVisitDaily.objects.create(
            date=event_date,
            entity_type='player',
            entity_id=701,
            entity_name_snapshot='stale',
            views_raw=99,
            views_deduped=99,
            unique_visitors=99,
            unique_sessions=99,
        )

        stdout = StringIO()
        call_command('rebuild_entity_visit_daily', stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['status'], 'completed')
        rebuilt_row = EntityVisitDaily.objects.get(
            entity_type='player', entity_id=701, date=event_date)
        self.assertEqual(rebuilt_row.views_raw, 2)
        self.assertEqual(rebuilt_row.views_deduped, 1)
        self.assertEqual(rebuilt_row.unique_visitors, 1)
        self.assertEqual(rebuilt_row.unique_sessions, 2)

    def test_rebuild_entity_visit_daily_dry_run_does_not_write_rows(self):
        event_date = timezone.now().date()
        EntityVisitEvent.objects.create(
            event_uuid='5cedd17d-1ea2-4a54-8aad-8ac22be25a03',
            occurred_at=timezone.now(),
            event_date=event_date,
            entity_type='clan',
            entity_id=801,
            entity_name_snapshot='Clan Eight',
            entity_slug_snapshot='801-clan-eight',
            route_path='/clan/801-clan-eight',
            referrer_path='/',
            visitor_key_hash='visitor-c',
            session_key_hash='session-c',
        )

        stdout = StringIO()
        call_command('rebuild_entity_visit_daily', dry_run=True, stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['status'], 'dry-run')
        self.assertEqual(EntityVisitDaily.objects.count(), 0)

    def test_cleanup_entity_visit_events_dry_run_and_delete(self):
        old_date = timezone.now().date() - timedelta(days=120)
        recent_date = timezone.now().date() - timedelta(days=5)
        EntityVisitEvent.objects.create(
            event_uuid='5cedd17d-1ea2-4a54-8aad-8ac22be25a04',
            occurred_at=timezone.now() - timedelta(days=120),
            event_date=old_date,
            entity_type='player',
            entity_id=901,
            entity_name_snapshot='Old Player',
            entity_slug_snapshot='old-player',
            route_path='/player/old-player',
            referrer_path='/',
            visitor_key_hash='visitor-old',
            session_key_hash='session-old',
        )
        EntityVisitEvent.objects.create(
            event_uuid='5cedd17d-1ea2-4a54-8aad-8ac22be25a05',
            occurred_at=timezone.now() - timedelta(days=5),
            event_date=recent_date,
            entity_type='player',
            entity_id=902,
            entity_name_snapshot='Recent Player',
            entity_slug_snapshot='recent-player',
            route_path='/player/recent-player',
            referrer_path='/',
            visitor_key_hash='visitor-recent',
            session_key_hash='session-recent',
        )
        EntityVisitDaily.objects.create(
            date=old_date,
            entity_type='player',
            entity_id=901,
            entity_name_snapshot='Old Player',
            views_raw=1,
            views_deduped=1,
            unique_visitors=1,
            unique_sessions=1,
        )

        dry_run_stdout = StringIO()
        call_command('cleanup_entity_visit_events',
                     older_than_days=90, dry_run=True, stdout=dry_run_stdout)
        dry_run_payload = json.loads(dry_run_stdout.getvalue())
        self.assertEqual(dry_run_payload['status'], 'dry-run')
        self.assertEqual(dry_run_payload['matching_rows'], 1)
        self.assertEqual(EntityVisitEvent.objects.count(), 2)
        self.assertEqual(EntityVisitDaily.objects.count(), 1)

        stdout = StringIO()
        call_command('cleanup_entity_visit_events',
                     older_than_days=90, stdout=stdout)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['status'], 'completed')
        self.assertEqual(EntityVisitEvent.objects.count(), 1)
        self.assertEqual(EntityVisitDaily.objects.count(), 1)
