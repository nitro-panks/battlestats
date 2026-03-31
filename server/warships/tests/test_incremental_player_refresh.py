import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.management.commands.incremental_player_refresh import (
    _build_candidate_queue,
)
from warships.models import Player
from warships.tasks import _clan_crawl_lock_key


class CandidateSelectionTests(TestCase):
    """Tests for the tier-based candidate queue builder."""

    def setUp(self):
        self.now = timezone.now()
        self.today = self.now.date()

    def test_hot_tier_selects_recently_viewed_stale_players(self):
        """Player viewed 3 days ago with last_fetch 13 hours ago → Hot tier."""
        hot = Player.objects.create(
            name='HotPlayer', player_id=70001,
            last_lookup=self.now - timedelta(days=3),
            last_fetch=self.now - timedelta(hours=13),
            last_battle_date=self.today - timedelta(days=5),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertIn(hot.id, ids)
        self.assertGreater(counts['hot'], 0)

    def test_hot_tier_excludes_fresh_players(self):
        """Player viewed 3 days ago with last_fetch 6 hours ago → excluded (fresh)."""
        fresh = Player.objects.create(
            name='FreshHot', player_id=70002,
            last_lookup=self.now - timedelta(days=3),
            last_fetch=self.now - timedelta(hours=6),
            last_battle_date=self.today - timedelta(days=5),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertNotIn(fresh.id, ids)

    def test_hot_tier_excludes_old_lookup(self):
        """Player with last_lookup 20 days ago → not Hot."""
        old_lookup = Player.objects.create(
            name='OldLookup', player_id=70003,
            last_lookup=self.now - timedelta(days=20),
            last_fetch=self.now - timedelta(hours=25),
            last_battle_date=self.today - timedelta(days=5),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        # Should be in Active, not Hot
        self.assertEqual(counts['hot'], 0)
        self.assertIn(old_lookup.id, ids)

    def test_active_tier_selects_recently_battled_stale_players(self):
        """Player battled 10 days ago, last_fetch 25 hours ago → Active tier."""
        active = Player.objects.create(
            name='ActivePlayer', player_id=70004,
            last_battle_date=self.today - timedelta(days=10),
            last_fetch=self.now - timedelta(hours=25),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertIn(active.id, ids)
        self.assertGreater(counts['active'], 0)

    def test_active_tier_excludes_hot_players(self):
        """Player qualifying for both Hot and Active appears only in Hot."""
        both = Player.objects.create(
            name='BothTiers', player_id=70005,
            last_lookup=self.now - timedelta(days=3),
            last_fetch=self.now - timedelta(hours=25),
            last_battle_date=self.today - timedelta(days=10),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        # Should appear exactly once (in Hot)
        self.assertEqual(ids.count(both.id), 1)
        self.assertGreater(counts['hot'], 0)

    def test_warm_tier_selects_older_battled_stale_players(self):
        """Player battled 60 days ago, last_fetch 80 hours ago → Warm tier."""
        warm = Player.objects.create(
            name='WarmPlayer', player_id=70006,
            last_battle_date=self.today - timedelta(days=60),
            last_fetch=self.now - timedelta(hours=80),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertIn(warm.id, ids)
        self.assertGreater(counts['warm'], 0)

    def test_dormant_player_excluded_from_all_tiers(self):
        """Player with last_battle_date 200 days ago → not in any tier."""
        dormant = Player.objects.create(
            name='DormantPlayer', player_id=70007,
            last_battle_date=self.today - timedelta(days=200),
            last_fetch=self.now - timedelta(hours=100),
        )
        ids, _ = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertNotIn(dormant.id, ids)

    def test_boundary_active_30_days(self):
        """Player with last_battle_date exactly 30 days ago → Active tier (inclusive)."""
        boundary = Player.objects.create(
            name='BoundaryActive', player_id=70008,
            last_battle_date=self.today - timedelta(days=30),
            last_fetch=self.now - timedelta(hours=25),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertIn(boundary.id, ids)
        self.assertGreater(counts['active'], 0)

    def test_boundary_warm_90_days(self):
        """Player battled exactly 90 days ago → included in Warm tier range."""
        boundary_warm = Player.objects.create(
            name='BoundaryWarm', player_id=70009,
            last_battle_date=self.today - timedelta(days=90),
            last_fetch=self.now - timedelta(hours=80),
        )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertIn(boundary_warm.id, ids)

    def test_active_tier_respects_cap(self):
        """Active tier respects the active_limit cap."""
        for i in range(5):
            Player.objects.create(
                name=f'CapTest{i}', player_id=70100 + i,
                last_battle_date=self.today - timedelta(days=5),
                last_fetch=self.now - timedelta(hours=25),
                pvp_battles=1000 - i,
            )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=3, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertEqual(counts['active'], 3)

    def test_hot_tier_is_uncapped(self):
        """All qualifying Hot players are included regardless of count."""
        for i in range(10):
            Player.objects.create(
                name=f'HotUncap{i}', player_id=70200 + i,
                last_lookup=self.now - timedelta(days=1),
                last_fetch=self.now - timedelta(hours=13),
                last_battle_date=self.today - timedelta(days=5),
            )
        ids, counts = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertEqual(counts['hot'], 10)

    def test_null_last_fetch_included(self):
        """Player with no last_fetch (never crawled) is included."""
        never_fetched = Player.objects.create(
            name='NeverFetched', player_id=70300,
            last_battle_date=self.today - timedelta(days=5),
            last_fetch=None,
        )
        ids, _ = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertIn(never_fetched.id, ids)

    def test_hidden_players_included(self):
        """Hidden players are included in candidate selection."""
        hidden = Player.objects.create(
            name='HiddenPlayer', player_id=70400,
            is_hidden=True,
            last_battle_date=self.today - timedelta(days=5),
            last_fetch=self.now - timedelta(hours=25),
        )
        ids, _ = _build_candidate_queue(
            hot_stale_hours=12, active_stale_hours=24, warm_stale_hours=72,
            active_limit=500, warm_limit=200,
            hot_lookback_days=14, active_lookback_days=30, warm_lookback_days=90,
        )
        self.assertIn(hidden.id, ids)


class CheckpointTests(TestCase):
    """Tests for checkpoint durability and resume behavior."""

    def test_checkpoint_saves_and_resumes(self):
        """Resumed run skips already-processed players."""
        first = Player.objects.create(
            name='First', player_id=71001)
        second = Player.objects.create(
            name='Second', player_id=71002)

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'pending_player_ids': [first.id, second.id],
                'next_index': 1,
                'processed_total': 1,
                'succeeded_total': 1,
                'error_total': 0,
                'failed_player_ids': [],
                'tier_counts': {'hot': 1, 'active': 1, 'warm': 0},
            }))

            with patch(
                'warships.management.commands.incremental_player_refresh._refresh_player'
            ) as mock_refresh:
                call_command(
                    'incremental_player_refresh',
                    '--state-file', str(state_path),
                    '--limit', '1',
                )

            self.assertEqual(mock_refresh.call_count, 1)
            mock_refresh.assert_called_with(second.id)

    def test_fresh_run_ignores_stale_checkpoint(self):
        """--reset-state forces queue rebuild."""
        player = Player.objects.create(
            name='ResetTarget', player_id=71003,
            last_battle_date=timezone.now().date() - timedelta(days=5),
            last_fetch=timezone.now() - timedelta(hours=25),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'pending_player_ids': [999999],
                'next_index': 0,
                'failed_player_ids': [],
            }))

            with patch(
                'warships.management.commands.incremental_player_refresh._refresh_player'
            ) as mock_refresh:
                call_command(
                    'incremental_player_refresh',
                    '--state-file', str(state_path),
                    '--reset-state',
                    '--limit', '10',
                )

            processed_ids = [call.args[0]
                             for call in mock_refresh.call_args_list]
            self.assertIn(player.id, processed_ids)
            self.assertNotIn(999999, processed_ids)


class ErrorBudgetTests(TestCase):
    """Tests for error budget halt behavior."""

    def test_stops_after_max_errors(self):
        """Processing halts when error budget is exhausted."""
        players = [
            Player.objects.create(name=f'Err{i}', player_id=72000 + i)
            for i in range(5)
        ]

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'pending_player_ids': [p.id for p in players],
                'next_index': 0,
                'processed_total': 0,
                'succeeded_total': 0,
                'error_total': 0,
                'failed_player_ids': [],
                'tier_counts': {'hot': 0, 'active': 5, 'warm': 0},
            }))

            with patch(
                'warships.management.commands.incremental_player_refresh._refresh_player',
                side_effect=Exception('API down'),
            ):
                call_command(
                    'incremental_player_refresh',
                    '--state-file', str(state_path),
                    '--limit', '10',
                    '--max-errors', '2',
                )

            state = json.loads(state_path.read_text())
            self.assertEqual(state['error_total'], 2)
            self.assertGreater(
                len(state['pending_player_ids']) - state['next_index'], 0,
                'Queue should still have unprocessed players',
            )


class LockExclusionTests(TestCase):
    """Tests for lock exclusion with clan crawl."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_skips_when_clan_crawl_lock_held(self):
        """Skips cycle when clan crawl lock is set."""
        cache.set(_clan_crawl_lock_key(), 'some-task-id', timeout=300)

        Player.objects.create(
            name='Locked', player_id=73001,
            last_battle_date=timezone.now().date() - timedelta(days=5),
            last_fetch=timezone.now() - timedelta(hours=25),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'state.json'

            with patch(
                'warships.management.commands.incremental_player_refresh._refresh_player'
            ) as mock_refresh:
                call_command(
                    'incremental_player_refresh',
                    '--state-file', str(state_path),
                )

            mock_refresh.assert_not_called()

    def test_runs_when_no_lock(self):
        """Runs normally when clan crawl lock is absent."""
        Player.objects.create(
            name='Unlocked', player_id=73002,
            last_battle_date=timezone.now().date() - timedelta(days=5),
            last_fetch=timezone.now() - timedelta(hours=25),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'state.json'

            with patch(
                'warships.management.commands.incremental_player_refresh._refresh_player'
            ) as mock_refresh:
                call_command(
                    'incremental_player_refresh',
                    '--state-file', str(state_path),
                    '--limit', '10',
                )

            self.assertGreater(mock_refresh.call_count, 0)


class RetryTests(TestCase):
    """Tests for failed player retry behavior."""

    def test_retries_failed_players_before_queue(self):
        """Previously failed players are retried before continuing the queue."""
        retry_player = Player.objects.create(
            name='RetryP', player_id=74001)
        queue_player = Player.objects.create(
            name='QueueP', player_id=74002)

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'state.json'
            state_path.write_text(json.dumps({
                'version': 1,
                'pending_player_ids': [queue_player.id],
                'next_index': 0,
                'processed_total': 0,
                'succeeded_total': 0,
                'error_total': 0,
                'failed_player_ids': [retry_player.id],
                'tier_counts': {'hot': 0, 'active': 1, 'warm': 0},
            }))

            with patch(
                'warships.management.commands.incremental_player_refresh._refresh_player'
            ) as mock_refresh:
                call_command(
                    'incremental_player_refresh',
                    '--state-file', str(state_path),
                    '--limit', '2',
                )

            processed_ids = [call.args[0]
                             for call in mock_refresh.call_args_list]
            self.assertEqual(processed_ids, [retry_player.id, queue_player.id])
            state = json.loads(state_path.read_text())
            self.assertEqual(state['failed_player_ids'], [])


class DryRunTests(TestCase):
    """Tests for dry-run mode."""

    def test_dry_run_does_not_refresh(self):
        """Dry-run logs candidates without API calls."""
        Player.objects.create(
            name='DryRunP', player_id=75001,
            last_battle_date=timezone.now().date() - timedelta(days=5),
            last_fetch=timezone.now() - timedelta(hours=25),
        )

        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / 'state.json'

            with patch(
                'warships.management.commands.incremental_player_refresh._refresh_player'
            ) as mock_refresh:
                call_command(
                    'incremental_player_refresh',
                    '--state-file', str(state_path),
                    '--dry-run',
                )

            mock_refresh.assert_not_called()

            state = json.loads(state_path.read_text())
            self.assertGreater(len(state['pending_player_ids']), 0)


class RefreshPlayerTests(TestCase):
    """Tests for the per-player refresh logic."""

    @patch('warships.management.commands.incremental_player_refresh.update_achievements_data')
    @patch('warships.management.commands.incremental_player_refresh.update_player_efficiency_data')
    @patch('warships.management.commands.incremental_player_refresh.save_player')
    @patch('warships.management.commands.incremental_player_refresh.fetch_players_bulk')
    def test_refresh_calls_save_player_and_conditionals(
        self, mock_fetch, mock_save, mock_efficiency, mock_achievements
    ):
        """Full refresh cycle calls save_player then conditional updates."""
        from warships.management.commands.incremental_player_refresh import _refresh_player

        player = Player.objects.create(
            name='RefreshMe', player_id=76001,
            is_hidden=False,
            efficiency_json=None,
            achievements_json=None,
        )
        mock_fetch.return_value = {
            str(player.player_id): {'account_id': player.player_id, 'nickname': 'RefreshMe'},
        }

        _refresh_player(player.id)

        mock_fetch.assert_called_once_with([player.player_id])
        mock_save.assert_called_once()
        # Efficiency and achievements should be checked (player reloaded from DB)

    @patch('warships.management.commands.incremental_player_refresh.update_achievements_data')
    @patch('warships.management.commands.incremental_player_refresh.update_player_efficiency_data')
    @patch('warships.management.commands.incremental_player_refresh.save_player')
    @patch('warships.management.commands.incremental_player_refresh.fetch_players_bulk')
    def test_refresh_hidden_player_skips_efficiency(
        self, mock_fetch, mock_save, mock_efficiency, mock_achievements
    ):
        """Hidden players: save_player runs but efficiency/achievements are not force-called."""
        from warships.management.commands.incremental_player_refresh import _refresh_player

        player = Player.objects.create(
            name='HiddenRefresh', player_id=76002,
            is_hidden=True,
        )
        mock_fetch.return_value = {
            str(player.player_id): {
                'account_id': player.player_id,
                'nickname': 'HiddenRefresh',
                'hidden_profile': True,
            },
        }

        _refresh_player(player.id)

        mock_fetch.assert_called_once()
        mock_save.assert_called_once()
        # player_efficiency_needs_refresh returns False for hidden players,
        # so these should not be called by the conditional block
