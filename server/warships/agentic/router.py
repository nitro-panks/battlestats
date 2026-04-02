from __future__ import annotations

import os
from typing import Any

from .crewai_runner import run_crewai_workflow
from .graph import run_graph
from .memory import get_memory_backend, get_memory_environment, is_phase0_memory_enabled, persist_phase0_memory_artifacts
from .runlog import write_agent_run_log
from .tracing import get_current_trace_url, get_langsmith_project_name, trace_block


def _prepare_langgraph_context(context: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(context)
    explicit_checkpoint_url = os.getenv(
        "LANGGRAPH_CHECKPOINT_POSTGRES_URL", "").strip()
    db_engine = os.getenv("DB_ENGINE", "postgresql_psycopg2").strip().lower()
    db_password = os.getenv("DB_PASSWORD", "").strip()

    if prepared.get("checkpoint_backend"):
        return prepared

    if explicit_checkpoint_url:
        return prepared

    if db_engine.startswith("postgresql") and not db_password:
        prepared["checkpoint_backend"] = "memory"

    if is_phase0_memory_enabled("langgraph", prepared):
        prepared["memory_enabled"] = True
        prepared.setdefault("memory_backend", get_memory_backend(prepared))
        prepared.setdefault("memory_environment", get_memory_environment())

    return prepared


def route_agent_workflow(task: str, context: dict[str, Any] | None = None, engine: str = "auto") -> dict[str, Any]:
    resolved_context = context or {}
    normalized_task = task.lower()
    explicit_engine = engine.strip().lower()

    if explicit_engine in {"crewai", "langgraph", "hybrid"}:
        chosen = explicit_engine
        rationale = f"Explicit engine override requested: {chosen}."
    else:
        planning_signal = any(token in normalized_task for token in (
            "plan", "design", "persona", "review", "scope"))
        execution_signal = any(token in normalized_task for token in (
            "implement", "fix", "test", "modify", "code", "refactor"))
        verification_signal = bool(
            resolved_context.get("verification_commands"))

        if planning_signal and execution_signal:
            chosen = "hybrid"
            rationale = "Task mixes persona-driven planning and implementation execution; hybrid routing is the safest fit."
        elif execution_signal or verification_signal:
            chosen = "langgraph"
            rationale = "Task emphasizes implementation or verification; guarded LangGraph execution is preferred."
        else:
            chosen = "crewai"
            rationale = "Task leans toward planning, coordination, or synthesis; CrewAI is preferred."

    return {
        "engine": chosen,
        "rationale": rationale,
        "task": task,
    }


def _route_trace_inputs(
    task: str,
    context: dict[str, Any] | None,
    engine: str,
    dry_run: bool,
    llm: str | None,
) -> dict[str, Any]:
    resolved_context = context or {}
    return {
        "task": task,
        "requested_engine": engine,
        "dry_run": dry_run,
        "llm": llm,
        "context_keys": sorted(resolved_context.keys()),
        "verification_command_count": len(resolved_context.get("verification_commands", [])),
    }


def _route_trace_outputs(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": result.get("workflow_id"),
        "status": result.get("status"),
        "selected_engine": result.get("selected_engine"),
        "summary": result.get("summary", []),
    }


def _build_hybrid_langgraph_context(
    context: dict[str, Any],
    crew_result: dict[str, Any],
) -> dict[str, Any]:
    prepared = _prepare_langgraph_context(context)
    crew_plan = crew_result.get("crew_plan") if isinstance(
        crew_result, dict) else None
    if not isinstance(crew_plan, dict):
        return prepared

    roles = [
        str(role.get("label", "")).strip()
        for role in crew_plan.get("roles", [])
        if isinstance(role, dict) and str(role.get("label", "")).strip()
    ]
    tasks = [
        str(task.get("assigned_to", "")).strip()
        for task in crew_plan.get("tasks", [])
        if isinstance(task, dict) and str(task.get("assigned_to", "")).strip()
    ]
    crew_artifacts = [
        artifact
        for artifact in crew_result.get("crew_artifacts", [])
        if isinstance(artifact, dict)
    ]
    planning_notes = list(prepared.get("planning_notes", []))
    if roles:
        planning_notes.append(
            "Follow the persona sequence shaped by CrewAI: " +
            " -> ".join(roles)
        )
    if tasks:
        planning_notes.append(
            "Preserve the planned execution handoff order: " +
            " -> ".join(tasks)
        )
    if crew_artifacts:
        planning_notes.append(
            f"Use {len(crew_artifacts)} structured CrewAI role artifact blueprints during guarded implementation planning."
        )
    if planning_notes:
        prepared["planning_notes"] = planning_notes
    prepared["hybrid_crew_plan"] = crew_plan
    prepared["crew_artifacts"] = crew_artifacts
    return prepared


def run_routed_workflow(
    task: str,
    context: dict[str, Any] | None = None,
    engine: str = "auto",
    dry_run: bool = False,
    llm: str | None = None,
) -> dict[str, Any]:
    with trace_block(
        "Agentic Routed Workflow",
        inputs=_route_trace_inputs(task, context, engine, dry_run, llm),
        metadata={"component": "agentic", "engine": "router"},
        tags=["agentic", "router"],
    ) as trace_run:
        route = route_agent_workflow(task, context=context, engine=engine)
        resolved_context = context or {}

        if trace_run is not None:
            trace_run.metadata["selected_engine"] = route["engine"]

        if route["engine"] == "langgraph":
            result = run_graph(
                task, context=_prepare_langgraph_context(resolved_context))
            result["selected_engine"] = "langgraph"
            result["route_rationale"] = route["rationale"]
            result["memory_store_activity"] = persist_phase0_memory_artifacts(
                result,
                review_context=resolved_context.get("memory_review") if isinstance(
                    resolved_context.get("memory_review"), dict) else None,
                context=resolved_context,
            )
            result["run_log_path"] = write_agent_run_log("langgraph", result)
        elif route["engine"] == "crewai":
            result = run_crewai_workflow(
                task, context=resolved_context, dry_run=dry_run, llm=llm)
            result["selected_engine"] = "crewai"
            result["route_rationale"] = route["rationale"]
            result["run_log_path"] = write_agent_run_log("crewai", result)
        else:
            hybrid_kickoff_enabled = bool(
                resolved_context.get("hybrid_crewai_kickoff")
            )
            crew_result = run_crewai_workflow(
                task,
                context=resolved_context,
                dry_run=not hybrid_kickoff_enabled,
                llm=llm,
            )
            graph_result = run_graph(
                task,
                context=_build_hybrid_langgraph_context(
                    resolved_context,
                    crew_result,
                ),
            )
            result = {
                "workflow_id": graph_result.get("workflow_id"),
                "status": "completed" if graph_result.get("status") == "completed" else "needs_attention",
                "selected_engine": "hybrid",
                "route_rationale": route["rationale"],
                "summary": [
                    "Hybrid workflow executed: CrewAI planning plus LangGraph guarded execution.",
                    f"CrewAI status: {crew_result.get('status')}",
                    f"LangGraph status: {graph_result.get('status')}",
                    "CrewAI planning notes were handed off to LangGraph before implementation planning.",
                ],
                "crew_result": crew_result,
                "crew_artifacts": list(crew_result.get("crew_artifacts", [])),
                "langgraph_result": graph_result,
                "memory_store_activity": {
                    "backend": get_memory_backend(resolved_context),
                    "queued_candidate_count": 0,
                    "promoted_count": 0,
                    "rejected_count": 0,
                    "reviewed_store_paths": [],
                    "candidate_queue_path": None,
                    "note": "Durable memory writes remain LangGraph-owned in this tranche.",
                },
            }
            if result["crew_artifacts"]:
                result["summary"].append(
                    f"Structured CrewAI artifacts surfaced: {len(result['crew_artifacts'])}."
                )
            result["run_log_path"] = write_agent_run_log("hybrid", result)

        trace_url = get_current_trace_url()
        if trace_url:
            result["langsmith_trace_url"] = trace_url
            result["langsmith_project"] = get_langsmith_project_name()

        if trace_run is not None:
            trace_run.metadata["workflow_id"] = result.get("workflow_id")
            trace_run.end(outputs=_route_trace_outputs(result))

        return result
