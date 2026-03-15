import json
from pathlib import Path

from django.core.management.base import BaseCommand

from warships.agentic import run_crewai_workflow


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    payload = Path(path)
    with payload.open("r", encoding="utf-8") as f:
        return json.load(f)


class Command(BaseCommand):
    help = "Run the CrewAI-backed multi-persona workflow for an implementation task."

    def add_arguments(self, parser):
        parser.add_argument("task", type=str, help="Task to send to the crew")
        parser.add_argument("--json", action="store_true",
                            dest="as_json", help="Output full result as JSON")
        parser.add_argument("--context-file", type=str, default=None,
                            help="Optional JSON file containing workflow context.")
        parser.add_argument("--workflow-id", type=str, default=None,
                            help="Optional workflow ID to stamp on the run.")
        parser.add_argument("--process", type=str, default="hierarchical",
                            choices=["hierarchical", "sequential"], help="CrewAI process mode.")
        parser.add_argument("--roles", type=str, default=None,
                            help="Comma-separated subset of persona keys to include.")
        parser.add_argument("--llm", type=str, default=None,
                            help="Optional CrewAI LLM identifier override.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Build the crew plan without kicking off the LLM workflow.")

    def handle(self, *args, **options):
        context = _load_json_file(options.get("context_file"))
        roles = [item.strip() for item in (options.get(
            "roles") or "").split(",") if item.strip()]
        result = run_crewai_workflow(
            options["task"],
            context=context,
            process=options.get("process"),
            roles=roles or None,
            llm=options.get("llm"),
            workflow_id=options.get("workflow_id"),
            dry_run=options.get("dry_run", False),
            verbose=False,
        )

        if options["as_json"]:
            self.stdout.write(json.dumps(result, indent=2))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Workflow: {result.get('workflow_id')}"))
        self.stdout.write(self.style.SUCCESS(f"Status: {result['status']}"))
        for line in result.get("summary", []):
            self.stdout.write(f"- {line}")
        if result.get("langsmith_trace_url"):
            self.stdout.write(f"Trace: {result['langsmith_trace_url']}")
