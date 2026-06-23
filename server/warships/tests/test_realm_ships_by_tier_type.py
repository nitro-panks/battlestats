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
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    _season_window_datetimes,
    compute_realm_ships_by_tier_type,
    ship_pct_bucket_cache_key,
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
        # Whole-bucket denominator = every T10 Destroyer battle in the window
        # (here exactly the two listed ships: 200 + 100).
        self.assertEqual(payload["total_battles"], 300)

    def test_total_battles_counts_ships_excluded_from_the_list(self):
        # "True class/tier total" semantics: a thin ship below the min-battles
        # floor is NOT listed, but its battles STILL count toward total_battles
        # (the share-% denominator). This assertion is what distinguishes the
        # chosen "whole bucket" denominator from a naive "sum of shown ships".
        self._snapshot(SHIMA)
        self._snapshot(GEARING)
        self._event(SHIMA, battles=200, wins=110)
        self._event(GEARING, battles=10, wins=6)  # below SHIP_LIST_MIN_BATTLES (50)

        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        # Only Shimakaze clears the floor and is listed…
        self.assertEqual([s["ship_id"] for s in payload["ships"]], [SHIMA])
        # …but the denominator still includes Gearing's 10 battles.
        self.assertEqual(payload["total_battles"], 210)

    def test_total_battles_zero_on_empty_bucket(self):
        # No snapshot anywhere → empty list and a 0 denominator (the field is
        # always present so the client can guard total <= 0).
        self._event(SHIMA, battles=500, wins=300)
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        self.assertEqual(payload["ships"], [])
        self.assertEqual(payload["total_battles"], 0)

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

        # The top-ships warm chains the per-realm all-buckets pct warmer (a
        # separate background task); stub the enqueue so this test stays scoped to
        # the all-view warm + the inline default-pct bucket.
        with mock.patch("warships.tasks.queue_realm_ships_pct_warm") as chain:
            result = warm_realm_top_ships_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(result["status"], "completed")
        # 1 badge tier (default {10}) x 5 ship types.
        self.assertEqual(result["results"]["tier_type_buckets"], 5)
        # The landing default (top-50%) bucket's percentile is pre-warmed inline so
        # the primary landing view loads instant; the rest are warmed by the chain.
        self.assertEqual(result["results"]["default_pct_bucket"], "t10/Battleship")
        chain.assert_called_once_with("na")

        # The T10/Destroyer bucket is now a warm hit carrying the WR-ranked ship.
        cached = cache.get(self._bucket_key(10, "Destroyer"))
        self.assertIsNotNone(cached)
        self.assertEqual([s["ship_id"] for s in cached["ships"]], [SHIMA])
        self.assertEqual(cached["ships"][0]["win_rate"], 60.0)
        # A bucket with no candidate ships (Battleship) short-circuits before the
        # BattleEvent aggregation, but the **warm** path still writes the empty
        # payload — to both the fresh key and the durable `:published` key — so a
        # bucket that went empty this window clears any stale last-good rather
        # than serving yesterday's ships forever (warm-before-evict). The read
        # path (use_cache=True) still does NOT cache the early-return empties.
        # See test_ship_warm_before_evict.test_ships_by_warm_publishes_empty_*.
        empty = cache.get(self._bucket_key(10, "Battleship"))
        self.assertIsNotNone(empty)
        self.assertEqual(empty["ships"], [])


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


class RealmShipsByTierTypeWrPctTests(TestCase):
    """Win-rate-percentile views (top 50% / 25% of each ship's players by WR).

    Pins the load-bearing constraint — the listed *ship set* is identical to the
    all-view (membership gates on full-population battles, never the subset) — plus
    the re-pooling math, the per-player ranking floor + its never-drop fallback,
    and the equivalence of the percentile path to the cheap all-path at 100%.
    """

    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        Ship.objects.create(ship_id=GEARING, name="Gearing", nation="usa",
                            ship_type="Destroyer", tier=10)
        self.snap_player = Player.objects.create(
            name="snap_anchor", player_id=889000, realm="na", pvp_battles=1)
        self.captured_on = timezone.now().date()
        self.window_start, _ = _season_window_datetimes(
            self.captured_on - timedelta(days=14), self.captured_on)
        self._next_pid = 889001

    def _snapshot(self, ship_id):
        ShipTopPlayerSnapshot.objects.create(
            captured_on=self.captured_on, realm="na", ship_id=ship_id,
            ship_name="x", rank=1, player=self.snap_player, win_rate=50.0, battles=1)

    def _player_event(self, ship_id, battles, wins, *, avg_damage=0, frags=0):
        """One player's whole-window aggregate for a ship (a fresh player each)."""
        p = Player.objects.create(
            name=f"p{self._next_pid}", player_id=self._next_pid, realm="na",
            pvp_battles=battles)
        self._next_pid += 1
        obs_a = BattleObservation.objects.create(player=p, pvp_battles=1)
        obs_b = BattleObservation.objects.create(player=p, pvp_battles=2)
        ev = BattleEvent.objects.create(
            player=p, ship_id=ship_id, ship_name="x", mode="random",
            battles_delta=battles, wins_delta=wins, losses_delta=battles - wins,
            frags_delta=frags, damage_delta=avg_damage * battles,
            from_observation=obs_a, to_observation=obs_b)
        BattleEvent.objects.filter(pk=ev.pk).update(
            detected_at=self.window_start + timedelta(days=1))
        return p

    def _four_skill_tiers(self, ship_id):
        # Four equal-battle players spanning the skill range, all clearing the
        # per-player floor (15). Pooled all-view WR = 240/400 = 60%.
        self._player_event(ship_id, 100, 90, avg_damage=80_000, frags=200)  # 90%
        self._player_event(ship_id, 100, 70, avg_damage=60_000, frags=150)  # 70%
        self._player_event(ship_id, 100, 50, avg_damage=40_000, frags=100)  # 50%
        self._player_event(ship_id, 100, 30, avg_damage=20_000, frags=50)   # 30%

    def test_top_quartile_pools_only_the_best_players(self):
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=25, use_cache=False)
        self.assertEqual(payload["wr_pct"], 25)
        s = payload["ships"][0]
        # Top 25% of 4 players = the single 90%-WR player.
        self.assertEqual(s["battles"], 100)
        self.assertEqual(s["win_rate"], 90.0)
        self.assertEqual(s["avg_damage"], 80_000)
        self.assertEqual(s["kills_per_battle"], 2.0)

    def test_top_half_pools_the_better_half(self):
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=50, use_cache=False)
        s = payload["ships"][0]
        # Top 50% = the 90% + 70% players: 160 wins / 200 battles = 80%.
        self.assertEqual(s["battles"], 200)
        self.assertEqual(s["win_rate"], 80.0)
        self.assertEqual(s["avg_damage"], 70_000)  # (80k + 60k) / 2

    def test_does_not_change_the_listed_ship_set(self):
        # Both ships are listed in the all-view; the percentile views must list the
        # exact same set (only the stats narrow) — the load-bearing constraint.
        self._snapshot(SHIMA)
        self._snapshot(GEARING)
        self._four_skill_tiers(SHIMA)
        self._four_skill_tiers(GEARING)
        all_ids = {s["ship_id"] for s in compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)["ships"]}
        for pct in (50, 25):
            ids = {s["ship_id"] for s in compute_realm_ships_by_tier_type(
                "na", tier=10, ship_type="Destroyer", wr_pct=pct,
                use_cache=False)["ships"]}
            self.assertEqual(ids, all_ids, f"wr_pct={pct} changed the ship set")

    def test_player_floor_fallback_never_drops_a_thin_ship(self):
        # A ship whose every player is below the per-player ranking floor (15) but
        # whose FULL-population battles clear the ship floor (50) stays listed —
        # falling back to full-population stats rather than vanishing.
        self._snapshot(SHIMA)
        for _ in range(6):
            self._player_event(SHIMA, 10, 6, avg_damage=50_000)  # 10 < floor 15
        payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=25, use_cache=False)
        self.assertEqual([s["ship_id"] for s in payload["ships"]], [SHIMA])
        s = payload["ships"][0]
        # Full-population fallback: 60 battles, 36/60 = 60% WR.
        self.assertEqual(s["battles"], 60)
        self.assertEqual(s["win_rate"], 60.0)
        self.assertEqual(s["avg_damage"], 50_000)

    def test_pct_100_no_floor_matches_the_all_path_exactly(self):
        # The equivalence hatch: top-100% with no floor must reproduce the cheap
        # all-path row-for-row, pinning the two code paths together.
        self._snapshot(SHIMA)
        self._snapshot(GEARING)
        self._four_skill_tiers(SHIMA)
        self._player_event(GEARING, 200, 130, avg_damage=55_000, frags=240)
        self._player_event(GEARING, 40, 10, avg_damage=30_000, frags=20)
        all_payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", use_cache=False)
        pct_payload = compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=100,
            player_min_battles=0, use_cache=False)
        # Same ships, same order, same stat fields (wr_pct aside).
        self.assertEqual(
            [{k: v for k, v in s.items()} for s in pct_payload["ships"]],
            [{k: v for k, v in s.items()} for s in all_payload["ships"]])

    def test_cold_pct_read_serves_pending_and_queues_one_warm(self):
        # The read path must NOT compute the heavy aggregation synchronously: on a
        # cold percentile key it returns a `pending` payload and queues a single
        # background warm (the client polls until the warm fills the key).
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        with mock.patch("warships.tasks.queue_ships_by_pct_warm") as q:
            payload = compute_realm_ships_by_tier_type(
                "na", tier=10, ship_type="Destroyer", wr_pct=25, use_cache=True)
        self.assertTrue(payload["pending"])
        self.assertEqual(payload["ships"], [])
        self.assertEqual(payload["wr_pct"], 25)
        q.assert_called_once()

    def test_warm_then_cached_read_serves_ready_not_pending(self):
        # After the background warm runs (use_cache=False), a normal read hits the
        # warm fresh key and serves the ready payload — no pending, no re-queue.
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        # Simulate the background warm (caches both 50 and 25 fresh keys).
        compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=50, use_cache=False)
        with mock.patch("warships.tasks.queue_ships_by_pct_warm") as q:
            payload = compute_realm_ships_by_tier_type(
                "na", tier=10, ship_type="Destroyer", wr_pct=25, use_cache=True)
        self.assertNotIn("pending", payload)
        self.assertEqual(payload["ships"][0]["win_rate"], 90.0)  # top 25%
        q.assert_not_called()

    def test_view_honors_wr_pct_param(self):
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        # Warm first so the view serves the ready payload (not a pending stub).
        compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=50, use_cache=False)
        resp = self.client.get(
            "/api/realm/na/ships/?tier=10&type=Destroyer&wr_pct=25")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["wr_pct"], 25)
        self.assertEqual(body["ships"][0]["win_rate"], 90.0)
        self.assertNotIn("X-Ships-WR-Pending", resp)

    def test_view_cold_pct_sets_pending_header(self):
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        with mock.patch("warships.tasks.queue_ships_by_pct_warm"):
            resp = self.client.get(
                "/api/realm/na/ships/?tier=10&type=Destroyer&wr_pct=25")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Ships-WR-Pending"], "true")
        self.assertEqual(resp.json()["ships"], [])

    def test_view_ignores_unsupported_wr_pct(self):
        # Anything outside the offered set falls through to the all-view.
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        resp = self.client.get(
            "/api/realm/na/ships/?tier=10&type=Destroyer&wr_pct=10")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["wr_pct"])
        self.assertEqual(body["ships"][0]["win_rate"], 60.0)  # full-population

    def test_pct_bucket_cache_key_matches_what_compute_writes(self):
        # The skip-if-warm guard reads ship_pct_bucket_cache_key; if it drifts even
        # slightly from the key compute writes, every warm trigger reads "cold" and
        # silently recomputes all buckets (the exact 2-3x daily PG load the design
        # avoids). Pin the helper against a key the REAL compute just wrote — both
        # build it through _ships_by_fresh_cache_key, so this guards that contract.
        self._snapshot(SHIMA)
        self._four_skill_tiers(SHIMA)
        compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=50, use_cache=False)
        key = ship_pct_bucket_cache_key("na", 10, "Destroyer", wr_pct=50)
        self.assertIsNotNone(cache.get(key))
        # Spacing/case normalization must collapse to the same key, too.
        self.assertEqual(
            ship_pct_bucket_cache_key("NA", "10", " Destroyer ".strip()),
            key)


class RealmShipsPctWarmTests(TestCase):
    """The per-realm all-buckets pct warmer (warm_realm_ships_pct_task).

    Pre-warms EVERY tier x type win-rate-percentile bucket so visitors never pay
    the ~20s crunch. Pins: it fills the fresh keys, is idempotent (skip-if-warm),
    and warms the default bucket first. Pause is patched to 0 so the test is fast.
    """

    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        self.snap_player = Player.objects.create(
            name="pct_warm_anchor", player_id=890000, realm="na", pvp_battles=1)
        self.captured_on = timezone.now().date()
        self.window_start, _ = _season_window_datetimes(
            self.captured_on - timedelta(days=14), self.captured_on)
        ShipTopPlayerSnapshot.objects.create(
            captured_on=self.captured_on, realm="na", ship_id=SHIMA,
            ship_name="x", rank=1, player=self.snap_player, win_rate=50.0,
            battles=1)
        # Four equal-battle players spanning the skill range on SHIMA (T10 DD),
        # all clearing the per-player floor (15) so 50% vs 25% select differently.
        for pid, (b, w, dmg) in enumerate([
                (100, 90, 80_000), (100, 70, 60_000),
                (100, 50, 40_000), (100, 30, 20_000)]):
            p = Player.objects.create(
                name=f"pw{pid}", player_id=890100 + pid, realm="na",
                pvp_battles=b)
            obs_a = BattleObservation.objects.create(player=p, pvp_battles=1)
            obs_b = BattleObservation.objects.create(player=p, pvp_battles=2)
            ev = BattleEvent.objects.create(
                player=p, ship_id=SHIMA, ship_name="x", mode="random",
                battles_delta=b, wins_delta=w, losses_delta=b - w,
                frags_delta=0, damage_delta=dmg * b,
                from_observation=obs_a, to_observation=obs_b)
            BattleEvent.objects.filter(pk=ev.pk).update(
                detected_at=self.window_start + timedelta(days=1))

    @mock.patch("warships.tasks.SHIP_PCT_WARM_PAUSE_SECONDS", 0)
    def test_warms_all_pct_buckets_filling_both_50_and_25(self):
        from warships.tasks import warm_realm_ships_pct_task

        # Cold before the warm.
        self.assertIsNone(
            cache.get(ship_pct_bucket_cache_key("na", 10, "Destroyer", wr_pct=50)))

        result = warm_realm_ships_pct_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(result["status"], "completed")
        # 1 badge tier (default {10}) x 5 types, all cold => all attempted.
        self.assertEqual(result["warmed"], 5)
        self.assertEqual(result["skipped"], 0)

        # Both percentiles for the populated T10/Destroyer bucket are filled from
        # the single per-(ship,player) query.
        p50 = cache.get(ship_pct_bucket_cache_key("na", 10, "Destroyer", wr_pct=50))
        p25 = cache.get(ship_pct_bucket_cache_key("na", 10, "Destroyer", wr_pct=25))
        self.assertEqual(p50["ships"][0]["win_rate"], 80.0)  # top 2 of 4: (90+70)/2
        self.assertEqual(p25["ships"][0]["win_rate"], 90.0)  # top 1 of 4

    @mock.patch("warships.tasks.SHIP_PCT_WARM_PAUSE_SECONDS", 0)
    def test_skip_if_warm_is_idempotent(self):
        from warships.tasks import warm_realm_ships_pct_task

        # Pre-warm the one populated bucket; the warmer must skip it next pass so
        # the repeated triggers (Beat + 2x/day snapshot) collapse to no real work.
        compute_realm_ships_by_tier_type(
            "na", tier=10, ship_type="Destroyer", wr_pct=50, use_cache=False)
        result = warm_realm_ships_pct_task.apply(kwargs={"realm": "na"}).get()
        # The populated bucket is skipped; the 4 empty types have no fresh key to
        # short-circuit on (compute bails before caching), so they re-attempt.
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["warmed"], 4)

    @mock.patch("warships.tasks.SHIP_PCT_WARM_PAUSE_SECONDS", 0)
    def test_second_realm_lock_prevents_concurrent_run(self):
        from warships.tasks import (
            warm_realm_ships_pct_task, _realm_ships_pct_warm_lock_key,
        )
        # Hold the per-realm lock; a concurrent run must no-op rather than double
        # the heavy load on the shared DB.
        cache.add(_realm_ships_pct_warm_lock_key("na"), "held", timeout=60)
        result = warm_realm_ships_pct_task.apply(kwargs={"realm": "na"}).get()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already-running")
