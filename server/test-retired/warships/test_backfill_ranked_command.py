import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player


class BackfillRankedCommandTests(TestCase):
    def test_backfill_ranked_command_resumes_after_checkpoint(self):
        first = Player.objects.create(name='FirstRanked', player_id=91001)
        second = Player.objects.create(name='SecondRanked', player_id=91002)
        third = Player.objects.create(name='ThirdRanked', player_id=91003)

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'ranked-state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'last_player_id': second.id,
                'failed_player_ids': [],
            }))

            with patch('warships.management.commands.backfill_ranked_data.update_ranked_data') as mock_update:
                call_command('backfill_ranked_data',
                             '--state-file', str(state_path))

            self.assertEqual(
                mock_update.call_args_list[0].args[0], third.player_id)
            self.assertEqual(len(mock_update.call_args_list), 1)

            state = json.loads(state_path.read_text())
            self.assertEqual(state['last_player_id'], third.id)
            self.assertEqual(state['failed_player_ids'], [])
            self.assertIsNotNone(state['completed_at'])

        self.assertIsNotNone(first.id)

    def test_backfill_ranked_command_retries_pending_failures_before_continuing(self):
        first = Player.objects.create(name='RetryRanked', player_id=92001)
        second = Player.objects.create(name='NextRanked', player_id=92002)

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'ranked-state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'last_player_id': second.id,
                'failed_player_ids': [first.id],
            }))

            with patch('warships.management.commands.backfill_ranked_data.update_ranked_data') as mock_update:
                call_command('backfill_ranked_data',
                             '--state-file', str(state_path))

            self.assertEqual(
                mock_update.call_args_list[0].args[0], first.player_id)
            self.assertEqual(len(mock_update.call_args_list), 1)

            state = json.loads(state_path.read_text())
            self.assertEqual(state['failed_player_ids'], [])
            self.assertEqual(state['last_player_id'], second.id)

    def test_backfill_ranked_command_only_processes_missing_or_stale_rows(self):
        missing = Player.objects.create(
            name='MissingRanked', player_id=93001, is_hidden=False)
        fresh = Player.objects.create(
            name='FreshRanked',
            player_id=93002,
            is_hidden=False,
            ranked_json=[],
            ranked_updated_at=timezone.now(),
        )
        stale = Player.objects.create(
            name='StaleRanked',
            player_id=93003,
            is_hidden=False,
            ranked_json=[],
            ranked_updated_at=timezone.now() - timedelta(hours=72),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'ranked-state.json'

            with patch('warships.management.commands.backfill_ranked_data.update_ranked_data') as mock_update:
                call_command(
                    'backfill_ranked_data',
                    '--state-file', str(state_path),
                    '--refresh-older-than-hours', '24',
                )

            processed_ids = [call.args[0]
                             for call in mock_update.call_args_list]
            self.assertEqual(
                processed_ids, [missing.player_id, stale.player_id])
            self.assertNotIn(fresh.player_id, processed_ids)

    def test_backfill_ranked_command_repairs_rows_missing_top_ship_enrichment(self):
        player = Player.objects.create(
            name='MissingTopShip',
            player_id=93010,
            is_hidden=False,
            ranked_json=[
                {
                    'season_id': 1100,
                    'total_battles': 42,
                    'total_wins': 24,
                    'win_rate': 0.5714,
                    'highest_league': 2,
                    'highest_league_name': 'Silver',
                }
            ],
            ranked_updated_at=timezone.now(),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'ranked-state.json'

            with patch('warships.management.commands.backfill_ranked_data.update_ranked_data') as mock_update:
                call_command(
                    'backfill_ranked_data',
                    '--state-file', str(state_path),
                )

            self.assertEqual([call.args[0] for call in mock_update.call_args_list], [
                             player.player_id])
