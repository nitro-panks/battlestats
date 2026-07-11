"""Warm-before-evict tests for the realm treemap + tier-type ship caches.

Both ``compute_realm_top_ships`` and ``compute_realm_ships_by_tier_type`` cache
under a window-end-tagged "fresh" key (26h TTL) that **rotates cold** when the
nightly snapshot advances the window date. To keep the previous numbers on
screen until the new ones are warm, each function also writes a window-date-
**independent** durable ``:published`` key (no expiry) and, on a cold fresh key,
serves that last-good payload + queues a warm instead of blocking on the heavy
``BattleEvent`` aggregation. These tests pin that contract.

See agents/runbooks/runbook-shipleaderboard-warm-before-evict-2026-06-18.md.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    _season_window_datetimes,
    compute_realm_ships_by_tier_type,
    compute_realm_top_ships,
    latest_ship_snapshot_window,
)
from warships.models import (
    BattleEvent, BattleObservation, Player, Ship, ShipTopPlayerSnapshot,
    realm_cache_key,
)


class ShipWarmBeforeEvictTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="wbe_bench", player_id=778001, realm="na",
            pvp_battles=1000, pvp_wins=520, pvp_losses=480, pvp_frags=900,
            pvp_survived_battles=600,
        )
        Ship.objects.create(
            ship_id=1, name="Yamato", nation="japan",
            ship_type="Battleship", tier=10, is_premium=False,
        )
        self.captured_on = timezone.now().date()
        ShipTopPlayerSnapshot.objects.create(
            captured_on=self.captured_on, realm="na", ship_id=1,
            ship_name="Yamato", rank=1, player=self.player,
            win_rate=50.0, battles=1)
        _, self.window_start_d, self.window_end_d = latest_ship_snapshot_window("na")
        self.window_start, self.window_end = _season_window_datetimes(
            self.window_start_d, self.window_end_d)
        # A couple of in-window battle events so the buckets are non-empty.
        self._event(1, "Yamato", 60, self.window_start + timedelta(days=1))

    def _event(self, ship_id, ship_name, battles, detected_at, mode="random",
               wins=0, frags=0):
        obs_a = BattleObservation.objects.create(player=self.player, pvp_battles=1)
        obs_b = BattleObservation.objects.create(player=self.player, pvp_battles=2)
        ev = BattleEvent.objects.create(
            player=self.player, ship_id=ship_id, ship_name=ship_name, mode=mode,
            battles_delta=battles, wins_delta=wins, losses_delta=0,
            frags_delta=frags, damage_delta=1000,
            from_observation=obs_a, to_observation=obs_b,
        )
        BattleEvent.objects.filter(pk=ev.pk).update(detected_at=detected_at)
        return ev

    # -- top-ships (treemap) ------------------------------------------------

    def _top_ships_fresh_key(self, mode="random", limit=25):
        return realm_cache_key(
            "na", f"top-ships:{mode}:win{self.window_end_d.isoformat()}:{limit}")

    def _top_ships_published_key(self, mode="random", limit=25):
        return realm_cache_key("na", f"top-ships:published:{mode}:{limit}")

    def test_top_ships_warm_writes_both_keys(self):
        """A warm (use_cache=False) populates the fresh AND published keys."""
        cache.clear()
        payload = compute_realm_top_ships("na", mode="random", use_cache=False)
        self.assertTrue(payload["ships"])
        self.assertEqual(cache.get(self._top_ships_fresh_key()), payload)
        self.assertEqual(cache.get(self._top_ships_published_key()), payload)

    @patch("warships.tasks.warm_realm_top_ships_task.delay")
    def test_top_ships_serves_published_when_fresh_cold(self, mock_delay):
        """Fresh key cold + published present → serve last-good, queue a warm,
        never run the aggregation in-request."""
        old_payload = {"realm": "na", "mode": "random", "ships": [{"ship_id": 9}],
                       "window_end": "2000-01-01", "captured_on": "2000-01-01"}
        cache.set(self._top_ships_published_key(), old_payload, timeout=None)
        # Fresh window key is absent (rotation gap).
        self.assertIsNone(cache.get(self._top_ships_fresh_key()))

        result = compute_realm_top_ships("na", mode="random", use_cache=True)

        self.assertEqual(result, old_payload)  # served the OLD numbers
        mock_delay.assert_called_once_with(realm="na")  # queued the warm
        # The cold read must not have computed/written the fresh key itself.
        self.assertIsNone(cache.get(self._top_ships_fresh_key()))

    @patch("warships.tasks.warm_realm_top_ships_task.delay")
    def test_top_ships_cold_read_dedups_warm(self, mock_delay):
        old_payload = {"realm": "na", "ships": []}
        cache.set(self._top_ships_published_key(), old_payload, timeout=None)
        compute_realm_top_ships("na", mode="random", use_cache=True)
        compute_realm_top_ships("na", mode="random", use_cache=True)
        mock_delay.assert_called_once()  # dispatch dedup coalesces the second

    def test_top_ships_both_miss_computes_and_writes_both(self):
        """No fresh, no published → synchronous compute writes both keys."""
        cache.clear()
        result = compute_realm_top_ships("na", mode="random", use_cache=True)
        self.assertTrue(result["ships"])
        self.assertEqual(cache.get(self._top_ships_fresh_key()), result)
        self.assertEqual(cache.get(self._top_ships_published_key()), result)

    # -- ships-by-tier-type (inline list) -----------------------------------

    def _ships_by_fresh_key(self, tier=10, ship_type="Battleship", mode="random"):
        return realm_cache_key(
            "na",
            f"ships-by:{mode}:win{self.window_end_d.isoformat()}:t{tier}:{ship_type}")

    def _ships_by_published_key(self, tier=10, ship_type="Battleship", mode="random"):
        return realm_cache_key(
            "na", f"ships-by:published:{mode}:t{tier}:{ship_type}")

    def test_ships_by_warm_writes_both_keys(self):
        cache.clear()
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Battleship", mode="random",
            min_battles=1, use_cache=False)
        self.assertTrue(payload["ships"])
        self.assertEqual(cache.get(self._ships_by_fresh_key()), payload)
        self.assertEqual(cache.get(self._ships_by_published_key()), payload)

    @patch("warships.tasks.warm_realm_top_ships_task.delay")
    def test_ships_by_serves_published_when_fresh_cold(self, mock_delay):
        old_payload = {"realm": "na", "tier": 10, "ship_type": "Battleship",
                       "ships": [{"ship_id": 9}], "window_end": "2000-01-01"}
        cache.set(self._ships_by_published_key(), old_payload, timeout=None)
        self.assertIsNone(cache.get(self._ships_by_fresh_key()))

        result = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Battleship", mode="random",
            use_cache=True)

        self.assertEqual(result, old_payload)
        mock_delay.assert_called_once_with(realm="na")
        self.assertIsNone(cache.get(self._ships_by_fresh_key()))

    def test_ships_by_warm_publishes_empty_to_clear_stale(self):
        """A bucket that has no ranked ships this window publishes the empty
        payload on the warm path, clearing a now-stale last-good."""
        cache.clear()
        # Stale published from a prior window (had a ship); the bucket is empty
        # now (no Destroyer is ranked in the snapshot).
        cache.set(self._ships_by_published_key(ship_type="Destroyer"),
                  {"ships": [{"ship_id": 9}]}, timeout=None)
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", mode="random",
            use_cache=False)
        self.assertEqual(payload["ships"], [])
        self.assertEqual(
            cache.get(self._ships_by_published_key(ship_type="Destroyer")),
            payload)


class SnapshotChainsWarmerTests(TestCase):
    """snapshot_ship_top_players_task must warm the rotated treemap/list keys
    immediately (warm-before-evict), not wait ~1h for the scheduled warmer."""

    def setUp(self):
        cache.clear()  # LocMemCache persists across tests; clear stale task locks

    @patch("warships.tasks.queue_realm_top_ships_warm")
    @patch("warships.data.compute_ship_top_player_snapshot")
    @patch.dict("os.environ", {"SHIP_BADGE_SNAPSHOT_ENABLED": "1"})
    def test_chains_warmer_on_completed(self, mock_compute, mock_warm):
        from warships.tasks import snapshot_ship_top_players_task
        mock_compute.return_value = {"status": "completed", "realm": "na"}
        snapshot_ship_top_players_task.run(realm="na")
        mock_warm.assert_called_once_with("na")

    @patch("warships.tasks.queue_realm_top_ships_warm")
    @patch("warships.data.compute_ship_top_player_snapshot")
    @patch.dict("os.environ", {"SHIP_BADGE_SNAPSHOT_ENABLED": "1"})
    def test_no_warm_on_lock_skip(self, mock_compute, mock_warm):
        from warships.tasks import snapshot_ship_top_players_task
        mock_compute.return_value = {"status": "skipped", "reason": "already-running"}
        snapshot_ship_top_players_task.run(realm="na")
        mock_warm.assert_not_called()

    @patch("warships.tasks.queue_realm_top_ships_warm")
    @patch.dict("os.environ", {"SHIP_BADGE_SNAPSHOT_ENABLED": "0"})
    def test_no_warm_when_disabled(self, mock_warm):
        from warships.tasks import snapshot_ship_top_players_task
        snapshot_ship_top_players_task.run(realm="na")
        mock_warm.assert_not_called()
