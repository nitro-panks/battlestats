#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from warships.agentic import run_routed_workflow


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    payload = Path(path)
    with payload.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Route a battlestats task through the available agentic engines.")
    parser.add_argument("task", type=str)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--context-file", type=str, default=None)
    parser.add_argument("--engine", type=str, default="auto",
                        choices=["auto", "langgraph", "crewai", "hybrid"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--llm", type=str, default=None)
    args = parser.parse_args()

    result = run_routed_workflow(
        args.task,
        context=_load_json_file(args.context_file),
        engine=args.engine,
        dry_run=args.dry_run,
        llm=args.llm,
    )
    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"Engine: {result.get('selected_engine', result.get('status'))}")
    for line in result.get("summary", []):
        print(f"- {line}")
    if result.get("langsmith_trace_url"):
        print(f"Trace: {result['langsmith_trace_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
