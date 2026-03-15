#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from warships.agentic import run_crewai_workflow


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    payload = Path(path)
    with payload.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the CrewAI-backed battlestats workflow.")
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
    args = parser.parse_args()

    roles = [item.strip()
             for item in (args.roles or "").split(",") if item.strip()]
    result = run_crewai_workflow(
        args.task,
        context=_load_json_file(args.context_file),
        process=args.process,
        roles=roles or None,
        llm=args.llm,
        workflow_id=args.workflow_id,
        dry_run=args.dry_run,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"Workflow: {result.get('workflow_id')}")
    print(f"Status: {result['status']}")
    for line in result.get("summary", []):
        print(f"- {line}")
    if result.get("langsmith_trace_url"):
        print(f"Trace: {result['langsmith_trace_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
