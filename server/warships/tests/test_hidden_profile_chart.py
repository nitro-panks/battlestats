"""Hidden-profile edge case: a WG-hidden account must not spin the Profile
chart forever, and ``update_battle_data`` must flip ``Player.is_hidden`` on a
reliable WG hidden signal (``meta.hidden``) — but never on a transient
empty/error response.

Root cause (diagnosed 2026-07-11, player ``castorice_my_beloved`` / na): when a
player hides their WoWS profile, WG ``ships/stats`` returns no ship data, so
``update_battle_data`` overwrote ``battles_json = []``. The tier-type
correlation builder + view keyed "still warming" on ``not battles_json``, which
cannot tell ``battles_json is None`` (never fetched → legitimately warming) from
``== []`` (fetched, came back empty → will never populate). Result: the Profile
chart polled "warming" forever and each poll re-dispatched a refresh that could
never succeed.

Two levers, both covered here:
  1. Discriminator — pending is gated on ``battles_json is None`` only; an empty
     ``[]`` is a terminal (no-pending, no re-dispatch) state.
  2. ``is_hidden`` flip — ``update_battle_data`` reads WG ``meta.hidden`` and
     flips the whole profile to hidden, so the page reflects reality instead of
     one chart silently spinning. A transient failure reports not-hidden, so a
     visible player is never hidden on a WG blip.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from warships.data import update_battle_data
from warships.models import Player


HIDDEN_META_RESPONSE = {"data": {}, "meta": {"count": 0, "hidden": [8901]}}
VISIBLE_RESPONSE = {
    "data": {"8901": [{"ship_id": 1, "pvp": {"battles": 10, "wins": 6}}]},
    "meta": {"count": 1, "hidden": []},
}

# A ready population heatmap. The discriminator lives in
# ``fetch_player_tier_type_correlation`` *after* the population payload is
# resolved, so the tests patch the population helper to a warm payload and
# focus purely on the None-vs-[] branch (sidestepping the Postgres-only
# jsonb aggregation SQL, which has no sqlite equivalent).
_POPULATION_PAYLOAD = {
    "metric": "tier_type",
    "label": "Tier vs Ship Type",
    "x_label": "Ship Type",
    "y_label": "Tier",
    "tracked_population": 5,
    "x_labels": ["Destroyer", "Cruiser", "Battleship",
                 "Aircraft Carrier", "Submarine"],
    "y_values": [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    "tiles": [{"x_index": 0, "y_index": 1, "count": 55}],
    "trend": [{"x_index": 0, "avg_tier": 9.5, "count": 55}],
}


class FetchShipStatsWithHiddenTests(TestCase):
    """The reliable hidden signal comes from WG's response ``meta.hidden``."""

    def test_reports_hidden_when_meta_lists_account(self):
        from warships.api.ships import _fetch_ship_stats_for_player_with_hidden
        with patch("warships.api.ships.make_api_request_with_meta",
                   return_value=HIDDEN_META_RESPONSE):
            data, is_hidden = _fetch_ship_stats_for_player_with_hidden(
                "8901", realm="na")
        self.assertTrue(is_hidden)
        self.assertFalse(data)

    def test_transient_failure_returns_none_data_and_not_hidden(self):
        # data is None (not {}) on a transient/transport failure, so callers can
        # tell it apart from a fetched-but-empty ({}, hidden or no ships) account.
        from warships.api.ships import _fetch_ship_stats_for_player_with_hidden
        with patch("warships.api.ships.make_api_request_with_meta",
                   return_value=None):
            data, is_hidden = _fetch_ship_stats_for_player_with_hidden(
                "8901", realm="na")
        self.assertFalse(is_hidden)
        self.assertIsNone(data)

    def test_visible_account_returns_ships_and_not_hidden(self):
        from warships.api.ships import _fetch_ship_stats_for_player_with_hidden
        with patch("warships.api.ships.make_api_request_with_meta",
                   return_value=VISIBLE_RESPONSE):
            data, is_hidden = _fetch_ship_stats_for_player_with_hidden(
                "8901", realm="na")
        self.assertFalse(is_hidden)
        self.assertTrue(data)


class UpdateBattleDataHiddenFlipTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            name="HiderNA", player_id=8901, realm="na", is_hidden=False,
            battles_json=[{"ship_name": "Old", "ship_type": "Cruiser",
                           "ship_tier": 10, "pvp_battles": 5, "wins": 3}],
        )

    def test_flips_is_hidden_when_wg_reports_hidden(self):
        with patch("warships.data._fetch_ship_stats_for_player_with_hidden",
                   return_value=({}, True)):
            update_battle_data("8901", realm="na")
        self.player.refresh_from_db()
        self.assertTrue(self.player.is_hidden)
        self.assertEqual(self.player.battles_json, [])

    def test_definitive_empty_records_empty_without_flipping_hidden(self):
        # ({}, False) = a fetched, visible account with no ships. Terminal empty,
        # but NOT hidden.
        with patch("warships.data._fetch_ship_stats_for_player_with_hidden",
                   return_value=({}, False)):
            update_battle_data("8901", realm="na")
        self.player.refresh_from_db()
        self.assertFalse(self.player.is_hidden)
        self.assertEqual(self.player.battles_json, [])

    def test_transient_failure_leaves_battles_json_unchanged(self):
        # (None, False) = a transient/transport failure. Must NOT clobber the
        # stored battles_json to [] (that would drop the chart until some other
        # trigger repopulates) and must NOT flip is_hidden — the player stays
        # eligible for retry by the floor / next view.
        original = list(self.player.battles_json)
        with patch("warships.data._fetch_ship_stats_for_player_with_hidden",
                   return_value=(None, False)):
            update_battle_data("8901", realm="na")
        self.player.refresh_from_db()
        self.assertFalse(self.player.is_hidden)
        self.assertEqual(self.player.battles_json, original)


class TierTypePendingDiscriminatorTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("warships.data._fetch_player_tier_type_population_correlation",
           return_value=dict(_POPULATION_PAYLOAD))
    @patch("warships.data.update_battle_data_task.delay")
    def test_never_fetched_battles_json_none_is_pending(self, mock_task, _pop):
        cache.clear()
        Player.objects.create(
            name="ColdNA", player_id=8811, realm="na",
            is_hidden=False, pvp_battles=1400, battles_json=None)

        resp = self.client.get("/api/fetch/player_correlation/tier_type/8811/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Tier-Type-Pending"], "true")
        self.assertEqual(resp.json()["player_cells"], [])
        mock_task.assert_called_once_with(player_id="8811", realm="na")

    @patch("warships.data._fetch_player_tier_type_population_correlation",
           return_value=dict(_POPULATION_PAYLOAD))
    @patch("warships.data.update_battle_data_task.delay")
    def test_empty_battles_json_is_terminal_not_pending(self, mock_task, _pop):
        cache.clear()
        Player.objects.create(
            name="HiddenNA", player_id=8813, realm="na",
            is_hidden=False, pvp_battles=1400, battles_json=[])

        resp = self.client.get("/api/fetch/player_correlation/tier_type/8813/")

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("X-Tier-Type-Pending", resp)
        self.assertEqual(resp.json()["player_cells"], [])
        mock_task.assert_not_called()
