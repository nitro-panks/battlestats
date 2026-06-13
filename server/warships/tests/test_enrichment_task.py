"""Unit coverage for enrich_player_data_task's lock/defer control flow.

Regression guard for the 2026-05-27 deferral fan-out: while a clan crawl is
active the task must NOT re-enqueue itself (the every-15-min Beat kickstart is
the retry), and the lock must be acquired BEFORE the crawl check so duplicate
dispatches dedup instead of each spawning a self-recurring chain. The defer
path must also never run the heavy `_maybe_redispatch_enrichment` candidate
scan. See agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md.
"""
import os
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.management.commands.enrich_player_data import (
    EnrichOutcome,
    _candidates,
    _process_player_ship_data,
    enrich_players,
)
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
        mock_redispatch.assert_called_once_with(made_progress=True)
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
        # A real batch DOES redispatch (the self-chain) exactly once, and
        # signals progress so the chain continues.
        mock_redispatch.assert_called_once_with(made_progress=True)
        # Lock released after the batch.
        self.assertIsNone(cache.get(_enrich_player_data_lock_key()))

    def test_no_progress_batch_does_not_self_chain(self):
        # A pure-skip batch (every candidate skipped — nothing enriched, nothing
        # marked empty) signals made_progress=False so the self-chain stops
        # instead of spinning ~37s on candidates that can't be resolved (e.g.
        # private-at-fetch PENDING/battles_json IS NULL rows). The 15-min Beat
        # kickstart remains the retry. Regression guard for the 2026-06-13
        # enrichment self-chain spin (146 passes/90min, 142 enriched:0/skipped:33).
        summary = {'enriched': 0, 'empty': 0, 'skipped': 33, 'errors': 0}

        with patch('warships.management.commands.enrich_player_data.enrich_players', return_value=summary), \
                patch('warships.tasks._maybe_redispatch_enrichment') as mock_redispatch:
            result = enrich_player_data_task.apply().get()

        self.assertEqual(result, summary)
        mock_redispatch.assert_called_once_with(made_progress=False)
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


class EnrichmentSkipCooldownTests(TestCase):
    """Root-fix for the private-at-fetch self-chain spin: a PENDING /
    battles_json-NULL row whose WG ship stats come back null is stamped with
    ``enrichment_skipped_at`` and suppressed from ``_candidates()`` for
    ``ENRICH_SKIP_RETRY_AFTER_DAYS`` (default 3), so it stops being re-selected
    every pass while staying PENDING for a later retry. Transient failures must
    NOT be stamped (they keep retrying immediately). See
    runbook-floor-throughput-tuning-2026-06-13.md.
    """

    def setUp(self):
        cache.clear()

    def _mk_pending(self, player_id, **kw):
        defaults = dict(
            realm="na", player_id=player_id, name=f"P{player_id}",
            is_hidden=False, pvp_battles=1000, pvp_ratio=60.0,
            days_since_last_battle=1, battles_json=None,
            enrichment_status=Player.ENRICHMENT_PENDING,
        )
        defaults.update(kw)
        return Player.objects.create(**defaults)

    def test_candidates_suppresses_in_cooldown_keeps_others(self):
        # never-skipped → eligible; stamped just now → suppressed; stamped past
        # the cooldown → eligible again.
        self._mk_pending(1, enrichment_skipped_at=None)
        self._mk_pending(2, enrichment_skipped_at=timezone.now())
        self._mk_pending(3, enrichment_skipped_at=timezone.now() - timedelta(days=5))

        ids = {row[0] for row in _candidates("na", min_pvp_battles=500,
                                             min_wr=0.0, limit=100)}
        self.assertEqual(ids, {1, 3})

    def test_private_at_fetch_skip_stamps_and_returns_skipped(self):
        # ship_data_list is None == WG returned null ship stats (private profile):
        # the row stays PENDING but is stamped so it leaves the candidate set.
        player = self._mk_pending(10)
        rows, outcome = _process_player_ship_data(player, None)
        self.assertEqual(outcome, EnrichOutcome.SKIPPED)
        player.refresh_from_db()
        self.assertIsNotNone(player.enrichment_skipped_at)
        # Still PENDING — orthogonal to the reclassify state machine.
        self.assertEqual(player.enrichment_status, Player.ENRICHMENT_PENDING)

    def test_empty_outcome_does_not_stamp_skip(self):
        # [] is a genuine no-ships result → EMPTY (its own cooldown via
        # battles_updated_at); it must not borrow the skip cooldown.
        player = self._mk_pending(11)
        rows, outcome = _process_player_ship_data(player, [])
        self.assertEqual(outcome, EnrichOutcome.EMPTY)
        player.refresh_from_db()
        self.assertIsNone(player.enrichment_skipped_at)
        self.assertEqual(player.enrichment_status, Player.ENRICHMENT_EMPTY)

    @patch("warships.management.commands.enrich_player_data._prewarm_ship_cache",
           return_value=0)
    @patch("warships.management.commands.enrich_player_data._bulk_fetch_ranked_account_info",
           return_value=({}, None))
    @patch("warships.api.ships._per_player_ship_fallback")
    @patch("warships.api.ships._bulk_fetch_ship_stats")
    def test_transient_skip_does_not_stamp_cooldown(
            self, mock_bulk_ship, mock_fallback, *_):
        # A transient per-player failure surfaces as the "SKIP" sentinel and
        # `continue`s before reaching the stamp — so a 5xx/timeout keeps retrying
        # immediately instead of being parked for the 3-day cooldown.
        player = self._mk_pending(20)
        mock_bulk_ship.return_value = ({}, "INVALID_ACCOUNT_ID")  # → fallback
        mock_fallback.return_value = {"20": "SKIP"}               # transient sentinel

        summary = enrich_players(batch=10, realms=("na",))

        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["enriched"], 0)
        player.refresh_from_db()
        self.assertIsNone(player.enrichment_skipped_at)
        self.assertEqual(player.enrichment_status, Player.ENRICHMENT_PENDING)
