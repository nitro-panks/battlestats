"""Tests for the mover-capture KPI in `benchmark_observation_floor`.

The benchmark is read-only. These tests pin the new mover-capture metric:
the *denominator* is the set of players who actually battled (cumulative
`Snapshot.battles` rose between the two most-recent snapshot dates), and the
*numerator* is `distinct_productive` (distinct players with a `BattleEvent` in
the trailing window). See the mover-capture KPI section of
agents/runbooks/runbook-bulk-battle-observation-capture-2026-06-06.md.
"""
import io
import json
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    Snapshot,
)


def _run_json():
    out = io.StringIO()
    call_command("benchmark_observation_floor", "--json", stdout=out)
    return json.loads(out.getvalue())


class MoverCaptureKPITest(TestCase):
    def setUp(self):
        self.today = timezone.now().date()
        self.yesterday = self.today - timedelta(days=1)

    def _player(self, player_id, *, realm="na", hidden=False):
        return Player.objects.create(
            name=f"p{player_id}", player_id=player_id, realm=realm,
            is_hidden=hidden, last_battle_date=self.today,
        )

    def _two_day_snapshots(self, player, *, prior_battles, today_battles):
        Snapshot.objects.create(
            player=player, date=self.yesterday, battles=prior_battles)
        Snapshot.objects.create(
            player=player, date=self.today, battles=today_battles)

    def _battle_event(self, player):
        """A BattleEvent needs a from/to observation pair; both are 'now',
        so the event lands inside the trailing 24h window."""
        frm = BattleObservation.objects.create(player=player, pvp_battles=10)
        to = BattleObservation.objects.create(player=player, pvp_battles=11)
        BattleEvent.objects.create(
            player=player, ship_id=42, battles_delta=1,
            from_observation=frm, to_observation=to)

    def test_mover_captured_yields_full_rate(self):
        mover = self._player(1001)
        flat = self._player(1002)
        self._two_day_snapshots(mover, prior_battles=100, today_battles=105)
        self._two_day_snapshots(flat, prior_battles=200, today_battles=200)
        self._battle_event(mover)  # the mover got productively captured

        na = _run_json()["realms"]["na"]
        self.assertEqual(na["snapshot_today"], 2)      # both have a today snap
        self.assertEqual(na["snapshot_movers"], 1)     # only the mover battled
        self.assertEqual(na["distinct_productive"], 1)  # and was captured
        self.assertEqual(na["mover_capture_rate"], 1.0)

    def test_uncaptured_mover_drags_rate_down(self):
        m1 = self._player(2001)
        m2 = self._player(2002)
        self._two_day_snapshots(m1, prior_battles=10, today_battles=12)
        self._two_day_snapshots(m2, prior_battles=30, today_battles=33)
        self._battle_event(m1)  # only one of two movers captured

        na = _run_json()["realms"]["na"]
        self.assertEqual(na["snapshot_movers"], 2)
        self.assertEqual(na["distinct_productive"], 1)
        self.assertEqual(na["mover_capture_rate"], 0.5)

    def test_hidden_players_excluded_from_denominator(self):
        visible = self._player(3001)
        hidden = self._player(3002, hidden=True)
        self._two_day_snapshots(visible, prior_battles=1, today_battles=2)
        self._two_day_snapshots(hidden, prior_battles=1, today_battles=9)

        na = _run_json()["realms"]["na"]
        self.assertEqual(na["snapshot_today"], 1)   # hidden excluded
        self.assertEqual(na["snapshot_movers"], 1)  # hidden mover not counted

    def test_single_snapshot_day_yields_none(self):
        p = self._player(4001)
        Snapshot.objects.create(player=p, date=self.today, battles=5)

        result = _run_json()
        na = result["realms"]["na"]
        # snapshot_today is computable from one day; movers/rate need two.
        self.assertEqual(na["snapshot_today"], 1)
        self.assertIsNone(na["snapshot_movers"])
        self.assertIsNone(na["mover_capture_rate"])

    def test_no_snapshots_at_all_is_safe(self):
        self._player(5001)  # active but never snapshotted
        na = _run_json()["realms"]["na"]
        self.assertIsNone(na["snapshot_today"])
        self.assertIsNone(na["snapshot_movers"])
        self.assertIsNone(na["mover_capture_rate"])


class Gap1dDecompositionTest(TestCase):
    """Pin the 24h-gap decomposition (`gap_1d`): active-1d players with no
    BattleEvent in the window, split into PvP movers (with a `no_event_48h`
    latency sub-count), non-PvP actives, and no-snapshot-pair."""

    def setUp(self):
        self.today = timezone.now().date()
        self.yesterday = self.today - timedelta(days=1)

    def _player(self, player_id, *, realm="na", hidden=False, days_ago=0):
        return Player.objects.create(
            name=f"g{player_id}", player_id=player_id, realm=realm,
            is_hidden=hidden,
            last_battle_date=self.today - timedelta(days=days_ago),
        )

    def _two_day_snapshots(self, player, *, prior_battles, today_battles):
        Snapshot.objects.create(
            player=player, date=self.yesterday, battles=prior_battles)
        Snapshot.objects.create(
            player=player, date=self.today, battles=today_battles)

    def _battle_event(self, player, *, hours_ago=0):
        frm = BattleObservation.objects.create(player=player, pvp_battles=10)
        to = BattleObservation.objects.create(player=player, pvp_battles=11)
        ev = BattleEvent.objects.create(
            player=player, ship_id=42, battles_delta=1,
            from_observation=frm, to_observation=to)
        if hours_ago:
            BattleEvent.objects.filter(pk=ev.pk).update(
                detected_at=timezone.now() - timedelta(hours=hours_ago))
        return ev

    def test_buckets_classified(self):
        # Captured mover: excluded from the gap entirely.
        captured = self._player(9001)
        self._two_day_snapshots(captured, prior_battles=10, today_battles=12)
        self._battle_event(captured)
        # Mover, no event at all: pvp_mover + no_event_48h (>48h latency).
        missed = self._player(9002)
        self._two_day_snapshots(missed, prior_battles=20, today_battles=25)
        # Missed mover with a 30h-old event: late capture, not lost at 48h.
        late = self._player(9003)
        self._two_day_snapshots(late, prior_battles=30, today_battles=31)
        self._battle_event(late, hours_ago=30)
        # Account clock moved but PvP battles flat: non-PvP activity.
        coop = self._player(9004)
        self._two_day_snapshots(coop, prior_battles=40, today_battles=40)
        # Active-1d but never snapshotted: unclassifiable.
        self._player(9005)

        na = _run_json()["realms"]["na"]
        g = na["gap_1d"]
        self.assertEqual(g["total"], 4)  # everyone but the captured mover
        self.assertEqual(g["pvp_mover"], 2)
        self.assertEqual(g["pvp_mover_no_event_48h"], 1)  # `late` had a 30h event
        self.assertEqual(g["non_pvp_active"], 1)
        self.assertEqual(g["no_snapshot_pair"], 1)

    def test_only_active_1d_players_counted(self):
        stale = self._player(9101, days_ago=3)  # active-7d but not active-1d
        self._two_day_snapshots(stale, prior_battles=5, today_battles=9)
        na = _run_json()["realms"]["na"]
        self.assertEqual(na["gap_1d"]["total"], 0)

    def test_hidden_players_excluded(self):
        hidden = self._player(9201, hidden=True)
        self._two_day_snapshots(hidden, prior_battles=1, today_battles=5)
        na = _run_json()["realms"]["na"]
        self.assertEqual(na["gap_1d"]["total"], 0)

    def test_none_without_two_snapshot_days(self):
        p = self._player(9301)
        Snapshot.objects.create(player=p, date=self.today, battles=5)
        na = _run_json()["realms"]["na"]
        self.assertIsNone(na["gap_1d"])

    def test_totals_aggregate_across_realms(self):
        na_gap = self._player(9401, realm="na")
        self._two_day_snapshots(na_gap, prior_battles=1, today_battles=1)
        eu_gap = self._player(9402, realm="eu")
        self._two_day_snapshots(eu_gap, prior_battles=1, today_battles=4)

        result = _run_json()
        self.assertEqual(result["realms"]["na"]["gap_1d"]["non_pvp_active"], 1)
        self.assertEqual(result["realms"]["eu"]["gap_1d"]["pvp_mover"], 1)
        totals = result["totals"]["gap_1d"]
        self.assertEqual(totals["total"], 2)
        self.assertEqual(totals["pvp_mover"], 1)
        self.assertEqual(totals["non_pvp_active"], 1)
