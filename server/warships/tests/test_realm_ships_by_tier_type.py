"""Tests for the inline ship-leaderboard list (ships by tier+type, WR-ranked).

Backs the landing-page filterable table under the treemap
(``compute_realm_ships_by_tier_type`` + the ``/api/realm/<realm>/ships`` view).
Pins the win-rate ordering, the tier+type filter, the rolling-window boundaries,
realm/mode isolation, nullable-damage handling, the min-battles floor, the
snapshot restriction (only ships with a drill-down board are listed), and the
view's validation (404 unknown realm, 400 bad tier/type). The window is the
rolling trailing ``SHIP_LEADERBOARD_WINDOW_DAYS`` anchored on the realm's latest
``ShipTopPlayerSnapshot.captured_on`` — 1:1 with the /ship leaderboards.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    _season_window_datetimes,
    compute_realm_ships_by_tier_type,
)
from warships.models import (
    BattleEvent, BattleObservation, Player, Ship, ShipTopPlayerSnapshot,
    realm_cache_key,
)

# Distinct ship_ids; only T10 Destroyers/Battleships are exercised below.
SHIMA = 4282267344       # T10 Destroyer
GEARING = 4282267345     # T10 Destroyer
YAMATO = 4262960912      # T10 Battleship
T9_DD = 3000000001       # T9 Destroyer (off-tier)


class RealmShipsByTierTypeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="ships_bench", player_id=888001, realm="na", pvp_battles=1000)
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        Ship.objects.create(ship_id=GEARING, name="Gearing", nation="usa",
                            ship_type="Destroyer", tier=10)
        Ship.objects.create(ship_id=YAMATO, name="Yamato", nation="japan",
                            ship_type="Battleship", tier=10)
        Ship.objects.create(ship_id=T9_DD, name="Fletcher", nation="usa",
                            ship_type="Destroyer", tier=9)
        # Anchor the rolling window on captured_on=today (the snapshots created by
        # _snapshot share this date, so they are the candidate set).
        self.captured_on = timezone.now().date()
        self.window_start_d = self.captured_on - timedelta(days=14)
        self.window_start, self.window_end = _season_window_datetimes(
            self.window_start_d, self.captured_on)

    def _snapshot(self, ship_id, realm="na"):
        """Mark a ship 'ranked' for the latest window so it's a list candidate."""
        ShipTopPlayerSnapshot.objects.create(
            captured_on=self.captured_on, realm=realm, ship_id=ship_id,
            ship_name="x", rank=1, player=self.player, win_rate=50.0, battles=1)

    def _event(self, ship_id, battles, wins, *, damage=0, frags=0,
               detected_at=None, mode="random", player=None):
        """One BattleEvent at an exact detected_at (own observation pair)."""
        player = player or self.player
        detected_at = detected_at or (self.window_start + timedelta(days=1))
        obs_a = BattleObservation.objects.create(player=player, pvp_battles=1)
        obs_b = BattleObservation.objects.create(player=player, pvp_battles=2)
        ev = BattleEvent.objects.create(
            player=player, ship_id=ship_id, ship_name="x", mode=mode,
            battles_delta=battles, wins_delta=wins, losses_delta=battles - wins,
            frags_delta=frags, damage_delta=damage,
            from_observation=obs_a, to_observation=obs_b,
        )
        BattleEvent.objects.filter(pk=ev.pk).update(detected_at=detected_at)
        return ev

    def test_orders_by_win_rate_and_shapes_payload(self):
        # Shimakaze 55% WR, Gearing 60% WR — Gearing must lead.
        self._snapshot(SHIMA)
        self._snapshot(GEARING)
        self._event(SHIMA, battles=200, wins=110, damage=200 * 50_000, frags=200)
        self._event(GEARING, battles=100, wins=60, damage=100 * 60_000, frags=140)

        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)

        self.assertEqual(payload["tier"], 10)
        self.assertEqual(payload["ship_type"], "Destroyer")
        self.assertEqual(payload["window_days"], 14)
        self.assertEqual(payload["captured_on"], self.captured_on.isoformat())
        self.assertNotIn("season_index", payload)
        ids = [s["ship_id"] for s in payload["ships"]]
        self.assertEqual(ids, [GEARING, SHIMA])  # 60% before 55%
        g = payload["ships"][0]
        self.assertEqual(g["win_rate"], 60.0)
        self.assertEqual(g["battles"], 100)
        self.assertEqual(g["avg_damage"], 60_000)
        self.assertEqual(g["kills_per_battle"], 1.4)
        self.assertEqual(g["ship_name"], "Gearing")

    def test_filters_to_requested_tier_and_type(self):
        # Yamato (BB) and the T9 DD have events + snapshots but must not appear
        # in the T10 Destroyer bucket.
        self._snapshot(SHIMA)
        self._snapshot(YAMATO)
        self._snapshot(T9_DD)
        self._event(SHIMA, battles=200, wins=100)
        self._event(YAMATO, battles=200, wins=100)
        self._event(T9_DD, battles=200, wins=100)

        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertEqual([s["ship_id"] for s in payload["ships"]], [SHIMA])

    def test_window_excludes_neighbouring_periods(self):
        self._snapshot(SHIMA)
        self._event(SHIMA, battles=200, wins=100, detected_at=self.window_start)  # in
        self._event(SHIMA, battles=300, wins=300,
                    detected_at=self.window_end + timedelta(hours=6))   # past window
        self._event(SHIMA, battles=300, wins=300,
                    detected_at=self.window_start - timedelta(hours=1))  # before

        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertEqual(len(payload["ships"]), 1)
        self.assertEqual(payload["ships"][0]["battles"], 200)  # only the in-window row

    def test_mode_and_realm_isolation(self):
        self._snapshot(SHIMA)
        # ranked event on the same ship — excluded from the default random list.
        self._event(SHIMA, battles=200, wins=100, mode="ranked")
        # different realm — excluded (and its own-realm snapshot is separate).
        other = Player.objects.create(
            name="eu_bench", player_id=888002, realm="eu", pvp_battles=10)
        self._event(SHIMA, battles=200, wins=100, player=other)

        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertEqual(payload["ships"], [])

    def test_nullable_damage_is_coalesced(self):
        # damage_delta is nullable; a None sum must not crash and reads as 0.
        self._snapshot(SHIMA)
        self._event(SHIMA, battles=200, wins=100, damage=None, frags=100)

        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertEqual(payload["ships"][0]["avg_damage"], 0)
        self.assertEqual(payload["ships"][0]["kills_per_battle"], 0.5)

    def test_min_battles_floor_drops_thin_ships(self):
        # Snapshot present but only 10 battles in-window — below the default floor.
        self._snapshot(SHIMA)
        self._event(SHIMA, battles=10, wins=8)
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertEqual(payload["ships"], [])

    def test_snapshot_restriction_excludes_unranked_ship(self):
        # Plenty of battles, right tier/type, but no snapshot → not listed (its
        # drill-down board would be empty).
        self._event(SHIMA, battles=500, wins=300)
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertEqual(payload["ships"], [])

    def test_no_snapshot_returns_empty_with_null_captured_on(self):
        # No snapshot anywhere for the realm → captured_on is None and the list is
        # empty (the candidate set comes from the latest snapshot).
        self._event(SHIMA, battles=500, wins=300)
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertIsNone(payload["captured_on"])
        self.assertEqual(payload["ships"], [])


class RealmShipsByTierTypeWarmTests(TestCase):
    """The daily warm_realm_top_ships_task pre-populates the tier/type buckets.

    Without this warm, the first click of a new tier/type combination paid the
    live BattleEvent aggregation on the request path (runbook-leaderboard-updates).
    """

    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="warm_bench", player_id=888004, realm="na", pvp_battles=1000)
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        self.captured_on = timezone.now().date()
        self.window_start, _ = _season_window_datetimes(
            self.captured_on - timedelta(days=14), self.captured_on)
        ShipTopPlayerSnapshot.objects.create(
            captured_on=self.captured_on, realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=self.player,
            win_rate=50.0, battles=1)
        obs_a = BattleObservation.objects.create(player=self.player, pvp_battles=1)
        obs_b = BattleObservation.objects.create(player=self.player, pvp_battles=2)
        ev = BattleEvent.objects.create(
            player=self.player, ship_id=SHIMA, ship_name="x", mode="random",
            battles_delta=200, wins_delta=120, losses_delta=80,
            frags_delta=200, damage_delta=200 * 50_000,
            from_observation=obs_a, to_observation=obs_b)
        BattleEvent.objects.filter(pk=ev.pk).update(
            detected_at=self.window_start + timedelta(days=1))

    def _bucket_key(self, tier, ship_type):
        return realm_cache_key(
            "na",
            f"ships-by:random:win{self.captured_on.isoformat()}:t{tier}:{ship_type}")

    def test_warm_populates_tier_type_bucket_cache(self):
        from warships.tasks import warm_realm_top_ships_task

        # Cold before the warm.
        self.assertIsNone(cache.get(self._bucket_key(10, "Destroyer")))

        result = warm_realm_top_ships_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(result["status"], "completed")
        # 1 badge tier (default {10}) x 5 ship types.
        self.assertEqual(result["results"]["tier_type_buckets"], 5)

        # The T10/Destroyer bucket is now a warm hit carrying the WR-ranked ship.
        cached = cache.get(self._bucket_key(10, "Destroyer"))
        self.assertIsNotNone(cached)
        self.assertEqual([s["ship_id"] for s in cached["ships"]], [SHIMA])
        self.assertEqual(cached["ships"][0]["win_rate"], 60.0)
        # A bucket with no candidate ships (Battleship) short-circuits before the
        # BattleEvent aggregation, so it isn't cached — and doesn't need to be:
        # that cheap early-return path was never the source of the switch lag.
        self.assertIsNone(cache.get(self._bucket_key(10, "Battleship")))


class RealmShipsByTierTypeViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="view_bench", player_id=888003, realm="na", pvp_battles=10)
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        ShipTopPlayerSnapshot.objects.create(
            captured_on=timezone.now().date(), realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=self.player,
            win_rate=50.0, battles=1)

    def test_happy_path_returns_payload(self):
        resp = self.client.get("/api/realm/na/ships/?tier=10&type=Destroyer")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["tier"], 10)
        self.assertEqual(resp.json()["ship_type"], "Destroyer")

    def test_unknown_realm_404(self):
        resp = self.client.get("/api/realm/xx/ships/?tier=10&type=Destroyer")
        self.assertEqual(resp.status_code, 404)

    def test_bad_tier_400(self):
        resp = self.client.get("/api/realm/na/ships/?tier=7&type=Destroyer")
        self.assertEqual(resp.status_code, 400)

    def test_missing_tier_400(self):
        resp = self.client.get("/api/realm/na/ships/?type=Destroyer")
        self.assertEqual(resp.status_code, 400)

    def test_bad_type_400(self):
        resp = self.client.get("/api/realm/na/ships/?tier=10&type=Frigate")
        self.assertEqual(resp.status_code, 400)

    def test_aircraft_carrier_space_variant_rejected(self):
        # The DB stores "AirCarrier" (no space); the spaced variant must 400.
        resp = self.client.get(
            "/api/realm/na/ships/?tier=10&type=Aircraft%20Carrier")
        self.assertEqual(resp.status_code, 400)

    def test_aircarrier_no_space_accepted(self):
        resp = self.client.get("/api/realm/na/ships/?tier=10&type=AirCarrier")
        self.assertEqual(resp.status_code, 200)
