"""Tests for the inline ship-leaderboard list (ships by tier+type, WR-ranked).

Backs the landing-page filterable table under the treemap
(``compute_realm_ships_by_tier_type`` + the ``/api/realm/<realm>/ships`` view).
Pins the win-rate ordering, the tier+type filter, the season window boundaries,
realm/mode isolation, nullable-damage handling, the min-battles floor, the
snapshot restriction (only ships with a drill-down board are listed), and the
view's validation (404 unknown realm, 400 bad tier/type).
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase

from warships.data import (
    _season_window_datetimes,
    compute_realm_ships_by_tier_type,
    most_recent_completed_season,
)
from warships.models import (
    BattleEvent, BattleObservation, Player, Ship, ShipTopPlayerSnapshot,
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
        self.season_idx, self.season_start, self.season_end = most_recent_completed_season()
        self.window_start, self.window_end = _season_window_datetimes(
            self.season_start, self.season_end)

    def _snapshot(self, ship_id, realm="na"):
        """Mark a ship 'ranked' for the latest season so it's a list candidate."""
        ShipTopPlayerSnapshot.objects.create(
            captured_on=self.season_start, realm=realm, ship_id=ship_id,
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
        self.assertEqual(payload["season_index"], self.season_idx)
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

    def test_window_excludes_neighbouring_seasons(self):
        self._snapshot(SHIMA)
        self._event(SHIMA, battles=200, wins=100, detected_at=self.window_start)  # in
        self._event(SHIMA, battles=300, wins=300,
                    detected_at=self.window_end + timedelta(hours=6))   # current season
        self._event(SHIMA, battles=300, wins=300,
                    detected_at=self.window_start - timedelta(hours=1))  # prior

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


class RealmShipsByTierTypeViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = Player.objects.create(
            name="view_bench", player_id=888003, realm="na", pvp_battles=10)
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        idx, start, end = most_recent_completed_season()
        ShipTopPlayerSnapshot.objects.create(
            captured_on=start, realm="na", ship_id=SHIMA, ship_name="Shimakaze",
            rank=1, player=self.player, win_rate=50.0, battles=1)

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
