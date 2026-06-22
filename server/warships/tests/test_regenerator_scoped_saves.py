"""Bare-save audit (runbook-player-refresh-pill-clobber-2026-06-21, follow-up #2).

The chart regenerators (update_tiers_data / update_type_data / update_randoms_data
/ update_activity_data) each load the full Player, recompute their own 2 columns
from battles_json, then historically did a bare player.save() — writing back the
whole stale snapshot and reverting any field a concurrent task (update_battle_data,
update_ranked_data, update_player_data) wrote between the load and the save. They
run as unrouted Celery tasks (default queue) + the request hydration path, so the
concurrency is real. The fix scopes each save to update_fields=[its 2 columns].
"""

from unittest import mock

from django.test import TestCase

from warships.data import (
    update_activity_data, update_randoms_data, update_tiers_data, update_type_data,
)
from warships.models import Player

PID = 1054131305
ORIG_RANKED = [{"season_id": 1}]
NEW_RANKED = [{"season_id": 99, "battles": 7}]


class RegeneratorScopedSaveTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            name="P", player_id=PID, realm="na",
            battles_json=[{"ship_tier": 10, "ship_type": "Cruiser",
                           "pvp_battles": 5, "wins": 3}],
            ranked_json=ORIG_RANKED,
        )

    def test_type_data_does_not_revert_concurrent_ranked_write(self):
        # The concurrent update_ranked_data (ranked_json=NEW) lands DURING this
        # regenerator's compute step (after it loaded the stale snapshot).
        def _agg(*_a, **_k):
            Player.objects.filter(player_id=PID, realm="na").update(ranked_json=NEW_RANKED)
            return [{"ship_type": "Cruiser", "pvp_battles": 5, "wins": 3}]

        with mock.patch("warships.data._aggregate_battles_by_key", side_effect=_agg):
            update_type_data(PID, realm="na")

        row = Player.objects.get(player_id=PID, realm="na")
        self.assertEqual(row.ranked_json, NEW_RANKED)          # concurrent write survived
        self.assertEqual(row.type_json[0]["ship_type"], "Cruiser")  # own field persisted

    def test_all_regenerators_use_scoped_update_fields(self):
        expected = {
            update_tiers_data: ["tiers_json", "tiers_updated_at"],
            update_type_data: ["type_json", "type_updated_at"],
            update_randoms_data: ["randoms_json", "randoms_updated_at"],
            update_activity_data: ["activity_json", "activity_updated_at"],
        }
        for fn, fields in expected.items():
            with mock.patch.object(Player, "save") as save_mock:
                fn(PID, realm="na")
                self.assertTrue(save_mock.called, f"{fn.__name__} did not save")
                kwargs = save_mock.call_args.kwargs
                self.assertEqual(
                    kwargs.get("update_fields"), fields,
                    f"{fn.__name__} must scope its save to {fields} (bare save clobbers)",
                )
