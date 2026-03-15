import json
from pathlib import Path

from django.core.management.base import BaseCommand

from warships.agentic import run_routed_workflow


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    payload = Path(path)
    with payload.open("r", encoding="utf-8") as f:
        return json.load(f)


class Command(BaseCommand):
    help = "Route a task through LangGraph, CrewAI, or a hybrid workflow."

    def add_arguments(self, parser):
        parser.add_argument("task", type=str, help="Task to route")
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--context-file", type=str, default=None)
        parser.add_argument("--engine", type=str, default="auto",
                            choices=["auto", "langgraph", "crewai", "hybrid"])
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--llm", type=str, default=None)

    def handle(self, *args, **options):
        result = run_routed_workflow(
            options["task"],
            context=_load_json_file(options.get("context_file")),
            engine=options.get("engine", "auto"),
            dry_run=options.get("dry_run", False),
            llm=options.get("llm"),
        )
        if options.get("as_json"):
            self.stdout.write(json.dumps(result, indent=2))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Engine: {result.get('selected_engine', result.get('status'))}"))
        for line in result.get("summary", []):
            self.stdout.write(f"- {line}")
        if result.get("langsmith_trace_url"):
            self.stdout.write(f"Trace: {result['langsmith_trace_url']}")
