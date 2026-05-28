"""Unit coverage for enrich_player_data_task's lock/defer control flow.

Regression guard for the 2026-05-27 deferral fan-out: while a clan crawl is
active the task must NOT re-enqueue itself (the every-15-min Beat kickstart is
the retry), and the lock must be acquired BEFORE the crawl check so duplicate
dispatches dedup instead of each spawning a self-recurring chain. The defer
path must also never run the heavy `_maybe_redispatch_enrichment` candidate
scan. See agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from warships.tasks import (
    ENRICH_PLAYER_DATA_LOCK_TIMEOUT,
    _clan_crawl_lock_key,
    _enrich_player_data_lock_key,
    enrich_player_data_task,
)


class EnrichPlayerDataTaskControlFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_defer_when_crawl_active_releases_lock_and_does_not_reenqueue(self):
        cache.set(_clan_crawl_lock_key('na'), '1', 300)

        with patch('warships.management.commands.enrich_player_data.enrich_players') as mock_enrich, \
                patch('warships.tasks._maybe_redispatch_enrichment') as mock_redispatch, \
                patch('warships.tasks.enrich_player_data_task.apply_async') as mock_apply_async:
            result = enrich_player_data_task.apply().get()

        self.assertEqual(result['status'], 'deferred')
        self.assertEqual(result['reason'], 'crawl-running')
        self.assertIn('na', result['active_crawls'])
        # No fan-out: deferral must not re-enqueue itself…
        mock_apply_async.assert_not_called()
        # …and must not run the heavy candidate scan…
        mock_redispatch.assert_not_called()
        # …nor touch the WG API.
        mock_enrich.assert_not_called()
        # Lock released so the next kickstart can acquire it cleanly.
        self.assertIsNone(cache.get(_enrich_player_data_lock_key()))

    def test_skips_when_lock_already_held(self):
        # Another enrichment (or a near-simultaneous duplicate dispatch) holds
        # the lock — even with no crawl active, we bail without doing work.
        cache.add(_enrich_player_data_lock_key(), 'other-task-id',
                  ENRICH_PLAYER_DATA_LOCK_TIMEOUT)

        with patch('warships.management.commands.enrich_player_data.enrich_players') as mock_enrich, \
                patch('warships.tasks._maybe_redispatch_enrichment') as mock_redispatch, \
                patch('warships.tasks.enrich_player_data_task.apply_async') as mock_apply_async:
            result = enrich_player_data_task.apply().get()

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'already-running')
        mock_enrich.assert_not_called()
        mock_redispatch.assert_not_called()
        mock_apply_async.assert_not_called()
        # We did not own the lock, so we must not have deleted the holder's.
        self.assertEqual(cache.get(_enrich_player_data_lock_key()), 'other-task-id')

    def test_runs_batch_and_redispatches_when_no_crawl(self):
        summary = {'enriched': 3, 'remaining': 0}

        with patch('warships.management.commands.enrich_player_data.enrich_players', return_value=summary) as mock_enrich, \
                patch('warships.tasks._maybe_redispatch_enrichment') as mock_redispatch:
            result = enrich_player_data_task.apply().get()

        self.assertEqual(result, summary)
        mock_enrich.assert_called_once()
        # A real batch DOES redispatch (the self-chain) exactly once.
        mock_redispatch.assert_called_once()
        # Lock released after the batch.
        self.assertIsNone(cache.get(_enrich_player_data_lock_key()))
