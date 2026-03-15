import os
from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import Mock, patch

from warships.agentic import run_graph
from warships.agentic.checkpoints import get_graph_checkpointer, get_langgraph_checkpoint_postgres_url
from langgraph.checkpoint.memory import MemorySaver


class AgenticGraphTests(TestCase):
    def test_checkpoint_url_derived_from_db_environment(self):
        with patch.dict(
            os.environ,
            {
                "DB_ENGINE": "postgresql_psycopg2",
                "DB_NAME": "battlestats",
                "DB_USERNAME": "django",
                "DB_PASSWORD": "secret value",
                "DB_HOST": "db",
                "DB_PORT": "5432",
            },
            clear=False,
        ):
            self.assertEqual(
                get_langgraph_checkpoint_postgres_url(),
                "postgresql://django:secret+value@db:5432/battlestats",
            )

    def test_graph_checkpointer_falls_back_to_memory_when_postgres_not_configured(self):
        with patch.dict(
            os.environ,
            {
                "DB_ENGINE": "sqlite3",
                "LANGGRAPH_CHECKPOINT_POSTGRES_URL": "",
            },
            clear=False,
        ):
            with get_graph_checkpointer() as saver:
                self.assertIsInstance(saver, MemorySaver)

    def test_graph_checkpointer_uses_postgres_backend_when_requested(self):
        captured: dict[str, object] = {}

        class FakeSaver:
            def __init__(self):
                self.setup_called = False

            def setup(self):
                self.setup_called = True

        @contextmanager
        def fake_from_conn_string(conn_string, pipeline=False):
            captured["conn_string"] = conn_string
            captured["pipeline"] = pipeline
            saver = FakeSaver()
            captured["saver"] = saver
            yield saver

        fake_postgres_saver = type(
            "FakePostgresSaver",
            (),
            {"from_conn_string": staticmethod(fake_from_conn_string)},
        )

        with patch.dict(
            os.environ,
            {
                "LANGGRAPH_CHECKPOINT_POSTGRES_URL": "postgresql://example/checkpoints",
                "LANGGRAPH_CHECKPOINT_AUTO_SETUP": "true",
            },
            clear=False,
        ), patch(
            "warships.agentic.checkpoints.PostgresSaver",
            fake_postgres_saver,
        ):
            with get_graph_checkpointer({"checkpoint_backend": "postgres"}) as saver:
                self.assertIs(saver, captured["saver"])

        self.assertEqual(captured["conn_string"],
                         "postgresql://example/checkpoints")
        self.assertFalse(captured["pipeline"])
        self.assertTrue(captured["saver"].setup_called)

    def test_run_graph_completes_when_verification_passes(self):
        result = run_graph(
            "Fix clan hydration in player page",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                }
            },
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["plan"])
        self.assertTrue(result["implementation_notes"])
        self.assertTrue(result["verification_notes"])
        self.assertTrue(result["checks_passed"])

    def test_run_graph_blocks_files_outside_allowed_paths(self):
        result = run_graph(
            "Fix clan hydration in player page",
            context={
                "touched_files": ["secrets/unsafe.txt"],
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                },
            },
        )

        self.assertEqual(result["status"], "needs_attention")
        self.assertFalse(result["boundary_ok"])
        self.assertTrue(result["issues"])

    def test_run_graph_handles_verification_failure(self):
        result = run_graph(
            "Fix clan hydration in player page",
            context={
                "verification": {
                    "tests_passed": False,
                    "lint_passed": True,
                },
                "max_retries": 0,
            },
        )

        self.assertEqual(result["status"], "needs_attention")
        self.assertFalse(result["checks_passed"])
        self.assertTrue(result["issues"])

    def test_run_graph_uses_clan_hydration_plan_template(self):
        result = run_graph(
            "clan information does not hydrate on first player page load",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                }
            },
        )

        self.assertGreaterEqual(len(result["plan"]), 4)
        self.assertIn("PlayerSearch.tsx", " ".join(result["target_files"]))

    def test_run_graph_executes_verification_commands_success(self):
        result = run_graph(
            "simple verification command",
            context={
                "verification_commands": ["python -c \"print('ok')\""],
                "verification_cwd": "server",
            },
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["checks_passed"])
        self.assertTrue(result["command_results"])
        self.assertEqual(result["command_results"][0]["returncode"], 0)

    def test_run_graph_executes_verification_commands_failure(self):
        result = run_graph(
            "failing verification command",
            context={
                "verification_commands": ["python -c \"import sys; sys.exit(2)\""],
                "verification_cwd": "server",
                "max_retries": 0,
            },
        )

        self.assertEqual(result["status"], "needs_attention")
        self.assertFalse(result["checks_passed"])
        self.assertTrue(result["command_results"])
        self.assertEqual(result["command_results"][0]["returncode"], 2)

    def test_run_graph_loads_default_team_doctrine(self):
        result = run_graph(
            "Fix clan hydration in player page",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                }
            },
        )

        self.assertIn("preferred_patterns", result["team_doctrine"])
        self.assertIn("review_priorities", result["team_doctrine"])
        self.assertTrue(result["doctrine_notes"])
        self.assertTrue(any(
            "battlestats doctrine" in note.lower()
            for note in result["doctrine_notes"]
        ))

    def test_run_graph_applies_team_doctrine_overrides_and_style_snippets(self):
        result = run_graph(
            "Design a safer player detail caching flow",
            context={
                "team_doctrine": {
                    "preferred_patterns": [
                        "Prefer feature-flagged rollout for user-visible cache changes.",
                    ],
                    "decision_rules": [
                        "Prefer reversible cache invalidation changes.",
                    ],
                },
                "team_style_snippets": [
                    "Bias toward additive diagnostics when touching agent workflows.",
                ],
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                },
            },
        )

        self.assertIn(
            "Prefer feature-flagged rollout for user-visible cache changes.",
            result["team_doctrine"]["preferred_patterns"],
        )
        self.assertIn(
            "Prefer reversible cache invalidation changes.",
            result["team_doctrine"]["decision_rules"],
        )
        self.assertIn(
            "Bias toward additive diagnostics when touching agent workflows.",
            result["team_doctrine"]["review_priorities"],
        )
        self.assertTrue(any(
            "doctrine overrides" in note.lower()
            for note in result["doctrine_notes"]
        ))

    def test_run_graph_design_review_revises_risky_plan_before_implementation(self):
        result = run_graph(
            "Add cache hydration guardrails for ranked API",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                },
            },
        )

        self.assertTrue(result["design_review_passed"])
        self.assertIn(
            "Add rollback, guardrail, and bounded-load checks before implementation.",
            result["plan"],
        )
        self.assertTrue(any(
            "revised the plan after design review findings" in note.lower()
            for note in result["doctrine_notes"]
        ))

    def test_run_graph_retrieves_guidance_for_matching_task(self):
        result = run_graph(
            "Review the LangSmith trace dashboard rollout and validation plan",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                },
            },
        )

        self.assertTrue(result["retrieved_guidance"])
        self.assertTrue(any(
            "trace" in item["path"]
            for item in result["retrieved_guidance"]
        ))
        self.assertTrue(any(
            "retrieved battlestats guidance" in note.lower()
            for note in result["guidance_notes"]
        ))

    def test_run_graph_api_review_revises_plan_before_implementation(self):
        result = run_graph(
            "Change player summary API response payload",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                },
            },
        )

        self.assertTrue(result["api_review_required"])
        self.assertTrue(result["api_review_passed"])
        self.assertIn(
            "Add API contract, serializer, and backward-compatibility checks for touched endpoints.",
            result["plan"],
        )
        self.assertIn(
            "Add API documentation updates and payload regression tests for user-facing endpoint changes.",
            result["plan"],
        )

    def test_run_graph_stops_when_api_review_cannot_retry(self):
        result = run_graph(
            "Change player summary API response payload",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                },
                "max_api_review_retries": 0,
            },
        )

        self.assertEqual(result["status"], "needs_attention")
        self.assertTrue(result["api_review_required"])
        self.assertFalse(result["api_review_passed"])
        self.assertTrue(result["api_review_notes"])

    def test_run_graph_stops_when_design_review_cannot_retry(self):
        result = run_graph(
            "Add cache hydration guardrails for ranked API",
            context={
                "verification": {
                    "tests_passed": True,
                    "lint_passed": True,
                },
                "max_design_review_retries": 0,
            },
        )

        self.assertEqual(result["status"], "needs_attention")
        self.assertFalse(result["design_review_passed"])
        self.assertTrue(result["design_review_notes"])

    @patch("warships.agentic.graph.get_langsmith_project_name", return_value="battlestats-agentic")
    @patch("warships.agentic.graph.get_current_trace_url", return_value="https://smith.example/runs/graph-1")
    @patch("warships.agentic.graph.trace_block")
    def test_run_graph_includes_langsmith_trace_url_when_available(self, mock_trace_block, _mock_trace_url, _mock_project):
        fake_trace = Mock()
        fake_trace.metadata = {}

        @contextmanager
        def fake_trace_context(*args, **kwargs):
            yield fake_trace

        mock_trace_block.side_effect = fake_trace_context

        result = run_graph(
            "simple verification command",
            context={
                "verification_commands": ["python -c \"print('ok')\""],
                "verification_cwd": "server",
            },
        )

        self.assertEqual(result["langsmith_trace_url"],
                         "https://smith.example/runs/graph-1")
        self.assertEqual(result["langsmith_project"], "battlestats-agentic")
        fake_trace.end.assert_called_once()
