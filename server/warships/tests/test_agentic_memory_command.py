from io import StringIO
import os
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from warships.agentic.memory import get_memory_store_snapshot, persist_phase0_memory_artifacts


class AgenticMemoryReviewCommandTests(TestCase):
    def _queue_candidate(self) -> None:
        persist_phase0_memory_artifacts(
            {
                "memory_enabled": True,
                "memory_backend": "langgraph_memory",
                "selected_engine": "langgraph",
                "workflow_id": "run-cmd",
                "workflow_kind": "agentic_workflow",
                "memory_environment": "local",
                "memory_namespace": ("battlestats", "local", "procedural"),
                "memory_candidates": [{
                    "candidate_id": "run-cmd:candidate:1",
                    "memory_id": "mem-cmd",
                    "memory_type": "procedural",
                    "workflow_kind": "agentic_workflow",
                    "namespace": ("battlestats", "local", "procedural"),
                    "summary": "Review the queued dashboard memory.",
                    "detail": "Queued for command review.",
                    "review_status": "candidate",
                    "confidence": 0.7,
                    "source_run_id": "run-cmd",
                    "engine": "langgraph",
                    "evidence": {"validation_commands": [], "file_paths": ["server/warships/agentic/dashboard.py"]},
                    "comparison_paths": [],
                    "created_at": "2026-03-26T12:00:00Z",
                }],
            },
            context={"memory_backend": "langgraph_memory",
                     "memory_environment": "local"},
        )

    def test_command_lists_pending_candidates_for_workflow(self):
        stdout = StringIO()

        with patch.dict(os.environ, {"BATTLESTATS_AGENTIC_MEMORY_BACKEND": "langgraph_memory"}, clear=False), patch("warships.agentic.memory._LANGGRAPH_IN_MEMORY_STORE", new=None):
            self._queue_candidate()
            call_command(
                "review_agent_memory",
                workflow_id="run-cmd",
                backend="langgraph_memory",
                stdout=stdout,
            )

        self.assertIn("run-cmd:candidate:1", stdout.getvalue())
        self.assertIn("Review the queued dashboard memory.", stdout.getvalue())

    def test_command_promotes_candidates_without_workflow_context_injection(self):
        stdout = StringIO()

        with patch.dict(os.environ, {"BATTLESTATS_AGENTIC_MEMORY_BACKEND": "langgraph_memory"}, clear=False), patch("warships.agentic.memory._LANGGRAPH_IN_MEMORY_STORE", new=None):
            self._queue_candidate()
            call_command(
                "review_agent_memory",
                workflow_id="run-cmd",
                approve=["run-cmd:candidate:1"],
                backend="langgraph_memory",
                reviewed_by="cli-maintainer",
                stdout=stdout,
            )
            snapshot = get_memory_store_snapshot(limit=5, context={
                                                 "memory_backend": "langgraph_memory", "memory_environment": "local"})

        self.assertIn("Promoted: 1", stdout.getvalue())
        self.assertEqual(snapshot["reviewed_total"], 1)
        self.assertEqual(snapshot["recent_reviewed"]
                         [0]["reviewed_by"], "cli-maintainer")
