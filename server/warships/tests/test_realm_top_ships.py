"""Window-boundary tests for the realm top-ships treemap.

The treemap aggregates ``BattleEvent`` over the **rolling trailing
``SHIP_LEADERBOARD_WINDOW_DAYS`` window the /ship leaderboards read** — i.e.
``[captured_on - window_days, captured_on)`` for the realm's most recent
``ShipTopPlayerSnapshot.captured_on`` (see ``latest_ship_snapshot_window``). These
tests pin the off-by-one boundaries (window-start included, the current day past
the window excluded, the day before excluded) and the per-ship summation, since
that is the failure mode most likely to slip when the window definition changes.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    SHIP_LEADERBOARD_WINDOW_DAYS,
    _season_window_datetimes,
    compute_realm_top_ships,
    latest_ship_snapshot_window,
)
from warships.models import (
    BattleEvent, BattleObservation, Player, Ship, ShipTopPlayerSnapshot,
)


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
        # Anchor the realm's rolling window on a snapshot's captured_on, exactly as
        # the /ship leaderboards do. The project runs USE_TZ=False, so
        # _season_window_datetimes returns naive datetimes — match the prod path.
        self.captured_on = timezone.now().date()
        ShipTopPlayerSnapshot.objects.create(
            captured_on=self.captured_on, realm="na", ship_id=1,
            ship_name="Yamato", rank=1, player=self.player,
            win_rate=50.0, battles=1)
        _, self.window_start_d, self.window_end_d = latest_ship_snapshot_window("na")
        self.window_start, self.window_end = _season_window_datetimes(
            self.window_start_d, self.window_end_d)

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

    def test_window_is_rolling_and_excludes_neighbours(self):
        # In-window: mid-window and exactly the window-start boundary (gte).
        self._event(1, "Yamato", 5, self.window_start + timedelta(days=1, hours=12))
        self._event(1, "Yamato", 3, self.window_start)  # exact lower bound, included
        # Out-of-window: the current day past the window end (>= window_end), and
        # the instant just before window_start.
        self._event(2, "Shimakaze", 99, self.window_end + timedelta(hours=6))
        self._event(3, "Montana", 77, self.window_start - timedelta(hours=1))

        payload = compute_realm_top_ships("na", mode="random", use_cache=False)

        self.assertEqual(payload["window_days"], SHIP_LEADERBOARD_WINDOW_DAYS)
        self.assertEqual(payload["captured_on"], self.captured_on.isoformat())
        self.assertEqual(payload["window_start"], self.window_start_d.isoformat())
        self.assertEqual(payload["window_end"], self.window_end_d.isoformat())
        # No fixed-season framing under the rolling model.
        self.assertNotIn("season_start", payload)
        self.assertNotIn("season_end", payload)

        by_id = {s["ship_id"]: s for s in payload["ships"]}
        # Ship 1: both in-window rows summed (5 + 3); neighbours excluded.
        self.assertIn(1, by_id)
        self.assertEqual(by_id[1]["battles"], 8)
        self.assertEqual(by_id[1]["ship_type"], "Battleship")
        self.assertEqual(by_id[1]["tier"], 10)
        self.assertNotIn(2, by_id)  # current day past window end excluded
        self.assertNotIn(3, by_id)  # before window_start excluded

    def test_falls_back_to_trailing_today_window_without_snapshot(self):
        # With no snapshot for the realm, the window falls back to a trailing
        # window ending today and captured_on is None.
        ShipTopPlayerSnapshot.objects.all().delete()
        cache.clear()
        captured_on, window_start_d, window_end_d = latest_ship_snapshot_window("na")
        self.assertIsNone(captured_on)
        self.assertEqual(window_end_d, timezone.now().date())
        self.assertEqual(
            window_start_d,
            timezone.now().date() - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS))

        payload = compute_realm_top_ships("na", mode="random", use_cache=False)
        self.assertIsNone(payload["captured_on"])
        self.assertEqual(payload["window_end"], window_end_d.isoformat())

    def test_mode_and_realm_isolation(self):
        # A ranked event and a different-realm event must not bleed into the
        # na/random treemap.
        self._event(1, "Yamato", 10, self.window_start + timedelta(days=2),
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
            detected_at=self.window_start + timedelta(days=2))

        payload = compute_realm_top_ships("na", mode="random", use_cache=False)
        self.assertEqual(payload["ships"], [])  # neither row qualifies
