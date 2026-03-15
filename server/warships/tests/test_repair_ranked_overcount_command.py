import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player


class RepairRankedOvercountCommandTests(TestCase):
    def test_audit_only_reports_affected_players_without_repairing(self):
        affected = Player.objects.create(
            name='AffectedAudit',
            player_id=99001,
            is_hidden=False,
            ranked_json=[
                {'season_id': 1002, 'total_battles': 22,
                    'total_wins': 154, 'win_rate': 7.0}
            ],
            ranked_updated_at=timezone.now(),
        )
        Player.objects.create(
            name='CleanAudit',
            player_id=99002,
            is_hidden=False,
            ranked_json=[
                {'season_id': 1008, 'total_battles': 22,
                    'total_wins': 12, 'win_rate': 0.5455}
            ],
            ranked_updated_at=timezone.now(),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'repair-state.json'

            with patch('warships.management.commands.repair_ranked_overcount.update_ranked_data') as mock_update:
                call_command(
                    'repair_ranked_overcount',
                    '--state-file', str(state_path),
                    '--audit-only',
                )

            mock_update.assert_not_called()
            state = json.loads(state_path.read_text())
            self.assertEqual(state['affected_total'], 1)
            self.assertEqual(state['repaired_total'], 0)
            self.assertEqual(state['last_player_id'], affected.id + 1)

    def test_repair_only_updates_affected_players(self):
        affected = Player.objects.create(
            name='AffectedRepair',
            player_id=99101,
            is_hidden=False,
            ranked_json=[
                {'season_id': 1003, 'total_battles': 22,
                    'total_wins': 84, 'win_rate': 3.8182}
            ],
            ranked_updated_at=timezone.now(),
        )
        Player.objects.create(
            name='CleanRepair',
            player_id=99102,
            is_hidden=False,
            ranked_json=[
                {'season_id': 1009, 'total_battles': 80,
                    'total_wins': 50, 'win_rate': 0.625}
            ],
            ranked_updated_at=timezone.now(),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'repair-state.json'

            with patch('warships.management.commands.repair_ranked_overcount.update_ranked_data') as mock_update:
                call_command(
                    'repair_ranked_overcount',
                    '--state-file', str(state_path),
                )

            self.assertEqual([call.args[0] for call in mock_update.call_args_list], [
                             affected.player_id])
            state = json.loads(state_path.read_text())
            self.assertEqual(state['affected_total'], 1)
            self.assertEqual(state['repaired_total'], 1)
            self.assertEqual(state['failed_player_ids'], [])
            self.assertIsNotNone(state['completed_at'])

    def test_repair_resumes_after_checkpoint(self):
        first = Player.objects.create(
            name='FirstRepairResume',
            player_id=99201,
            is_hidden=False,
            ranked_json=[
                {'season_id': 1001, 'total_battles': 2,
                    'total_wins': 5, 'win_rate': 2.5}
            ],
            ranked_updated_at=timezone.now(),
        )
        second = Player.objects.create(
            name='SecondRepairResume',
            player_id=99202,
            is_hidden=False,
            ranked_json=[
                {'season_id': 1002, 'total_battles': 2,
                    'total_wins': 7, 'win_rate': 3.5}
            ],
            ranked_updated_at=timezone.now(),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'repair-state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'last_player_id': first.id,
                'failed_player_ids': [],
                'scanned_total': 1,
                'affected_total': 1,
                'repaired_total': 1,
            }))

            with patch('warships.management.commands.repair_ranked_overcount.update_ranked_data') as mock_update:
                call_command(
                    'repair_ranked_overcount',
                    '--state-file', str(state_path),
                )

            self.assertEqual([call.args[0] for call in mock_update.call_args_list], [
                             second.player_id])
            state = json.loads(state_path.read_text())
            self.assertEqual(state['affected_total'], 2)
            self.assertEqual(state['repaired_total'], 2)
