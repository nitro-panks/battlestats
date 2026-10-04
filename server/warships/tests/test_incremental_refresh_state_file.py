"""The incremental refresh lanes keep one checkpoint file per realm.

A checkpoint holds one realm's Player rows. Shared across realms, the ranked
lane aborted on every foreign row and the player lane counted each one as a
success without refreshing it.
runbook-incremental-refresh-shared-state-file-2026-10-04.md
"""
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from warships.management.commands.incremental_player_refresh import _refresh_player
from warships.models import Player
from warships.tasks import (
    _realm_state_file,
    incremental_player_refresh_task,
    incremental_ranked_data_task,
)

RANKED = "warships.management.commands.incremental_ranked_data"
PLAYER = "warships.management.commands.incremental_player_refresh"


def _write_state(path: Path, **overrides) -> None:
    state = {
        "version": 1,
        "pending_player_ids": [],
        "next_index": 0,
        "processed_total": 0,
        "succeeded_total": 0,
        "error_total": 0,
        "failed_player_ids": [],
        "last_error": None,
    }
    state.update(overrides)
    path.write_text(json.dumps(state))


class RealmStateFileTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_realm_is_inserted_before_the_suffix(self):
        self.assertEqual(
            _realm_state_file("/shared/logs/incremental_ranked_data_state.json", "eu"),
            "/shared/logs/incremental_ranked_data_state.eu.json",
        )

    @patch("warships.tasks.call_command")
    def test_ranked_task_passes_its_realm_checkpoint(self, mock_call_command):
        incremental_ranked_data_task.apply(kwargs={"realm": "asia"})

        kwargs = mock_call_command.call_args.kwargs
        self.assertEqual(kwargs["realm"], "asia")
        self.assertTrue(kwargs["state_file"].endswith(
            "incremental_ranked_data_state.asia.json"))

    @patch("warships.tasks.call_command")
    def test_player_task_passes_its_realm_checkpoint(self, mock_call_command):
        incremental_player_refresh_task.apply(kwargs={"realm": "eu"})

        kwargs = mock_call_command.call_args.kwargs
        self.assertEqual(kwargs["realm"], "eu")
        self.assertTrue(kwargs["state_file"].endswith(
            "incremental_player_refresh_state.eu.json"))


class IncrementalRankedCheckpointRealmTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="RankedEU", player_id=5101, realm="eu", is_hidden=False)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state_file = Path(self.tmpdir.name) / "ranked-state.json"

    def _run(self, **options):
        call_command(
            "incremental_ranked_data", realm="eu",
            state_file=str(self.state_file), stdout=StringIO(), stderr=StringIO(),
            **options)

    @patch(f"{RANKED}.update_ranked_data")
    @patch(f"{RANKED}._build_candidate_queue")
    def test_checkpoint_from_another_realm_is_rebuilt(self, mock_build, mock_update):
        # 999 and 998 stand for another realm's rows, one pending and one failed.
        _write_state(self.state_file, realm="asia",
                     pending_player_ids=[999], failed_player_ids=[998])
        mock_build.return_value = [self.player.id]

        self._run()

        mock_build.assert_called_once()
        mock_update.assert_called_once_with(self.player.player_id, realm="eu")
        self.assertEqual(json.loads(self.state_file.read_text())["realm"], "eu")

    @patch(f"{RANKED}.update_ranked_data")
    @patch(f"{RANKED}._build_candidate_queue")
    def test_unstamped_checkpoint_is_rebuilt(self, mock_build, mock_update):
        # The pre-fix shared file carries no realm at all.
        _write_state(self.state_file, pending_player_ids=[999])
        mock_build.return_value = [self.player.id]

        self._run()

        mock_build.assert_called_once()
        mock_update.assert_called_once_with(self.player.player_id, realm="eu")

    @patch(f"{RANKED}.update_ranked_data")
    @patch(f"{RANKED}._build_candidate_queue")
    def test_own_realm_checkpoint_is_resumed(self, mock_build, mock_update):
        _write_state(self.state_file, realm="eu",
                     pending_player_ids=[self.player.id])

        self._run()

        mock_build.assert_not_called()
        mock_update.assert_called_once_with(self.player.player_id, realm="eu")

    @patch(f"{RANKED}.update_ranked_data", side_effect=RuntimeError("boom"))
    @patch(f"{RANKED}._build_candidate_queue")
    def test_abort_on_max_errors_raises_after_saving(self, mock_build, _mock_update):
        mock_build.return_value = [self.player.id]

        with self.assertRaises(CommandError):
            self._run(max_errors=1)

        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["failed_player_ids"], [self.player.id])


class IncrementalPlayerRefreshCheckpointRealmTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="RefreshEU", player_id=5201, realm="eu", is_hidden=False)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state_file = Path(self.tmpdir.name) / "player-state.json"

    def _run(self, **options):
        call_command(
            "incremental_player_refresh", realm="eu",
            state_file=str(self.state_file), stdout=StringIO(), stderr=StringIO(),
            **options)

    @patch(f"{PLAYER}._refresh_player")
    @patch(f"{PLAYER}._build_candidate_queue")
    def test_checkpoint_from_another_realm_is_rebuilt(self, mock_build, mock_refresh):
        _write_state(self.state_file, realm="na", pending_player_ids=[999, 998])
        mock_build.return_value = (
            [self.player.id], {"hot": 1, "active": 0, "warm": 0})

        self._run()

        mock_build.assert_called_once()
        mock_refresh.assert_called_once_with(self.player.id, realm="eu")
        self.assertEqual(json.loads(self.state_file.read_text())["realm"], "eu")

    @patch(f"{PLAYER}._refresh_player")
    @patch(f"{PLAYER}._build_candidate_queue")
    def test_own_realm_checkpoint_is_resumed(self, mock_build, mock_refresh):
        _write_state(self.state_file, realm="eu",
                     pending_player_ids=[self.player.id])

        self._run()

        mock_build.assert_not_called()
        mock_refresh.assert_called_once_with(self.player.id, realm="eu")

    @patch(f"{PLAYER}.fetch_players_bulk")
    def test_refresh_player_makes_no_wg_call_for_another_realm(self, mock_fetch):
        _refresh_player(self.player.id, realm="na")

        mock_fetch.assert_not_called()

    @patch(f"{PLAYER}._refresh_player", side_effect=RuntimeError("boom"))
    @patch(f"{PLAYER}._build_candidate_queue")
    def test_abort_on_max_errors_raises_after_saving(self, mock_build, _mock_refresh):
        mock_build.return_value = (
            [self.player.id], {"hot": 1, "active": 0, "warm": 0})

        with self.assertRaises(CommandError):
            self._run(max_errors=1)

        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["failed_player_ids"], [self.player.id])
