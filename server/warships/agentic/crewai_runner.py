from __future__ import annotations

from datetime import datetime
import os
from typing import Any
from uuid import uuid4

from crewai import Agent, Crew, Process, Task

from .personas import PersonaSpec, get_persona_sequence, persona_keys, read_persona_markdown
from .policy import resolve_crewai_policy
from .runlog import write_agent_run_log
from .tracing import get_current_trace_url, get_langsmith_project_name, trace_block


DEFAULT_CREW_PROCESS = "hierarchical"


def _resolve_process(process: str | None) -> Process:
    value = (process or DEFAULT_CREW_PROCESS).strip().lower()
    if value == "sequential":
        return Process.sequential
    return Process.hierarchical


def _resolve_llm(llm: str | None = None) -> str | None:
    return resolve_crewai_policy(llm).model


def _build_task_description(spec: PersonaSpec, task: str, context: dict[str, Any]) -> str:
    context_lines = []
    for key, value in sorted(context.items()):
        context_lines.append(f"- {key}: {value}")

    context_block = "\n".join(
        context_lines) if context_lines else "- No additional workflow context supplied."
    return (
        f"Primary request: {task}\n"
        f"Assigned persona: {spec.label}\n"
        "Produce the best contribution for your role while respecting the existing battlestats architecture and role contract.\n"
        "Workflow context:\n"
        f"{context_block}"
    )


def build_crewai_plan(
    task: str,
    context: dict[str, Any] | None = None,
    process: str | None = None,
    roles: list[str] | None = None,
    llm: str | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    resolved_context = context or {}
    specs = get_persona_sequence(roles)
    resolved_process = _resolve_process(process)
    resolved_llm = _resolve_llm(llm)
    resolved_workflow_id = workflow_id or f"crew-{uuid4().hex[:12]}"

    return {
        "workflow_id": resolved_workflow_id,
        "task": task,
        "process": resolved_process.value,
        "llm": resolved_llm,
        "llm_policy": resolve_crewai_policy(llm).as_dict(),
        "roles": [
            {
                "key": spec.key,
                "label": spec.label,
                "crew_role": spec.crew_role,
                "file_path": spec.file_path,
                "allow_delegation": spec.allow_delegation,
                "artifact_model": spec.artifact_model.__name__,
            }
            for spec in specs
        ],
        "tasks": [
            {
                "name": f"{spec.key}_task",
                "assigned_to": spec.key,
                "expected_output": spec.expected_output,
                "artifact_model": spec.artifact_model.__name__,
                "artifact_fields": list(spec.artifact_model.model_fields.keys()),
                "depends_on": specs[index - 1].key if index > 0 else None,
            }
            for index, spec in enumerate(specs)
        ],
        "context": resolved_context,
    }


def build_crewai_crew(
    task: str,
    context: dict[str, Any] | None = None,
    process: str | None = None,
    roles: list[str] | None = None,
    llm: str | None = None,
    verbose: bool = False,
) -> Crew:
    resolved_context = context or {}
    specs = get_persona_sequence(roles)
    resolved_process = _resolve_process(process)
    resolved_llm = _resolve_llm(llm)

    agents_by_key: dict[str, Agent] = {}
    for spec in specs:
        agents_by_key[spec.key] = Agent(
            role=spec.crew_role,
            goal=spec.crew_goal,
            backstory=read_persona_markdown(spec.key),
            allow_delegation=spec.allow_delegation,
            verbose=verbose,
            llm=resolved_llm,
        )

    tasks: list[Task] = []
    for spec in specs:
        task_kwargs: dict[str, Any] = {
            "name": f"{spec.key}_task",
            "description": _build_task_description(spec, task, resolved_context),
            "expected_output": spec.expected_output,
            "agent": agents_by_key[spec.key],
            "markdown": True,
            "output_pydantic": spec.artifact_model,
        }
        if tasks:
            task_kwargs["context"] = [tasks[-1]]
        tasks.append(Task(**task_kwargs))

    manager_agent = agents_by_key.get(
        "project_coordinator") if resolved_process == Process.hierarchical else None
    return Crew(
        name="battlestats_agent_federation",
        agents=list(agents_by_key.values()),
        tasks=tasks,
        process=resolved_process,
        manager_agent=manager_agent,
        planning=True,
        verbose=verbose,
    )


def _crewai_trace_inputs(
    task: str,
    context: dict[str, Any] | None,
    process: str | None,
    roles: list[str] | None,
    llm: str | None,
    workflow_id: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    resolved_context = context or {}
    return {
        "task": task,
        "workflow_id": workflow_id,
        "process": process or DEFAULT_CREW_PROCESS,
        "roles": roles or [],
        "llm": llm,
        "dry_run": dry_run,
        "context_keys": sorted(resolved_context.keys()),
    }


def _crewai_trace_outputs(result: dict[str, Any]) -> dict[str, Any]:
    crew_plan = result.get("crew_plan") or {}
    return {
        "workflow_id": result.get("workflow_id"),
        "status": result.get("status"),
        "summary": result.get("summary", []),
        "process": crew_plan.get("process"),
        "role_count": len(crew_plan.get("roles", [])),
    }


def run_crewai_workflow(
    task: str,
    context: dict[str, Any] | None = None,
    process: str | None = None,
    roles: list[str] | None = None,
    llm: str | None = None,
    workflow_id: str | None = None,
    verbose: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    with trace_block(
        "CrewAI Persona Workflow",
        inputs=_crewai_trace_inputs(
            task,
            context,
            process,
            roles,
            llm,
            workflow_id,
            dry_run,
        ),
        metadata={"component": "agentic", "engine": "crewai"},
        tags=["agentic", "crewai"],
    ) as trace_run:
        plan = build_crewai_plan(
            task,
            context=context,
            process=process,
            roles=roles,
            llm=llm,
            workflow_id=workflow_id,
        )
        resolved_llm = plan.get("llm")
        if dry_run or not resolved_llm:
            summary = [
                f"Prepared CrewAI workflow with {len(plan['roles'])} persona(s).",
                f"Process mode: {plan['process']}",
            ]
            if not resolved_llm:
                summary.append(
                    "No CrewAI LLM configured; returning the orchestration plan without kickoff.")
            result = {
                "workflow_id": plan["workflow_id"],
                "status": "planned",
                "summary": summary,
                "crew_plan": plan,
                "available_roles": persona_keys(),
                "started_at": datetime.utcnow().isoformat() + "Z",
                "run_log_path": write_agent_run_log("crewai", {
                    "workflow_id": plan["workflow_id"],
                    "status": "planned",
                    "crew_plan": plan,
                    "summary": summary,
                }),
            }
        else:
            crew = build_crewai_crew(
                task,
                context=context,
                process=process,
                roles=roles,
                llm=resolved_llm,
                verbose=verbose,
            )
            output = crew.kickoff(
                inputs={"task": task, "context": context or {}})
            result = {
                "workflow_id": plan["workflow_id"],
                "status": "completed",
                "summary": [
                    f"CrewAI workflow completed with process={plan['process']}.",
                    f"Participating personas: {', '.join(role['label'] for role in plan['roles'])}",
                ],
                "crew_plan": plan,
                "output": str(output),
                "started_at": datetime.utcnow().isoformat() + "Z",
            }
            result["run_log_path"] = write_agent_run_log("crewai", result)

        trace_url = get_current_trace_url()
        if trace_url:
            result["langsmith_trace_url"] = trace_url
            result["langsmith_project"] = get_langsmith_project_name()

        if trace_run is not None:
            trace_run.metadata["workflow_id"] = plan["workflow_id"]
            trace_run.end(outputs=_crewai_trace_outputs(result))

        return result
