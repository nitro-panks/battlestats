"""Tests for the rolling nightly ship leaderboard + top-player badge snapshot.

Covers `data.compute_ship_top_player_snapshot` (ranking, the per-player battle
floor, the per-ship population guard, tier scope, realm isolation, hidden
exclusion, the trailing rolling window, idempotency, captured_on=run-date,
displaced-holder cache invalidation), `get_player_ship_badges` (badges = ranks
1..N only, worn while held), `get_ship_leaderboard` + the `ship_leaderboard`
endpoint, and the task's enable gate.
See agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md.
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    SHIP_LEADERBOARD_WINDOW_DAYS,
    compute_ship_top_player_snapshot,
    get_player_ship_badges,
    get_players_ship_badges_bulk,
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
    # CVs and subs each get a lower class-specific floor than the universal one.
    "SHIP_BADGE_MIN_SHIP_POPULATION_CV": "2",
    "SHIP_BADGE_MIN_SHIP_POPULATION_SUB": "2",
    "SHIP_BADGE_TOP_N": "3",
    "SHIP_BADGE_LIST_SIZE": "50",
    "SHIP_BADGE_TIER": "10",
    "SHIP_BADGE_RETENTION_DAYS": "21",
    "SHIP_BADGE_PRIOR_BATTLES": "30",
    "SHIP_BADGE_PRIOR_WR": "0.5",
}

SHIMA = 10      # T10
ZAO = 20        # T10
CV = 30         # T10 carrier — exercises the class-specific population floor
SUB = 40        # T10 submarine — exercises the class-specific population floor
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
        Ship.objects.create(ship_id=SUB, name="Gato",
                            nation="usa", ship_type="Submarine", tier=10)
        self._next_pid = 1000

    def _player(self, name, realm="na", is_hidden=False):
        self._next_pid += 1
        return Player.objects.create(
            name=name, player_id=self._next_pid, realm=realm,
            is_hidden=is_hidden, pvp_battles=500,
        )

    def _event(self, player, ship_id, battles, wins, detected_days_ago=1,
               damage=0, frags=0, survived=None):
        """One BattleEvent carrying the player's whole window total for a ship.

        Each player gets its own observation pair, so the per-pair unique
        constraint never collides across players. Default `detected_days_ago=1`
        (yesterday) so events land inside BOTH the explicit-window `_run` and the
        task's default trailing window `[today-30d, today)` (whose end is exclusive
        at today-midnight, excluding events stamped at the current time).
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
                window_start=today - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS),
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

    def test_win_rate_gate_excludes_sub_threshold_damage_farmer(self):
        # The essential_HT case: a losing record (sub-50% WR) with elite damage +
        # kills is dropped from the board entirely by the hard win-rate gate
        # (default 50). The ship stays ranked — the population guard counts the full
        # pool *before* the gate — and its >=50% players are unaffected. A player at
        # exactly 50% (break-even) is kept.
        farmer = self._player("Farmer")   # 40% WR, top damage + kills
        winner = self._player("Winner")   # 70% WR
        steady = self._player("Steady")   # 55% WR
        even = self._player("Even")       # exactly 50% — kept
        self._event(farmer, SHIMA, battles=30, wins=12,
                    damage=4_000_000, frags=75)
        self._event(winner, SHIMA, battles=30, wins=21,
                    damage=1_500_000, frags=30)
        self._event(steady, SHIMA, battles=20, wins=11,
                    damage=1_000_000, frags=20)
        self._event(even, SHIMA, battles=20, wins=10,
                    damage=1_000_000, frags=20)

        # _run uses BADGE_ENV, which omits SHIP_BADGE_MIN_WIN_RATE → code default 50.
        self._run("na")

        ranked_ids = set(
            ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA)
            .values_list("player_id", flat=True))
        self.assertNotIn(farmer.id, ranked_ids)  # gated out despite top damage
        self.assertEqual(ranked_ids, {winner.id, steady.id, even.id})

    def test_win_rate_gate_disabled_admits_sub_threshold_player(self):
        # With the gate disabled (0) the same sub-50% farmer is admitted on
        # composite score — confirming the gate, not some other filter, removes it.
        farmer = self._player("Farmer")
        winner = self._player("Winner")
        steady = self._player("Steady")
        self._event(farmer, SHIMA, battles=30, wins=12,
                    damage=4_000_000, frags=75)
        self._event(winner, SHIMA, battles=30, wins=21,
                    damage=1_500_000, frags=30)
        self._event(steady, SHIMA, battles=20, wins=11,
                    damage=1_000_000, frags=20)

        today = timezone.now().date()
        with mock.patch.dict("os.environ",
                             {**BADGE_ENV, "SHIP_BADGE_MIN_WIN_RATE": "0"},
                             clear=False):
            compute_ship_top_player_snapshot(
                realm="na",
                window_start=today - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS),
                window_end=today + timedelta(days=1),
                captured_on=today,
            )

        self.assertTrue(
            ShipTopPlayerSnapshot.objects.filter(
                ship_id=SHIMA, player=farmer).exists())

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

    def test_submarine_uses_lower_population_floor(self):
        # Subs clear a lower, class-specific floor (SUB=2) than the universal
        # floor (3): a submarine with 2 qualifiers ranks, while a cruiser (Zao)
        # with the same 2 qualifiers stays suppressed.
        for i in range(2):
            self._event(self._player(f"Sub{i}"), SUB, battles=20, wins=10 + i)
        for i in range(2):
            self._event(self._player(f"Zp{i}"), ZAO, battles=20, wins=10 + i)

        result = self._run("na")

        self.assertEqual(result["ships_qualified"], 1)
        self.assertEqual(
            ShipTopPlayerSnapshot.objects.filter(ship_id=SUB).count(), 2)
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
        # The treemap now aggregates the same rolling window as the snapshot, so a
        # recent-but-out-of-tier ship could enter it; mock the treemap empty to
        # isolate the "not a target" path, decoupled from the treemap's window.
        for i in range(5):
            self._event(self._player(f"T9p{i}"), T9_SHIP, battles=30, wins=20)

        with mock.patch("warships.data.compute_realm_top_ships",
                        return_value={"ships": []}):
            result = self._run("na")

        self.assertEqual(result["ranked_rows"], 0)
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(ship_id=T9_SHIP).exists())

    def test_non_t10_in_treemap_is_included(self):
        # A non-T10 ship that IS among the most-played (returned by the realm
        # treemap) is unioned into the snapshot target set and ranked, even though
        # it isn't Tier 10. Mock the treemap to exercise the union mechanism
        # directly, decoupled from its window.
        for i in range(3):
            self._event(self._player(f"T9p{i}"), T9_SHIP, battles=30,
                        wins=20 + i, detected_days_ago=2)

        with mock.patch("warships.data.compute_realm_top_ships",
                        return_value={"ships": [{"ship_id": T9_SHIP}]}):
            self._run("na")

        self.assertTrue(
            ShipTopPlayerSnapshot.objects.filter(ship_id=T9_SHIP).exists())

    def test_rolling_window_excludes_older_events(self):
        # Battles beyond the trailing window fall outside it.
        for i in range(3):
            self._event(self._player(f"Old{i}"), SHIMA,
                        battles=20, wins=10,
                        detected_days_ago=SHIP_LEADERBOARD_WINDOW_DAYS + 6)

        result = self._run("na")

        self.assertEqual(result["ranked_rows"], 0)

    def test_rolling_window_includes_recent_events(self):
        # 10 days ago is inside the trailing rolling window.
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
        self.assertEqual(b["window_days"], SHIP_LEADERBOARD_WINDOW_DAYS)
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
        # No code populated for this fixture ship -> field present but null,
        # so the frontend hides the Ship Tool link.
        self.assertIsNone(board["ship"]["shiptool_code"])
        self.assertEqual(board["window_days"], SHIP_LEADERBOARD_WINDOW_DAYS)
        self.assertEqual([p["player_name"] for p in board["players"]],
                        ["Ace", "Mid", "Low"])
        self.assertEqual([p["rank"] for p in board["players"]], [1, 2, 3])
        self.assertAlmostEqual(board["players"][0]["win_rate"], 90.0)
        self.assertEqual(board["players"][0]["avg_damage"], 50_000)  # 1_000_000 / 20
        self.assertEqual(board["players"][0]["kills_per_battle"], 1.5)  # 30 / 20

    def test_get_ship_leaderboard_surfaces_shiptool_code(self):
        # A populated shiptool_code is passed through verbatim so the frontend
        # can deep-link to shiptool.st/params?S=<code>.
        Ship.objects.filter(ship_id=SHIMA).update(shiptool_code="JD110")
        self._event(self._player("Ace"), SHIMA, battles=20, wins=18)
        for i in range(3):
            self._event(self._player(f"Pad{i}"), SHIMA, battles=20, wins=10 + i)
        self._run("na")

        board = get_ship_leaderboard("na", SHIMA)
        self.assertEqual(board["ship"]["shiptool_code"], "JD110")

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

    def test_badge_dropped_when_absent_from_current_generation(self):
        # Regression (the "displaced #1 still wears the badge" bug): a player who
        # held #1 in an OLDER snapshot generation but is ABSENT from the ship's
        # CURRENT generation must NOT wear the badge. Badges anchor on the realm's
        # latest captured_on (the same the /ship board reads), not the player's own
        # most-recent row — which lingers up to SHIP_BADGE_RETENTION_DAYS after they
        # fall off the board. Keying on player-latest would show a stale "1st place"
        # the live board has already reassigned.
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        dropped = self._player("Dropped")
        holder = self._player("Holder")
        # Yesterday's generation: `dropped` held Shimakaze #1 (thin 19-battle run).
        ShipTopPlayerSnapshot.objects.create(
            captured_on=yesterday, realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=dropped, win_rate=94.0,
            battles=19, damage=1_000_000,
        )
        # Today's (current) generation: `holder` is #1; `dropped` has no row.
        ShipTopPlayerSnapshot.objects.create(
            captured_on=today, realm="na", ship_id=SHIMA, ship_name="Shimakaze",
            rank=1, player=holder, win_rate=67.0, battles=83, damage=5_000_000,
        )

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            self.assertEqual(get_player_ship_badges(dropped), [])
            self.assertEqual(self._badge_ranks(holder), [1])
            # Bulk variant agrees even when the dropped player is queried too — its
            # anchor is the realm-current generation over ALL rows, so a candidate
            # who fell off can't drag it back to their stale generation.
            bulk = get_players_ship_badges_bulk([dropped.pk, holder.pk])
            self.assertNotIn(dropped.pk, bulk)
            self.assertEqual(bulk[holder.pk], get_player_ship_badges(holder))

    def test_badge_dropped_when_player_absent_from_all_current_boards(self):
        # Same failure isolated to the bulk path when EVERY queried candidate has
        # only stale rows: the anchor must still be the realm's current generation
        # (computed over all rows, including boards the candidates aren't on), so a
        # lone dropped player returns no badge rather than their own stale latest.
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        dropped = self._player("Dropped")
        holder = self._player("Holder")
        ShipTopPlayerSnapshot.objects.create(
            captured_on=yesterday, realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=dropped, win_rate=94.0,
            battles=19, damage=1_000_000,
        )
        ShipTopPlayerSnapshot.objects.create(
            captured_on=today, realm="na", ship_id=SHIMA, ship_name="Shimakaze",
            rank=1, player=holder, win_rate=67.0, battles=83, damage=5_000_000,
        )

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            self.assertEqual(get_players_ship_badges_bulk([dropped.pk]), {})

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
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch("warships.tasks.queue_realm_top_ships_warm"):
            result = snapshot_ship_top_players_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result["badges"], 3)
        self.assertEqual(ShipTopPlayerSnapshot.objects.count(), 3)

    def test_completion_dispatches_treemap_warm(self):
        # A real snapshot run warms this realm's treemap + tier/type caches (on
        # the background queue) so the rotated window serves warm.
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch(
                    "warships.tasks.queue_realm_top_ships_warm"
                ) as warm:
            snapshot_ship_top_players_task.apply(kwargs={"realm": "na"}).get()

        warm.assert_called_once_with("na")

    def test_completion_dispatches_ship_pop_bulk_warm(self):
        # A real snapshot run also chains the bulk avg-damage baseline warm —
        # the day-scoped baseline keys rotated cold at UTC midnight, and the
        # snapshot (02:00+ UTC) is the first post-rotation hook, so one
        # grouped scan re-warms every ship's damage-treemap baseline for the
        # day (the per-ship lazy warm stays as the gap fallback).
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch("warships.tasks.queue_realm_top_ships_warm"), \
                mock.patch(
                    "warships.tasks.warm_all_ship_pop_avg_damage_task"
                ) as bulk:
            snapshot_ship_top_players_task.apply(kwargs={"realm": "na"}).get()

        bulk.apply_async.assert_called_once_with(
            args=["na"], queue="background")

    def test_disabled_run_does_not_dispatch_treemap_warm(self):
        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "0"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch(
                    "warships.tasks.queue_realm_top_ships_warm"
                ) as warm:
            snapshot_ship_top_players_task.apply(kwargs={"realm": "na"}).get()

        warm.assert_not_called()

    def test_lock_skip_does_not_dispatch_treemap_warm(self):
        # When another snapshot holds the lock the task no-ops; nothing was
        # rewritten, so it must not trigger a downstream warm.
        from warships.tasks import _task_lock_key

        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)
        cache.add(_task_lock_key("snapshot_ship_top_players", "na"), "held")

        env = {**BADGE_ENV, "SHIP_BADGE_SNAPSHOT_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch(
                    "warships.tasks.queue_realm_top_ships_warm"
                ) as warm:
            result = snapshot_ship_top_players_task.apply(
                kwargs={"realm": "na"}).get()

        self.assertEqual(result.get("status"), "skipped")
        warm.assert_not_called()

    # --- rolling nightly recompute -------------------------------------------

    def test_default_window_is_trailing_and_captured_on_is_run_date(self):
        # With no explicit window, compute uses the trailing window ending today
        # and stamps captured_on = today (the run date / snapshot identity).
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i,
                        detected_days_ago=2)
        today = timezone.now().date()

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            result = compute_ship_top_player_snapshot(realm="na")

        self.assertEqual(result["captured_on"], today)
        self.assertEqual(result["ranked_rows"], 3)
        self.assertEqual(
            set(ShipTopPlayerSnapshot.objects.values_list("captured_on", flat=True)),
            {today})

    def test_displaced_holder_cache_invalidated(self):
        # A player who held a top-3 badge on the previous run but is absent from
        # tonight's board must have their cached detail payload invalidated, so the
        # stale badge drops immediately rather than lingering until TTL.
        gone = self._player("Gone")
        yesterday = timezone.now().date() - timedelta(days=1)
        ShipTopPlayerSnapshot.objects.create(
            captured_on=yesterday, realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=gone, win_rate=90.0, battles=50)
        # Tonight's pool is three different players; `gone` has no events.
        for i in range(3):
            self._event(self._player(f"New{i}"), SHIMA, battles=20, wins=10 + i)

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False), \
                mock.patch("warships.data.invalidate_player_detail_cache") as inv:
            self._run("na")  # captured_on=today; prev run = yesterday

        invalidated = {c.args[0] for c in inv.call_args_list}
        self.assertIn(gone.player_id, invalidated)
        # `gone` is no longer on the board.
        self.assertFalse(
            ShipTopPlayerSnapshot.objects.filter(
                player=gone, captured_on=timezone.now().date()).exists())

    def test_hidden_after_snapshot_excluded_from_reads(self):
        # A player public at snapshot time who LATER sets their profile to hidden
        # must drop off the board + badges + bulk reads immediately, even though
        # the precomputed snapshot row still exists (read-time is_hidden filter).
        champ = self._player("Champ")
        self._event(champ, SHIMA, battles=40, wins=38)
        for i in range(3):
            self._event(self._player(f"Pad{i}"), SHIMA, battles=20, wins=10 + i)
        self._run("na")

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            self.assertTrue(get_player_ship_badges(champ))  # ranked while public
            self.assertIn(
                "Champ",
                [p["player_name"] for p in get_ship_leaderboard("na", SHIMA)["players"]])

        champ.is_hidden = True
        champ.save(update_fields=["is_hidden"])  # snapshot row untouched

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            self.assertEqual(get_player_ship_badges(champ), [])
            board = get_ship_leaderboard("na", SHIMA)
            self.assertNotIn(
                "Champ", [p["player_name"] for p in board["players"]])
            self.assertNotIn(champ.pk, get_players_ship_badges_bulk([champ.pk]))

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
                realm="na", window_start=today - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS),
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
                realm="na", window_start=today - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS),
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
                realm="na", window_start=today - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS),
                window_end=today + timedelta(days=1), captured_on=today)
            badges = get_player_ship_badges(champ)

        self.assertTrue(ShipTopPlayerSnapshot.objects.filter(ship_id=T5).exists())  # board
        self.assertEqual(badges, [])                                                # no badge (read-gated)

    def test_leaderboard_payload_is_rolling_not_seasonal(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)
        today = timezone.now().date()
        self._run("na")  # captured_on=today

        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            lb = get_ship_leaderboard("na", SHIMA)

        self.assertEqual(lb["captured_on"], today.isoformat())
        self.assertEqual(lb["window_days"], SHIP_LEADERBOARD_WINDOW_DAYS)
        self.assertEqual(lb["window_start"], (today - timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS)).isoformat())
        # No fixed-season framing under the rolling model.
        self.assertNotIn("season_start", lb)
        self.assertNotIn("season_end", lb)
        self.assertNotIn("next_window_open", lb)
