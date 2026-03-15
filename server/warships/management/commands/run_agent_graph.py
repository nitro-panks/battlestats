import json
from pathlib import Path

from django.core.management.base import BaseCommand

from warships.agentic import run_graph


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    payload = Path(path)
    with payload.open("r", encoding="utf-8") as f:
        return json.load(f)


class Command(BaseCommand):
    help = "Run the starter LangGraph workflow for an implementation task."

    def add_arguments(self, parser):
        parser.add_argument("task", type=str, help="Task to send to the graph")
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Output full state as JSON",
        )
        parser.add_argument(
            "--context-file",
            type=str,
            default=None,
            help="Optional JSON file containing workflow context.",
        )
        parser.add_argument(
            "--workflow-id",
            type=str,
            default=None,
            help="Optional workflow/thread ID used for checkpointed runs.",
        )

    def handle(self, *args, **options):
        context = _load_json_file(options.get("context_file"))
        if options.get("workflow_id"):
            context["workflow_id"] = options["workflow_id"]
        result = run_graph(options["task"], context=context)

        if options["as_json"]:
            self.stdout.write(json.dumps(result, indent=2))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Workflow: {result.get('workflow_id')}"))
        self.stdout.write(self.style.SUCCESS(f"Status: {result['status']}"))
        self.stdout.write("Plan:")
        for step in result.get("plan", []):
            self.stdout.write(f"- {step}")

        self.stdout.write("Implementation Notes:")
        for note in result.get("implementation_notes", []):
            self.stdout.write(f"- {note}")

        self.stdout.write("Verification Notes:")
        for note in result.get("verification_notes", []):
            self.stdout.write(f"- {note}")

        self.stdout.write("Summary:")
        for line in result.get("summary", []):
            self.stdout.write(f"- {line}")

        if result.get("langsmith_trace_url"):
            self.stdout.write(f"Trace: {result['langsmith_trace_url']}")

        if result.get("issues"):
            self.stdout.write("Issues:")
            for issue in result.get("issues", []):
                self.stdout.write(f"- {issue}")
