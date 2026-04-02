import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from warships.agentic.runlog import write_agent_run_log


class AgenticRunLogTests(TestCase):
    def test_write_agent_run_log_redacts_sensitive_values(self):
        with TemporaryDirectory() as temp_dir:
            log_root = Path(temp_dir)
            with patch("warships.agentic.runlog._log_root", return_value=log_root):
                log_path = write_agent_run_log(
                    "langgraph",
                    {
                        "workflow_id": "graph-1",
                        "context": {
                            "db_password": "super-secret",
                            "api_token": "abc123",
                            "dsn": "postgresql://django:topsecret@db:5432/battlestats",
                            "note": "password=hunter2",
                        },
                    },
                )

            payload = json.loads(Path(log_path).read_text(encoding="utf-8"))

        context = payload["context"]
        self.assertEqual(context["db_password"], "[REDACTED]")
        self.assertEqual(context["api_token"], "[REDACTED]")
        self.assertIn("[REDACTED]", context["dsn"])
        self.assertIn("password=[REDACTED]", context["note"])
