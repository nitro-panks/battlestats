import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from warships.agentic.dashboard import _log_root, get_agentic_trace_dashboard


class AgenticDashboardTests(TestCase):
    def test_dashboard_summarizes_recent_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_root = Path(temp_dir) / "agentic"
            (log_root / "hybrid").mkdir(parents=True)
            (log_root / "langgraph").mkdir(parents=True)

            (log_root / "hybrid" / "run-1.json").write_text(json.dumps({
                "workflow_id": "run-1",
                "status": "completed",
                "selected_engine": "hybrid",
                "route_rationale": "Task mixes planning and implementation.",
                "summary": ["Hybrid workflow executed."],
                "logged_at": "2026-03-15T12:00:00Z",
                "langgraph_result": {
                    "task": "Evaluate trace dashboard spec",
                    "workflow_kind": "agentic_workflow",
                    "memory_enabled": True,
                    "retrieved_memories": [{
                        "summary": "Reuse the trace validation commands for dashboard work.",
                        "review_status": "reviewed",
                    }],
                    "memory_candidates": [{
                        "summary": "Reuse the validated command set for agentic_workflow workflows.",
                        "review_status": "candidate",
                    }],
                    "memory_store_activity": {
                        "backend": "file",
                        "candidate_queue_path": "logs/agentic/memory/pending/run-1.json",
                        "queued_candidate_count": 1,
                        "promoted_count": 1,
                        "reviewed_store_paths": ["logs/agentic/memory/reviewed/battlestats__local__procedural.json"],
                    },
                    "checks_passed": True,
                    "boundary_ok": True,
                    "design_review_passed": True,
                    "api_review_required": True,
                    "api_review_passed": True,
                    "doctrine_notes": ["Loaded repo doctrine from agents/knowledge/agentic-team-doctrine.json."],
                    "guidance_notes": ["Retrieved 2 guidance documents from runbooks/reviews."],
                    "retrieved_guidance": [
                        {"path": "agents/runbooks/runbook-langgraph-opinionated-workflow.md"},
                        {"path": "agents/reviews/architect-analysis.md"},
                    ],
                    "issues": [],
                    "verification_commands": ["python -m pytest warships/tests/test_views.py -q"],
                    "touched_files": ["server/warships/views.py", "client/app/trace/page.tsx"],
                },
                "langsmith_trace_url": "https://smith.example/runs/run-1",
            }), encoding="utf-8")

            (log_root / "langgraph" / "run-2.json").write_text(json.dumps({
                "workflow_id": "run-2",
                "status": "needs_attention",
                "selected_engine": "langgraph",
                "summary": ["Verification failed."],
                "logged_at": "2026-03-14T12:00:00Z",
                "task": "Fix trace dashboard endpoint",
                "workflow_kind": "agentic_workflow",
                "memory_enabled": False,
                "retrieved_memories": [],
                "memory_candidates": [],
                "memory_store_activity": {
                    "backend": "disabled",
                    "queued_candidate_count": 0,
                    "promoted_count": 0,
                    "reviewed_store_paths": [],
                },
                "checks_passed": False,
                "boundary_ok": False,
                "design_review_passed": False,
                "api_review_required": False,
                "api_review_passed": None,
                "doctrine_notes": ["Loaded repo doctrine from agents/knowledge/agentic-team-doctrine.json."],
                "guidance_notes": [],
                "retrieved_guidance": [],
                "issues": ["Verification failed: required checks did not pass"],
                "verification_commands": ["python -m pytest warships/tests/test_agentic_dashboard.py -q"],
                "touched_files": ["server/warships/agentic/dashboard.py"],
            }), encoding="utf-8")

            memory_root = Path(temp_dir) / "memory"
            (memory_root / "reviewed").mkdir(parents=True)
            (memory_root / "pending").mkdir(parents=True)
            (memory_root / "reviewed" / "battlestats__local__procedural.json").write_text(json.dumps({
                "version": 1,
                "namespace": ["battlestats", "local", "procedural"],
                "records": [{
                    "memory_id": "mem-1",
                    "summary": "Reuse the trace validation commands for dashboard work.",
                    "workflow_kind": "agentic_workflow",
                    "review_status": "reviewed",
                    "reviewed_at": "2026-03-15T12:10:00Z",
                    "provenance": {"source_run_id": "run-1", "engine": "langgraph"},
                    "supersedes": [],
                }, {
                    "memory_id": "mem-old",
                    "summary": "Old trace guidance.",
                    "workflow_kind": "agentic_workflow",
                    "review_status": "superseded",
                    "reviewed_at": "2026-03-14T11:00:00Z",
                    "superseded_by": "mem-1",
                    "provenance": {"source_run_id": "run-0", "engine": "langgraph"},
                    "supersedes": [],
                }],
            }), encoding="utf-8")
            (memory_root / "pending" / "run-1.json").write_text(json.dumps({
                "version": 1,
                "workflow_id": "run-1",
                "namespace": ["battlestats", "local", "procedural"],
                "candidates": [{
                    "candidate_id": "run-1:candidate:1",
                    "summary": "Reuse the validated command set for agentic_workflow workflows.",
                    "workflow_kind": "agentic_workflow",
                    "review_status": "reviewed",
                    "memory_id": "mem-1",
                    "provenance": {"source_run_id": "run-1", "engine": "langgraph"},
                    "supersedes": ["mem-old"],
                }],
            }), encoding="utf-8")

            with patch("warships.agentic.dashboard._log_root", return_value=log_root), patch("warships.agentic.memory._memory_root", return_value=memory_root), patch.dict(
                "os.environ",
                {
                    "LANGSMITH_TRACING_V2": "true",
                    "LANGSMITH_API_KEY": "secret-key",
                    "BATTLESTATS_LANGSMITH_PROJECT": "trace-lab",
                },
                clear=False,
            ):
                payload = get_agentic_trace_dashboard(limit=10)

        self.assertEqual(payload["project_name"], "trace-lab")
        self.assertTrue(payload["tracing_enabled"])
        self.assertTrue(payload["api_key_configured"])
        self.assertEqual(payload["diagnostics"]["total_runs"], 2)
        self.assertEqual(payload["diagnostics"]["runs_with_trace_urls"], 1)
        self.assertEqual(payload["diagnostics"]["boundary_block_count"], 1)
        self.assertEqual(payload["diagnostics"]["runs_with_doctrine"], 2)
        self.assertEqual(payload["diagnostics"]["runs_with_guidance"], 1)
        self.assertEqual(payload["diagnostics"]["design_review_fail_count"], 1)
        self.assertEqual(payload["diagnostics"]["api_review_fail_count"], 0)
        self.assertEqual(payload["diagnostics"]
                         ["verification_pass_rate"], 50.0)
        self.assertEqual(payload["recent_runs"][0]["workflow_id"], "run-1")
        self.assertEqual(payload["recent_runs"][0]["guidance_match_count"], 2)
        self.assertEqual(payload["recent_runs"][0]["doctrine_note_count"], 1)
        self.assertTrue(payload["recent_runs"][0]["design_review_passed"])
        self.assertTrue(payload["recent_runs"][0]["api_review_passed"])
        self.assertTrue(payload["recent_runs"][0]["memory_enabled"])
        self.assertEqual(payload["recent_runs"][0]
                         ["memory_retrieval_count"], 1)
        self.assertEqual(payload["recent_runs"][0]
                         ["memory_candidate_count"], 1)
        self.assertEqual(payload["diagnostics"]["runs_with_memory_enabled"], 1)
        self.assertEqual(payload["diagnostics"]
                         ["runs_with_memory_retrievals"], 1)
        self.assertEqual(payload["diagnostics"]["memory_candidate_total"], 1)
        self.assertEqual(payload["diagnostics"]["reviewed_memory_total"], 1)
        self.assertEqual(payload["diagnostics"]["pending_review_total"], 0)
        self.assertEqual(payload["diagnostics"]["superseded_memory_total"], 1)
        self.assertEqual(
            payload["learning"]["common_guidance_paths"][0]["label"],
            "agents/runbooks/runbook-langgraph-opinionated-workflow.md",
        )
        self.assertEqual(
            payload["learning"]["common_workflow_kinds"][0]["label"],
            "agentic_workflow",
        )
        self.assertEqual(
            payload["learning"]["memory_candidate_summaries"][0]["label"],
            "Reuse the validated command set for agentic_workflow workflows.",
        )
        self.assertEqual(
            payload["learning"]["reviewed_store_paths"][0]["label"],
            "logs/agentic/memory/reviewed/battlestats__local__procedural.json",
        )
        self.assertEqual(payload["memory_store"]
                         ["recent_reviewed"][0]["memory_id"], "mem-1")
        self.assertEqual(payload["memory_store"]["recent_reviewed"]
                         [0]["provenance"]["source_run_id"], "run-1")
        self.assertEqual(
            payload["memory_store"]["recent_candidates"][0]["supersedes"], ["mem-old"])
        self.assertEqual(
            payload["learning"]["chart_tuning_notes"][0]["runbook_path"],
            "agents/runbooks/archive/runbook-ranked-wr-battles-heatmap-granularity.md",
        )
        self.assertEqual(
            payload["learning"]["chart_tuning_notes"][0]["details"][1]["value"],
            "0.75 win-rate points",
        )
        self.assertIn(
            "server/warships/views.py",
            [item["label"]
                for item in payload["learning"]["common_touched_files"]],
        )

    def test_dashboard_handles_missing_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_root = Path(temp_dir) / "agentic"
            log_root.mkdir(parents=True)

            with patch("warships.agentic.dashboard._log_root", return_value=log_root), patch.dict("os.environ", {}, clear=False):
                payload = get_agentic_trace_dashboard(limit=5)

        self.assertEqual(payload["recent_runs"], [])
        self.assertEqual(payload["diagnostics"]["total_runs"], 0)
        self.assertEqual(payload["learning"]["recurring_issues"], [])
        self.assertEqual(payload["learning"]["common_guidance_paths"], [])
        self.assertEqual(payload["learning"]["memory_candidate_summaries"], [])
        self.assertEqual(payload["memory_store"]["recent_reviewed"], [])
        self.assertEqual(payload["learning"]["chart_tuning_notes"]
                         [0]["slug"], "ranked_wr_battles_heatmap")

    def test_log_root_uses_server_app_layout_when_manage_py_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "manage.py").write_text("", encoding="utf-8")

            with patch("warships.agentic.dashboard._project_root", return_value=project_root):
                self.assertEqual(_log_root(), project_root /
                                 "logs" / "agentic")
