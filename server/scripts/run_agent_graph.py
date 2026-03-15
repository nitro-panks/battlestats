#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from warships.agentic import run_graph


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    payload = Path(path)
    with payload.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the starter LangGraph workflow for a task."
    )
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
        help="Optional JSON file containing context (verification, touched_files, etc).",
    )
    parser.add_argument(
        "--workflow-id",
        type=str,
        default=None,
        help="Optional workflow/thread ID used for checkpointed runs.",
    )
    args = parser.parse_args()

    context = _load_json_file(args.context_file)
    if args.workflow_id:
        context["workflow_id"] = args.workflow_id
    result = run_graph(args.task, context=context)

    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"Workflow: {result.get('workflow_id')}")
    print(f"Status: {result['status']}")
    print("Plan:")
    for step in result.get("plan", []):
        print(f"- {step}")
    print("Implementation Notes:")
    for note in result.get("implementation_notes", []):
        print(f"- {note}")
    print("Verification Notes:")
    for note in result.get("verification_notes", []):
        print(f"- {note}")
    print("Summary:")
    for line in result.get("summary", []):
        print(f"- {line}")
    if result.get("langsmith_trace_url"):
        print(f"Trace: {result['langsmith_trace_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
