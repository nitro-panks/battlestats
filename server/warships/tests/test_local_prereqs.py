"""Tests for `scripts/lib/local_prereqs.sh`.

A repo-root shell helper tested from the Django suite on purpose: CI runs only
`python -m pytest warships/tests/` (.github/workflows/ci.yml), so a test placed
anywhere else would never execute. This helper exists because the same class of
failure — a script invoked from a git worktree cannot see gitignored material
that only the main checkout has — recurred three times, and both previous fixes
(a doc, and a widened path list) were not checks. An untested shell helper would
be a fourth non-check.

See agents/runbooks/runbook-worktree-local-prereqs-2026-08-13.md.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "scripts" / "lib" / "local_prereqs.sh"


def _sh(body, cwd=None, env=None):
    """Run `body` with the helper sourced; return CompletedProcess."""
    script = f'set -euo pipefail\nsource "{HELPER}"\n{body}\n'
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        ["bash", "-c", script], cwd=str(cwd or REPO_ROOT),
        capture_output=True, text=True, env=full_env)


class LocalPrereqsHelperTests(unittest.TestCase):
    def test_helper_exists_and_is_syntactically_valid(self):
        self.assertTrue(HELPER.is_file(), f"missing helper: {HELPER}")
        r = subprocess.run(["bash", "-n", str(HELPER)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_main_checkout_resolves_from_a_linked_worktree(self):
        """The whole mechanism. `git rev-parse --git-common-dir`'s parent is the
        main checkout even when invoked from a linked worktree."""
        r = _sh("bs_main_checkout")
        self.assertEqual(r.returncode, 0, r.stderr)
        main = Path(r.stdout.strip())
        self.assertTrue((main / ".git").exists(),
                        f"{main} does not look like a checkout")
        # A main checkout has .git as a directory; a linked worktree has a file.
        self.assertTrue((main / ".git").is_dir(),
                        f"{main}/.git should be a directory (main checkout)")

    def test_resolve_prefers_a_tree_local_file(self):
        """Tree-local wins so a deliberate per-worktree override still works."""
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "thing.txt"
            local.write_text("local")
            r = _sh(f'bs_resolve_prereq "{local}" "/nonexistent/thing.txt"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), str(local))

    def test_resolve_falls_back_to_the_main_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            fallback = Path(d) / "thing.txt"
            fallback.write_text("fallback")
            r = _sh(f'bs_resolve_prereq "/nonexistent/thing.txt" "{fallback}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), str(fallback))

    def test_resolve_fails_when_neither_location_has_it(self):
        r = _sh('bs_resolve_prereq "/nonexistent/a" "/nonexistent/b"')
        self.assertNotEqual(r.returncode, 0)

    def test_require_reports_every_missing_item_not_just_the_first(self):
        """The point of the change: three round trips collapse to one message."""
        r = _sh(
            'bs_require_prereqs "demo" '
            '"/nonexistent/alpha.env|run alpha-recovery" '
            '"/nonexistent/beta.crt|run beta-recovery" '
            '"/nonexistent/gamma|run gamma-recovery" || true')
        combined = r.stdout + r.stderr
        for name in ("alpha.env", "beta.crt", "gamma"):
            self.assertIn(name, combined,
                          f"{name} missing from the preflight report")
        for recovery in ("alpha-recovery", "beta-recovery", "gamma-recovery"):
            self.assertIn(recovery, combined,
                          f"recovery hint {recovery!r} not shown")

    def test_require_exits_nonzero_when_anything_is_missing(self):
        r = _sh('bs_require_prereqs "demo" "/nonexistent/a|fix it"')
        self.assertNotEqual(r.returncode, 0)

    def test_require_is_quiet_and_zero_when_everything_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a"
            a.write_text("x")
            b = Path(d) / "b"
            b.mkdir()          # directories count too (node_modules, .venv)
            r = _sh(f'bs_require_prereqs "demo" "{a}|fix" "{b}|fix"')
            self.assertEqual(r.returncode, 0, r.stderr)


class ReleaseGateInterpreterTests(unittest.TestCase):
    """The gate must never silently fall back to a bare `python`."""

    GATE = REPO_ROOT / "scripts" / "run_release_gate.sh"

    def test_gate_is_syntactically_valid(self):
        r = subprocess.run(["bash", "-n", str(self.GATE)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gate_has_no_bare_python_fallback(self):
        """`DEFAULT_PYTHON_BIN="python"` is the exact line that made the gate
        report on whichever interpreter the shell happened to resolve."""
        text = self.GATE.read_text()
        self.assertNotIn('DEFAULT_PYTHON_BIN="python"', text)

    def test_gate_preflights_before_running_any_step(self):
        """Preflight must precede step 1; discovering node_modules at step 1 and
        the venv at step 4 is the serial-discovery pattern being removed."""
        text = self.GATE.read_text()
        self.assertIn("bs_require_prereqs", text)
        self.assertLess(text.index("bs_require_prereqs"), text.index("[1/4]"),
                        "preflight must run before the first gate step")


class DeployPreflightTests(unittest.TestCase):
    DEPLOY = REPO_ROOT / "server" / "deploy" / "deploy_to_droplet.sh"

    def test_deploy_is_syntactically_valid(self):
        r = subprocess.run(["bash", "-n", str(self.DEPLOY)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_deploy_preflights_before_the_ci_check_and_rsync(self):
        """2026-08-12: two failed invocations, each re-running the CI check and
        rsync before dying at the next scp.

        Anchor on the real commands at line start — the word "rsync" also appears
        in a comment near the top of the file, which an `index()` on the bare
        string matches instead.
        """
        lines = self.DEPLOY.read_text().splitlines()

        def line_of(pred, what):
            for i, ln in enumerate(lines):
                if pred(ln):
                    return i
            self.fail(f"{what} not found in {self.DEPLOY}")

        preflight = line_of(lambda l: l.startswith("bs_require_prereqs"),
                            "bs_require_prereqs call")
        ci_gate = line_of(lambda l: "check_ci_status.sh" in l and not l.lstrip().startswith("#"),
                          "check_ci_status.sh invocation")
        rsync = line_of(lambda l: l.startswith("rsync "), "rsync command")

        self.assertLess(preflight, ci_gate,
                        "preflight must run before the CI-status gate")
        self.assertLess(preflight, rsync,
                        "preflight must run before any expensive work")

    def test_compaction_unit_passes_the_keep_pin_to_the_command(self):
        """2026-09-19: `BATTLE_OBSERVATION_COMPACT_KEEP=1` was pinned in the
        deploy script AND in live /etc AND documented in three runbooks, and
        production ignored it for six weeks.

        When the compaction moved off Celery onto this timer (2026-08-06), the
        unit was written passing only --statement-timeout. Every other knob fell
        back to its argparse default, and --keep-per-player's default is the
        module constant, not the env var — so prod kept three JSON generations
        per player instead of one, on the largest table in the schema.

        The failure was a missing ARGUMENT, so the assertion has to be on the
        command line the unit runs. Asserting on the function's behaviour would
        have passed throughout.
        """
        lines = self.DEPLOY.read_text().splitlines()
        execs = [ln for ln in lines
                 if ln.startswith("ExecStart=")
                 and "prune_battle_observations" in ln]
        self.assertEqual(len(execs), 1,
                         "expected exactly one compaction ExecStart line")
        exec_line = execs[0]
        self.assertIn("--keep-per-player", exec_line,
                      "the compaction unit must pass the keep pin explicitly; "
                      "the command's own default is the module constant")
        self.assertIn("BATTLE_OBSERVATION_COMPACT_KEEP", exec_line,
                      "the keep argument must read the pinned env value")

    def test_battles_json_prune_unit_carries_its_measured_bounds(self):
        """2026-09-20: the flag was armed and the first real run failed twice.

        The unit had passed only --batch-size and --sleep since June, and every
        firing no-opped behind PRUNE_BATTLES_JSON_ENABLED=0, so its arguments
        had never once been exercised. On the first live run the default 180s
        statement timeout killed it, and the unbounded candidate scan killed it
        again at a smaller batch. Bounded, the same work finished in seconds.

        Asserting on the command line rather than the behaviour, for the same
        reason as the compaction test above: what was wrong was an argument
        that was not there.
        """
        lines = self.DEPLOY.read_text().splitlines()
        execs = [ln for ln in lines
                 if ln.startswith("ExecStart=")
                 and "prune_inactive_player_battles_json" in ln]
        self.assertEqual(len(execs), 1,
                         "expected exactly one battles_json prune ExecStart")
        exec_line = execs[0]
        for flag in ("--statement-timeout", "--max-rows", "--batch-size"):
            self.assertIn(flag, exec_line,
                          f"the prune unit must pass {flag}; its default was "
                          "measured to fail on this table")
        self.assertNotIn("--batch-size 5000", exec_line,
                         "5000 exceeded the statement timeout on its own")

    def test_battle_history_archive_timer_fires_daily(self):
        """2026-09-20: the prune cadence sets the permanent size of two tables.

        A delete-based prune returns nothing to the OS, so BattleEvent and
        PlayerDailyShipStats stay at their high-water mark forever. On the old
        1st-and-15th schedule that mark was ~120 days of rows against a 105-day
        retention; pruned daily it is ~106. At 0.163 GB per day that is ~2.3 GB
        that is either never allocated or never given back, and the choice had
        to be made before the first peak (the window only filled 2026-09-26).

        Pinned here because a timer's schedule is invisible until the day it
        matters, and reverting it would cost nothing to write and gigabytes to
        live with — the same reason the two ExecStart tests above exist.
        """
        text = self.DEPLOY.read_text()
        start = text.index("battlestats-archive-battle-history.timer <<")
        unit = text[start:text.index("\nEOF", start)]
        calendars = [ln for ln in unit.splitlines()
                     if ln.startswith("OnCalendar=")]
        self.assertEqual(len(calendars), 1, "expected exactly one OnCalendar")
        # `*-*-*` is every day. Reject a day-of-month list such as `*-*-01,15`.
        self.assertRegex(
            calendars[0], r"^OnCalendar=\*-\*-\* \d{2}:\d{2}:\d{2} UTC$",
            "the battle-history archive must fire daily, not on listed days")

    def test_deploy_resolves_all_three_untracked_files(self):
        text = self.DEPLOY.read_text()
        for name in (".env.cloud", ".env.secrets.cloud", "ca-certificate.crt"):
            self.assertIn(name, text)
        # Each scp must ship a resolved path, not a bare ${SERVER_DIR}/ one.
        for bad in ('scp "${SERVER_DIR}/.env.cloud"',
                    'scp "${SERVER_DIR}/.env.secrets.cloud"',
                    'scp "${SERVER_DIR}/ca-certificate.crt"'):
            self.assertNotIn(bad, text,
                             "scp must use the resolved path, not SERVER_DIR")
