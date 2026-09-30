"""Tests for the ShipStats combat-profile endpoint's off-request-thread read path.

`_ship_population_brackets_30d` is a per-(ship, player) aggregation over
`PlayerDailyShipStats` joined to `Player`. Measured at **36s** on prod for a
popular T10 (`EXPLAIN ANALYZE`, 2026-08-12): the `ship_id`-only index forces a
scan of the ship's whole history, then a nested loop probes `warships_player`
once per surviving row. That is over the 25s `GUNICORN_TIMEOUT_SECONDS`, so the
worker was SIGABRT'd and the endpoint returned a 500 with an empty body — the
modal hard-failed. Four occurrences in the 14 days to 2026-08-12.

These tests pin the fix: the request thread NEVER computes the aggregation. It
serves a durable `:published` last-good copy when there is one, otherwise a
`pending` stub, and queues a single background warm either way. Mirrors the
ships-by-pct percentile bucket idiom (`test_realm_ships_by_tier_type`).
"""

from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    SHIP_COMBAT_WINDOW_DAYS,
    _ship_combat_pop_fresh_cache_key,
    _ship_population_brackets_30d,
    compute_ship_combat_comparison,
)
from warships.models import Player, PlayerDailyShipStats, Ship

SHIMA = 4282267344  # T10 Destroyer


class ShipCombatStatsPendingTests(TestCase):
    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        self.player = Player.objects.create(
            name="combat_bench", player_id=990001, realm="na",
            pvp_battles=1000, pvp_ratio=55.0)
        # A second account so the population is non-trivial and the brackets
        # have someone to rank.
        self.other = Player.objects.create(
            name="combat_pop", player_id=990002, realm="na",
            pvp_battles=800, pvp_ratio=48.0)
        today = timezone.now().date()
        for player, battles, damage in ((self.player, 20, 1_400_000),
                                        (self.other, 30, 1_500_000)):
            PlayerDailyShipStats.objects.create(
                player=player, ship_id=SHIMA, mode="random",
                date=today - timedelta(days=1),
                battles=battles, wins=battles // 2, losses=battles // 2,
                frags=battles, damage=damage, xp=battles * 1000,
                survived_battles=battles // 2,
                main_shots=battles * 10, main_hits=battles * 5,
            )

    def _warm(self):
        """Run the heavy compute exactly as the background warm task does."""
        return _ship_population_brackets_30d(
            SHIMA, "na", SHIP_COMBAT_WINDOW_DAYS, use_cache=False)

    # ── read path ────────────────────────────────────────────────────────────

    def test_cold_read_returns_none_and_queues_one_warm(self):
        # The request thread must not run the 36s aggregation. Cold, with no
        # published copy, the helper reports "no data yet" and queues one warm.
        with mock.patch("warships.tasks.queue_ship_combat_pop_warm") as q:
            brackets = _ship_population_brackets_30d(
                SHIMA, "na", SHIP_COMBAT_WINDOW_DAYS, use_cache=True)
        self.assertIsNone(brackets)
        q.assert_called_once()

    def test_cold_read_does_not_touch_the_database(self):
        # The strongest form of the guarantee: on the cold read path the heavy
        # queryset is never evaluated.
        with mock.patch("warships.tasks.queue_ship_combat_pop_warm"), \
                self.assertNumQueries(0):
            _ship_population_brackets_30d(
                SHIMA, "na", SHIP_COMBAT_WINDOW_DAYS, use_cache=True)

    def test_warm_then_read_serves_ready(self):
        self._warm()
        with mock.patch("warships.tasks.queue_ship_combat_pop_warm") as q:
            brackets = _ship_population_brackets_30d(
                SHIMA, "na", SHIP_COMBAT_WINDOW_DAYS, use_cache=True)
        self.assertIsNotNone(brackets)
        self.assertEqual(brackets["all"]["players"], 2)
        self.assertEqual(brackets["all"]["battles"], 50)
        q.assert_not_called()

    def test_cold_read_serves_published_last_good_and_queues_warm(self):
        # The fresh key is date-scoped, so it rotates at UTC midnight. Without a
        # durable fallback EVERY ship's first daily viewer would get `pending`.
        self._warm()
        cache.delete(_ship_combat_pop_fresh_cache_key(
            "na", SHIMA, SHIP_COMBAT_WINDOW_DAYS))
        with mock.patch("warships.tasks.queue_ship_combat_pop_warm") as q:
            brackets = _ship_population_brackets_30d(
                SHIMA, "na", SHIP_COMBAT_WINDOW_DAYS, use_cache=True)
        self.assertIsNotNone(brackets)
        self.assertEqual(brackets["all"]["battles"], 50)
        q.assert_called_once()

    # ── payload ──────────────────────────────────────────────────────────────

    def test_comparison_payload_is_pending_when_population_cold(self):
        with mock.patch("warships.tasks.queue_ship_combat_pop_warm"):
            payload = compute_ship_combat_comparison(self.player, SHIMA, "na")
        self.assertTrue(payload["pending"])
        self.assertEqual(payload["clusters"], [])
        # Identity still resolves so the modal can render its header while it
        # polls, rather than showing "Ship <id>".
        self.assertEqual(payload["ship_name"], "Shimakaze")
        self.assertEqual(payload["ship_id"], SHIMA)
        self.assertEqual(payload["window_days"], SHIP_COMBAT_WINDOW_DAYS)

    def test_comparison_payload_ready_after_warm(self):
        self._warm()
        payload = compute_ship_combat_comparison(self.player, SHIMA, "na")
        self.assertNotIn("pending", payload)
        self.assertTrue(len(payload["clusters"]) > 0)

    # ── view ─────────────────────────────────────────────────────────────────

    def test_view_cold_sets_pending_header(self):
        with mock.patch("warships.tasks.queue_ship_combat_pop_warm"):
            resp = self.client.get(
                f"/api/player/combat_bench/ship/{SHIMA}/combat-stats?realm=na")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Ship-Combat-Pending"], "true")
        self.assertEqual(resp.json()["clusters"], [])

    def test_view_warm_serves_ready_without_pending_header(self):
        self._warm()
        resp = self.client.get(
            f"/api/player/combat_bench/ship/{SHIMA}/combat-stats?realm=na")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("X-Ship-Combat-Pending", resp)
        self.assertTrue(len(resp.json()["clusters"]) > 0)


class ShipCombatPopWarmTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze", nation="japan",
                            ship_type="Destroyer", tier=10)
        player = Player.objects.create(
            name="warm_bench", player_id=990003, realm="na",
            pvp_battles=1000, pvp_ratio=55.0)
        PlayerDailyShipStats.objects.create(
            player=player, ship_id=SHIMA, mode="random",
            date=timezone.now().date() - timedelta(days=1),
            battles=10, wins=5, losses=5, frags=10, damage=700_000, xp=10_000)

    def test_warm_task_fills_the_fresh_key(self):
        from warships.tasks import warm_ship_combat_pop_task

        result = warm_ship_combat_pop_task(
            ship_id=SHIMA, realm="na", window_days=SHIP_COMBAT_WINDOW_DAYS)
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(cache.get(_ship_combat_pop_fresh_cache_key(
            "na", SHIMA, SHIP_COMBAT_WINDOW_DAYS)))

    def test_concurrent_warm_is_skipped_by_the_lock(self):
        from warships.tasks import (
            _ship_combat_pop_warm_lock_key, warm_ship_combat_pop_task,
        )

        cache.add(_ship_combat_pop_warm_lock_key(
            "na", SHIMA, SHIP_COMBAT_WINDOW_DAYS), "held", timeout=300)
        result = warm_ship_combat_pop_task(
            ship_id=SHIMA, realm="na", window_days=SHIP_COMBAT_WINDOW_DAYS)
        self.assertEqual(result["status"], "skipped")

    def test_queue_helper_coalesces_a_burst_into_one_dispatch(self):
        from warships.tasks import queue_ship_combat_pop_warm

        with mock.patch("warships.tasks.warm_ship_combat_pop_task.apply_async") as d:
            first = queue_ship_combat_pop_warm(
                SHIMA, "na", SHIP_COMBAT_WINDOW_DAYS)
            second = queue_ship_combat_pop_warm(
                SHIMA, "na", SHIP_COMBAT_WINDOW_DAYS)
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "skipped")
        d.assert_called_once()
