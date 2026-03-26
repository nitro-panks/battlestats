from pathlib import Path
from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import Mock, patch

from warships.agentic import resolve_crewai_policy, route_agent_workflow, run_routed_workflow
from warships.agentic.router import _prepare_langgraph_context


class AgenticRouterTests(TestCase):
    def test_resolve_crewai_policy_defaults_to_unconfigured(self):
        with patch.dict("os.environ", {}, clear=False):
            policy = resolve_crewai_policy()
        self.assertFalse(policy.configured)
        self.assertIsNone(policy.model)

    def test_resolve_crewai_policy_rejects_disallowed_provider(self):
        with patch.dict("os.environ", {"CREWAI_ALLOWED_PROVIDERS": "openai", "CREWAI_LLM_PROVIDER": "anthropic", "CREWAI_LLM_MODEL": "claude-3-7-sonnet"}, clear=False):
            with self.assertRaises(ValueError):
                resolve_crewai_policy()

    def test_route_agent_workflow_prefers_hybrid_for_plan_and_implementation(self):
        route = route_agent_workflow("plan and implement CrewAI rollout")
        self.assertEqual(route["engine"], "hybrid")

    def test_prepare_langgraph_context_forces_memory_without_checkpoint_credentials(self):
        with patch.dict("os.environ", {"DB_ENGINE": "postgresql_psycopg2", "DB_PASSWORD": "", "LANGGRAPH_CHECKPOINT_POSTGRES_URL": ""}, clear=False):
            context = _prepare_langgraph_context({})
        self.assertEqual(context["checkpoint_backend"], "memory")

    def test_prepare_langgraph_context_enables_phase0_memory_when_flagged(self):
        with patch.dict("os.environ", {"BATTLESTATS_LANGMEM_ENABLED": "true", "BATTLESTATS_AGENTIC_ENV": "staging"}, clear=False):
            context = _prepare_langgraph_context({})

        self.assertTrue(context["memory_enabled"])
        self.assertEqual(context["memory_environment"], "staging")

    @patch("warships.agentic.router.write_agent_run_log")
    @patch("warships.agentic.router.persist_phase0_memory_artifacts")
    @patch("warships.agentic.router.run_crewai_workflow")
    @patch("warships.agentic.router.run_graph")
    def test_run_routed_workflow_hybrid_combines_both_engines(self, mock_run_graph, mock_run_crewai_workflow, mock_persist_phase0_memory_artifacts, mock_write_agent_run_log):
        mock_run_crewai_workflow.return_value = {
            "workflow_id": "crew-1", "status": "planned", "summary": ["crew"]}
        mock_run_graph.return_value = {
            "workflow_id": "graph-1", "status": "completed", "summary": ["graph"]}
        mock_persist_phase0_memory_artifacts.return_value = {
            "backend": "file", "queued_candidate_count": 0}
        mock_write_agent_run_log.return_value = "/tmp/hybrid.json"

        result = run_routed_workflow(
            "plan and implement CrewAI rollout", engine="hybrid")

        self.assertEqual(result["selected_engine"], "hybrid")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["run_log_path"], "/tmp/hybrid.json")
        mock_persist_phase0_memory_artifacts.assert_not_called()

    @patch("warships.agentic.router.write_agent_run_log")
    @patch("warships.agentic.router.persist_phase0_memory_artifacts")
    @patch("warships.agentic.router.run_graph")
    def test_run_routed_workflow_langgraph_persists_memory_artifacts(self, mock_run_graph, mock_persist_phase0_memory_artifacts, mock_write_agent_run_log):
        mock_run_graph.return_value = {
            "workflow_id": "graph-1",
            "status": "completed",
            "summary": ["graph"],
            "memory_enabled": True,
        }
        mock_persist_phase0_memory_artifacts.return_value = {
            "backend": "file", "queued_candidate_count": 1}
        mock_write_agent_run_log.return_value = "/tmp/langgraph.json"

        result = run_routed_workflow(
            "implement routed tracing", engine="langgraph", context={"memory_review": {"approved_candidate_ids": ["graph-1:candidate:1"]}})

        self.assertEqual(result["memory_store_activity"]
                         ["queued_candidate_count"], 1)
        mock_persist_phase0_memory_artifacts.assert_called_once()

    def test_crewai_dry_run_writes_log_file(self):
        result = run_routed_workflow(
            "plan CrewAI rollout", engine="crewai", dry_run=True)
        self.assertEqual(result["selected_engine"], "crewai")
        self.assertTrue(result["run_log_path"])
        self.assertTrue(Path(result["run_log_path"]).exists())

    @patch("warships.agentic.router.get_langsmith_project_name", return_value="battlestats-agentic")
    @patch("warships.agentic.router.get_current_trace_url", return_value="https://smith.example/runs/router-1")
    @patch("warships.agentic.router.trace_block")
    @patch("warships.agentic.router.write_agent_run_log")
    @patch("warships.agentic.router.persist_phase0_memory_artifacts")
    @patch("warships.agentic.router.run_graph")
    def test_run_routed_workflow_includes_langsmith_trace_url_when_available(self, mock_run_graph, mock_persist_phase0_memory_artifacts, mock_write_agent_run_log, mock_trace_block, _mock_trace_url, _mock_project):
        fake_trace = Mock()
        fake_trace.metadata = {}

        @contextmanager
        def fake_trace_context(*args, **kwargs):
            yield fake_trace

        mock_trace_block.side_effect = fake_trace_context
        mock_run_graph.return_value = {
            "workflow_id": "graph-1",
            "status": "completed",
            "summary": ["graph"],
            "memory_enabled": True,
        }
        mock_persist_phase0_memory_artifacts.return_value = {
            "backend": "file", "queued_candidate_count": 0}
        mock_write_agent_run_log.return_value = "/tmp/langgraph.json"

        result = run_routed_workflow(
            "implement routed tracing", engine="langgraph")

        self.assertEqual(result["langsmith_trace_url"],
                         "https://smith.example/runs/router-1")
        self.assertEqual(result["langsmith_project"], "battlestats-agentic")
        fake_trace.end.assert_called_once()
