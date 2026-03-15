from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .checkpoints import get_graph_checkpointer
from .tracing import get_current_trace_url, get_langsmith_project_name, trace_block


class AgentState(TypedDict, total=False):
    """Shared workflow state for a guarded implementation pipeline."""

    workflow_id: str
    task: str
    status: str
    plan: list[str]
    target_files: list[str]
    allowed_paths: list[str]
    touched_files: list[str]
    implementation_notes: list[str]
    execution_notes: list[str]
    role_context: dict[str, str]
    verification_commands: list[str]
    verification_cwd: str
    command_results: list[dict[str, Any]]
    verification: dict[str, Any]
    verification_notes: list[str]
    issues: list[str]
    summary: list[str]
    retry_count: int
    max_retries: int
    checks_passed: bool
    boundary_ok: bool
    transition_log: list[str]


DEFAULT_ALLOWED_PATHS = [
    "server/",
    "client/",
    "README.md",
    "agents/",
]

ALLOWED_VERIFICATION_BINARIES = {
    "python",
    "python3",
    "pytest",
    "npm",
    "echo",
    "true",
    "false",
}


def _repo_root() -> Path:
    current = Path(__file__).resolve()

    for candidate in current.parents:
        if (candidate / "docker-compose.yml").exists():
            return candidate

    for candidate in current.parents:
        if (candidate / "manage.py").exists():
            return candidate

    return current.parents[2]


def _read_role_files() -> dict[str, str]:
    role_files = {
        "architect": "agents/architect.md",
        "engineer": "agents/engineer-web-dev.md",
        "qa": "agents/qa.md",
    }
    out: dict[str, str] = {}
    root = _repo_root()

    for role, rel_path in role_files.items():
        target = root / rel_path
        if not target.exists():
            out[role] = ""
            continue
        with target.open("r", encoding="utf-8") as f:
            out[role] = f.read()

    return out


def _append_transition(state: AgentState, node_name: str) -> list[str]:
    transition_log = list(state.get("transition_log", []))
    transition_log.append(f"{datetime.utcnow().isoformat()}Z:{node_name}")
    return transition_log


def _is_allowed_binary(binary: str) -> bool:
    return Path(binary).name in ALLOWED_VERIFICATION_BINARIES


def _normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv

    binary_name = Path(argv[0]).name
    if binary_name in {"python", "python3"}:
        argv[0] = sys.executable
    return argv


def _safe_cwd(cwd_hint: str | None) -> Path:
    root = _repo_root()
    if not cwd_hint:
        return root

    normalized_hint = cwd_hint.strip().strip("/")
    if normalized_hint in {"", "."}:
        return root

    if normalized_hint == "server" and (root / "manage.py").exists():
        return root

    requested = (root / normalized_hint).resolve()
    if not str(requested).startswith(str(root)):
        return root
    if not requested.exists():
        return root
    return requested


def _is_clan_hydration_use_case(task: str) -> bool:
    normalized = task.lower()
    return "clan" in normalized and "hydrate" in normalized


def _plan_task(state: AgentState) -> dict:
    task = state["task"].strip() or "No task provided"

    if _is_clan_hydration_use_case(task):
        plan = [
            "Reproduce the stale player page state where clan fields are initially missing",
            "Add bounded player re-fetch in the frontend while clan hydration is pending",
            "Force a backend refresh task when clan is missing to avoid fresh-cache lock",
            "Add tests for forced refresh behavior and run focused test suite",
        ]
        target_files = [
            "client/app/components/PlayerSearch.tsx",
            "server/warships/views.py",
            "server/warships/tasks.py",
            "server/warships/tests/test_views.py",
        ]
    else:
        plan = [
            f"Clarify acceptance criteria for: {task}",
            "Identify files and tests affected by the task",
            "Implement the smallest safe change and validate",
        ]
        target_files = []

    return {
        "plan": plan,
        "target_files": target_files,
        "role_context": _read_role_files(),
        "status": "planned",
        "transition_log": _append_transition(state, "plan_task"),
    }


def _implement_task(state: AgentState) -> dict:
    plan = state.get("plan", [])

    touched_files = list(state.get("touched_files", []))
    if not touched_files:
        touched_files = list(state.get("target_files", []))

    notes = [
        f"Prepared implementation checklist with {len(plan)} step(s).",
        f"Scoped touched files: {len(touched_files)}",
    ]
    notes.extend([f"Execute: {step}" for step in plan])

    return {
        "touched_files": touched_files,
        "implementation_notes": notes,
        "execution_notes": notes,
        "status": "ready_for_implementation",
        "transition_log": _append_transition(state, "implement_task"),
    }


def _enforce_tool_boundaries(state: AgentState) -> dict:
    allowed_paths = list(state.get("allowed_paths", DEFAULT_ALLOWED_PATHS))
    touched_files = list(state.get("touched_files", []))

    disallowed = [
        file_path
        for file_path in touched_files
        if not any(file_path.startswith(prefix) for prefix in allowed_paths)
    ]

    boundary_ok = len(disallowed) == 0
    issues = list(state.get("issues", []))
    if disallowed:
        issues.append(
            "Blocked files outside allowed paths: " + ", ".join(disallowed)
        )

    return {
        "boundary_ok": boundary_ok,
        "issues": issues,
        "status": "boundary_checked" if boundary_ok else "blocked_by_boundaries",
        "transition_log": _append_transition(state, "enforce_tool_boundaries"),
    }


def _run_verification_commands(state: AgentState) -> dict:
    commands = list(state.get("verification_commands", []))
    results: list[dict[str, Any]] = []
    notes = list(state.get("verification_notes", []))

    if not commands:
        notes.append(
            "No verification_commands provided; using caller-supplied verification flags.")
        return {
            "command_results": results,
            "verification_notes": notes,
            "status": "verification_commands_skipped",
            "transition_log": _append_transition(state, "run_verification_commands"),
        }

    cwd = _safe_cwd(state.get("verification_cwd"))
    notes.append(f"Running {len(commands)} verification command(s) in {cwd}")

    for command in commands:
        argv = shlex.split(command)
        if not argv:
            continue
        argv = _normalize_argv(argv)

        if not _is_allowed_binary(argv[0]):
            results.append(
                {
                    "command": command,
                    "returncode": 126,
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Blocked command binary: {argv[0]}",
                }
            )
            continue

        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        results.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "ok": completed.returncode == 0,
                "stdout": (completed.stdout or "")[-2000:],
                "stderr": (completed.stderr or "")[-2000:],
            }
        )

    all_ok = all(result.get("ok") for result in results) if results else False
    verification = dict(state.get("verification", {}))
    verification["tests_passed"] = all_ok
    verification.setdefault("lint_passed", True)

    notes.append(f"Verification commands completed: all_ok={all_ok}")
    return {
        "command_results": results,
        "verification": verification,
        "verification_notes": notes,
        "status": "verification_commands_completed",
        "transition_log": _append_transition(state, "run_verification_commands"),
    }


def _verify_changes(state: AgentState) -> dict:
    verification = dict(state.get("verification", {}))
    tests_passed = bool(verification.get("tests_passed", False))
    lint_passed = bool(verification.get("lint_passed", True))
    checks_passed = tests_passed and lint_passed

    notes = list(state.get("verification_notes", []))
    notes.append(
        f"Verification gate: tests_passed={tests_passed}, lint_passed={lint_passed}"
    )

    issues = list(state.get("issues", []))
    if not checks_passed:
        issues.append("Verification failed: required checks did not pass")

    return {
        "checks_passed": checks_passed,
        "verification_notes": notes,
        "issues": issues,
        "status": "verified" if checks_passed else "verification_failed",
        "transition_log": _append_transition(state, "verify_changes"),
    }


def _retry_verification(state: AgentState) -> dict:
    retry_count = int(state.get("retry_count", 0)) + 1
    notes = list(state.get("verification_notes", []))
    notes.append(f"Retrying verification attempt {retry_count}")
    return {
        "retry_count": retry_count,
        "verification_notes": notes,
        "status": "verification_retrying",
        "transition_log": _append_transition(state, "retry_verification"),
    }


def _summarize(state: AgentState) -> dict:
    summary = [
        f"Task: {state.get('task', '')}",
        f"Plan steps: {len(state.get('plan', []))}",
        f"Touched files: {len(state.get('touched_files', []))}",
        f"Boundary gate: {'pass' if state.get('boundary_ok', False) else 'fail'}",
        f"Verification gate: {'pass' if state.get('checks_passed', False) else 'fail'}",
    ]
    if state.get("issues"):
        summary.append("Issues: " + " | ".join(state.get("issues", [])))

    final_status = "completed" if state.get("boundary_ok") and state.get(
        "checks_passed") else "needs_attention"
    return {
        "summary": summary,
        "status": final_status,
        "transition_log": _append_transition(state, "summarize"),
    }


def _route_after_boundaries(state: AgentState) -> Literal["run_verification_commands", "summarize"]:
    if state.get("boundary_ok", False):
        return "run_verification_commands"
    return "summarize"


def _route_after_verification(
    state: AgentState,
) -> Literal["summarize", "retry_verification"]:
    if state.get("checks_passed", False):
        return "summarize"

    retry_count = int(state.get("retry_count", 0))
    max_retries = int(state.get("max_retries", 1))
    if retry_count < max_retries:
        return "retry_verification"
    return "summarize"


def _graph_trace_inputs(task: str, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": task,
        "workflow_id": context.get("workflow_id"),
        "context_keys": sorted(context.keys()),
        "touched_file_count": len(context.get("touched_files", [])),
        "verification_command_count": len(context.get("verification_commands", [])),
        "checkpoint_backend": context.get("checkpoint_backend"),
    }


def _graph_trace_outputs(result: AgentState) -> dict[str, Any]:
    return {
        "workflow_id": result.get("workflow_id"),
        "status": result.get("status"),
        "boundary_ok": result.get("boundary_ok"),
        "checks_passed": result.get("checks_passed"),
        "issue_count": len(result.get("issues", [])),
        "summary": result.get("summary", []),
    }


def build_graph(checkpointer: Any | None = None):
    """Build and compile the guarded LangGraph workflow."""

    graph_builder = StateGraph(AgentState)

    graph_builder.add_node("plan_task", _plan_task)
    graph_builder.add_node("implement_task", _implement_task)
    graph_builder.add_node("enforce_tool_boundaries", _enforce_tool_boundaries)
    graph_builder.add_node("run_verification_commands",
                           _run_verification_commands)
    graph_builder.add_node("verify_changes", _verify_changes)
    graph_builder.add_node("retry_verification", _retry_verification)
    graph_builder.add_node("summarize", _summarize)

    graph_builder.add_edge(START, "plan_task")
    graph_builder.add_edge("plan_task", "implement_task")
    graph_builder.add_edge("implement_task", "enforce_tool_boundaries")
    graph_builder.add_conditional_edges(
        "enforce_tool_boundaries",
        _route_after_boundaries,
    )
    graph_builder.add_edge("run_verification_commands", "verify_changes")
    graph_builder.add_conditional_edges(
        "verify_changes",
        _route_after_verification,
    )
    graph_builder.add_edge("retry_verification", "verify_changes")
    graph_builder.add_edge("summarize", END)

    return graph_builder.compile(checkpointer=checkpointer or MemorySaver())


def run_graph(task: str, context: dict[str, Any] | None = None) -> AgentState:
    """Run the graph from an incoming task string and optional context."""

    context = context or {}
    with trace_block(
        "LangGraph Implementation Workflow",
        inputs=_graph_trace_inputs(task, context),
        metadata={"component": "agentic", "engine": "langgraph"},
        tags=["agentic", "langgraph"],
    ) as trace_run:
        workflow_id = context.get(
            "workflow_id") or f"run-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        initial_state: AgentState = {
            "workflow_id": workflow_id,
            "task": task,
            "status": "queued",
            "plan": [],
            "target_files": list(context.get("target_files", [])),
            "allowed_paths": list(context.get("allowed_paths", DEFAULT_ALLOWED_PATHS)),
            "touched_files": list(context.get("touched_files", [])),
            "implementation_notes": [],
            "execution_notes": [],
            "role_context": {},
            "verification_commands": list(context.get("verification_commands", [])),
            "verification_cwd": str(context.get("verification_cwd", "")),
            "command_results": [],
            "verification": dict(context.get("verification", {})),
            "verification_notes": [],
            "issues": [],
            "summary": [],
            "retry_count": int(context.get("retry_count", 0)),
            "max_retries": int(context.get("max_retries", 1)),
            "checks_passed": False,
            "boundary_ok": False,
            "transition_log": [],
        }

        with get_graph_checkpointer(context=context) as checkpointer:
            compiled = build_graph(checkpointer=checkpointer)
            result = compiled.invoke(
                initial_state,
                config={"configurable": {"thread_id": workflow_id}},
            )

        trace_url = get_current_trace_url()
        if trace_url:
            result["langsmith_trace_url"] = trace_url
            result["langsmith_project"] = get_langsmith_project_name()

        if trace_run is not None:
            trace_run.metadata["workflow_id"] = workflow_id
            trace_run.end(outputs=_graph_trace_outputs(result))

        return result
