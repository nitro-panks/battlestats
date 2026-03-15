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
                    "checks_passed": True,
                    "boundary_ok": True,
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
                "checks_passed": False,
                "boundary_ok": False,
                "issues": ["Verification failed: required checks did not pass"],
                "verification_commands": ["python -m pytest warships/tests/test_agentic_dashboard.py -q"],
                "touched_files": ["server/warships/agentic/dashboard.py"],
            }), encoding="utf-8")

            with patch("warships.agentic.dashboard._log_root", return_value=log_root), patch.dict(
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
        self.assertEqual(payload["diagnostics"]
                         ["verification_pass_rate"], 50.0)
        self.assertEqual(payload["recent_runs"][0]["workflow_id"], "run-1")
        self.assertEqual(
            payload["learning"]["chart_tuning_notes"][0]["runbook_path"],
            "agents/runbooks/runbook-ranked-wr-battles-heatmap-granularity.md",
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
        self.assertEqual(payload["learning"]["chart_tuning_notes"]
                         [0]["slug"], "ranked_wr_battles_heatmap")

    def test_log_root_uses_server_app_layout_when_manage_py_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "manage.py").write_text("", encoding="utf-8")

            with patch("warships.agentic.dashboard._project_root", return_value=project_root):
                self.assertEqual(_log_root(), project_root /
                                 "logs" / "agentic")
