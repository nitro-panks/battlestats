"""Tests for the enrichment backfill tooling:

* ``retry_empty_enrichments`` — re-queues ``empty`` false-negatives.
* ``enrichment_lift_report`` — quantifies the ranking lift afterwards.

See agents/work-items/player-enrichment-map-2026-06-08.md §12.
"""
import os
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player, PlayerExplorerSummary
from warships.tasks import enrichment_pool_maintenance_task


class RetryEmptyEnrichmentsCommandTests(TestCase):
    def _mk(self, **kw):
        defaults = dict(
            realm="na", is_hidden=False, pvp_battles=3000,
            pvp_ratio=60.0, days_since_last_battle=1,
            enrichment_status=Player.ENRICHMENT_EMPTY, battles_json=[],
        )
        defaults.update(kw)
        return Player.objects.create(**defaults)

    def test_apply_requeues_only_eligible_empties(self):
        elig = self._mk(name="Elig", player_id=4001)
        hidden = self._mk(name="Hidden", player_id=4002, is_hidden=True)
        low_wr = self._mk(name="LowWR", player_id=4003, pvp_ratio=40.0)
        low_bat = self._mk(name="LowBat", player_id=4004, pvp_battles=100)
        enriched = self._mk(
            name="Enriched", player_id=4005,
            enrichment_status=Player.ENRICHMENT_ENRICHED,
            battles_json=[{"ship_tier": 10, "pvp_battles": 10, "wins": 6}],
        )

        call_command("retry_empty_enrichments", "--apply", stdout=StringIO())

        elig.refresh_from_db()
        self.assertEqual(elig.enrichment_status, Player.ENRICHMENT_PENDING)
        self.assertIsNone(elig.battles_json)
        for p in (hidden, low_wr, low_bat):
            p.refresh_from_db()
            self.assertEqual(p.enrichment_status, Player.ENRICHMENT_EMPTY)
            self.assertEqual(p.battles_json, [])
        enriched.refresh_from_db()
        self.assertEqual(enriched.enrichment_status, Player.ENRICHMENT_ENRICHED)

    def test_dry_run_is_the_default_and_writes_nothing(self):
        elig = self._mk(name="Elig", player_id=4101)
        out = StringIO()
        call_command("retry_empty_enrichments", stdout=out)  # no --apply
        elig.refresh_from_db()
        self.assertEqual(elig.enrichment_status, Player.ENRICHMENT_EMPTY)
        self.assertEqual(elig.battles_json, [])
        self.assertIn("DRY RUN", out.getvalue())

    def test_dry_run_flag_overrides_apply(self):
        elig = self._mk(name="Elig", player_id=4111)
        call_command("retry_empty_enrichments", "--apply", "--dry-run", stdout=StringIO())
        elig.refresh_from_db()
        self.assertEqual(elig.enrichment_status, Player.ENRICHMENT_EMPTY)

    def test_min_wr_zero_includes_low_wr_band(self):
        low_wr = self._mk(name="LowWR", player_id=4201, pvp_ratio=40.0)
        call_command("retry_empty_enrichments", "--apply", "--min-wr", "0", stdout=StringIO())
        low_wr.refresh_from_db()
        self.assertEqual(low_wr.enrichment_status, Player.ENRICHMENT_PENDING)
        self.assertIsNone(low_wr.battles_json)

    def test_retry_after_days_skips_recently_attempted_empties(self):
        # Convergence guard: a row re-emptied recently (battles_updated_at near
        # now) must NOT be re-queued under a cooldown, so genuinely-empty rows
        # aren't re-fetched every run. An older / never-attempted one is.
        fresh = self._mk(name="Fresh", player_id=4301,
                         battles_updated_at=timezone.now() - timedelta(days=2))
        stale = self._mk(name="Stale", player_id=4302,
                         battles_updated_at=timezone.now() - timedelta(days=30))
        never = self._mk(name="Never", player_id=4303, battles_updated_at=None)

        call_command("retry_empty_enrichments", "--apply",
                     "--retry-after-days", "14", stdout=StringIO())

        fresh.refresh_from_db()
        self.assertEqual(fresh.enrichment_status, Player.ENRICHMENT_EMPTY)
        for p in (stale, never):
            p.refresh_from_db()
            self.assertEqual(p.enrichment_status, Player.ENRICHMENT_PENDING)
            self.assertIsNone(p.battles_json)

    def test_retry_after_days_zero_is_no_cooldown(self):
        # Default behavior (one-shot): cooldown disabled re-queues even a
        # just-attempted empty.
        fresh = self._mk(name="Fresh", player_id=4401,
                         battles_updated_at=timezone.now())
        call_command("retry_empty_enrichments", "--apply",
                     "--retry-after-days", "0", stdout=StringIO())
        fresh.refresh_from_db()
        self.assertEqual(fresh.enrichment_status, Player.ENRICHMENT_PENDING)


class ReclassifyRecentHoursTests(TestCase):
    """Incremental reclassify: --recent-hours scopes the pass to recently-fetched
    rows so the daily drift rescue doesn't scan the full catalog."""

    def _mk(self, **kw):
        # A skipped_hidden row that is now visible+eligible -> should reclassify
        # to pending, but only if it falls inside the recency window.
        defaults = dict(
            realm="na", is_hidden=False, pvp_battles=3000, pvp_ratio=60.0,
            days_since_last_battle=1, battles_json=None,
            enrichment_status=Player.ENRICHMENT_SKIPPED_HIDDEN,
        )
        defaults.update(kw)
        return Player.objects.create(**defaults)

    def test_recent_hours_only_reclassifies_recently_fetched(self):
        recent = self._mk(name="Recent", player_id=7001,
                         last_fetch=timezone.now() - timedelta(hours=2))
        old = self._mk(name="Old", player_id=7002,
                      last_fetch=timezone.now() - timedelta(hours=72))
        never = self._mk(name="Never", player_id=7003, last_fetch=None)

        call_command("reclassify_enrichment_status", "--recent-hours", "25",
                     stdout=StringIO())

        recent.refresh_from_db()
        self.assertEqual(recent.enrichment_status, Player.ENRICHMENT_PENDING)
        # Outside the window (or never fetched) -> untouched by the incremental pass
        for p in (old, never):
            p.refresh_from_db()
            self.assertEqual(p.enrichment_status, Player.ENRICHMENT_SKIPPED_HIDDEN)

    def test_full_catalog_default_reclassifies_all(self):
        old = self._mk(name="Old", player_id=7101,
                      last_fetch=timezone.now() - timedelta(hours=72))
        call_command("reclassify_enrichment_status", stdout=StringIO())  # no --recent-hours
        old.refresh_from_db()
        self.assertEqual(old.enrichment_status, Player.ENRICHMENT_PENDING)


class EnrichmentLiftReportCommandTests(TestCase):
    def _mk(self, **kw):
        defaults = dict(
            realm="na", is_hidden=False, pvp_battles=3000,
            pvp_ratio=60.0, days_since_last_battle=1,
            enrichment_status=Player.ENRICHMENT_ENRICHED,
            battles_updated_at=timezone.now(),
        )
        defaults.update(kw)
        return Player.objects.create(**defaults)

    def test_quantifies_board_eligibility_and_bar_clearers(self):
        # 80% high-tier WR, 3000 high-tier battles -> board eligible + clears 73% bar
        self._mk(name="Top", player_id=5001,
                 battles_json=[{"ship_tier": 10, "pvp_battles": 3000, "wins": 2400}])
        # 55% high-tier WR -> rankable + board eligible, does NOT clear bar
        self._mk(name="Mid", player_id=5002,
                 battles_json=[{"ship_tier": 10, "pvp_battles": 3000, "wins": 1650}])
        # enriched but stale battles_updated_at -> outside the cohort window
        self._mk(name="Old", player_id=5003,
                 battles_updated_at=timezone.now() - timedelta(days=3),
                 battles_json=[{"ship_tier": 10, "pvp_battles": 3000, "wins": 2400}])

        out = StringIO()
        since = (timezone.now() - timedelta(hours=1)).isoformat()
        call_command("enrichment_lift_report", "--since", since, stdout=out)
        text = out.getvalue()

        self.assertIn("Recovered profiles (now complete): 2", text)        # Top + Mid, not Old
        self.assertIn("board-eligible (> 2500 battles + ht>= 50 + active<= 180d): 2", text)
        self.assertIn("top-25 bar (high-tier WR >= 73.0%): 1", text)        # only Top

    def test_low_tier_player_is_not_high_tier_rankable(self):
        # all battles at T3 -> 0 high-tier battles -> not rankable
        self._mk(name="LowTier", player_id=5101,
                 battles_json=[{"ship_tier": 3, "pvp_battles": 4000, "wins": 3000}])
        out = StringIO()
        since = (timezone.now() - timedelta(hours=1)).isoformat()
        call_command("enrichment_lift_report", "--since", since, stdout=out)
        text = out.getvalue()
        self.assertIn("Recovered profiles (now complete): 1", text)
        self.assertIn("High-tier rankable (>= 50 T5-10 battles): 0", text)

    def test_efficiency_percentile_coverage_is_counted(self):
        top = self._mk(name="Eff", player_id=5201,
                       battles_json=[{"ship_tier": 10, "pvp_battles": 3000, "wins": 2400}])
        PlayerExplorerSummary.objects.create(
            player=top, realm="na", efficiency_rank_percentile=99.5)
        out = StringIO()
        since = (timezone.now() - timedelta(hours=1)).isoformat()
        call_command("enrichment_lift_report", "--since", since, stdout=out)
        self.assertIn("Efficiency ranking: 1 now carry a percentile", out.getvalue())


class EnrichmentPoolMaintenanceTaskTests(TestCase):
    """The daily DB-only maintenance task: cooldown-guarded re-queue of empty
    false-negatives, feeding the crawler's pending pool. (Full-catalog reclassify
    is intentionally NOT run here — it is a supervised manual op; see the runbook.)"""

    def _mk(self, **kw):
        defaults = dict(
            realm="na", is_hidden=False, pvp_battles=3000,
            pvp_ratio=60.0, days_since_last_battle=1, battles_json=None,
        )
        defaults.update(kw)
        return Player.objects.create(**defaults)

    @mock.patch.dict(os.environ, {"ENRICHMENT_EMPTY_RETRY_AFTER_DAYS": "14"})
    def test_maintenance_requeues_stale_empties_with_cooldown(self):
        # empty false-negative, last attempt well past the cooldown -> re-queued
        stale_empty = self._mk(name="StaleEmpty", player_id=6002,
                               enrichment_status=Player.ENRICHMENT_EMPTY,
                               battles_json=[],
                               battles_updated_at=timezone.now() - timedelta(days=30))
        # empty re-attempted recently: cooldown must protect it from re-queue
        fresh_empty = self._mk(name="FreshEmpty", player_id=6003,
                              enrichment_status=Player.ENRICHMENT_EMPTY,
                              battles_json=[],
                              battles_updated_at=timezone.now() - timedelta(days=2))

        result = enrichment_pool_maintenance_task()
        self.assertEqual(result["status"], "ok")

        stale_empty.refresh_from_db()
        self.assertEqual(stale_empty.enrichment_status, Player.ENRICHMENT_PENDING)
        self.assertIsNone(stale_empty.battles_json)

        fresh_empty.refresh_from_db()
        self.assertEqual(fresh_empty.enrichment_status, Player.ENRICHMENT_EMPTY)
        self.assertEqual(fresh_empty.battles_json, [])

    @mock.patch.dict(os.environ, {"ENRICHMENT_RECLASSIFY_RECENT_HOURS": "25"})
    def test_maintenance_incrementally_reclassifies_recent_drift(self):
        # skipped_hidden but now visible+eligible, fetched recently -> rescued to
        # pending by the incremental reclassify pass.
        recent_drift = self._mk(name="RecentDrift", player_id=6201,
                               enrichment_status=Player.ENRICHMENT_SKIPPED_HIDDEN,
                               last_fetch=timezone.now() - timedelta(hours=2))
        # same drift but not fetched in the window -> incremental pass skips it
        # (it's left for a periodic full reclassify).
        old_drift = self._mk(name="OldDrift", player_id=6202,
                            enrichment_status=Player.ENRICHMENT_SKIPPED_HIDDEN,
                            last_fetch=timezone.now() - timedelta(hours=72))

        result = enrichment_pool_maintenance_task()
        self.assertEqual(result["status"], "ok")

        recent_drift.refresh_from_db()
        self.assertEqual(recent_drift.enrichment_status, Player.ENRICHMENT_PENDING)
        old_drift.refresh_from_db()
        self.assertEqual(old_drift.enrichment_status,
                         Player.ENRICHMENT_SKIPPED_HIDDEN)

    @mock.patch.dict(os.environ, {"ENRICHMENT_POOL_MAINTENANCE_ENABLED": "0"})
    def test_kill_switch_disables_task(self):
        empty = self._mk(name="Empty", player_id=6101,
                        enrichment_status=Player.ENRICHMENT_EMPTY,
                        battles_json=[],
                        battles_updated_at=timezone.now() - timedelta(days=30))
        result = enrichment_pool_maintenance_task()
        self.assertEqual(result, {"status": "skipped", "reason": "disabled"})
        empty.refresh_from_db()
        self.assertEqual(empty.enrichment_status, Player.ENRICHMENT_EMPTY)
