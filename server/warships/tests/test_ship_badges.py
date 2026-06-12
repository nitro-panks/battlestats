"""Tests for the fortnight ship leaderboard + top-player badge snapshot.

Covers `data.compute_ship_top_player_snapshot` (ranking, the per-player battle
floor, the per-ship population guard, tier scope, realm isolation, hidden
exclusion, the fixed-season window, idempotency), the fixed-season pivot (season
math, captured_on=season-start, `times_first` counts seasons, boundary gate,
backfill command), `get_player_ship_badges` (badges = ranks 1..N only),
`get_ship_leaderboard` + the `ship_leaderboard` endpoint, and the task's gates.
See agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md.
"""
import contextlib
from datetime import date, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    SHIP_SEASON_EPOCH,
    compute_ship_top_player_snapshot,
    current_season_index,
    get_player_ship_awards,
    get_player_ship_badges,
    get_players_ship_badges_bulk,
    get_ship_leaderboard,
    is_season_boundary,
    most_recent_completed_season,
    ship_season_bounds,
)
from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    Ship,
    ShipAward,
    ShipTopPlayerSnapshot,
)
from warships.tasks import snapshot_ship_top_players_task


@contextlib.contextmanager
def _season_boundary_now():
    """Make the boundary-gated task run and target a window covering "now"-stamped
    fixtures: force `is_season_boundary` True and point the default
    most-recently-completed season at [today-14d, today+1d)."""
    today = timezone.now().date()
    with mock.patch("warships.data.is_season_boundary", return_value=True), \
            mock.patch("warships.data.most_recent_completed_season",
                       return_value=(0, today - timedelta(days=14),
                                     today + timedelta(days=1))):
        yield


# Small thresholds so a handful of fixture rows exercise the guards.
BADGE_ENV = {
    "SHIP_BADGE_MIN_BATTLES": "10",
    "SHIP_BADGE_MIN_SHIP_POPULATION": "3",
    # CVs get a lower class-specific floor than the universal one above.
    "SHIP_BADGE_MIN_SHIP_POPULATION_CV": "2",
    "SHIP_BADGE_TOP_N": "3",
    "SHIP_BADGE_LIST_SIZE": "50",
    "SHIP_BADGE_TIER": "10",
    "SHIP_BADGE_RETENTION_DAYS": "21",
    "SHIP_BADGE_PRIOR_BATTLES": "30",
    "SHIP_BADGE_PRIOR_WR": "0.5",
    # Durable award ledger defaults OFF in prod (held during the coverage ramp);
    # the snapshot/award tests assert ledger writes, so enable it for them.
    "SHIP_AWARD_LEDGER_ENABLED": "1",
}

SHIMA = 10      # T10
ZAO = 20        # T10
CV = 30         # T10 carrier — exercises the class-specific population floor
T9_SHIP = 99    # tier 9 — ignored under T10-only scope; in scope for T8–10
T8_SHIP = 88    # tier 8


class ShipBadgeSnapshotTests(TestCase):
    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze",
                            nation="japan", ship_type="Destroyer", tier=10)
        Ship.objects.create(ship_id=ZAO, name="Zao",
                            nation="japan", ship_type="Cruiser", tier=10)
        Ship.objects.create(ship_id=T9_SHIP, name="Kitakaze",
                            nation="japan", ship_type="Destroyer", tier=9)
        Ship.objects.create(ship_id=CV, name="Shinano",
                            nation="japan", ship_type="AirCarrier", tier=10)
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
        # Drive compute over a fixed window covering the "now"-stamped fixtures
        # (the default targets the last *completed* season, which wouldn't include
        # events created at test time). captured_on=today keeps the latest-snapshot
        # read paths working.
        today = timezone.now().date()
        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            return compute_ship_top_player_snapshot(
                realm=realm,
                window_start=today - timedelta(days=14),
                window_end=today + timedelta(days=1),
                captured_on=today,
            )

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

    def test_carrier_uses_lower_population_floor(self):
        # CVs clear a lower, class-specific floor (CV=2) than the universal floor
        # (3): a carrier with 2 qualifiers ranks, while a cruiser (Zao) with the
        # same 2 qualifiers stays suppressed.
        for i in range(2):
            self._event(self._player(f"Cv{i}"), CV, battles=20, wins=10 + i)
        for i in range(2):
            self._event(self._player(f"Zp{i}"), ZAO, battles=20, wins=10 + i)

        result = self._run("na")

        self.assertEqual(result["ships_qualified"], 1)
        self.assertEqual(
            ShipTopPlayerSnapshot.objects.filter(ship_id=CV).count(), 2)
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(ship_id=ZAO).exists())

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
        # A non-T10 ship that IS among the most-played (returned by the realm
        # treemap) is unioned into the snapshot target set and ranked, even though
        # it isn't Tier 10. The treemap window is now the completed ship season
        # (not a rolling 7d, per the treemap-season-alignment change), so mock the
        # treemap to exercise the union mechanism directly, decoupled from its
        # window.
        for i in range(3):
            self._event(self._player(f"T9p{i}"), T9_SHIP, battles=30,
                        wins=20 + i, detected_days_ago=2)

        with mock.patch("warships.data.compute_realm_top_ships",
                        return_value={"ships": [{"ship_id": T9_SHIP}]}):
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

    def test_get_players_ship_badges_bulk_maps_pk_to_badges(self):
        # Bulk fetch (used by landing/clan lists) returns each player's badges
        # keyed by Player PK, matching the per-player function, with no row for
        # a player who holds no top spot.
        ace = self._player("Ace")
        mid = self._player("Mid")
        nobody = self._player("Nobody")  # below floor → no snapshot row
        self._event(ace, SHIMA, battles=20, wins=18)
        self._event(mid, SHIMA, battles=20, wins=14)
        for i in range(3):  # pad pool past the population guard
            self._event(self._player(f"Pad{i}"), SHIMA, battles=20, wins=10 + i)
        self._event(nobody, SHIMA, battles=5, wins=5)  # 5 < floor (10)
        self._run("na")

        bulk = get_players_ship_badges_bulk([ace.pk, mid.pk, nobody.pk])
        self.assertEqual(bulk[ace.pk], get_player_ship_badges(ace))
        self.assertEqual(bulk[mid.pk], get_player_ship_badges(mid))
        self.assertNotIn(nobody.pk, bulk)
        self.assertEqual(bulk[ace.pk][0]["ship_name"], "Shimakaze")

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
        with mock.patch.dict("os.environ", env, clear=False), _season_boundary_now(), \
                mock.patch(
                    "warships.tasks.materialize_landing_player_best_snapshots_task.apply_async"
                ):
            result = snapshot_ship_top_players_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result["badges"], 3)
        self.assertEqual(ShipTopPlayerSnapshot.objects.count(), 3)

    def test_task_noop_off_season_boundary(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch("warships.data.is_season_boundary", return_value=False):
            result = snapshot_ship_top_players_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result.get("status"), "skipped")
        self.assertEqual(result.get("reason"), "not-a-season-boundary")
        self.assertEqual(ShipTopPlayerSnapshot.objects.count(), 0)

    def test_completion_dispatches_landing_best_rematerialize(self):
        # A real snapshot run re-materializes this realm's landing Best-player
        # snapshots (on the background queue) so new badges surface promptly.
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False), _season_boundary_now(), \
                mock.patch(
                    "warships.tasks.materialize_landing_player_best_snapshots_task.apply_async"
                ) as dispatch:
            snapshot_ship_top_players_task.apply(kwargs={"realm": "na"}).get()

        dispatch.assert_called_once_with(
            kwargs={"realm": "na"}, queue="background")

    def test_disabled_run_does_not_dispatch_rematerialize(self):
        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "0"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch(
                    "warships.tasks.materialize_landing_player_best_snapshots_task.apply_async"
                ) as dispatch:
            snapshot_ship_top_players_task.apply(kwargs={"realm": "na"}).get()

        dispatch.assert_not_called()

    def test_lock_skip_does_not_dispatch_rematerialize(self):
        # When another snapshot holds the lock the task no-ops; nothing was
        # rewritten, so it must not trigger a re-materialize.
        from warships.tasks import _task_lock_key

        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)
        cache.add(_task_lock_key("snapshot_ship_top_players", "na"), "held")

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False), _season_boundary_now(), \
                mock.patch(
                    "warships.tasks.materialize_landing_player_best_snapshots_task.apply_async"
                ) as dispatch:
            result = snapshot_ship_top_players_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result.get("status"), "skipped")
        dispatch.assert_not_called()

    # --- fixed-season pivot --------------------------------------------------

    def _run_window(self, captured_on, realm="na"):
        """Run compute over a window covering the 'now' fixtures but stamp the
        snapshot/awards with an explicit `captured_on` (the season identity)."""
        today = timezone.now().date()
        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            return compute_ship_top_player_snapshot(
                realm=realm,
                window_start=today - timedelta(days=14),
                window_end=today + timedelta(days=1),
                captured_on=captured_on,
            )

    def test_captured_on_is_the_season_start(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)
        s0_start, _ = ship_season_bounds(0)  # 2026-05-11

        self._run_window(s0_start)

        self.assertTrue(ShipTopPlayerSnapshot.objects.exists())
        self.assertEqual(
            set(ShipTopPlayerSnapshot.objects.values_list("captured_on", flat=True)),
            {s0_start})
        self.assertEqual(
            set(ShipAward.objects.values_list("captured_on", flat=True)), {s0_start})

    def test_times_first_counts_distinct_seasons(self):
        # Same standings finalized for two consecutive seasons → the #1 holder's
        # ledger shows times_first == 2 (seasons held #1), not a per-run count.
        winner = self._player("Champ")
        self._event(winner, SHIMA, battles=40, wins=30)
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)
        s0_start, _ = ship_season_bounds(0)
        s1_start, _ = ship_season_bounds(1)

        self._run_window(s0_start)
        self._run_window(s1_start)

        awards = get_player_ship_awards(winner)
        shima = next(a for a in awards if a["ship_id"] == SHIMA)
        self.assertEqual(shima["times_first"], 2)
        self.assertEqual(shima["times_top3"], 2)
        self.assertEqual(shima["first_on"], s0_start.isoformat())
        self.assertEqual(shima["last_on"], s1_start.isoformat())
        # `seasons` lists each placement {captured_on, rank}, newest first — the
        # UI spells these out as WK<n>'YY for the Ship Honors panel.
        self.assertEqual(
            shima["seasons"],
            [{"captured_on": s1_start.isoformat(), "rank": 1},
             {"captured_on": s0_start.isoformat(), "rank": 1}])
        self.assertEqual(shima["tier"], 10)  # tier surfaced for the Ship Honors label

    def test_multi_tier_ranks_each_scoped_tier_independently(self):
        # With SHIP_BADGE_TIERS spanning 8–10, ships in each tier are ranked in
        # their own pool — no cross-tier comparison.
        Ship.objects.create(ship_id=T8_SHIP, name="Tirpitz", nation="germany",
                            ship_type="Battleship", tier=8)
        for sid in (T8_SHIP, T9_SHIP, SHIMA):
            for i in range(3):
                self._event(self._player(f"P{sid}_{i}"), sid, battles=20, wins=10 + i)
        today = timezone.now().date()
        env = {**BADGE_ENV, "SHIP_BADGE_TIERS": "8,9,10"}
        with mock.patch.dict("os.environ", env, clear=False):
            compute_ship_top_player_snapshot(
                realm="na", window_start=today - timedelta(days=14),
                window_end=today + timedelta(days=1), captured_on=today)

        self.assertEqual(
            set(ShipTopPlayerSnapshot.objects.values_list("ship_id", flat=True)),
            {T8_SHIP, T9_SHIP, SHIMA})

    def test_badges_carry_tier_and_order_tier_desc(self):
        # A player who is #1 in both a T9 and a T10 ship: the badges carry `tier`
        # and the T10 badge leads (most prestigious first).
        star = self._player("Star")
        self._event(star, SHIMA, battles=40, wins=38)    # T10 #1
        self._event(star, T9_SHIP, battles=40, wins=38)  # T9 #1
        for i in range(3):  # filler so both ships clear the population guard
            self._event(self._player(f"F{i}"), SHIMA, battles=20, wins=10)
            self._event(self._player(f"G{i}"), T9_SHIP, battles=20, wins=10)
        today = timezone.now().date()
        env = {**BADGE_ENV, "SHIP_BADGE_TIERS": "8,9,10"}
        with mock.patch.dict("os.environ", env, clear=False):
            compute_ship_top_player_snapshot(
                realm="na", window_start=today - timedelta(days=14),
                window_end=today + timedelta(days=1), captured_on=today)
            badges = get_player_ship_badges(star)
            bulk = get_players_ship_badges_bulk([star.pk])[star.pk]

        self.assertEqual([b["ship_id"] for b in badges], [SHIMA, T9_SHIP])
        self.assertEqual([b["tier"] for b in badges], [10, 9])
        # Bulk reader carries tier + the same tier-desc ordering.
        self.assertEqual([b["tier"] for b in bulk], [10, 9])

    def test_off_scope_treemap_ship_gets_board_not_badge(self):
        # A popular off-scope (T5) ship pulled in via the treemap union gets a
        # /ship board (snapshot rows) but NEVER a profile badge or award — the
        # excluded-tier guarantee the density study made.
        T5 = 55
        Ship.objects.create(ship_id=T5, name="Kamikaze", nation="japan",
                            ship_type="Destroyer", tier=5)
        champ = self._player("T5Champ")
        self._event(champ, T5, battles=40, wins=38)
        for i in range(3):
            self._event(self._player(f"H{i}"), T5, battles=20, wins=10)
        today = timezone.now().date()
        env = {**BADGE_ENV, "SHIP_BADGE_TIERS": "8,9,10"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch("warships.data.compute_realm_top_ships",
                           return_value={"ships": [{"ship_id": T5}]}):
            compute_ship_top_player_snapshot(
                realm="na", window_start=today - timedelta(days=14),
                window_end=today + timedelta(days=1), captured_on=today)
            badges = get_player_ship_badges(champ)

        self.assertTrue(ShipTopPlayerSnapshot.objects.filter(ship_id=T5).exists())  # board
        self.assertFalse(ShipAward.objects.filter(ship_id=T5).exists())             # no award (write-gated)
        self.assertEqual(badges, [])                                                # no badge (read-gated)

    def test_leaderboard_payload_carries_season_bounds(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)
        today = timezone.now().date()
        self._run_window(today)

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            lb = get_ship_leaderboard("na", SHIMA)

        self.assertEqual(lb["season_start"], today.isoformat())
        self.assertEqual(lb["season_end"], (today + timedelta(days=14)).isoformat())
        self.assertEqual(
            lb["next_window_open"],
            ship_season_bounds(current_season_index())[1].isoformat())


class ShipSeasonHelpersTests(TestCase):
    """Pure date math for the fixed 2-week seasons (epoch = Mon 11 May 2026)."""

    def test_epoch_and_bounds(self):
        self.assertEqual(SHIP_SEASON_EPOCH, date(2026, 5, 11))
        self.assertEqual(ship_season_bounds(0), (date(2026, 5, 11), date(2026, 5, 25)))
        self.assertEqual(ship_season_bounds(1)[0], date(2026, 5, 25))

    def test_current_index_and_completed(self):
        d = date(2026, 6, 5)  # mid W22-23 (season index 1)
        self.assertEqual(current_season_index(d), 1)
        idx, start, end = most_recent_completed_season(d)
        self.assertEqual((idx, start, end),
                         (0, date(2026, 5, 11), date(2026, 5, 25)))

    def test_is_season_boundary(self):
        self.assertTrue(is_season_boundary(date(2026, 5, 11)))   # season 0 start
        self.assertTrue(is_season_boundary(date(2026, 5, 25)))   # season 1 start
        self.assertFalse(is_season_boundary(date(2026, 5, 12)))  # mid-season
        self.assertFalse(is_season_boundary(date(2026, 5, 1)))   # before epoch


class MaterializeBestSnapshotWarmChainTests(TestCase):
    """The materialize task self-republishes the Redis Best-player payloads on
    success so a fresh snapshot reaches the live API without waiting for the
    independent landing warmer. `warm_after=False` opts out."""

    def setUp(self):
        cache.clear()

    def test_success_dispatches_players_scope_warm(self):
        from warships.tasks import (
            materialize_landing_player_best_snapshots_task,
        )

        with mock.patch(
            "warships.landing.materialize_landing_player_best_snapshots",
            return_value={"status": "completed", "realm": "na", "results": []},
        ), mock.patch(
            "warships.tasks.warm_landing_page_content_task.apply_async"
        ) as warm:
            materialize_landing_player_best_snapshots_task.apply(
                kwargs={"realm": "na"}).get()

        warm.assert_called_once_with(
            kwargs={"realm": "na", "scope": "players"},
            queue="background",
        )

    def test_warm_after_false_suppresses_warm(self):
        from warships.tasks import (
            materialize_landing_player_best_snapshots_task,
        )

        with mock.patch(
            "warships.landing.materialize_landing_player_best_snapshots",
            return_value={"status": "completed", "realm": "na", "results": []},
        ), mock.patch(
            "warships.tasks.warm_landing_page_content_task.apply_async"
        ) as warm:
            materialize_landing_player_best_snapshots_task.apply(
                kwargs={"realm": "na", "warm_after": False}).get()

        warm.assert_not_called()

    def test_lock_skip_does_not_dispatch_warm(self):
        from warships.tasks import (
            materialize_landing_player_best_snapshots_task,
            _landing_player_best_snapshot_refresh_lock_key,
        )

        cache.add(
            _landing_player_best_snapshot_refresh_lock_key("na"), "held")

        with mock.patch(
            "warships.landing.materialize_landing_player_best_snapshots"
        ) as inner, mock.patch(
            "warships.tasks.warm_landing_page_content_task.apply_async"
        ) as warm:
            result = materialize_landing_player_best_snapshots_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result.get("status"), "skipped")
        inner.assert_not_called()
        warm.assert_not_called()


class BackfillShipSeasonsCommandTests(TestCase):
    """`backfill_ship_seasons` walks completed seasons and (optionally) wipes the
    rolling-era rows first. Orchestration is tested with compute mocked so it does
    not depend on event timing."""

    def setUp(self):
        cache.clear()

    def test_wipe_then_walk_completed_seasons(self):
        from django.core.management import call_command

        # A stale rolling-era row (keyed by an arbitrary run-day) that --wipe clears.
        player = Player.objects.create(
            name="Stale", player_id=7777, realm="na", pvp_battles=500)
        ShipAward.objects.create(
            captured_on=date(2026, 6, 3), realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=player)

        cmd = "warships.management.commands.backfill_ship_seasons"
        with mock.patch(f"{cmd}.current_season_index", return_value=2), \
                mock.patch(f"{cmd}.compute_ship_top_player_snapshot",
                           return_value={"badges": 0, "ranked_rows": 0,
                                         "ships_qualified": 0, "ships_total": 0}) as comp, \
                mock.patch(
                    "warships.tasks.materialize_landing_player_best_snapshots_task.apply_async"
                ):
            call_command("backfill_ship_seasons", "--wipe", "--realms", "na")

        # Stale ledger row wiped (compute is mocked, writes nothing back).
        self.assertEqual(ShipAward.objects.count(), 0)
        # current index 2 → last completed season = 1 → seasons 0 and 1 computed.
        captured = sorted(c.kwargs["captured_on"] for c in comp.call_args_list)
        self.assertEqual(captured, [date(2026, 5, 11), date(2026, 5, 25)])
        for call in comp.call_args_list:
            self.assertEqual(call.kwargs["realm"], "na")
            # window matches the season the captured_on names.
            self.assertEqual(
                ship_season_bounds(
                    (call.kwargs["captured_on"] - SHIP_SEASON_EPOCH).days // 14),
                (call.kwargs["window_start"], call.kwargs["window_end"]))

    def test_backfill_rematerializes_landing_per_realm(self):
        # A direct ShipTopPlayerSnapshot rewrite must refresh the landing
        # Best-player snapshots (which bake in ship_badges) so the landing list and
        # the profile don't disagree on medal counts until the daily cron.
        from django.core.management import call_command

        cmd = "warships.management.commands.backfill_ship_seasons"
        with mock.patch(f"{cmd}.current_season_index", return_value=2), \
                mock.patch(f"{cmd}.compute_ship_top_player_snapshot",
                           return_value={"badges": 0}), \
                mock.patch(
                    "warships.tasks.materialize_landing_player_best_snapshots_task.apply_async"
                ) as dispatch:
            call_command("backfill_ship_seasons", "--realms", "na,eu")

        self.assertEqual(
            sorted(c.kwargs["kwargs"]["realm"] for c in dispatch.call_args_list),
            ["eu", "na"])
        for call in dispatch.call_args_list:
            self.assertEqual(call.kwargs["queue"], "background")

    def test_no_landing_refresh_flag_skips_dispatch(self):
        from django.core.management import call_command

        cmd = "warships.management.commands.backfill_ship_seasons"
        with mock.patch(f"{cmd}.current_season_index", return_value=2), \
                mock.patch(f"{cmd}.compute_ship_top_player_snapshot",
                           return_value={"badges": 0}), \
                mock.patch(
                    "warships.tasks.materialize_landing_player_best_snapshots_task.apply_async"
                ) as dispatch:
            call_command("backfill_ship_seasons", "--realms", "na",
                         "--no-landing-refresh")

        dispatch.assert_not_called()
