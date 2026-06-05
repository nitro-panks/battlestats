"""Window-boundary tests for the realm top-ships treemap.

The treemap is a *static daily count* over the previous 7 full UTC days, with the
current UTC day excluded — i.e. the window is ``[midnight_utc - 7d, midnight_utc)``.
These tests pin the off-by-one boundaries (current day excluded, day-7 included,
day-8 excluded) and the per-ship summation, since that is the failure mode most
likely to slip when the window definition changes.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone as django_timezone

from warships.data import compute_realm_top_ships
from warships.models import BattleEvent, BattleObservation, Player, Ship


class RealmTopShipsWindowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="topships_bench", player_id=777001, realm="na",
            pvp_battles=1000, pvp_wins=520, pvp_losses=480, pvp_frags=900,
            pvp_survived_battles=600,
        )
        Ship.objects.create(
            ship_id=1, name="Yamato", nation="japan",
            ship_type="Battleship", tier=10,
        )
        # Today's UTC midnight is the exclusive window end; the project runs
        # USE_TZ=False, so now() is naive — match the production code path.
        self.window_end = django_timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0)
        self.window_start = self.window_end - timedelta(days=7)

    def _event(self, ship_id, ship_name, battles, detected_at, mode="random"):
        """Create one BattleEvent at an exact detected_at.

        Each event gets its own observation pair so the
        (from_observation, to_observation, ship_id) unique constraint never
        collides, and detected_at is overridden after create (the field is
        auto_now_add, so the create-time value is ignored).
        """
        obs_a = BattleObservation.objects.create(player=self.player, pvp_battles=1)
        obs_b = BattleObservation.objects.create(player=self.player, pvp_battles=2)
        ev = BattleEvent.objects.create(
            player=self.player, ship_id=ship_id, ship_name=ship_name, mode=mode,
            battles_delta=battles, wins_delta=0, losses_delta=0, frags_delta=0,
            from_observation=obs_a, to_observation=obs_b,
        )
        BattleEvent.objects.filter(pk=ev.pk).update(detected_at=detected_at)
        return ev

    def test_window_excludes_current_and_day8_includes_day1_and_day7(self):
        # In-window: yesterday and exactly the window-start boundary (gte).
        self._event(1, "Yamato", 5, self.window_end - timedelta(days=1, hours=12))
        self._event(1, "Yamato", 3, self.window_start)  # exact lower bound, included
        # Out-of-window: the in-progress current day, and day-8 (before start).
        self._event(2, "Shimakaze", 99, self.window_end + timedelta(hours=6))
        self._event(3, "Montana", 77, self.window_start - timedelta(hours=1))

        payload = compute_realm_top_ships("na", mode="random", use_cache=False)

        self.assertEqual(payload["days"], 7)
        self.assertEqual(payload["window_start"], self.window_start.isoformat())
        self.assertEqual(payload["window_end"], self.window_end.isoformat())

        by_id = {s["ship_id"]: s for s in payload["ships"]}
        # Ship 1: both in-window rows summed (5 + 3); current-day & day-8 excluded.
        self.assertIn(1, by_id)
        self.assertEqual(by_id[1]["battles"], 8)
        self.assertEqual(by_id[1]["ship_type"], "Battleship")
        self.assertEqual(by_id[1]["tier"], 10)
        self.assertNotIn(2, by_id)  # current UTC day excluded
        self.assertNotIn(3, by_id)  # day-8 excluded

    def test_mode_and_realm_isolation(self):
        # A ranked event and a different-realm event must not bleed into the
        # na/random treemap.
        self._event(1, "Yamato", 10, self.window_end - timedelta(days=2),
                    mode="ranked")
        other = Player.objects.create(
            name="eu_bench", player_id=777002, realm="eu", pvp_battles=10)
        obs_a = BattleObservation.objects.create(player=other, pvp_battles=1)
        obs_b = BattleObservation.objects.create(player=other, pvp_battles=2)
        ev = BattleEvent.objects.create(
            player=other, ship_id=1, ship_name="Yamato", mode="random",
            battles_delta=50, wins_delta=0, losses_delta=0, frags_delta=0,
            from_observation=obs_a, to_observation=obs_b,
        )
        BattleEvent.objects.filter(pk=ev.pk).update(
            detected_at=self.window_end - timedelta(days=2))

        payload = compute_realm_top_ships("na", mode="random", use_cache=False)
        self.assertEqual(payload["ships"], [])  # neither row qualifies
