"""The best-* prewarm gate on the 12h bulk-entity-loader.

`score_best_clans()` (the #1 DB-time sink) and the Best-player landing payloads
ranked the landing Best boards decommissioned 2026-06-22. When
``BULK_CACHE_BEST_PREWARM_ENABLED`` is off the loader must skip those cohorts
(so `score_best_clans` has no live caller) while still warming the cheap,
view-relevant pinned + recently-viewed cohorts.
"""
from unittest import mock

from django.test import TestCase

from warships import data


class BulkCacheBestPrewarmGateTests(TestCase):

    @mock.patch("warships.data.get_recently_viewed_player_ids", return_value=[])
    @mock.patch("warships.data._get_pinned_player_ids", return_value=[])
    @mock.patch("warships.landing.get_landing_players_payload")
    @mock.patch("warships.data.score_best_clans")
    def test_gate_off_skips_best_cohorts(
        self, score_mock, landing_mock, pinned_mock, rv_mock,
    ):
        with mock.patch.object(data, "BULK_CACHE_BEST_PREWARM_ENABLED", False):
            player_result = data.bulk_load_player_cache(realm="na")
            clan_result = data.bulk_load_clan_cache(realm="na")

        # The two heavy rankers never ran.
        score_mock.assert_not_called()
        landing_mock.assert_not_called()
        # Clan prewarm short-circuits; pinned/recently-viewed still consulted.
        self.assertEqual(clan_result["status"], "skipped")
        self.assertEqual(clan_result["reason"], "best-prewarm-disabled")
        self.assertEqual(player_result["status"], "completed")
        pinned_mock.assert_called_once()
        rv_mock.assert_called_once()

    @mock.patch("warships.data.get_recently_viewed_player_ids", return_value=[])
    @mock.patch("warships.data._get_pinned_player_ids", return_value=[])
    @mock.patch("warships.landing.get_landing_players_payload", return_value=[])
    @mock.patch("warships.data.score_best_clans", return_value=([], {}))
    def test_gate_on_runs_score_best_clans(
        self, score_mock, landing_mock, pinned_mock, rv_mock,
    ):
        with mock.patch.object(data, "BULK_CACHE_BEST_PREWARM_ENABLED", True):
            data.bulk_load_player_cache(realm="na")
            clan_result = data.bulk_load_clan_cache(realm="na")

        # Default behaviour preserved: the rankers are consulted.
        self.assertTrue(score_mock.called)
        self.assertTrue(landing_mock.called)
        self.assertEqual(clan_result["status"], "completed")
