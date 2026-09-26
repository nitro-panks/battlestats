"""Plan pin for the ship top-player snapshot aggregation.

The production DB runs `random_page_cost = 1`, so the planner prices a random
`warships_player` pkey probe like a sequential page and chooses a Nested Loop
into Player for the (ship, player) aggregate. Measured on prod 2026-09-26
(EXPLAIN ANALYZE, eu): 322s, ~275s of it 369,350 random probes at 0.68 ms.
With `SET LOCAL enable_nestloop = off` the same query took a Hash Join over a
Player seq scan: 65.5s, identical 187,730 rows. These tests pin the helper and
its use by the snapshot. See runbook-ship-leaderboard-architecture-2026-06-18.md.
"""

import contextlib
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from warships.data import _prefer_hash_join, compute_ship_top_player_snapshot
from warships.models import Ship


class PreferHashJoinHelperTests(TestCase):
    def test_postgres_disables_nested_loops_for_the_transaction(self):
        fake = MagicMock()
        fake.vendor = "postgresql"
        cursor = fake.cursor.return_value.__enter__.return_value
        with patch("warships.data.connection", fake):
            with _prefer_hash_join():
                pass
        cursor.execute.assert_called_once_with("SET LOCAL enable_nestloop = off")

    def test_noop_off_postgres(self):
        fake = MagicMock()
        fake.vendor = "sqlite"
        with patch("warships.data.connection", fake):
            with _prefer_hash_join():
                pass
        fake.cursor.assert_not_called()


class SnapshotUsesHashJoinTests(TestCase):
    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=4001, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)

    @patch.dict("os.environ", {"SHIP_BADGE_TIERS": "10"})
    def test_snapshot_aggregate_runs_under_the_hash_join_pin(self):
        entered = []

        @contextlib.contextmanager
        def spy():
            entered.append(True)
            yield

        with patch("warships.data._prefer_hash_join", spy):
            compute_ship_top_player_snapshot(realm="na")
        self.assertEqual(entered, [True])
