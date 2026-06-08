"""Tests for the durable ship-award ledger (Phase 1).

Covers the ledger write (idempotent per day, append-only across days) in
`compute_ship_top_player_snapshot`, and the `get_player_ship_awards` career
aggregate (times_first / times_top3 / best_rank / current_rank / first_on /
last_on), including the vacation case (current_rank None).
See agents/runbooks/runbook-ship-award-ledger-2026-06-05.md.
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import (
    compute_ship_top_player_snapshot,
    get_player_ship_awards,
)
from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    Ship,
    ShipAward,
    ShipTopPlayerSnapshot,
)

BADGE_ENV = {
    "SHIP_BADGE_MIN_BATTLES": "10",
    "SHIP_BADGE_MIN_SHIP_POPULATION": "3",
    "SHIP_BADGE_TOP_N": "3",
    "SHIP_BADGE_LIST_SIZE": "50",
    "SHIP_BADGE_TIER": "10",
    "SHIP_BADGE_RETENTION_DAYS": "21",
    "SHIP_BADGE_PRIOR_BATTLES": "30",
    "SHIP_BADGE_PRIOR_WR": "0.5",
    # Durable award ledger defaults OFF in prod (held during the coverage ramp);
    # this suite asserts ledger writes, so enable it. The gate itself (snapshot
    # writes but ledger skipped when off) is covered by test_ledger_gated_off.
    "SHIP_AWARD_LEDGER_ENABLED": "1",
}

SHIMA = 10  # T10


class ShipAwardLedgerTests(TestCase):
    def setUp(self):
        cache.clear()
        Ship.objects.create(ship_id=SHIMA, name="Shimakaze",
                            nation="japan", ship_type="Destroyer", tier=10)
        self._next_pid = 1000

    def _player(self, name, realm="na"):
        self._next_pid += 1
        return Player.objects.create(
            name=name, player_id=self._next_pid, realm=realm, pvp_battles=500)

    def _event(self, player, ship_id, battles, wins):
        from_obs = BattleObservation.objects.create(player=player, pvp_battles=0)
        to_obs = BattleObservation.objects.create(
            player=player, pvp_battles=battles)
        return BattleEvent.objects.create(
            player=player, ship_id=ship_id, ship_name="x", mode="random",
            battles_delta=battles, wins_delta=wins,
            from_observation=from_obs, to_observation=to_obs)

    def _run(self, realm="na"):
        # Fixed-season pivot: drive compute over a window covering the "now"
        # fixtures, stamping captured_on=today (these tests assert today-keyed
        # rows). The default window now targets the last *completed* season.
        today = timezone.now().date()
        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            return compute_ship_top_player_snapshot(
                realm=realm,
                window_start=today - timedelta(days=14),
                window_end=today + timedelta(days=1),
                captured_on=today,
            )

    def _awards(self, player):
        with mock.patch.dict("os.environ", BADGE_ENV, clear=False):
            return get_player_ship_awards(player)

    def test_ledger_written_for_top3(self):
        a = self._player("Ace")
        b = self._player("Bravo")
        c = self._player("Charlie")
        self._event(a, SHIMA, battles=20, wins=18)
        self._event(b, SHIMA, battles=20, wins=14)
        self._event(c, SHIMA, battles=20, wins=10)

        self._run("na")

        today = timezone.now().date()
        rows = ShipAward.objects.filter(ship_id=SHIMA).order_by("rank")
        self.assertEqual([r.rank for r in rows], [1, 2, 3])
        self.assertTrue(all(r.captured_on == today for r in rows))
        self.assertEqual(rows[0].player_id, a.id)

    def test_ledger_gated_off(self):
        # Coverage-ramp hold (2026-06-08): with SHIP_AWARD_LEDGER_ENABLED off the
        # ephemeral leaderboard snapshot still writes, but the durable award
        # ledger does NOT — so boards/badges show while Ship Honors stays empty.
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        today = timezone.now().date()
        env = {**BADGE_ENV, "SHIP_AWARD_LEDGER_ENABLED": "0"}
        with mock.patch.dict("os.environ", env, clear=False):
            compute_ship_top_player_snapshot(
                realm="na",
                window_start=today - timedelta(days=14),
                window_end=today + timedelta(days=1),
                captured_on=today,
            )

        self.assertTrue(ShipTopPlayerSnapshot.objects.filter(ship_id=SHIMA).exists())
        self.assertEqual(ShipAward.objects.count(), 0)

    def test_idempotent_same_day_rerun(self):
        for i in range(3):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=10 + i)

        self._run("na")
        first = ShipAward.objects.count()
        self._run("na")
        second = ShipAward.objects.count()

        self.assertEqual(first, 3)
        self.assertEqual(second, 3)  # delete-today + re-append, no inflation

    def test_append_only_across_dates(self):
        ace = self._player("Ace")
        # A prior-date #1 placement already in the ledger.
        prior = timezone.now().date() - timedelta(days=7)
        ShipAward.objects.create(
            captured_on=prior, realm="na", ship_id=SHIMA,
            ship_name="Shimakaze", rank=1, player=ace)
        # Today the same player is #1 again.
        self._event(ace, SHIMA, battles=40, wins=38)
        for i in range(2):  # padding to clear the population guard
            self._event(self._player(f"Pad{i}"), SHIMA, battles=20, wins=8)

        self._run("na")

        # Prior row survives (ledger is never pruned).
        self.assertTrue(
            ShipAward.objects.filter(player=ace, captured_on=prior).exists())
        awards = self._awards(ace)
        self.assertEqual(len(awards), 1)
        self.assertEqual(awards[0]["times_first"], 2)  # prior + today

    def test_aggregate_career_summary(self):
        ace = self._player("Ace")
        d = timezone.now().date()
        for offset, rank in ((21, 1), (14, 1), (7, 2)):
            ShipAward.objects.create(
                captured_on=d - timedelta(days=offset), realm="na",
                ship_id=SHIMA, ship_name="Shimakaze", rank=rank, player=ace)

        awards = self._awards(ace)

        self.assertEqual(len(awards), 1)
        s = awards[0]
        self.assertEqual(s["ship_id"], SHIMA)
        self.assertEqual(s["ship_name"], "Shimakaze")
        self.assertEqual(s["times_first"], 2)
        self.assertEqual(s["times_top3"], 3)
        self.assertEqual(s["best_rank"], 1)
        self.assertEqual(s["first_on"], (d - timedelta(days=21)).isoformat())
        self.assertEqual(s["last_on"], (d - timedelta(days=7)).isoformat())

    def test_vacation_has_record_but_no_current_rank(self):
        # Ledger rows but no current snapshot → still has a record, current None.
        ace = self._player("Ace")
        ShipAward.objects.create(
            captured_on=timezone.now().date() - timedelta(days=14), realm="na",
            ship_id=SHIMA, ship_name="Shimakaze", rank=1, player=ace)

        awards = self._awards(ace)

        self.assertEqual(len(awards), 1)
        self.assertEqual(awards[0]["times_first"], 1)
        self.assertIsNone(awards[0]["current_rank"])
        self.assertEqual(
            awards[0]["last_on"],
            (timezone.now().date() - timedelta(days=14)).isoformat())

    def test_current_rank_reflects_latest_snapshot(self):
        a = self._player("Ace")
        for i in range(2):
            self._event(self._player(f"P{i}"), SHIMA, battles=20, wins=8)
        self._event(a, SHIMA, battles=40, wins=38)  # current #1

        self._run("na")

        awards = self._awards(a)
        self.assertEqual(awards[0]["current_rank"], 1)

    def test_no_awards_returns_empty(self):
        self.assertEqual(self._awards(self._player("Nobody")), [])
