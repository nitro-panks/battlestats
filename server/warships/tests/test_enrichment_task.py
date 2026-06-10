"""Unit coverage for enrich_player_data_task's lock/defer control flow.

Regression guard for the 2026-05-27 deferral fan-out: while a clan crawl is
active the task must NOT re-enqueue itself (the every-15-min Beat kickstart is
the retry), and the lock must be acquired BEFORE the crawl check so duplicate
dispatches dedup instead of each spawning a self-recurring chain. The defer
path must also never run the heavy `_maybe_redispatch_enrichment` candidate
scan. See agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md.
"""
import os
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from warships.models import Player
from warships.tasks import (
    ENRICH_PLAYER_DATA_LOCK_TIMEOUT,
    _clan_crawl_lock_key,
    _enrich_player_data_lock_key,
    _maybe_enrich_on_view,
    enrich_player_data_task,
    enrich_player_on_view_task,
)


class EnrichPlayerDataTaskControlFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch.dict(os.environ, {"ENRICH_DEFER_DURING_CRAWL": "1"})
    def test_defer_when_crawl_active_and_kill_switch_set(self):
        # Kill switch ENRICH_DEFER_DURING_CRAWL=1 restores the old behavior:
        # defer entirely while a crawl is active. Regression guard for the
        # 2026-05-27 fan-out — the defer path must release the lock, NOT
        # re-enqueue, and NOT run the heavy candidate scan or touch WG.
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

    def test_coexists_with_crawl_by_default(self):
        # Default (kill switch unset): enrichment runs ALONGSIDE an active crawl
        # instead of deferring — the fix for backlog starvation through multi-day
        # crawls. It runs a batch (at the gentler crawl delay) and self-chains.
        cache.set(_clan_crawl_lock_key('na'), '1', 300)
        summary = {'enriched': 5, 'remaining': 100}

        with patch('warships.management.commands.enrich_player_data.enrich_players', return_value=summary) as mock_enrich, \
                patch('warships.tasks._maybe_redispatch_enrichment') as mock_redispatch:
            result = enrich_player_data_task.apply().get()

        self.assertEqual(result, summary)
        # Ran a real batch despite the active crawl…
        mock_enrich.assert_called_once()
        # …at the gentler during-crawl delay (default 0.5s, not the 0.2s baseline).
        self.assertAlmostEqual(mock_enrich.call_args.kwargs['delay'], 0.5)
        # …and self-chained so the drain continues through the crawl.
        mock_redispatch.assert_called_once()
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


class EnrichPlayerOnViewTaskTests(TestCase):
    """The on-view fast-path: enrich a just-viewed, eligible, un-enriched player
    immediately instead of waiting for the daily drift reclassify. Self-guards
    on the same gate as the crawler so a tight ENRICH_MAX_INACTIVE_DAYS window
    carries a low penalty (returning players re-enroll the moment they're seen).
    """

    def setUp(self):
        cache.clear()

    def _mk(self, **kw):
        defaults = dict(
            realm="na", player_id=9001, name="Viewer", is_hidden=False,
            pvp_battles=600, pvp_ratio=50.0, days_since_last_battle=2,
            battles_json=None, enrichment_status=Player.ENRICHMENT_SKIPPED_INACTIVE,
        )
        defaults.update(kw)
        return Player.objects.create(**defaults)

    @patch.dict(os.environ, {"ENRICH_ON_VIEW_ENABLED": "1", "ENRICH_MAX_INACTIVE_DAYS": "7"})
    @patch("warships.management.commands.enrich_player_data._enrich_player_parallel")
    def test_enriches_eligible_unenriched_player(self, mock_enrich):
        self._mk()
        result = enrich_player_on_view_task.apply((9001, "na")).get()
        self.assertEqual(result["status"], "enriched")
        mock_enrich.assert_called_once_with(9001, "na")

    @patch.dict(os.environ, {"ENRICH_ON_VIEW_ENABLED": "1", "ENRICH_MAX_INACTIVE_DAYS": "7"})
    @patch("warships.management.commands.enrich_player_data._enrich_player_parallel")
    def test_skips_already_enriched(self, mock_enrich):
        # battles_json non-null (real data OR empty []) is terminal — never re-enrich.
        self._mk(battles_json=[{"ship_id": 1}], enrichment_status=Player.ENRICHMENT_ENRICHED)
        result = enrich_player_on_view_task.apply((9001, "na")).get()
        self.assertEqual(result["reason"], "already-enriched")
        mock_enrich.assert_not_called()

    @patch.dict(os.environ, {"ENRICH_ON_VIEW_ENABLED": "1", "ENRICH_MAX_INACTIVE_DAYS": "7"})
    @patch("warships.management.commands.enrich_player_data._enrich_player_parallel")
    def test_skips_inactive_beyond_window(self, mock_enrich):
        self._mk(days_since_last_battle=30)  # > 7d window
        result = enrich_player_on_view_task.apply((9001, "na")).get()
        self.assertEqual(result["reason"], "ineligible")
        mock_enrich.assert_not_called()

    @patch.dict(os.environ, {"ENRICH_ON_VIEW_ENABLED": "0"})
    @patch("warships.management.commands.enrich_player_data._enrich_player_parallel")
    def test_kill_switch_disables(self, mock_enrich):
        self._mk()
        result = enrich_player_on_view_task.apply((9001, "na")).get()
        self.assertEqual(result["reason"], "disabled")
        mock_enrich.assert_not_called()

    @patch.dict(os.environ, {"ENRICH_ON_VIEW_ENABLED": "1"})
    @patch("warships.tasks.enrich_player_on_view_task.apply_async")
    def test_hook_enqueues_once_then_debounces(self, mock_apply):
        player = self._mk()
        _maybe_enrich_on_view(player, "na")
        _maybe_enrich_on_view(player, "na")  # within cooldown → no second enqueue
        self.assertEqual(mock_apply.call_count, 1)

    @patch.dict(os.environ, {"ENRICH_ON_VIEW_ENABLED": "1"})
    @patch("warships.tasks.enrich_player_on_view_task.apply_async")
    def test_hook_skips_already_enriched(self, mock_apply):
        player = self._mk(battles_json=[{"ship_id": 1}])
        _maybe_enrich_on_view(player, "na")
        mock_apply.assert_not_called()
