"""Tests for the fortnight ship leaderboard + top-player badge snapshot.

Covers `data.compute_ship_top_player_snapshot` (ranking, the per-player battle
floor, the per-ship population guard, tier scope, realm isolation, hidden
exclusion, the rolling 14-day window, idempotency), `get_player_ship_badges`
(badges = ranks 1..N only), `get_ship_leaderboard` + the `ship_leaderboard`
endpoint, and the task's env gate.
See agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    compute_ship_top_player_snapshot,
    get_player_ship_badges,
    get_ship_leaderboard,
)
from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    Ship,
    ShipTopPlayerSnapshot,
)
from warships.tasks import snapshot_ship_top_players_task


# Small thresholds so a handful of fixture rows exercise the guards.
BADGE_ENV = {
    "SHIP_BADGE_MIN_BATTLES": "10",
    "SHIP_BADGE_MIN_SHIP_POPULATION": "3",
    "SHIP_BADGE_TOP_N": "3",
    "SHIP_BADGE_LIST_SIZE": "50",
    "SHIP_BADGE_TIER": "10",
    "SHIP_BADGE_RETENTION_DAYS": "21",
}

SHIMA = 10      # T10
ZAO = 20        # T10
T9_SHIP = 99    # tier 9 — must be ignored


class ShipBadgeSnapshotTests(TestCase):
    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze",
                            nation="japan", ship_type="Destroyer", tier=10)
        Ship.objects.create(ship_id=ZAO, name="Zao",
                            nation="japan", ship_type="Cruiser", tier=10)
        Ship.objects.create(ship_id=T9_SHIP, name="Kitakaze",
                            nation="japan", ship_type="Destroyer", tier=9)
        self._next_pid = 1000

    def _player(self, name, realm="na", is_hidden=False):
        self._next_pid += 1
        return Player.objects.create(
            name=name, player_id=self._next_pid, realm=realm,
            is_hidden=is_hidden, pvp_battles=500,
        )

    def _event(self, player, ship_id, battles, wins, detected_days_ago=0):
        """One BattleEvent carrying the player's whole window total for a ship.

        Each player gets its own observation pair, so the per-pair unique
        constraint never collides across players.
        """
        from_obs = BattleObservation.objects.create(player=player, pvp_battles=0)
        to_obs = BattleObservation.objects.create(
            player=player, pvp_battles=battles)
        event = BattleEvent.objects.create(
            player=player, ship_id=ship_id, ship_name="x", mode="random",
            battles_delta=battles, wins_delta=wins,
            from_observation=from_obs, to_observation=to_obs,
        )
        if detected_days_ago:
            # auto_now_add stamps "now"; queryset .update bypasses it.
            BattleEvent.objects.filter(pk=event.pk).update(
                detected_at=timezone.now() - timedelta(days=detected_days_ago))
        return event

    def _run(self, realm="na"):
        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            return compute_ship_top_player_snapshot(realm=realm)

    def _badge_ranks(self, player):
        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            return [b["rank"] for b in get_player_ship_badges(player)]

    def test_ranks_by_win_rate_badges_are_top_three(self):
        # Shimakaze pool: 4 qualifiers + 1 sub-floor player.
        a = self._player("Ace")     # 90%
        b = self._player("Bravo")   # 80%
        c = self._player("Charlie")  # 70%
        d = self._player("Delta")   # 60% — ranked #4 (on the page, not a badge)
        e = self._player("Echo")    # below the battle floor
        self._event(a, SHIMA, battles=20, wins=18)
        self._event(b, SHIMA, battles=15, wins=12)
        self._event(c, SHIMA, battles=30, wins=21)
        self._event(d, SHIMA, battles=10, wins=6)
        self._event(e, SHIMA, battles=5, wins=5)  # 100% but only 5 battles

        result = self._run("na")

        # The full ranked list (page) has all 4 qualifiers; badges are top 3.
        self.assertEqual(result["ranked_rows"], 4)
        self.assertEqual(result["badges"], 3)
        rows = list(ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA)
                    .order_by("rank"))
        self.assertEqual([r.player_id for r in rows], [a.id, b.id, c.id, d.id])
        self.assertEqual([r.rank for r in rows], [1, 2, 3, 4])
        self.assertEqual(rows[0].ship_name, "Shimakaze")
        self.assertAlmostEqual(rows[0].win_rate, 90.0)
        # Badges only for ranks 1-3; #4 is on the page but holds no badge.
        self.assertEqual(self._badge_ranks(a), [1])
        self.assertEqual(self._badge_ranks(d), [])
        # Sub-floor player never ranked at all.
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(player=e).exists())

    def test_population_guard_suppresses_sparse_ship(self):
        # Only 2 qualifiers for Zao (< population floor of 3) → not ranked.
        for i in range(2):
            self._event(self._player(f"Zp{i}"), ZAO, battles=20, wins=10)
        for i in range(3):
            self._event(self._player(f"Sp{i}"), SHIMA, battles=20, wins=10 + i)

        result = self._run("na")

        self.assertEqual(result["ships_qualified"], 1)
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(ship_id=ZAO).exists())
        self.assertEqual(
            ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA).count(), 3)

    def test_realm_isolation(self):
        eu_ace = self._player("EuAce", realm="eu")
        self._event(eu_ace, SHIMA, battles=50, wins=50)
        for i in range(3):
            self._event(self._player(f"NaP{i}"), SHIMA, battles=20, wins=10 + i)

        self._run("na")

        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(player=eu_ace).exists())
        self.assertEqual(
            ShipTopPlayerSnapshot.objects.filter(realm="na").count(), 3)

    def test_hidden_player_excluded(self):
        hidden_ace = self._player("Ghost", is_hidden=True)
        self._event(hidden_ace, SHIMA, battles=50, wins=50)
        for i in range(3):
            self._event(self._player(f"NaP{i}"), SHIMA, battles=20, wins=10 + i)

        self._run("na")

        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(player=hidden_ace).exists())

    def test_non_t10_ignored(self):
        for i in range(5):
            self._event(self._player(f"T9p{i}"), T9_SHIP, battles=30, wins=20)

        result = self._run("na")

        self.assertEqual(result["ranked_rows"], 0)
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(ship_id=T9_SHIP).exists())

    def test_rolling_14d_window_excludes_older_events(self):
        # Battles 20 days ago fall outside the 14-day fortnight window.
        for i in range(3):
            self._event(self._player(f"Old{i}"), SHIMA,
                        battles=20, wins=10, detected_days_ago=20)

        result = self._run("na")

        self.assertEqual(result["ranked_rows"], 0)

    def test_rolling_14d_window_includes_recent_events(self):
        # 10 days ago is inside the fortnight window.
        for i in range(3):
            self._event(self._player(f"Recent{i}"), SHIMA,
                        battles=20, wins=10 + i, detected_days_ago=10)

        result = self._run("na")

        self.assertEqual(result["ranked_rows"], 3)

    def test_idempotent_rerun(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        self._run("na")
        first = ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA).count()
        self._run("na")
        second = ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA).count()

        self.assertEqual(first, 3)
        self.assertEqual(second, 3)

    def test_get_player_ship_badges_reads_latest(self):
        players = [self._player(f"P{i}") for i in range(3)]
        for i, p in enumerate(players):
            self._event(p, SHIMA, battles=20, wins=18 - i)
        self._run("na")

        badges = self._badge_ranks(players[0])
        self.assertEqual(badges, [1])
        # A player who earned nothing has no badges.
        self.assertEqual(self._badge_ranks(self._player("Nobody")), [])

    def test_get_ship_leaderboard_returns_ranked_players(self):
        ace = self._player("Ace")
        mid = self._player("Mid")
        low = self._player("Low")
        self._event(ace, SHIMA, battles=20, wins=18)   # 90%
        self._event(mid, SHIMA, battles=20, wins=14)   # 70%
        self._event(low, SHIMA, battles=20, wins=10)   # 50%
        self._run("na")

        board = get_ship_leaderboard("na", SHIMA)
        self.assertEqual(board["ship"]["name"], "Shimakaze")
        self.assertEqual(board["window_days"], 14)
        self.assertEqual([p["player_name"] for p in board["players"]],
                        ["Ace", "Mid", "Low"])
        self.assertEqual([p["rank"] for p in board["players"]], [1, 2, 3])
        self.assertAlmostEqual(board["players"][0]["win_rate"], 90.0)

    def test_get_ship_leaderboard_unknown_ship_returns_none(self):
        self.assertIsNone(get_ship_leaderboard("na", 1234567))

    def test_get_ship_leaderboard_empty_when_ship_not_ranked(self):
        # Below the population guard → no snapshot rows, but ship meta present.
        for i in range(2):
            self._event(self._player(f"Zp{i}"), ZAO, battles=20, wins=10)
        self._run("na")

        board = get_ship_leaderboard("na", ZAO)
        self.assertEqual(board["ship"]["name"], "Zao")
        self.assertEqual(board["players"], [])
        self.assertIsNone(board["captured_on"])

    def test_ship_leaderboard_endpoint(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=18 - i)
        self._run("na")

        response = self.client.get(f"/api/realm/na/ship/{SHIMA}/leaderboard/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ship"]["ship_id"], SHIMA)
        self.assertEqual(len(payload["players"]), 3)
        self.assertEqual(payload["players"][0]["rank"], 1)

    def test_ship_leaderboard_endpoint_unknown_ship_404(self):
        response = self.client.get("/api/realm/na/ship/7654321/leaderboard/")
        self.assertEqual(response.status_code, 404)

    def test_ship_leaderboard_endpoint_unknown_realm_404(self):
        response = self.client.get(f"/api/realm/xx/ship/{SHIMA}/leaderboard/")
        self.assertEqual(response.status_code, 404)

    def test_task_noop_when_flag_off(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "0"}
        with mock.patch.dict("os.environ", env, clear=False):
            result = snapshot_ship_top_players_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result.get("status"), "disabled")
        self.assertEqual(ShipTopPlayerSnapshot.objects.count(), 0)

    def test_task_runs_when_flag_on(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False):
            result = snapshot_ship_top_players_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result["badges"], 3)
        self.assertEqual(ShipTopPlayerSnapshot.objects.count(), 3)
