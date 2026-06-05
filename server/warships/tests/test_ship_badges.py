"""Tests for the weekly T10 top-player badge snapshot.

Covers `data.compute_ship_top_player_snapshot` (ranking, the per-player battle
floor, the per-ship population guard, tier scope, realm isolation, hidden
exclusion, idempotency, rolling-window exclusion) and the task's env gate.
See agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from warships.data import (
    compute_ship_top_player_snapshot,
    get_player_ship_badges,
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
    "SHIP_BADGE_TIER": "10",
    "SHIP_BADGE_RETENTION_DAYS": "21",
}

SHIMA = 10      # T10
ZAO = 20        # T10
T9_SHIP = 99    # tier 9 — must be ignored


class ShipBadgeSnapshotTests(TestCase):
    def setUp(self):
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

    def test_top3_by_win_rate_with_floor(self):
        # Shimakaze pool: 4 qualifiers + 1 sub-floor player.
        a = self._player("Ace")     # 90%
        b = self._player("Bravo")   # 80%
        c = self._player("Charlie")  # 70%
        d = self._player("Delta")   # 60% — qualifies but ranks 4th (dropped)
        e = self._player("Echo")    # below the battle floor
        self._event(a, SHIMA, battles=20, wins=18)
        self._event(b, SHIMA, battles=15, wins=12)
        self._event(c, SHIMA, battles=30, wins=21)
        self._event(d, SHIMA, battles=10, wins=6)
        self._event(e, SHIMA, battles=5, wins=5)  # 100% but only 5 battles

        result = self._run("na")

        self.assertEqual(result["badges"], 3)
        rows = list(ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA)
                    .order_by("rank"))
        self.assertEqual([r.player_id for r in rows], [a.id, b.id, c.id])
        self.assertEqual([r.rank for r in rows], [1, 2, 3])
        self.assertAlmostEqual(rows[0].win_rate, 90.0)
        self.assertEqual(rows[0].battles, 20)
        self.assertEqual(rows[0].ship_name, "Shimakaze")
        # Sub-floor player never minted.
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(player=e).exists())
        # Qualifying-but-4th player dropped by top-N.
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(player=d).exists())

    def test_population_guard_suppresses_sparse_ship(self):
        # Only 2 qualifiers for Zao (< population floor of 3) → no badge.
        for i in range(2):
            self._event(self._player(f"Zp{i}"), ZAO, battles=20, wins=10)
        # And a fully-qualifying Shimakaze pool to prove the run did work.
        for i in range(3):
            self._event(self._player(f"Sp{i}"), SHIMA, battles=20, wins=10 + i)

        result = self._run("na")

        self.assertEqual(result["ships_qualified"], 1)
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(ship_id=ZAO).exists())
        self.assertEqual(
            ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA).count(), 3)

    def test_realm_isolation(self):
        # EU ace with the best WR must not appear in the NA snapshot.
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

        self.assertEqual(result["badges"], 0)
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(ship_id=T9_SHIP).exists())

    def test_rolling_window_excludes_old_events(self):
        # All Shimakaze battles happened 10 days ago → outside the 7d window.
        for i in range(3):
            self._event(self._player(f"Old{i}"), SHIMA,
                        battles=20, wins=10, detected_days_ago=10)

        result = self._run("na")

        self.assertEqual(result["badges"], 0)

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

        top = players[0]
        badges = get_player_ship_badges(top)
        self.assertEqual(len(badges), 1)
        self.assertEqual(badges[0]["ship_id"], SHIMA)
        self.assertEqual(badges[0]["rank"], 1)
        self.assertEqual(badges[0]["ship_name"], "Shimakaze")
        # A player who earned nothing has no badges.
        self.assertEqual(get_player_ship_badges(self._player("Nobody")), [])

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
