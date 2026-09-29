"""Tests for the lapsed-player recapture sweep.

Command: ``recapture_lapsed_players`` (+ ``recapture_lapsed_players_task``).
See agents/runbooks/runbook-recapture-lapsed-players-2026-06-26.md.
"""
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
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


class RecaptureSoftTimeLimitTests(TestCase):
    """A truncated pass must keep what it earned.

    Until 2026-08-06 every write sat past the end of the scan, so a run that blew
    the worker's soft time limit lost the whole pass — WG calls spent, no promotes,
    no cursor advance, no snapshot. EU and ASIA hit that daily (ASIA's last complete
    pass was 2026-07-20) and the /recapture readout simply went stale.
    """

    def _mk_band(self, n, start_pid=7100):
        """`n` in-band players, all never-checked, distinct idle depths."""
        made = []
        for i in range(n):
            days = 20 + i
            made.append(Player.objects.create(
                name=f"T{start_pid + i}", player_id=start_pid + i, realm="na",
                is_hidden=False, pvp_battles=1000, pvp_wins=550,
                last_battle_date=timezone.now().date() - timedelta(days=days),
                days_since_last_battle=days,
                last_fetch=timezone.now() - timedelta(days=50),
                last_idle_check_at=None,
            ))
        return made

    def _run_interrupted(self, tmpdir, batch=10, fail_on_call=3, limit=0):
        """Run with the soft limit landing at the start of `fail_on_call`."""
        seen, calls = [], {"n": 0}

        def side(ids, realm):
            calls["n"] += 1
            if calls["n"] >= fail_on_call:
                raise SoftTimeLimitExceeded()
            seen.extend(ids)
            return ({str(i): _info(i, 1) for i in ids}, None)

        with patch("warships.api.players._bulk_fetch_account_info", side_effect=side):
            with patch(
                "warships.management.commands.recapture_lapsed_players."
                "RECAPTURE_BENCHMARK_DIR", tmpdir
            ):
                outcome = call_command(
                    "recapture_lapsed_players", "--realm", "na", "--delay", "0",
                    "--batch-size", str(batch), "--limit", str(limit),
                    apply=True, stdout=StringIO())
        return seen, outcome

    def test_truncated_run_persists_completed_batches(self):
        import tempfile
        self._mk_band(30)
        with tempfile.TemporaryDirectory() as d:
            seen, outcome = self._run_interrupted(d)
            snap = _read_snapshot(d)

        # Two batches completed before the limit landed; both are durable.
        self.assertEqual(len(seen), 20)
        for pid in seen:
            p = Player.objects.get(player_id=pid, realm="na")
            self.assertEqual(p.last_battle_date,
                             timezone.now().date() - timedelta(days=1),
                             "a checked returner must be promoted")
            self.assertIsNotNone(p.last_idle_check_at)
        # The interrupted batch was never answered — neither promoted nor rotated
        # past. Stamping it would hide those rows for a whole LRU cycle.
        untouched = Player.objects.filter(
            realm="na", last_idle_check_at__isnull=True)
        self.assertEqual(untouched.count(), 10)

        self.assertEqual(outcome, "partial")
        self.assertTrue(snap["partial"])
        self.assertEqual(snap["scanned"], 20)
        self.assertEqual(snap["candidates"], 30)
        self.assertEqual(snap["advanced"], 20)
        self.assertEqual(snap["cursor_stamped"], 20)

    def test_complete_run_is_not_partial(self):
        import tempfile
        self._mk_band(20)
        with tempfile.TemporaryDirectory() as d:
            # fail_on_call past the end -> the scan finishes normally.
            seen, outcome = self._run_interrupted(d, fail_on_call=99)
            snap = _read_snapshot(d)
        self.assertEqual(len(seen), 20)
        self.assertIsNone(outcome)
        self.assertFalse(snap["partial"])
        self.assertEqual(snap["scanned"], snap["candidates"])

    def test_advanced_accumulates_across_flushes_without_double_counting(self):
        """`advanced` is the headline number in the daily mail.

        It used to be `len(promote)` read once at the end; it now sums across
        flushes, so a failed chunk between two good ones is the case to pin.
        """
        import tempfile
        self._mk_band(30)
        calls = {"n": 0}

        def side(ids, realm):
            calls["n"] += 1
            if calls["n"] == 2:                       # middle chunk fails
                return (None, "REQUEST_LIMIT_EXCEEDED")
            return ({str(i): _info(i, 1) for i in ids}, None)

        with tempfile.TemporaryDirectory() as d:
            with patch("warships.api.players._bulk_fetch_account_info",
                       side_effect=side):
                with patch("warships.management.commands."
                           "recapture_lapsed_players.RECAPTURE_BENCHMARK_DIR", d):
                    call_command("recapture_lapsed_players", "--realm", "na",
                                 "--delay", "0", "--batch-size", "10",
                                 apply=True, stdout=StringIO(), stderr=StringIO())
            snap = _read_snapshot(d)
        self.assertEqual(snap["advanced"], 20, "the failed chunk must not count")
        self.assertEqual(snap["cursor_stamped"], 20)
        self.assertEqual(snap["chunk_errors"], 1)
        self.assertFalse(snap["partial"])
        # The failed chunk's rows keep a NULL cursor -> retried next run.
        self.assertEqual(
            Player.objects.filter(realm="na",
                                  last_idle_check_at__isnull=True).count(), 10)

    def test_snapshot_lands_even_when_the_finalizing_flush_raises(self):
        """The snapshot must NOT be gated behind the tail write.

        2026-08-15 asia: the soft limit landed inside a write's transaction
        handling, leaving the connection in `transaction status ACTIVE` while
        Django believed it was in autocommit. The finalizing `flush()` died
        117ms later on `can't change 'autocommit' now`, and because the
        snapshot write sat downstream of that bare `flush()`, the entire run's
        record vanished — ~26,000 rows scanned, nothing recorded. The alert
        surfaced it a day late as staleness, mislabelled by a `partial`
        condition that was reading the PREVIOUS day's file.

        The invariant is not "truncation is handled"; the existing tests cover
        that. It is that no failure of the tail write can erase the record of
        the run. `_run_interrupted` raises SoftTimeLimitExceeded synchronously
        at a controlled point, so it can never reproduce the real interleaving:
        the tail flush has to be failed directly.
        """
        import tempfile
        self._mk_band(40)
        real_bulk_update = Player.objects.bulk_update
        state = {"n": 0}

        def exploding_bulk_update(*a, **kw):
            state["n"] += 1
            if state["n"] >= 2:      # the finalizing flush, after one in-loop one
                raise Exception(
                    "can't change 'autocommit' now: "
                    "connection in transaction status ACTIVE")
            return real_bulk_update(*a, **kw)

        with tempfile.TemporaryDirectory() as d:
            # chunk 15 vs batch 10: calls 1+2 buffer to 20 and flush in-loop,
            # call 3 buffers 10 and does NOT, so the finalizer has real work.
            with patch("warships.management.commands.recapture_lapsed_players."
                       "CURSOR_STAMP_CHUNK", 15):
                with patch.object(Player.objects, "bulk_update",
                                  side_effect=exploding_bulk_update):
                    seen, outcome = self._run_interrupted(d, fail_on_call=4)
            snap = _read_snapshot(d)

        # The run is still reported, and reported honestly.
        self.assertIsNotNone(snap, "a failed tail flush must not erase the run")
        self.assertTrue(snap["partial"])
        self.assertTrue(snap["flush_failed"])
        self.assertEqual(snap["scanned"], 30)
        self.assertEqual(snap["candidates"], 40)
        self.assertEqual(outcome, "partial")
        # What the in-loop flush earned before the failure is durable, and the
        # tail's rows keep a NULL cursor so they retry rather than rotate past.
        self.assertEqual(snap["cursor_stamped"], 20)
        self.assertEqual(
            Player.objects.filter(realm="na",
                                  last_idle_check_at__isnull=False).count(), 20)

    def test_interrupted_flush_does_not_double_count_advanced(self):
        """A signal landing mid-flush must not tally the same buffer twice.

        `flush()` counted `advanced += len(promote)` BEFORE the write and
        cleared the buffer after, so an abort in between left the buffer full
        and the finalizing flush() counted those rows a second time —
        overstating the headline returner figure on exactly the truncated runs
        the ops mail scrutinises.
        """
        import tempfile
        self._mk_band(30)
        real_bulk_update = Player.objects.bulk_update
        state = {"n": 0}

        def interrupt_once(*a, **kw):
            state["n"] += 1
            if state["n"] == 2:      # abort the 2nd in-loop flush mid-write
                raise SoftTimeLimitExceeded()
            return real_bulk_update(*a, **kw)

        with tempfile.TemporaryDirectory() as d:
            with patch("warships.management.commands.recapture_lapsed_players."
                       "CURSOR_STAMP_CHUNK", 10):
                with patch.object(Player.objects, "bulk_update",
                                  side_effect=interrupt_once):
                    self._run_interrupted(d, fail_on_call=99)
            snap = _read_snapshot(d)

        # 20 rows were buffered across the two flushes; the interrupted one is
        # retried by the finalizer, so each row counts exactly once.
        self.assertEqual(snap["advanced"], 20,
                         "the retried buffer must not be counted twice")
        self.assertEqual(snap["advanced"], snap["into7d"])
        self.assertLessEqual(snap["advanced"], snap["scanned"])

    def test_snapshot_carries_duration(self):
        """`captured_at` is stamped at receipt, so duration needed journalctl.

        Every budget question about this task so far has required a droplet
        login to reconstruct `succeeded in Ns`. It is also the input a near-miss
        detector (duration > 85% of the soft limit) needs.
        """
        import tempfile
        self._mk_band(20)
        with tempfile.TemporaryDirectory() as d:
            self._run_interrupted(d, fail_on_call=99)
            snap = _read_snapshot(d)
        self.assertIn("duration_s", snap)
        self.assertIsInstance(snap["duration_s"], float)
        self.assertGreaterEqual(snap["duration_s"], 0.0)

    def test_incremental_flush_lands_before_the_end_of_the_scan(self):
        """Writes must not all sit past the scan — that was the original defect."""
        import tempfile
        self._mk_band(30)
        with tempfile.TemporaryDirectory() as d:
            # Flush every 10 checked rows -> batches 1 and 2 each flush in-loop.
            with patch("warships.management.commands.recapture_lapsed_players."
                       "CURSOR_STAMP_CHUNK", 10):
                seen, _ = self._run_interrupted(d)
            snap = _read_snapshot(d)
        self.assertEqual(snap["cursor_stamped"], 20)
        self.assertEqual(
            Player.objects.filter(realm="na",
                                  last_idle_check_at__isnull=False).count(), 20)


def _read_snapshot(d):
    import json
    files = os.listdir(d)
    assert len(files) == 1, files
    with open(os.path.join(d, files[0])) as fh:
        return json.load(fh)


class RecaptureUpstreamFailureAbortTests(TestCase):
    """A dead upstream must stop the pass, not be ground through chunk by chunk.

    On 2026-08-12 every one of ASIA's 300 chunks failed name resolution and the
    sweep still walked all of them, then wrote a snapshot carrying partial=false
    — indistinguishable on that field from a healthy pass. See
    agents/runbooks/runbook-recapture-upstream-failure-guard-2026-08-12.md.
    """

    def _mk_band(self, n, start_pid=7300):
        for i in range(n):
            days = 20 + i
            Player.objects.create(
                name=f"A{start_pid + i}", player_id=start_pid + i, realm="na",
                is_hidden=False, pvp_battles=1000, pvp_wins=550,
                last_battle_date=timezone.now().date() - timedelta(days=days),
                days_since_last_battle=days,
                last_fetch=timezone.now() - timedelta(days=50),
                last_idle_check_at=None,
            )

    def _run(self, tmpdir, side, batch=10, env=None, fallback=None):
        """Run a full apply pass against `side`, returning (outcome, snapshot)."""
        stack_env = {"RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES": "3",
                     "RECAPTURE_CHUNK_FAILURE_BACKOFF_S": "0"}
        stack_env.update(env or {})
        with patch.dict("os.environ", stack_env):
            with patch("warships.api.players._bulk_fetch_account_info",
                       side_effect=side):
                with patch("warships.api.players._per_player_account_fallback",
                           side_effect=fallback or (lambda ids, realm: {})):
                    with patch(
                        "warships.management.commands.recapture_lapsed_players."
                        "RECAPTURE_BENCHMARK_DIR", tmpdir
                    ):
                        outcome = call_command(
                            "recapture_lapsed_players", "--realm", "na",
                            "--delay", "0", "--batch-size", str(batch),
                            apply=True, stdout=StringIO(), stderr=StringIO())
        return outcome, _read_snapshot(tmpdir)

    def test_sustained_chunk_errors_abort_the_pass(self):
        import tempfile
        self._mk_band(100)
        calls = {"n": 0}

        def side(ids, realm):
            calls["n"] += 1
            return (None, "REQUEST_TIMEOUT")

        with tempfile.TemporaryDirectory() as d:
            outcome, snap = self._run(d, side)

        # Stopped at the threshold instead of walking all 10 chunks.
        self.assertEqual(calls["n"], 3)
        self.assertEqual(outcome, "aborted")
        self.assertTrue(snap["aborted"])
        self.assertIn("consecutive", snap["abort_reason"])
        # `partial` keeps its own meaning: not truncated by the soft time limit.
        self.assertFalse(snap["partial"])
        self.assertEqual(snap["chunk_errors"], 3)
        self.assertEqual(snap["cursor_stamped"], 0)
        self.assertEqual(
            Player.objects.filter(realm="na",
                                  last_idle_check_at__isnull=True).count(), 100)

    def test_a_usable_chunk_resets_the_streak(self):
        """Interleaved failures below the threshold must NOT abort."""
        import tempfile
        self._mk_band(100)
        calls = {"n": 0}

        def side(ids, realm):
            calls["n"] += 1
            if calls["n"] % 2 == 0:          # every other chunk fails
                return (None, "REQUEST_TIMEOUT")
            return ({str(i): _info(i, 1) for i in ids}, None)

        with tempfile.TemporaryDirectory() as d:
            outcome, snap = self._run(d, side)

        self.assertEqual(calls["n"], 10, "the pass must walk the whole band")
        self.assertIsNone(outcome)
        self.assertFalse(snap["aborted"])
        self.assertIsNone(snap["abort_reason"])
        self.assertEqual(snap["chunk_errors"], 5)
        self.assertEqual(snap["cursor_stamped"], 50)

    def test_invalid_account_id_outage_aborts(self):
        """The failure mode that a naive reset rule cannot see.

        `INVALID_ACCOUNT_ID` routes to `_per_player_account_fallback`, which under
        a total outage returns a TRUTHY dict of Nones. Every row then takes the
        `no_data` path, so a streak keyed on "the chunk avoided `elif err:`" would
        reset on every chunk and the guard would never fire — while stamping the
        cursor on rows nothing ever answered for.
        """
        import tempfile
        self._mk_band(100)
        calls = {"n": 0}

        def side(ids, realm):
            calls["n"] += 1
            return (None, "INVALID_ACCOUNT_ID")

        def dead_fallback(ids, realm):
            return {str(i): None for i in ids}     # truthy dict, no usable rows

        with tempfile.TemporaryDirectory() as d:
            outcome, snap = self._run(d, side, fallback=dead_fallback)

        self.assertEqual(calls["n"], 3, "must abort at the threshold")
        self.assertEqual(outcome, "aborted")
        self.assertTrue(snap["aborted"])
        # Crucially: nothing was rotated past unchecked.
        self.assertEqual(snap["cursor_stamped"], 0)
        self.assertEqual(
            Player.objects.filter(realm="na",
                                  last_idle_check_at__isnull=True).count(), 100)

    def test_partial_no_data_within_a_chunk_still_stamps_and_resets(self):
        """Normal operation is unchanged: some no_data rows are a real answer."""
        import tempfile
        self._mk_band(20)

        def side(ids, realm):
            out = {str(i): _info(i, 1) for i in ids}
            out[str(ids[0])] = None            # one genuinely missing account
            return (out, None)

        with tempfile.TemporaryDirectory() as d:
            outcome, snap = self._run(d, side)

        self.assertIsNone(outcome)
        self.assertFalse(snap["aborted"])
        self.assertEqual(snap["no_data"], 2)
        self.assertEqual(snap["cursor_stamped"], 20, "no_data rows still rotate")

    def test_failed_chunks_back_off_so_a_blip_cannot_abort(self):
        """2026-09-28: ten instant 504s inside 0.13s aborted a whole NA sweep.

        Each unproductive chunk short of the threshold now sleeps an exponential
        backoff, so the streak spans real time; the aborting chunk does not sleep.
        """
        import tempfile
        self._mk_band(100)

        def side(ids, realm):
            return (None, "SOURCE_NOT_AVAILABLE")

        cmd = "warships.management.commands.recapture_lapsed_players"
        with tempfile.TemporaryDirectory() as d:
            with patch(f"{cmd}.time.sleep") as mock_sleep:
                outcome, snap = self._run(
                    d, side, env={"RECAPTURE_CHUNK_FAILURE_BACKOFF_S": "2"})

        self.assertEqual(outcome, "aborted")
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [2, 4])

    def test_backoff_is_capped(self):
        import tempfile
        self._mk_band(100)

        def side(ids, realm):
            return (None, "SOURCE_NOT_AVAILABLE")

        cmd = "warships.management.commands.recapture_lapsed_players"
        with tempfile.TemporaryDirectory() as d:
            with patch(f"{cmd}.time.sleep") as mock_sleep:
                self._run(d, side, env={
                    "RECAPTURE_CHUNK_FAILURE_BACKOFF_S": "20",
                    "RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES": "4"})

        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list],
                         [20, 30, 30])

    def test_threshold_zero_disables_the_guard(self):
        import tempfile
        self._mk_band(100)
        calls = {"n": 0}

        def side(ids, realm):
            calls["n"] += 1
            return (None, "REQUEST_TIMEOUT")

        with tempfile.TemporaryDirectory() as d:
            outcome, snap = self._run(
                d, side, env={"RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES": "0"})

        self.assertEqual(calls["n"], 10, "0 disables the abort (old behavior)")
        self.assertIsNone(outcome)
        self.assertFalse(snap["aborted"])
        self.assertEqual(snap["chunk_errors"], 10)

    def test_promotes_earned_before_the_outage_are_durable(self):
        import tempfile
        self._mk_band(100)
        calls = {"n": 0}

        def side(ids, realm):
            calls["n"] += 1
            if calls["n"] == 1:
                return ({str(i): _info(i, 1) for i in ids}, None)
            return (None, "REQUEST_TIMEOUT")

        with tempfile.TemporaryDirectory() as d:
            outcome, snap = self._run(d, side)

        self.assertEqual(outcome, "aborted")
        self.assertEqual(snap["advanced"], 10)
        self.assertEqual(snap["cursor_stamped"], 10,
                         "the good chunk's rows rotate; the failed ones do not")
        self.assertEqual(
            Player.objects.filter(realm="na",
                                  last_idle_check_at__isnull=True).count(), 90)


class RecaptureLapsedTaskGateTests(TestCase):
    def test_task_skips_when_disabled(self):
        from warships.tasks import recapture_lapsed_players_task
        with patch.dict("os.environ", {"RECAPTURE_LAPSED_ENABLED": "0"}):
            res = recapture_lapsed_players_task.run(realm="na")
        self.assertEqual(res, {"status": "skipped", "reason": "disabled"})

    def test_task_reports_partial_when_the_command_truncated(self):
        from warships.tasks import recapture_lapsed_players_task
        with patch.dict("os.environ", {"RECAPTURE_LAPSED_ENABLED": "1"}):
            with patch("warships.tasks.call_command", return_value="partial"):
                res = recapture_lapsed_players_task.run(realm="na")
        self.assertEqual(res, {"status": "partial"})

    def test_task_reports_aborted_when_the_upstream_died(self):
        from warships.tasks import recapture_lapsed_players_task
        with patch.dict("os.environ", {"RECAPTURE_LAPSED_ENABLED": "1"}):
            with patch("warships.tasks.call_command", return_value="aborted"):
                res = recapture_lapsed_players_task.run(realm="na")
        self.assertEqual(res, {"status": "aborted", "reason": "upstream-failures"})

    def test_task_reports_completed_on_a_full_pass(self):
        from warships.tasks import recapture_lapsed_players_task
        with patch.dict("os.environ", {"RECAPTURE_LAPSED_ENABLED": "1"}):
            with patch("warships.tasks.call_command", return_value=None):
                res = recapture_lapsed_players_task.run(realm="na")
        self.assertEqual(res, {"status": "completed"})

    def test_task_budget_clears_a_full_prod_sized_pass(self):
        """540s (TASK_OPTS) truncated EU/ASIA daily; the sweep needs its own budget.

        Pinned to a real truncation rather than to a bare "bigger than TASK_OPTS",
        so a regression to 15 min fails here. ASIA on 2026-08-30 covered 23,600 of
        30,000 inside the then-current 900s soft limit; scaling that rate to a full
        pass is the width this budget must absorb. The scaling is conservative — it
        stretches the fixed ordering query along with the per-chunk work.

        It deliberately does NOT cover 2026-08-19 (18,100 rows, ~1,493s needed).
        That day was a platform-wide throughput decay across all three realms, not
        a recapture sizing question, and sizing the budget for it would hide the
        signal that says so.
        """
        from warships.tasks import (PLAYER_REFRESH_LOCK_TIMEOUT,
                                    RECAPTURE_TASK_OPTS)
        soft = RECAPTURE_TASK_OPTS["soft_time_limit"]
        hard = RECAPTURE_TASK_OPTS["time_limit"]
        self.assertGreater(soft, 540)
        prod_limit = 30000
        truncated_at_seconds, truncated_rows = 900, 23600   # asia, 2026-08-30
        needed = truncated_at_seconds * prod_limit / truncated_rows
        self.assertGreater(
            soft, needed,
            "soft_time_limit must clear a 30k pass at the rate asia sustained "
            f"when it truncated on 2026-08-30 (~{needed:.0f}s)")
        # Invariant: soft < hard <= lock TTL, with room for the final flush.
        self.assertLess(soft, hard)
        self.assertLessEqual(hard, PLAYER_REFRESH_LOCK_TIMEOUT)
        # The flush headroom is load-bearing: overrunning the HARD limit raises
        # TimeLimitExceeded, which cannot be caught, so no snapshot is written and
        # the next ops alert reads the previous day's file (2026-08-15).
        self.assertGreaterEqual(hard - soft, 60)


class RecapturePerRealmLimitTests(TestCase):
    """Per-realm candidate cap (`RECAPTURE_LAPSED_LIMIT_<REALM>`).

    ASIA is the slowest realm per row (a fixed latency cost to
    api.worldofwarships.asia) *and* has the smallest dormant pool, so it needs a
    lower cap and pays the least rotation latency for one. A single global limit
    could only buy asia that headroom by taxing EU — the largest pool, and the
    realm that needs no cap at all. Runbook:
    runbook-recapture-soft-limit-budget-2026-08-13.md (L2b).
    """

    def _limit(self, realm, env):
        """Resolve the limit for `realm` under exactly `env` for the recapture keys."""
        from warships.tasks import _recapture_limit
        with patch.dict("os.environ", env):
            for key in ("RECAPTURE_LAPSED_LIMIT",
                        "RECAPTURE_LAPSED_LIMIT_NA",
                        "RECAPTURE_LAPSED_LIMIT_EU",
                        "RECAPTURE_LAPSED_LIMIT_ASIA"):
                if key not in env:
                    os.environ.pop(key, None)
            return _recapture_limit(realm)

    def test_realm_override_wins_over_the_global(self):
        self.assertEqual(
            self._limit("asia", {"RECAPTURE_LAPSED_LIMIT": "30000",
                                 "RECAPTURE_LAPSED_LIMIT_ASIA": "24000"}),
            24000)

    def test_realm_without_an_override_keeps_the_global(self):
        """The whole point: capping asia must not touch EU's rotation."""
        self.assertEqual(
            self._limit("eu", {"RECAPTURE_LAPSED_LIMIT": "30000",
                               "RECAPTURE_LAPSED_LIMIT_ASIA": "24000"}),
            30000)

    def test_falls_back_to_the_code_default_when_nothing_is_set(self):
        self.assertEqual(self._limit("na", {}), 30000)

    def test_task_passes_the_per_realm_limit_to_the_command(self):
        """The helper is inert unless the call site actually uses it."""
        from warships.tasks import recapture_lapsed_players_task
        with patch.dict("os.environ", {"RECAPTURE_LAPSED_ENABLED": "1",
                                       "RECAPTURE_LAPSED_LIMIT": "30000",
                                       "RECAPTURE_LAPSED_LIMIT_ASIA": "24000"}):
            with patch("warships.tasks.call_command") as call:
                recapture_lapsed_players_task.run(realm="asia")
                recapture_lapsed_players_task.run(realm="na")
        self.assertEqual(call.call_args_list[0].kwargs["limit"], 24000)
        self.assertEqual(call.call_args_list[1].kwargs["limit"], 30000)
