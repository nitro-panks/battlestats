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
from .doctrine import merge_team_doctrine, summarize_team_doctrine
from .memory import PHASE0_MEMORY_LIMIT, build_phase0_memory_candidates, prepare_phase0_memory_context
from .retrieval import retrieve_doctrine_guidance
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
    doctrine_overrides: dict[str, Any]
    team_style_snippets: list[str]
    team_doctrine: dict[str, list[str]]
    doctrine_notes: list[str]
    retrieved_guidance: list[dict[str, Any]]
    guidance_notes: list[str]
    workflow_kind: str
    memory_enabled: bool
    memory_environment: str
    memory_namespace: tuple[str, str, str]
    retrieved_memories: list[dict[str, Any]]
    memory_notes: list[str]
    memory_candidates: list[dict[str, Any]]
    design_review_notes: list[str]
    design_review_passed: bool
    design_review_retry_count: int
    max_design_review_retries: int
    api_review_notes: list[str]
    api_review_passed: bool
    api_review_required: bool
    api_review_retry_count: int
    max_api_review_retries: int
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


def _load_team_doctrine(state: AgentState) -> dict:
    overrides = state.get("doctrine_overrides", {})
    style_snippets = list(state.get("team_style_snippets", []))
    doctrine = merge_team_doctrine(
        overrides=overrides,
        team_style_snippets=style_snippets,
    )
    doctrine_summary = summarize_team_doctrine(doctrine)

    notes = [
        "Loaded battlestats team doctrine into workflow state.",
        f"Preferred patterns: {doctrine_summary.get('preferred_patterns', 'None recorded.')}",
        f"Discouraged patterns: {doctrine_summary.get('discouraged_patterns', 'None recorded.')}",
        f"Review priorities: {doctrine_summary.get('review_priorities', 'None recorded.')}",
        f"Pre-commit requirements: {doctrine_summary.get('pre_commit_requirements', 'None recorded.')}",
    ]
    if overrides:
        notes.append(
            "Applied doctrine overrides for: " +
            ", ".join(sorted(overrides.keys()))
        )
    if style_snippets:
        notes.append(
            f"Applied {len(style_snippets)} team-style snippet(s) as dynamic review priorities."
        )

    return {
        "team_doctrine": doctrine,
        "doctrine_notes": notes,
        "status": "doctrine_loaded",
        "transition_log": _append_transition(state, "load_team_doctrine"),
    }


def _retrieve_guidance(state: AgentState) -> dict:
    task = state.get("task", "")
    guidance = retrieve_doctrine_guidance(task, limit=3)
    notes = list(state.get("guidance_notes", []))
    if guidance:
        notes.append(
            "Retrieved battlestats guidance from: " +
            ", ".join(item["path"] for item in guidance)
        )
    else:
        notes.append(
            "No closely matched battlestats guidance documents were retrieved for this task.")

    return {
        "retrieved_guidance": guidance,
        "guidance_notes": notes,
        "status": "guidance_loaded",
        "transition_log": _append_transition(state, "retrieve_guidance"),
    }


def _plan_task(state: AgentState) -> dict:
    task = state["task"].strip() or "No task provided"
    doctrine = state.get("team_doctrine", {})
    doctrine_summary = summarize_team_doctrine(doctrine)
    retrieved_memories = list(state.get("retrieved_memories", []))
    memory_notes = list(state.get("memory_notes", []))

    if _is_clan_hydration_use_case(task):
        plan = [
            "Reproduce the stale player page state where clan fields are initially missing",
            "Add bounded player re-fetch in the frontend while clan hydration is pending",
            "Force a backend refresh task when clan is missing to avoid fresh-cache lock",
            "Add tests for forced refresh behavior and run focused test suite",
            f"Avoid doctrine anti-patterns while revising the flow: {doctrine_summary.get('discouraged_patterns', 'None recorded.')}",
            f"Check the approach against review priorities: {doctrine_summary.get('review_priorities', 'None recorded.')}",
            f"Confirm the change can clear pre-commit doctrine requirements: {doctrine_summary.get('pre_commit_requirements', 'None recorded.')}",
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
            f"Avoid battlestats doctrine anti-patterns: {doctrine_summary.get('discouraged_patterns', 'None recorded.')}",
            f"Review the approach against battlestats doctrine: {doctrine_summary.get('decision_rules', 'None recorded.')}",
            f"Confirm the change can clear pre-commit doctrine requirements: {doctrine_summary.get('pre_commit_requirements', 'None recorded.')}",
        ]
        target_files = []

    doctrine_notes = list(state.get("doctrine_notes", []))
    guidance = list(state.get("retrieved_guidance", []))
    guidance_notes = list(state.get("guidance_notes", []))
    doctrine_notes.append(
        "Planning used battlestats doctrine for discouraged patterns, review priorities, decision rules, and pre-commit requirements."
    )
    if guidance:
        plan.append(
            "Review relevant battlestats guidance artifacts before implementation: "
            + ", ".join(item["title"] for item in guidance)
        )
        guidance_notes.append(
            f"Planning referenced {len(guidance)} retrieved guidance artifact(s)."
        )
    if retrieved_memories:
        plan.append(
            "Apply bounded reviewed procedural memory before implementation: "
            + "; ".join(
                str(item.get("summary") or "")
                for item in retrieved_memories[:2]
                if item.get("summary")
            )
        )
        memory_notes.append(
            f"Planning consumed {len(retrieved_memories)} reviewed procedural memory entr{'y' if len(retrieved_memories) == 1 else 'ies'}."
        )

    return {
        "plan": plan,
        "target_files": target_files,
        "role_context": _read_role_files(),
        "doctrine_notes": doctrine_notes,
        "guidance_notes": guidance_notes,
        "memory_notes": memory_notes,
        "status": "planned",
        "transition_log": _append_transition(state, "plan_task"),
    }


def _load_memory_context(state: AgentState) -> dict:
    memory_context = prepare_phase0_memory_context(
        state.get("task", ""),
        {
            "memory_enabled": state.get("memory_enabled"),
            "memory_records": state.get("retrieved_memories", []),
            "verification_commands": state.get("verification_commands", []),
            "touched_files": state.get("touched_files", []),
            "memory_limit": PHASE0_MEMORY_LIMIT,
        },
    )
    notes = list(state.get("memory_notes", []))
    notes.extend(memory_context.get("memory_notes", []))
    return {
        "memory_enabled": memory_context["memory_enabled"],
        "memory_environment": memory_context["memory_environment"],
        "memory_namespace": memory_context["memory_namespace"],
        "workflow_kind": memory_context["workflow_kind"],
        "retrieved_memories": memory_context["retrieved_memories"],
        "memory_notes": notes,
        "status": "memory_loaded",
        "transition_log": _append_transition(state, "load_memory_context"),
    }


def _plan_has_validation_step(plan: list[str]) -> bool:
    return any(
        any(token in step.lower() for token in ("test", "validat", "verify"))
        for step in plan
    )


def _is_generic_doctrine_step(step: str) -> bool:
    normalized = step.lower()
    return any(
        normalized.startswith(prefix)
        for prefix in (
            "clarify acceptance criteria for:",
            "avoid battlestats doctrine anti-patterns:",
            "review the approach against battlestats doctrine:",
            "confirm the change can clear pre-commit doctrine requirements:",
            "review relevant battlestats guidance artifacts before implementation:",
        )
    )


def _plan_has_risk_control_step(plan: list[str]) -> bool:
    return any(
        not _is_generic_doctrine_step(step)
        and any(token in step.lower() for token in ("rollback", "guardrail", "monitor", "bound", "load"))
        for step in plan
    )


def _task_needs_risk_controls(task: str) -> bool:
    normalized = task.lower()
    return any(
        token in normalized
        for token in ("migrate", "schema", "cache", "hydrate", "queue", "api", "ranked", "trace")
    )


def _task_needs_api_contract_review(task: str) -> bool:
    normalized = task.lower()
    return any(
        token in normalized
        for token in ("api", "endpoint", "payload", "serializer", "schema", "response", "route", "contract", "fetch")
    )


def _plan_has_api_contract_step(plan: list[str]) -> bool:
    return any(
        not _is_generic_doctrine_step(step)
        and
        any(token in step.lower() for token in ("contract", "payload",
            "serializer", "schema", "response", "endpoint", "backward", "compat"))
        for step in plan
    )


def _plan_has_docs_and_api_test_step(plan: list[str]) -> bool:
    has_docs = any(
        not _is_generic_doctrine_step(step)
        and any(token in step.lower() for token in ("documentation", "docs", "runbook", "readme"))
        for step in plan
    )
    has_tests = any(
        not _is_generic_doctrine_step(step)
        and
        not step.lower().startswith("implement the smallest safe change and validate")
        and not step.lower().startswith("identify files and tests affected by the task")
        and any(token in step.lower() for token in ("test", "validat", "regression"))
        for step in plan
    )
    return has_docs and has_tests


def _design_pattern_review(state: AgentState) -> dict:
    plan = list(state.get("plan", []))
    task = state.get("task", "")
    review_notes: list[str] = []

    if not plan:
        review_notes.append(
            "Design review: plan is empty and needs concrete implementation steps.")

    if not _plan_has_validation_step(plan):
        review_notes.append(
            "Design review: plan needs an explicit validation or regression step."
        )

    if _task_needs_risk_controls(task) and not _plan_has_risk_control_step(plan):
        review_notes.append(
            "Design review: risky tasks should include rollback, guardrail, or load-control planning."
        )

    design_review_passed = not review_notes
    doctrine_notes = list(state.get("doctrine_notes", []))
    if design_review_passed:
        doctrine_notes.append(
            "Design pattern review passed against battlestats doctrine."
        )
    else:
        doctrine_notes.append(
            f"Design pattern review found {len(review_notes)} issue(s) that require plan revision."
        )

    return {
        "design_review_notes": review_notes,
        "design_review_passed": design_review_passed,
        "doctrine_notes": doctrine_notes,
        "status": "design_review_passed" if design_review_passed else "design_review_failed",
        "transition_log": _append_transition(state, "design_pattern_review"),
    }


def _revise_plan(state: AgentState) -> dict:
    plan = list(state.get("plan", []))
    notes = list(state.get("design_review_notes", []))
    retry_count = int(state.get("design_review_retry_count", 0)) + 1

    if any("validation" in note.lower() for note in notes):
        remediation = "Add focused validation commands and regression coverage for touched surfaces."
        if remediation not in plan:
            plan.append(remediation)

    if any(
        any(token in note.lower()
            for token in ("rollback", "guardrail", "load-control", "load control"))
        for note in notes
    ):
        remediation = "Add rollback, guardrail, and bounded-load checks before implementation."
        if remediation not in plan:
            plan.append(remediation)

    if any("plan is empty" in note.lower() for note in notes):
        remediation = "Identify concrete implementation files and acceptance checks before coding."
        if remediation not in plan:
            plan.append(remediation)

    doctrine_notes = list(state.get("doctrine_notes", []))
    doctrine_notes.append(
        f"Revised the plan after design review findings (attempt {retry_count})."
    )

    return {
        "plan": plan,
        "design_review_retry_count": retry_count,
        "doctrine_notes": doctrine_notes,
        "status": "plan_revised",
        "transition_log": _append_transition(state, "revise_plan"),
    }


def _api_contract_review(state: AgentState) -> dict:
    task = state.get("task", "")
    plan = list(state.get("plan", []))
    review_required = _task_needs_api_contract_review(task)
    review_notes: list[str] = []

    if review_required and not _plan_has_api_contract_step(plan):
        review_notes.append(
            "API review: API-facing tasks need an explicit contract or payload compatibility step."
        )

    if review_required and not _plan_has_docs_and_api_test_step(plan):
        review_notes.append(
            "API review: API-facing tasks should include documentation updates and regression coverage in the same tranche."
        )

    api_review_passed = not review_notes
    doctrine_notes = list(state.get("doctrine_notes", []))
    if review_required:
        if api_review_passed:
            doctrine_notes.append(
                "API contract review passed against battlestats doctrine.")
        else:
            doctrine_notes.append(
                f"API contract review found {len(review_notes)} issue(s) that require plan revision."
            )
    else:
        doctrine_notes.append(
            "API contract review skipped because the task does not appear API-facing.")

    return {
        "api_review_notes": review_notes,
        "api_review_passed": api_review_passed,
        "api_review_required": review_required,
        "doctrine_notes": doctrine_notes,
        "status": "api_review_passed" if api_review_passed else "api_review_failed",
        "transition_log": _append_transition(state, "api_contract_review"),
    }


def _revise_plan_after_api_review(state: AgentState) -> dict:
    plan = list(state.get("plan", []))
    notes = list(state.get("api_review_notes", []))
    retry_count = int(state.get("api_review_retry_count", 0)) + 1

    if any("contract or payload compatibility" in note.lower() for note in notes):
        remediation = "Add API contract, serializer, and backward-compatibility checks for touched endpoints."
        if remediation not in plan:
            plan.append(remediation)

    if any("documentation updates and regression coverage" in note.lower() for note in notes):
        remediation = "Add API documentation updates and payload regression tests for user-facing endpoint changes."
        if remediation not in plan:
            plan.append(remediation)

    doctrine_notes = list(state.get("doctrine_notes", []))
    doctrine_notes.append(
        f"Revised the plan after API contract review findings (attempt {retry_count})."
    )

    return {
        "plan": plan,
        "api_review_retry_count": retry_count,
        "doctrine_notes": doctrine_notes,
        "status": "api_plan_revised",
        "transition_log": _append_transition(state, "revise_plan_after_api_review"),
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
    api_review_required = state.get("api_review_required", False)
    api_review_label = "n/a" if not api_review_required else (
        "pass" if state.get("api_review_passed", False) else "fail")
    summary = [
        f"Task: {state.get('task', '')}",
        f"Plan steps: {len(state.get('plan', []))}",
        f"Touched files: {len(state.get('touched_files', []))}",
        f"Design review: {'pass' if state.get('design_review_passed', False) else 'fail'}",
        f"API review: {api_review_label}",
        f"Boundary gate: {'pass' if state.get('boundary_ok', False) else 'fail'}",
        f"Verification gate: {'pass' if state.get('checks_passed', False) else 'fail'}",
    ]
    doctrine_notes = list(state.get("doctrine_notes", []))
    if doctrine_notes:
        summary.append("Doctrine: " + " | ".join(doctrine_notes[:2]))
    guidance_notes = list(state.get("guidance_notes", []))
    if guidance_notes:
        summary.append("Guidance: " + " | ".join(guidance_notes[:1]))
    memory_notes = list(state.get("memory_notes", []))
    if memory_notes:
        summary.append("Memory: " + " | ".join(memory_notes[:2]))
    if state.get("issues"):
        summary.append("Issues: " + " | ".join(state.get("issues", [])))

    final_status = "completed" if (
        state.get("design_review_passed", False)
        and (not api_review_required or state.get("api_review_passed", False))
        and state.get("boundary_ok")
        and state.get("checks_passed")
    ) else "needs_attention"
    memory_candidates = build_phase0_memory_candidates({
        **state,
        "status": final_status,
    })
    return {
        "summary": summary,
        "memory_candidates": memory_candidates,
        "status": final_status,
        "transition_log": _append_transition(state, "summarize"),
    }


def _route_after_boundaries(state: AgentState) -> Literal["run_verification_commands", "summarize"]:
    if state.get("boundary_ok", False):
        return "run_verification_commands"
    return "summarize"


def _route_after_design_review(
    state: AgentState,
) -> Literal["api_contract_review", "revise_plan", "summarize"]:
    if state.get("design_review_passed", False):
        return "api_contract_review"

    retry_count = int(state.get("design_review_retry_count", 0))
    max_retries = int(state.get("max_design_review_retries", 1))
    if retry_count < max_retries:
        return "revise_plan"
    return "summarize"


def _route_after_api_review(
    state: AgentState,
) -> Literal["implement_task", "revise_plan_after_api_review", "summarize"]:
    if state.get("api_review_passed", False):
        return "implement_task"

    retry_count = int(state.get("api_review_retry_count", 0))
    max_retries = int(state.get("max_api_review_retries", 1))
    if retry_count < max_retries:
        return "revise_plan_after_api_review"
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
        "memory_enabled": context.get("memory_enabled"),
    }


def _graph_trace_outputs(result: AgentState) -> dict[str, Any]:
    return {
        "workflow_id": result.get("workflow_id"),
        "status": result.get("status"),
        "boundary_ok": result.get("boundary_ok"),
        "checks_passed": result.get("checks_passed"),
        "issue_count": len(result.get("issues", [])),
        "memory_candidate_count": len(result.get("memory_candidates", [])),
        "summary": result.get("summary", []),
    }


def build_graph(checkpointer: Any | None = None):
    """Build and compile the guarded LangGraph workflow."""

    graph_builder = StateGraph(AgentState)

    graph_builder.add_node("load_team_doctrine", _load_team_doctrine)
    graph_builder.add_node("retrieve_guidance", _retrieve_guidance)
    graph_builder.add_node("load_memory_context", _load_memory_context)
    graph_builder.add_node("plan_task", _plan_task)
    graph_builder.add_node("design_pattern_review", _design_pattern_review)
    graph_builder.add_node("revise_plan", _revise_plan)
    graph_builder.add_node("api_contract_review", _api_contract_review)
    graph_builder.add_node("revise_plan_after_api_review",
                           _revise_plan_after_api_review)
    graph_builder.add_node("implement_task", _implement_task)
    graph_builder.add_node("enforce_tool_boundaries", _enforce_tool_boundaries)
    graph_builder.add_node("run_verification_commands",
                           _run_verification_commands)
    graph_builder.add_node("verify_changes", _verify_changes)
    graph_builder.add_node("retry_verification", _retry_verification)
    graph_builder.add_node("summarize", _summarize)

    graph_builder.add_edge(START, "load_team_doctrine")
    graph_builder.add_edge("load_team_doctrine", "retrieve_guidance")
    graph_builder.add_edge("retrieve_guidance", "load_memory_context")
    graph_builder.add_edge("load_memory_context", "plan_task")
    graph_builder.add_edge("plan_task", "design_pattern_review")
    graph_builder.add_conditional_edges(
        "design_pattern_review",
        _route_after_design_review,
    )
    graph_builder.add_edge("revise_plan", "design_pattern_review")
    graph_builder.add_conditional_edges(
        "api_contract_review",
        _route_after_api_review,
    )
    graph_builder.add_edge(
        "revise_plan_after_api_review", "api_contract_review")
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
            "doctrine_overrides": dict(context.get("team_doctrine", {})),
            "team_style_snippets": list(context.get("team_style_snippets", [])),
            "team_doctrine": {},
            "doctrine_notes": [],
            "retrieved_guidance": [],
            "guidance_notes": [],
            "workflow_kind": str(context.get("workflow_kind", "")),
            "memory_enabled": bool(context.get("memory_enabled", False)),
            "memory_environment": str(context.get("memory_environment", "")),
            "memory_namespace": tuple(context.get("memory_namespace", ()) or ()),
            "retrieved_memories": list(context.get("memory_records", [])),
            "memory_notes": [],
            "memory_candidates": [],
            "design_review_notes": [],
            "design_review_passed": False,
            "design_review_retry_count": int(context.get("design_review_retry_count", 0)),
            "max_design_review_retries": int(context.get("max_design_review_retries", 1)),
            "api_review_notes": [],
            "api_review_passed": False,
            "api_review_required": False,
            "api_review_retry_count": int(context.get("api_review_retry_count", 0)),
            "max_api_review_retries": int(context.get("max_api_review_retries", 1)),
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
