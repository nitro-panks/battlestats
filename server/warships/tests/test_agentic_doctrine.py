import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from warships.agentic.doctrine import load_repo_team_doctrine, merge_team_doctrine
from warships.agentic.retrieval import retrieve_doctrine_guidance


class AgenticDoctrineTests(TestCase):
    def test_load_repo_team_doctrine_merges_file_backed_values(self):
        with TemporaryDirectory() as temp_dir:
            doctrine_path = Path(temp_dir) / "team-doctrine.json"
            doctrine_path.write_text(json.dumps({
                "preferred_patterns": [
                    "Prefer feature flags for high-risk user-visible changes.",
                ],
                "decision_rules": [
                    "Keep API contract updates in the same tranche as payload changes.",
                ],
                "pre_commit_requirements": [
                    "Always reconcile docs and focused tests before commit.",
                ],
            }), encoding="utf-8")

            doctrine = load_repo_team_doctrine(str(doctrine_path))

        self.assertIn(
            "Prefer feature flags for high-risk user-visible changes.",
            doctrine["preferred_patterns"],
        )
        self.assertIn(
            "Keep API contract updates in the same tranche as payload changes.",
            doctrine["decision_rules"],
        )
        self.assertIn(
            "Always reconcile docs and focused tests before commit.",
            doctrine["pre_commit_requirements"],
        )

    def test_merge_team_doctrine_applies_runtime_overrides(self):
        doctrine = merge_team_doctrine(
            base={
                "preferred_patterns": ["Prefer additive API changes."],
                "discouraged_patterns": [],
                "review_priorities": [],
                "pre_commit_requirements": [],
                "decision_rules": [],
            },
            overrides={
                "preferred_patterns": ["Prefer reversible migrations."],
                "pre_commit_requirements": ["Always archive superseded runbooks before commit."],
            },
            team_style_snippets=["Bias toward explicit validation evidence."],
        )

        self.assertIn("Prefer additive API changes.",
                      doctrine["preferred_patterns"])
        self.assertIn("Prefer reversible migrations.",
                      doctrine["preferred_patterns"])
        self.assertIn("Bias toward explicit validation evidence.",
                      doctrine["review_priorities"])
        self.assertIn("Always archive superseded runbooks before commit.",
                      doctrine["pre_commit_requirements"])


class AgenticRetrievalTests(TestCase):
    def test_retrieve_doctrine_guidance_returns_curated_runbook_matches(self):
        guidance = retrieve_doctrine_guidance(
            "Review the LangSmith trace dashboard rollout and validation plan",
            limit=3,
        )

        self.assertTrue(guidance)
        self.assertTrue(any(
            "trace" in item["path"] or "trace" in item["title"].lower()
            for item in guidance
        ))

    def test_retrieve_doctrine_guidance_includes_knowledge_base_matches(self):
        guidance = retrieve_doctrine_guidance(
            "Review the encyclopedia surface and upstream contract behavior",
            limit=5,
        )

        self.assertTrue(guidance)
        self.assertTrue(any(
            item["path"].startswith("agents/knowledge/")
            for item in guidance
        ))
