from unittest import TestCase
from unittest.mock import patch
import tempfile
from pathlib import Path
import os

from warships.agentic.memory import (
    build_phase0_memory_candidates,
    get_memory_backend,
    get_memory_environment,
    get_memory_namespace,
    get_memory_store_snapshot,
    infer_workflow_kind,
    is_phase0_memory_enabled,
    persist_phase0_memory_artifacts,
    prepare_phase0_memory_context,
    retrieve_reviewed_memories,
)


class AgenticMemoryTests(TestCase):
    def test_get_memory_environment_normalizes_known_values(self):
        with patch.dict("os.environ", {"BATTLESTATS_AGENTIC_ENV": "production"}, clear=False):
            self.assertEqual(get_memory_environment(), "prod-agentic")

    def test_get_memory_namespace_uses_battlestats_prefix(self):
        namespace = get_memory_namespace("procedural", environment="staging")
        self.assertEqual(namespace, ("battlestats", "staging", "procedural"))

    def test_get_memory_backend_defaults_to_file(self):
        self.assertEqual(get_memory_backend({}), "file")

    def test_get_memory_backend_supports_langgraph_aliases(self):
        with patch.dict("os.environ", {"BATTLESTATS_AGENTIC_MEMORY_BACKEND": "langgraph-memory"}, clear=False):
            self.assertEqual(get_memory_backend({}), "langgraph_memory")

    def test_phase0_memory_is_langgraph_only(self):
        with patch.dict("os.environ", {"BATTLESTATS_LANGMEM_ENABLED": "true"}, clear=False):
            self.assertFalse(is_phase0_memory_enabled("crewai", {}))
            self.assertTrue(is_phase0_memory_enabled("langgraph", {}))

    def test_infer_workflow_kind_prefers_client_route_smoke(self):
        workflow_kind = infer_workflow_kind(
            "Add browser smoke coverage for player detail tabs",
            touched_files=["client/e2e/player-detail-tabs.spec.ts"],
            verification_commands=[
                "npm run test:e2e -- e2e/player-detail-tabs.spec.ts"],
        )
        self.assertEqual(workflow_kind, "client_route_smoke")

    def test_retrieve_reviewed_memories_filters_and_bounds_results(self):
        records = [
            {
                "memory_type": "procedural",
                "namespace": ("battlestats", "local", "procedural"),
                "workflow_kind": "cache_behavior",
                "summary": "Use focused cache tests first.",
                "review_status": "reviewed",
                "confidence": 0.8,
                "created_at": "2026-03-26T12:00:00Z",
            },
            {
                "memory_type": "procedural",
                "namespace": ("battlestats", "local", "procedural"),
                "workflow_kind": "cache_behavior",
                "summary": "Use paced browser checks for warmup work.",
                "review_status": "approved",
                "confidence": 0.9,
                "created_at": "2026-03-26T13:00:00Z",
            },
            {
                "memory_type": "procedural",
                "namespace": ("battlestats", "local", "procedural"),
                "workflow_kind": "cache_behavior",
                "summary": "Ignore this unreviewed record.",
                "review_status": "candidate",
                "confidence": 1.0,
                "created_at": "2026-03-26T14:00:00Z",
            },
        ]

        retrieved = retrieve_reviewed_memories(
            records,
            workflow_kind="cache_behavior",
            namespace=("battlestats", "local", "procedural"),
            limit=1,
        )

        self.assertEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0]["summary"],
                         "Use paced browser checks for warmup work.")

    def test_prepare_phase0_memory_context_returns_retrieval_notes_when_enabled(self):
        records = [{
            "memory_type": "procedural",
            "namespace": ("battlestats", "local", "procedural"),
            "workflow_kind": "agentic_workflow",
            "summary": "Use focused dashboard tests before broad suites.",
            "review_status": "reviewed",
            "confidence": 0.8,
            "created_at": "2026-03-26T12:00:00Z",
            "evidence": {
                "validation_commands": ["python -m pytest warships/tests/test_agentic_dashboard.py -q"],
                "file_paths": ["server/warships/agentic/dashboard.py"],
            },
        }]

        with patch.dict("os.environ", {"BATTLESTATS_LANGMEM_ENABLED": "true", "BATTLESTATS_AGENTIC_ENV": "local"}, clear=False):
            context = prepare_phase0_memory_context(
                "Improve the agentic trace dashboard",
                {"memory_records": records},
            )

        self.assertTrue(context["memory_enabled"])
        self.assertEqual(context["workflow_kind"], "agentic_workflow")
        self.assertEqual(len(context["retrieved_memories"]), 1)
        self.assertTrue(
            any("Reviewed procedural memory" in note for note in context["memory_notes"]))

    def test_build_phase0_memory_candidates_requires_completed_memory_enabled_run(self):
        candidates = build_phase0_memory_candidates({
            "memory_enabled": True,
            "status": "completed",
            "workflow_kind": "cache_behavior",
            "memory_environment": "local",
            "workflow_id": "run-123",
            "selected_engine": "langgraph",
            "touched_files": ["server/warships/views.py"],
            "verification_commands": ["python -m pytest warships/tests/test_views.py -q"],
            "retrieved_guidance": [{"path": "agents/runbooks/runbook-langgraph-opinionated-workflow.md"}],
        })

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["review_status"], "candidate")
        self.assertIn("comparison_paths", candidates[0])

    def test_persist_phase0_memory_artifacts_queues_candidates_and_requires_explicit_review_for_promotion(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch("warships.agentic.memory._memory_root", return_value=Path(temp_dir) / "memory"):
            result = {
                "memory_enabled": True,
                "selected_engine": "langgraph",
                "workflow_id": "run-123",
                "workflow_kind": "agentic_workflow",
                "memory_environment": "local",
                "memory_namespace": ("battlestats", "local", "procedural"),
                "memory_candidates": [{
                    "candidate_id": "run-123:candidate:1",
                    "memory_id": "mem-1",
                    "memory_type": "procedural",
                    "workflow_kind": "agentic_workflow",
                    "namespace": ("battlestats", "local", "procedural"),
                    "summary": "Reuse the validated command set for agentic_workflow workflows.",
                    "detail": "Focused validation commands succeeded.",
                    "review_status": "candidate",
                    "confidence": 0.6,
                    "source_run_id": "run-123",
                    "engine": "langgraph",
                    "evidence": {
                        "validation_commands": ["python -m pytest warships/tests/test_agentic_dashboard.py -q"],
                        "file_paths": ["server/warships/agentic/dashboard.py"],
                    },
                    "comparison_paths": ["agents/runbooks/runbook-langgraph-opinionated-workflow.md"],
                    "created_at": "2026-03-26T12:00:00Z",
                }],
            }

            queued = persist_phase0_memory_artifacts(result)
            snapshot_after_queue = get_memory_store_snapshot(limit=5)
            promoted = persist_phase0_memory_artifacts(
                result,
                review_context={
                    "approved_candidate_ids": ["run-123:candidate:1"],
                    "reviewed_by": "maintainer",
                },
            )
            snapshot_after_review = get_memory_store_snapshot(limit=5)

        self.assertEqual(queued["queued_candidate_count"], 1)
        self.assertEqual(queued["promoted_count"], 0)
        self.assertEqual(snapshot_after_queue["pending_review_total"], 1)
        self.assertEqual(promoted["promoted_count"], 1)
        self.assertEqual(snapshot_after_review["reviewed_total"], 1)
        self.assertEqual(
            snapshot_after_review["recent_reviewed"][0]["reviewed_by"], "maintainer")

    def test_persist_phase0_memory_artifacts_marks_superseded_reviewed_records(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch("warships.agentic.memory._memory_root", return_value=Path(temp_dir) / "memory"):
            first = {
                "memory_enabled": True,
                "selected_engine": "langgraph",
                "workflow_id": "run-old",
                "workflow_kind": "agentic_workflow",
                "memory_environment": "local",
                "memory_namespace": ("battlestats", "local", "procedural"),
                "memory_candidates": [{
                    "candidate_id": "run-old:candidate:1",
                    "memory_id": "mem-old",
                    "memory_type": "procedural",
                    "workflow_kind": "agentic_workflow",
                    "namespace": ("battlestats", "local", "procedural"),
                    "summary": "Use the old validation flow.",
                    "detail": "Older guidance.",
                    "review_status": "candidate",
                    "confidence": 0.4,
                    "source_run_id": "run-old",
                    "engine": "langgraph",
                    "evidence": {"validation_commands": [], "file_paths": ["server/warships/views.py"]},
                    "comparison_paths": [],
                    "created_at": "2026-03-26T11:00:00Z",
                }],
            }
            second = {
                "memory_enabled": True,
                "selected_engine": "langgraph",
                "workflow_id": "run-new",
                "workflow_kind": "agentic_workflow",
                "memory_environment": "local",
                "memory_namespace": ("battlestats", "local", "procedural"),
                "memory_candidates": [{
                    "candidate_id": "run-new:candidate:1",
                    "memory_id": "mem-new",
                    "memory_type": "procedural",
                    "workflow_kind": "agentic_workflow",
                    "namespace": ("battlestats", "local", "procedural"),
                    "summary": "Use the new validation flow.",
                    "detail": "Newer guidance.",
                    "review_status": "candidate",
                    "confidence": 0.8,
                    "source_run_id": "run-new",
                    "engine": "langgraph",
                    "evidence": {"validation_commands": [], "file_paths": ["server/warships/agentic/dashboard.py"]},
                    "comparison_paths": [],
                    "created_at": "2026-03-26T12:00:00Z",
                }],
            }

            persist_phase0_memory_artifacts(first, {"approved_candidate_ids": [
                                            "run-old:candidate:1"], "reviewed_by": "maintainer"})
            persist_phase0_memory_artifacts(second, {"approved_candidate_ids": [
                                            "run-new:candidate:1"], "reviewed_by": "maintainer", "supersedes": {"run-new:candidate:1": ["mem-old"]}})
            snapshot = get_memory_store_snapshot(limit=5)

        self.assertEqual(snapshot["reviewed_total"], 1)
        self.assertEqual(snapshot["superseded_total"], 1)
        self.assertEqual(snapshot["recent_reviewed"]
                         [0]["memory_id"], "mem-new")

    def test_persist_phase0_memory_artifacts_supports_langgraph_memory_backend(self):
        result = {
            "memory_enabled": True,
            "memory_backend": "langgraph_memory",
            "selected_engine": "langgraph",
            "workflow_id": "run-store",
            "workflow_kind": "agentic_workflow",
            "memory_environment": "local",
            "memory_namespace": ("battlestats", "local", "procedural"),
            "memory_candidates": [{
                "candidate_id": "run-store:candidate:1",
                "memory_id": "mem-store",
                "memory_type": "procedural",
                "workflow_kind": "agentic_workflow",
                "namespace": ("battlestats", "local", "procedural"),
                "summary": "Use the LangGraph store-backed review flow.",
                "detail": "Store-backed candidate.",
                "review_status": "candidate",
                "confidence": 0.7,
                "source_run_id": "run-store",
                "engine": "langgraph",
                "evidence": {"validation_commands": [], "file_paths": ["server/warships/agentic/memory.py"]},
                "comparison_paths": [],
                "created_at": "2026-03-26T12:00:00Z",
            }],
        }

        with patch.dict(os.environ, {"BATTLESTATS_AGENTIC_MEMORY_BACKEND": "langgraph_memory"}, clear=False), patch("warships.agentic.memory._LANGGRAPH_IN_MEMORY_STORE", new=None):
            activity = persist_phase0_memory_artifacts(
                result,
                review_context={"approved_candidate_ids": [
                    "run-store:candidate:1"], "reviewed_by": "maintainer"},
                context={"memory_backend": "langgraph_memory",
                         "memory_environment": "local"},
            )
            snapshot = get_memory_store_snapshot(limit=5, context={
                                                 "memory_backend": "langgraph_memory", "memory_environment": "local"})

        self.assertEqual(activity["backend"], "langgraph_memory")
        self.assertEqual(activity["promoted_count"], 1)
        self.assertEqual(snapshot["backend"], "langgraph_memory")
        self.assertEqual(snapshot["reviewed_total"], 1)
        self.assertEqual(snapshot["recent_reviewed"]
                         [0]["memory_id"], "mem-store")
