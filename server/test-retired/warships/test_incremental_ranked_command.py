import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player


class IncrementalRankedCommandTests(TestCase):
    def test_incremental_ranked_command_interleaves_discovery_into_run(self):
        now = timezone.now()
        known_players = [
            Player.objects.create(
                name=f'KnownInterleave{index}',
                player_id=97000 + index,
                is_hidden=False,
                pvp_battles=5000 - index,
                last_lookup=now,
                ranked_json=[{'season_id': 9, 'total_battles': 80,
                              'total_wins': 45, 'win_rate': 0.5625}],
                ranked_updated_at=now - timedelta(hours=48),
            )
            for index in range(4)
        ]
        discovery_player = Player.objects.create(
            name='DiscoveryInterleave',
            player_id=98001,
            is_hidden=False,
            pvp_battles=3000,
            last_lookup=now,
            ranked_json=None,
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'incremental-state.json'

            with patch('warships.management.commands.incremental_ranked_data.update_ranked_data') as mock_update:
                call_command(
                    'incremental_ranked_data',
                    '--state-file', str(state_path),
                    '--limit', '5',
                    '--known-limit', '4',
                    '--discovery-limit', '1',
                )

            processed_ids = [call.args[0]
                             for call in mock_update.call_args_list]
            self.assertEqual(processed_ids[:5], [
                known_players[0].player_id,
                known_players[1].player_id,
                known_players[2].player_id,
                known_players[3].player_id,
                discovery_player.player_id,
            ])

    def test_incremental_ranked_command_builds_priority_queue(self):
        now = timezone.now()
        known_ranked = Player.objects.create(
            name='KnownRanked',
            player_id=94001,
            is_hidden=False,
            pvp_battles=4000,
            last_lookup=now,
            ranked_json=[{'season_id': 9, 'total_battles': 90,
                          'total_wins': 50, 'win_rate': 0.5556}],
            ranked_updated_at=now - timedelta(hours=48),
        )
        discovery = Player.objects.create(
            name='DiscoveryRanked',
            player_id=94002,
            is_hidden=False,
            pvp_battles=2400,
            last_lookup=now,
            ranked_json=None,
        )
        fresh_known = Player.objects.create(
            name='FreshRanked',
            player_id=94003,
            is_hidden=False,
            pvp_battles=3500,
            last_lookup=now,
            ranked_json=[{'season_id': 9, 'total_battles': 70,
                          'total_wins': 40, 'win_rate': 0.5714}],
            ranked_updated_at=now,
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'incremental-state.json'

            with patch('warships.management.commands.incremental_ranked_data.update_ranked_data') as mock_update:
                call_command(
                    'incremental_ranked_data',
                    '--state-file', str(state_path),
                    '--limit', '2',
                    '--batch-size', '2',
                    '--skip-fresh-hours', '18',
                    '--known-limit', '10',
                    '--discovery-limit', '10',
                )

            processed_ids = [call.args[0]
                             for call in mock_update.call_args_list]
            self.assertEqual(
                processed_ids, [known_ranked.player_id, discovery.player_id])
            self.assertNotIn(fresh_known.player_id, processed_ids)

            state = json.loads(state_path.read_text())
            self.assertEqual(state['pending_player_ids'], [])
            self.assertIsNotNone(state['cycle_completed_at'])

    def test_incremental_ranked_command_resumes_existing_queue(self):
        first = Player.objects.create(
            name='FirstInc', player_id=95001, is_hidden=False)
        second = Player.objects.create(
            name='SecondInc', player_id=95002, is_hidden=False)

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'incremental-state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'pending_player_ids': [first.id, second.id],
                'next_index': 1,
                'failed_player_ids': [],
            }))

            with patch('warships.management.commands.incremental_ranked_data.update_ranked_data') as mock_update:
                call_command('incremental_ranked_data',
                             '--state-file', str(state_path), '--limit', '1')

            self.assertEqual(
                mock_update.call_args_list[0].args[0], second.player_id)
            state = json.loads(state_path.read_text())
            self.assertEqual(state['pending_player_ids'], [])
            self.assertEqual(state['next_index'], 0)

    def test_incremental_ranked_command_retries_failures_before_queue(self):
        retry_player = Player.objects.create(
            name='RetryInc', player_id=96001, is_hidden=False)
        queue_player = Player.objects.create(
            name='QueueInc', player_id=96002, is_hidden=False)

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'incremental-state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'pending_player_ids': [queue_player.id],
                'next_index': 0,
                'failed_player_ids': [retry_player.id],
            }))

            with patch('warships.management.commands.incremental_ranked_data.update_ranked_data') as mock_update:
                call_command('incremental_ranked_data',
                             '--state-file', str(state_path), '--limit', '2')

            processed_ids = [call.args[0]
                             for call in mock_update.call_args_list]
            self.assertEqual(
                processed_ids, [retry_player.player_id, queue_player.player_id])
            state = json.loads(state_path.read_text())
            self.assertEqual(state['failed_player_ids'], [])
