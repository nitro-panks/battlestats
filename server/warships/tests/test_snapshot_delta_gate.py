"""Tests for the Snapshot delta-gate (DB audit levers F3.2 + F4).

Skip writing a Snapshot row when a player's cumulative PvP stats have not
moved since their latest stored row; readers already synthesize zero-battle
days for missing dates. Spec:
``agents/work-items/snapshot-delta-gated-writes-spec.md``.
"""
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from warships.data import update_activity_data, update_snapshot_data
from warships.models import Player, Snapshot


def _mk_player(pid, battles=1000, wins=550):
    return Player.objects.create(
        name=f"P{pid}", player_id=pid, realm="na", is_hidden=False,
        pvp_battles=battles, pvp_wins=wins,
        last_battle_date=timezone.now().date(), days_since_last_battle=0)


def _snap(player, days_ago, battles, wins):
    return Snapshot.objects.create(
        player=player, date=timezone.now().date() - timedelta(days=days_ago),
        battles=battles, wins=wins, interval_battles=0, interval_wins=0)


class SnapshotDeltaGateTests(TestCase):
    def test_unchanged_player_skips_today_row(self):
        p = _mk_player(7001, battles=1000, wins=550)
        _snap(p, 1, 1000, 550)
        status = update_snapshot_data(7001, realm="na", refresh_player=False)
        self.assertEqual(status, "skipped-unchanged")
        self.assertFalse(
            Snapshot.objects.filter(player=p, date=timezone.now().date()).exists())
        # Activity still refreshes on the skip path (the 29-day window slides).
        p.refresh_from_db()
        self.assertIsNotNone(p.activity_json)
        self.assertEqual(len(p.activity_json), 29)

    def test_changed_player_writes_row_with_interval(self):
        p = _mk_player(7002, battles=1010, wins=556)
        _snap(p, 1, 1000, 550)
        status = update_snapshot_data(7002, realm="na", refresh_player=False)
        self.assertEqual(status, "written")
        snap = Snapshot.objects.get(player=p, date=timezone.now().date())
        self.assertEqual(snap.battles, 1010)
        self.assertEqual(snap.interval_battles, 10)
        self.assertEqual(snap.interval_wins, 6)

    def test_returning_mover_interval_seeds_from_pre_window_row(self):
        # The latest prior row is OLDER than the 28d recompute window: the
        # interval must seed from it, not reset to 0 at the window edge.
        p = _mk_player(7003, battles=1010, wins=556)
        _snap(p, 40, 1000, 550)
        update_snapshot_data(7003, realm="na", refresh_player=False)
        snap = Snapshot.objects.get(player=p, date=timezone.now().date())
        self.assertEqual(snap.interval_battles, 10)
        self.assertEqual(snap.interval_wins, 6)

    def test_first_ever_snapshot_writes(self):
        p = _mk_player(7004, battles=1000, wins=550)
        status = update_snapshot_data(7004, realm="na", refresh_player=False)
        self.assertEqual(status, "written")
        snap = Snapshot.objects.get(player=p, date=timezone.now().date())
        self.assertEqual(snap.battles, 1000)
        self.assertEqual(snap.interval_battles, 0)

    def test_gate_disabled_writes_zero_interval_row(self):
        p = _mk_player(7005, battles=1000, wins=550)
        _snap(p, 1, 1000, 550)
        with patch.dict(os.environ, {"SNAPSHOT_DELTA_GATE_ENABLED": "0"}):
            update_snapshot_data(7005, realm="na", refresh_player=False)
        snap = Snapshot.objects.get(player=p, date=timezone.now().date())
        self.assertEqual(snap.battles, 1000)
        self.assertEqual(snap.interval_battles, 0)

    def test_existing_today_row_is_maintained_even_through_gate(self):
        # A today-row means the write path already ran; it must keep being
        # maintained (never skipped, never deleted).
        p = _mk_player(7006, battles=1010, wins=556)
        _snap(p, 1, 1000, 550)
        _snap(p, 0, 1005, 553)  # earlier run today; player battled again since
        status = update_snapshot_data(7006, realm="na", refresh_player=False)
        self.assertEqual(status, "written")
        snap = Snapshot.objects.get(player=p, date=timezone.now().date())
        self.assertEqual(snap.battles, 1010)
        self.assertEqual(snap.interval_battles, 10)

    def test_skip_path_issues_no_snapshot_writes(self):
        p = _mk_player(7007, battles=1000, wins=550)
        _snap(p, 1, 1000, 550)
        with CaptureQueriesContext(connection) as ctx:
            update_snapshot_data(7007, realm="na", refresh_player=False)
        snapshot_writes = [
            q["sql"] for q in ctx.captured_queries
            if "warships_snapshot" in q["sql"]
            and q["sql"].lstrip().split()[0].upper() in ("UPDATE", "INSERT", "DELETE")
        ]
        self.assertEqual(snapshot_writes, [])

    def test_update_activity_data_bounds_history_query(self):
        p = _mk_player(7008, battles=1000, wins=550)
        _snap(p, 100, 900, 500)
        with CaptureQueriesContext(connection) as ctx:
            update_activity_data(7008, realm="na")
        selects = [
            q["sql"] for q in ctx.captured_queries
            if "warships_snapshot" in q["sql"]
            and q["sql"].lstrip().upper().startswith("SELECT")
        ]
        self.assertTrue(selects)
        for sql in selects:
            self.assertIn(">=", sql)


class SnapshotEngineSkipCounterTests(TestCase):
    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_engine_reports_unchanged_skips(self, mock_bulk):
        p = _mk_player(6501, battles=1010, wins=560)
        _snap(p, 1, 1010, 560)  # yesterday's row already matches WG's numbers
        mock_bulk.return_value = {
            "6501": {
                "account_id": 6501,
                "nickname": "P6501",
                "last_battle_time": int(timezone.now().timestamp()),
                "statistics": {
                    "battles": 1015,
                    "pvp": {"battles": 1010, "wins": 560, "losses": 450,
                            "frags": 0, "survived_battles": 0},
                },
            },
        }
        out = StringIO()
        call_command("snapshot_active_players", "--realm", "na", "--delay", "0",
                     stdout=out)
        self.assertFalse(
            Snapshot.objects.filter(player=p, date=timezone.now().date()).exists())
        self.assertIn("Unchanged-skipped: 1", out.getvalue())


class SnapshotEngineCheckedSetTests(TestCase):
    """Under delta-gating a non-mover never gains a today-row, so the engine's
    has-today-row idempotency alone would re-select the same recency-ordered
    top of the pool every 30-min run (re-polling it all day and never reaching
    deeper players). A per-day cache-backed checked set keeps runs converging
    across the whole pool."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_second_run_advances_past_unchanged_players(self, mock_bulk):
        newer = _mk_player(6601, battles=1000, wins=550)
        older = _mk_player(6602, battles=2000, wins=1100)
        Player.objects.filter(player_id=6602).update(
            last_battle_date=timezone.now().date() - timedelta(days=1))
        _snap(newer, 1, 1000, 550)   # unchanged -> gate will skip the row
        _snap(older, 1, 1990, 1090)  # mover, but behind `newer` in recency order

        def bulk(ids, realm=None, request_delay=None):
            acct = {
                6601: (1000, 550),
                6602: (2000, 1100),
            }
            return {
                str(pid): {
                    "account_id": pid,
                    "nickname": f"P{pid}",
                    "last_battle_time": int(timezone.now().timestamp()),
                    "statistics": {
                        "battles": acct[pid][0] + 5,
                        "pvp": {"battles": acct[pid][0], "wins": acct[pid][1],
                                "losses": acct[pid][0] - acct[pid][1],
                                "frags": 0, "survived_battles": 0},
                    },
                } for pid in ids
            }
        mock_bulk.side_effect = bulk

        call_command("snapshot_active_players", "--realm", "na", "--limit", "1",
                     "--delay", "0", stdout=StringIO())
        self.assertEqual(list(mock_bulk.call_args[0][0]), [6601])

        # Run 2 must NOT re-poll the unchanged (row-less) 6601; it advances.
        call_command("snapshot_active_players", "--realm", "na", "--limit", "1",
                     "--delay", "0", stdout=StringIO())
        self.assertEqual(list(mock_bulk.call_args[0][0]), [6602])
        self.assertTrue(
            Snapshot.objects.filter(
                player=older, date=timezone.now().date()).exists())


class SkipPathActivityThrottleTests(TestCase):
    def test_first_skip_of_day_refreshes_activity_then_throttles(self):
        p = _mk_player(6701, battles=1000, wins=550)
        _snap(p, 1, 1000, 550)
        update_snapshot_data(6701, realm="na", refresh_player=False)
        p.refresh_from_db()
        first_stamp = p.activity_updated_at
        self.assertIsNotNone(first_stamp)

        # Second unchanged pass the same day: no activity rebuild.
        update_snapshot_data(6701, realm="na", refresh_player=False)
        p.refresh_from_db()
        self.assertEqual(p.activity_updated_at, first_stamp)


class CandidateQueryShapeTests(TestCase):
    """DB audit F9.1: with the checked set carrying idempotency (written AND
    unchanged players), the candidate query must not anti-join Snapshot —
    the NOT EXISTS probe was the 55 s component on prod."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_candidate_query_has_no_snapshot_anti_join(self, mock_bulk):
        _mk_player(6801, battles=1000, wins=550)
        mock_bulk.return_value = {}
        from django.db import connection as conn
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(conn) as ctx:
            call_command("snapshot_active_players", "--realm", "na",
                         "--delay", "0", stdout=StringIO())
        candidate_sqls = [
            q["sql"] for q in ctx.captured_queries
            if "warships_player" in q["sql"]
            and "ORDER BY" in q["sql"] and "last_battle_date" in q["sql"]
        ]
        self.assertTrue(candidate_sqls)
        for sql in candidate_sqls:
            self.assertNotIn("warships_snapshot", sql)

    @patch("warships.clan_crawl.fetch_players_bulk")
    def test_written_mover_not_repolled_same_day(self, mock_bulk):
        p = _mk_player(6802, battles=1010, wins=560)
        _snap(p, 1, 1000, 550)  # mover: run 1 writes today's row

        def bulk(ids, realm=None, request_delay=None):
            return {
                str(pid): {
                    "account_id": pid, "nickname": f"P{pid}",
                    "last_battle_time": int(timezone.now().timestamp()),
                    "statistics": {
                        "battles": 1015,
                        "pvp": {"battles": 1010, "wins": 560, "losses": 450,
                                "frags": 0, "survived_battles": 0},
                    },
                } for pid in ids
            }
        mock_bulk.side_effect = bulk

        call_command("snapshot_active_players", "--realm", "na", "--delay", "0",
                     stdout=StringIO())
        self.assertTrue(
            Snapshot.objects.filter(player=p, date=timezone.now().date()).exists())

        mock_bulk.reset_mock()
        call_command("snapshot_active_players", "--realm", "na", "--delay", "0",
                     stdout=StringIO())
        self.assertEqual(mock_bulk.call_count, 0)
