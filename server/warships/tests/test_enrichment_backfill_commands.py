"""Tests for the enrichment backfill tooling:

* ``retry_empty_enrichments`` — re-queues ``empty`` false-negatives.
* ``enrichment_lift_report`` — quantifies the ranking lift afterwards.

See agents/work-items/player-enrichment-map-2026-06-08.md §12.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player, PlayerExplorerSummary


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
