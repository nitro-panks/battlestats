"""The retention commands self-gate on their env kill switch so the systemd
timers can call them directly (no fragile inline shell gate — that escaping
broke the deploy heredoc, runbook-data-lifecycle-architecture-2026-06-21).

Each command no-ops while its gate is unset, runs the work when it is "1", and
allows --dry-run regardless. These tests pin that contract.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase


class PruneBattlesJsonGateTests(TestCase):
    CMD = "prune_inactive_player_battles_json"
    WORK = ("warships.management.commands."
            "prune_inactive_player_battles_json.prune_inactive_player_battles_json")

    @mock.patch.dict("os.environ", {}, clear=False)
    def test_no_op_when_gate_unset(self):
        import os
        os.environ.pop("PRUNE_BATTLES_JSON_ENABLED", None)
        with mock.patch(self.WORK) as work:
            out = StringIO()
            call_command(self.CMD, "--batch-size", "100", stdout=out)
            work.assert_not_called()
            self.assertIn("no-op", out.getvalue())

    @mock.patch.dict("os.environ", {"PRUNE_BATTLES_JSON_ENABLED": "1"})
    def test_runs_when_gate_enabled(self):
        with mock.patch(self.WORK, return_value={
            "dry_run": False, "cleared": 0, "batches": 0, "inactive_days": 200,
            "cutoff": "x",
        }) as work:
            call_command(self.CMD, "--batch-size", "100", stdout=StringIO())
            work.assert_called_once()

    @mock.patch.dict("os.environ", {}, clear=False)
    def test_dry_run_bypasses_gate(self):
        import os
        os.environ.pop("PRUNE_BATTLES_JSON_ENABLED", None)
        with mock.patch(self.WORK, return_value={
            "dry_run": True, "candidates": 0, "inactive_days": 200, "cutoff": "x",
            "pending_intersection": 0, "reclaimable_bytes": 0,
        }) as work:
            call_command(self.CMD, "--batch-size", "100", "--dry-run", stdout=StringIO())
            work.assert_called_once()


class CleanupEntityVisitsGateTests(TestCase):
    CMD = "cleanup_entity_visit_events"
    WORK = ("warships.management.commands."
            "cleanup_entity_visit_events.cleanup_entity_visit_events")

    @mock.patch.dict("os.environ", {}, clear=False)
    def test_no_op_when_gate_unset(self):
        import os
        os.environ.pop("ENTITY_VISIT_CLEANUP_ENABLED", None)
        with mock.patch(self.WORK) as work:
            out = StringIO()
            call_command(self.CMD, stdout=out)  # no --older-than-days: env default
            work.assert_not_called()
            self.assertIn("no-op", out.getvalue())

    @mock.patch.dict("os.environ", {"ENTITY_VISIT_CLEANUP_ENABLED": "1"})
    def test_runs_when_gate_enabled(self):
        with mock.patch(self.WORK, return_value={"deleted": 0}) as work:
            call_command(self.CMD, stdout=StringIO())
            work.assert_called_once()
            # window defaulted from env (180), no inline arg needed by the timer
            self.assertEqual(work.call_args.kwargs["older_than_days"], 180)
