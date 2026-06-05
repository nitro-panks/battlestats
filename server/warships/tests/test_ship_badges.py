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
    "SHIP_BADGE_PRIOR_BATTLES": "30",
    "SHIP_BADGE_PRIOR_WR": "0.5",
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

    def _event(self, player, ship_id, battles, wins, detected_days_ago=0,
               damage=0, frags=0, survived=None):
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
            damage_delta=damage, frags_delta=frags, survived=survived,
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

    def test_composite_ranking_demotes_small_sample_streaks(self):
        # The volume-aware score (shrink WR toward 50% by 30 pseudo-battles)
        # must rank a high-volume 70% player ABOVE a 10-0 hot streak, even
        # though the streak's raw win rate is higher.
        #   streak : (10 + 30*0.5)/(10+30) = 0.625
        #   grinder: (70 + 30*0.5)/(100+30) = 0.654   <- wins despite lower raw WR
        #   mid    : (33 + 30*0.5)/(50+30)  = 0.600
        streak = self._player("Streak")    # 100% raw on 10 battles
        grinder = self._player("Grinder")  # 70% raw on 100 battles
        mid = self._player("Mid")          # 66% raw on 50 battles
        self._event(streak, SHIMA, battles=10, wins=10)
        self._event(grinder, SHIMA, battles=100, wins=70)
        self._event(mid, SHIMA, battles=50, wins=33)

        result = self._run("na")

        self.assertEqual(result["badges"], 3)
        rows = list(ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA)
                    .order_by("rank"))
        # Composite order: grinder, streak, mid — NOT raw-WR order.
        self.assertEqual([r.player_id for r in rows],
                        [grinder.id, streak.id, mid.id])
        self.assertEqual(self._badge_ranks(grinder), [1])
        # Display still uses the raw win rate, not the shrunk score.
        self.assertAlmostEqual(rows[0].win_rate, 70.0)

    def test_composite_ranking_breaks_winrate_ties_on_damage_and_kills(self):
        # Equal win rate + equal battles → the win-rate signal is flat, so the
        # damage and kills components decide the order (wins-led blend, but here
        # wins is a wash). Higher damage/kills ranks higher.
        carry = self._player("Carry")    # 60% — most dmg + kills
        avg = self._player("Average")    # 60% — middling dmg + kills
        passive = self._player("Passive")  # 60% — least dmg + kills
        self._event(carry, SHIMA, battles=50, wins=30, damage=4_000_000, frags=75)
        self._event(avg, SHIMA, battles=50, wins=30, damage=2_500_000, frags=50)
        self._event(passive, SHIMA, battles=50, wins=30, damage=1_500_000, frags=25)

        self._run("na")

        rows = list(ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA)
                    .order_by("rank"))
        self.assertEqual([r.player_id for r in rows],
                        [carry.id, avg.id, passive.id])
        # All three share the raw win rate; only the composite order differs.
        self.assertAlmostEqual(rows[0].win_rate, 60.0)

    def test_battle_floor_excludes_sub_floor_players(self):
        a = self._player("Ace")
        b = self._player("Bravo")
        c = self._player("Charlie")
        e = self._player("Echo")  # below the battle floor (5 < 10)
        self._event(a, SHIMA, battles=20, wins=18)
        self._event(b, SHIMA, battles=15, wins=12)
        self._event(c, SHIMA, battles=30, wins=21)
        self._event(e, SHIMA, battles=5, wins=5)

        result = self._run("na")

        self.assertEqual(result["ranked_rows"], 3)
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

    def test_non_t10_not_in_treemap_is_ignored(self):
        # A non-T10 ship that is NOT in the realm treemap top-25 isn't a target.
        # (These events are stamped "today", which compute_realm_top_ships's
        # previous-7-full-UTC-days window excludes, so the ship never enters the
        # treemap set here.)
        for i in range(5):
            self._event(self._player(f"T9p{i}"), T9_SHIP, battles=30, wins=20)

        result = self._run("na")

        self.assertEqual(result["ranked_rows"], 0)
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(ship_id=T9_SHIP).exists())

    def test_non_t10_in_treemap_is_included(self):
        # A non-T10 ship that IS among the most-played (in the treemap's
        # previous-7-UTC-day window) is unioned into the target set and ranked,
        # even though it isn't Tier 10. Events dated 2 days ago land in both the
        # treemap window and the 14-day snapshot window.
        for i in range(3):
            self._event(self._player(f"T9p{i}"), T9_SHIP, battles=30,
                        wins=20 + i, detected_days_ago=2)

        self._run("na")

        self.assertTrue(
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

    def test_badge_payload_derives_window_aggregates(self):
        from warships.data import get_player_ship_badges
        p = self._player("Aggs")
        ShipTopPlayerSnapshot.objects.create(
            captured_on=timezone.now().date(), realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=p, win_rate=70.0,
            battles=100, damage=6_240_000, frags=150, survived=68,
        )

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            badges = get_player_ship_badges(p)

        self.assertEqual(len(badges), 1)
        b = badges[0]
        self.assertEqual(b["avg_damage"], 62_400)        # 6_240_000 / 100
        self.assertEqual(b["window_days"], 14)
        # KDR / survival are intentionally not exposed (not accurately computable).
        self.assertNotIn("kdr", b)
        self.assertNotIn("survival_rate", b)

    def test_snapshot_persists_window_aggregates_from_events(self):
        # End-to-end: events carrying damage/frags/survived → run → stored row.
        # `survived` is the per-event boolean (1 if survived else 0), matching the
        # daily rollup writer's `survived_battles` convention (incremental_battles).
        ace = self._player("Ace")
        self._event(ace, SHIMA, battles=20, wins=15, damage=1_000_000,
                    frags=30, survived=True)
        for i in range(2):  # padding to clear the population guard (3)
            self._event(self._player(f"Pad{i}"), SHIMA, battles=20, wins=10,
                        damage=400_000, frags=10, survived=False)

        self._run("na")

        row = ShipTopPlayerSnapshot.objects.get(ship_id=SHIMA, player=ace)
        self.assertEqual(row.battles, 20)
        self.assertEqual(row.damage, 1_000_000)
        self.assertEqual(row.frags, 30)
        self.assertEqual(row.survived, 1)  # one event, survived=True

    def test_get_ship_leaderboard_returns_ranked_players(self):
        ace = self._player("Ace")
        mid = self._player("Mid")
        low = self._player("Low")
        self._event(ace, SHIMA, battles=20, wins=18, damage=1_000_000, frags=30)  # 90%, 50k avg, 1.5 kpb
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
        self.assertEqual(board["players"][0]["avg_damage"], 50_000)  # 1_000_000 / 20
        self.assertEqual(board["players"][0]["kills_per_battle"], 1.5)  # 30 / 20

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
