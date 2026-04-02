from unittest import TestCase
from contextlib import contextmanager
from unittest.mock import Mock, patch

from warships.agentic import build_crewai_plan, persona_keys, run_crewai_workflow


class AgenticCrewAITests(TestCase):
    def test_persona_keys_expose_full_existing_role_set(self):
        self.assertEqual(
            persona_keys(),
            [
                "project_coordinator",
                "project_manager",
                "architect",
                "ux",
                "designer",
                "engineer",
                "qa",
                "safety",
            ],
        )

    def test_build_crewai_plan_defaults_to_hierarchical_full_federation(self):
        plan = build_crewai_plan("Add CrewAI integration")

        self.assertEqual(plan["process"], "hierarchical")
        self.assertEqual(len(plan["roles"]), 8)
        self.assertEqual(plan["roles"][0]["key"], "project_coordinator")
        self.assertIn("crew_goal", plan["roles"][0])
        self.assertIn("expected_output", plan["roles"][0])
        self.assertIn("artifact_fields", plan["roles"][0])
        self.assertEqual(plan["tasks"][1]["depends_on"], "project_coordinator")
        self.assertEqual(
            plan["tasks"][0]["artifact_model"], "RoutingPlanArtifact")

    def test_build_crewai_uses_full_persona_registry_artifact_fields(self):
        plan = build_crewai_plan("Review persona orchestration")

        qa_role = next(role for role in plan["roles"] if role["key"] == "qa")
        self.assertIn("release_recommendation", qa_role["artifact_fields"])

    def test_run_crewai_workflow_returns_planned_status_without_llm(self):
        result = run_crewai_workflow(
            "Add CrewAI integration",
            context={"scope": "agentic framework"},
            dry_run=False,
            llm=None,
        )

        self.assertEqual(result["status"], "planned")
        self.assertIn("crew_plan", result)
        self.assertIn("crew_artifacts", result)
        self.assertTrue(result["crew_artifacts"])
        self.assertIn("No CrewAI LLM configured", " ".join(result["summary"]))
        self.assertTrue(result["run_log_path"])

    @patch("warships.agentic.crewai_runner.build_crewai_crew")
    def test_run_crewai_workflow_can_kickoff_when_llm_present(self, mock_build_crewai_crew):
        fake_crew = mock_build_crewai_crew.return_value
        fake_crew.kickoff.return_value = "crew output"

        result = run_crewai_workflow(
            "Add CrewAI integration",
            llm="gpt-4o-mini",
            dry_run=False,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output"], "crew output")
        fake_crew.kickoff.assert_called_once()

    @patch("warships.agentic.crewai_runner.get_langsmith_project_name", return_value="battlestats-agentic")
    @patch("warships.agentic.crewai_runner.get_current_trace_url", return_value="https://smith.example/runs/crew-1")
    @patch("warships.agentic.crewai_runner.trace_block")
    def test_run_crewai_workflow_includes_langsmith_trace_url_when_available(self, mock_trace_block, _mock_trace_url, _mock_project):
        fake_trace = Mock()
        fake_trace.metadata = {}

        @contextmanager
        def fake_trace_context(*args, **kwargs):
            yield fake_trace

        mock_trace_block.side_effect = fake_trace_context

        result = run_crewai_workflow(
            "Add CrewAI integration",
            context={"scope": "agentic framework"},
            dry_run=True,
        )

        self.assertEqual(result["langsmith_trace_url"],
                         "https://smith.example/runs/crew-1")
        self.assertEqual(result["langsmith_project"], "battlestats-agentic")
        fake_trace.end.assert_called_once()
