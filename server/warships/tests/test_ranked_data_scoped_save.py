"""Regression: update_ranked_data must not clobber battles_updated_at.

Root cause (runbook-player-refresh-pill-clobber-2026-06-21): on a player visit,
``update_ranked_data`` and ``update_battle_data`` are dispatched concurrently.
``update_ranked_data`` loads the full Player row, makes a slow WG fetch, then
historically did a bare ``player.save()`` — writing back EVERY field on its now-stale
snapshot, including ``battles_updated_at``. If ``update_battle_data`` stamped
``battles_updated_at = now()`` during that fetch, the bare save reverted it, re-arming
the "Updating…" pill (``_player_refresh_signals`` anchors solely on ``battles_updated_at``).

The fix scopes the saves to ``update_fields=['ranked_json', 'ranked_updated_at',
'ranked_last_season_id']``. These tests reproduce the interleaving by having the WG
fetch's side effect write a fresh ``battles_updated_at`` straight to the DB row (the
"concurrent update_battle_data") and asserting it survives.
"""

from datetime import datetime
from unittest.mock import patch

from django.test import TestCase

from warships.data import update_ranked_data
from warships.models import Player

PID = 1054131305
OLD = datetime(2026, 6, 20, 10, 0, 0)      # snapshot loaded by update_ranked_data
FRESH = datetime(2026, 6, 21, 20, 37, 18)  # what the concurrent update_battle_data writes


def _concurrent_battle_refresh(*_args, **_kwargs):
    """Simulate update_battle_data landing its now()-write mid-ranked-fetch."""
    Player.objects.filter(player_id=PID, realm='na').update(battles_updated_at=FRESH)


class RankedDataScopedSaveTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            name='HMSHOOD06', player_id=PID, realm='na', battles_updated_at=OLD)

    @patch('warships.data._get_ranked_seasons_metadata', return_value={})
    @patch('warships.data._fetch_ranked_account_info')
    def test_no_rank_info_branch_preserves_battles_updated_at(self, mock_acct, _meta):
        # No rank_info → the early save branch; side effect = concurrent battle refresh.
        mock_acct.side_effect = lambda *a, **k: (
            _concurrent_battle_refresh() or None)

        update_ranked_data(PID, realm='na')

        row = Player.objects.get(player_id=PID, realm='na')
        # The fresh battles_updated_at from the concurrent writer must NOT be reverted.
        self.assertEqual(row.battles_updated_at, FRESH)
        # And the task's own column was still persisted.
        self.assertEqual(row.ranked_json, [])

    @patch('warships.data.refresh_player_explorer_summary')
    @patch('warships.data.ranked_last_season_from_json', return_value=None)
    @patch('warships.data._aggregate_ranked_seasons', return_value=[{'season_id': 5}])
    @patch('warships.data._build_top_ranked_ship_names_by_season', return_value={})
    @patch('warships.data._fetch_ranked_ship_stats_for_player', return_value=[])
    @patch('warships.data._get_ranked_seasons_metadata', return_value={})
    @patch('warships.data._fetch_ranked_account_info')
    def test_main_branch_preserves_battles_updated_at(
        self, mock_acct, _meta, _ships, _top, _agg, _last, _refresh,
    ):
        # rank_info present → the main save branch; concurrent refresh during the fetch.
        def _side_effect(*_a, **_k):
            _concurrent_battle_refresh()
            return {'rank_info': {'5': {'battles': 10}}}
        mock_acct.side_effect = _side_effect

        update_ranked_data(PID, realm='na')

        row = Player.objects.get(player_id=PID, realm='na')
        self.assertEqual(row.battles_updated_at, FRESH)
        # The ranked payload the task owns was still written.
        self.assertEqual(row.ranked_json, [{'season_id': 5}])
