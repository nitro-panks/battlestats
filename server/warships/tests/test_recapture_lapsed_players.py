"""Tests for the lapsed-player recapture sweep.

Command: ``recapture_lapsed_players`` (+ ``recapture_lapsed_players_task``).
See agents/runbooks/runbook-recapture-lapsed-players-2026-06-26.md.
"""
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.models import Player


def _info(pid, days_ago, hidden=False):
    """A WG account/info row whose last_battle_time is `days_ago` days back."""
    if hidden:
        return {"account_id": pid, "nickname": f"P{pid}", "hidden_profile": True}
    ts = int((timezone.now() - timedelta(days=days_ago)).timestamp())
    return {"account_id": pid, "nickname": f"P{pid}", "last_battle_time": ts}


class RecaptureLapsedPlayersTests(TestCase):
    def _mk(self, pid, days_idle, **kw):
        """Create a player whose stored last battle is `days_idle` days ago."""
        lbd = timezone.now().date() - timedelta(days=days_idle)
        d = dict(
            realm="na", is_hidden=False, pvp_battles=1000, pvp_wins=550,
            last_battle_date=lbd, days_since_last_battle=days_idle,
            last_fetch=timezone.now() - timedelta(days=50),
            last_idle_check_at=None,
        )
        d.update(kw)
        return Player.objects.create(name=f"P{pid}", player_id=pid, **d)

    def _run(self, side, **extra):
        with patch("warships.api.players._bulk_fetch_account_info",
                   side_effect=side) as m:
            call_command("recapture_lapsed_players", "--realm", "na",
                         "--delay", "0", stdout=StringIO(), **extra)
        return m

    def test_detect_only_makes_no_writes(self):
        p = self._mk(7001, days_idle=100)
        # WG says they played yesterday — a returner — but detect-only writes nothing.
        self._run(lambda ids, realm: ({str(i): _info(i, 1) for i in ids}, None))
        p.refresh_from_db()
        self.assertEqual(p.last_battle_date,
                         timezone.now().date() - timedelta(days=100))
        self.assertIsNone(p.last_idle_check_at)

    def test_apply_promotes_returner_into_floor_scope(self):
        p = self._mk(7002, days_idle=120)
        before_fetch = p.last_fetch
        self._run(lambda ids, realm: ({str(i): _info(i, 1) for i in ids}, None),
                  apply=True)
        p.refresh_from_db()
        # last_battle_date advanced to ~yesterday -> back inside active_7d.
        self.assertEqual(p.last_battle_date,
                         timezone.now().date() - timedelta(days=1))
        self.assertEqual(p.days_since_last_battle, 1)
        # cursor stamped; last_fetch NOT bumped (floor refresh stays armed).
        self.assertIsNotNone(p.last_idle_check_at)
        self.assertEqual(p.last_fetch, before_fetch)

    def test_apply_stamps_cursor_but_does_not_promote_still_dormant(self):
        p = self._mk(7003, days_idle=100)
        # WG reports the SAME old battle -> not a returner.
        self._run(lambda ids, realm: ({str(i): _info(i, 100) for i in ids}, None),
                  apply=True)
        p.refresh_from_db()
        self.assertEqual(p.last_battle_date,
                         timezone.now().date() - timedelta(days=100))
        # still checked -> cursor advances so we rotate past them next run.
        self.assertIsNotNone(p.last_idle_check_at)

    def test_band_excludes_active_and_deep_tail(self):
        active = self._mk(7004, days_idle=3)     # inside active_7d
        deep = self._mk(7005, days_idle=400)     # past the 365d default ceiling
        lapsed = self._mk(7006, days_idle=50)    # in band
        seen = {}

        def side(ids, realm):
            seen["ids"] = list(ids)
            return ({str(i): _info(i, 1) for i in ids}, None)

        self._run(side, apply=True)
        self.assertEqual(seen["ids"], [lapsed.player_id])
        for p in (active, deep):
            p.refresh_from_db()
            self.assertIsNone(p.last_idle_check_at)

    def test_emits_structured_summary_line(self):
        # The /recapture readout skill greps this line out of the worker journal.
        self._mk(7009, days_idle=100)
        with self.assertLogs(
                "warships.management.commands.recapture_lapsed_players",
                level="INFO") as cm:
            self._run(lambda ids, realm: ({str(i): _info(i, 1) for i in ids}, None),
                      apply=True)
        line = next(m for m in cm.output if "recapture-summary" in m)
        self.assertIn("realm=na", line)
        self.assertIn("mode=apply", line)
        self.assertIn("advanced=1", line)
        self.assertIn("into7d=1", line)

    def test_writes_yield_snapshot_file(self):
        # The /recapture skill reads these per-run JSON snapshots.
        import json
        import tempfile
        self._mk(7010, days_idle=100)
        with tempfile.TemporaryDirectory() as d:
            with patch(
                "warships.management.commands.recapture_lapsed_players."
                "RECAPTURE_BENCHMARK_DIR", d):
                self._run(
                    lambda ids, realm: ({str(i): _info(i, 1) for i in ids}, None),
                    apply=True)
            files = os.listdir(d)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].endswith("_na.json"))
            with open(os.path.join(d, files[0])) as fh:
                snap = json.load(fh)
        self.assertEqual(snap["mode"], "apply")
        self.assertEqual(snap["advanced"], 1)
        self.assertEqual(snap["into7d"], 1)
        self.assertEqual(snap["cursor_stamped"], 1)

    def test_lru_cursor_orders_never_checked_first(self):
        recent = self._mk(7007, days_idle=60,
                          last_idle_check_at=timezone.now())
        fresh = self._mk(7008, days_idle=60, last_idle_check_at=None)
        seen = {}

        def side(ids, realm):
            seen.setdefault("ids", []).extend(ids)
            return ({str(i): _info(i, 60) for i in ids}, None)

        # Only room for one this run -> the never-checked row must win.
        self._run(side, apply=True, limit=1)
        self.assertEqual(seen["ids"], [fresh.player_id])
        recent.refresh_from_db()
        # the recently-checked row keeps its old (now-ish) cursor, untouched here.
        self.assertEqual(seen["ids"].count(recent.player_id), 0)


class RecaptureLapsedTaskGateTests(TestCase):
    def test_task_skips_when_disabled(self):
        from warships.tasks import recapture_lapsed_players_task
        with patch.dict("os.environ", {"RECAPTURE_LAPSED_ENABLED": "0"}):
            res = recapture_lapsed_players_task.run(realm="na")
        self.assertEqual(res, {"status": "skipped", "reason": "disabled"})
