# CrewAI Integration Runbook

> **RETIRED (v1.12.1, `f0fbbe3`).** This documents the experimental in-process LangGraph/CrewAI agentic runtime, which was removed from the repo — the `run_agent_graph`/`run_agent_crew`/`run_agent_workflow` commands, the LangSmith trace dashboard, and the in-app memory seam it describes no longer exist. Kept for historical reference only.

_Last updated: 2026-03-10_

_Status: Active platform reference_

## Purpose

Add CrewAI as a second orchestration layer for the existing battlestats agent federation so the team can use either:

- LangGraph for guarded, checkpointed implementation workflows with explicit verification gates.
- CrewAI for persona-first coordination, delegation, and role-shaped synthesis across the existing agent roster.

This runbook documents the integration plan, execution steps, validation commands, and rollout guidance.

## Why CrewAI Here

CrewAI enriches the current role markdown approach by turning passive personas into runnable collaborators.

Current state:

- Personas exist as markdown role contracts under `agents/`.
- LangGraph already provides a controlled workflow for implementation planning and verification.
- Persona sequencing exists as process guidance, not an executable multi-agent runtime.

CrewAI adds:

- A concrete agent runtime for the current personas.
- Hierarchical or sequential routing based on the same federation model.
- Explicit task ownership per role.
- Easier experimentation with role subsets for planning, design, QA, or risk review.

## Integration Plan

### Phase 1: Shared Persona Registry

- Centralize the existing role markdown files into a reusable persona registry.
- Map each persona to CrewAI role metadata: role name, goal, delegation behavior, expected output.
- Preserve the existing markdown files as the canonical source of role behavior.

### Phase 2: CrewAI Adapter

- Add a `crewai_runner` adapter beside the existing LangGraph code.
- Support two modes:
  - `dry-run` for plan generation without a live model call.
  - `kickoff` for real CrewAI execution when an LLM is configured.
- Keep CrewAI additive, not a rewrite of the existing LangGraph system.

### Phase 3: Command Surface

- Add `python manage.py run_agent_crew ...`.
- Add `python scripts/run_agent_crew.py ...` for standalone use.
- Support context files, workflow IDs, process modes, and persona subsets.

### Phase 4: Validation

- Unit test the persona registry and generated crew plan.
- Smoke test dry-run execution through Django and standalone command surfaces.
- Keep live model execution optional until a team model policy and credentials are finalized.

### Phase 5: Policy, Routing, and Logging

- Add a provider policy layer so CrewAI model resolution is explicit and governable.
- Add a hybrid router that can choose LangGraph, CrewAI, or both.
- Add durable run logs so CrewAI and hybrid runs can be reviewed after execution.
- Add structured artifact contracts per persona so outputs are less free-form.

## Persona Enrichment Model

CrewAI improves the existing personas by giving each one a bounded execution lane.

### Project Coordinator

- Crew role: manager and traffic controller.
- Enrichment: can act as hierarchical manager agent, sequencing the rest of the crew.

### Project Manager

- Crew role: scope and acceptance owner.
- Enrichment: generates concrete requirements and release criteria before build work begins.

### Architect

- Crew role: boundary and rollout designer.
- Enrichment: contributes explicit interface, migration, and rollback guidance into the workflow chain.

### UX

- Crew role: interaction and state spec author.
- Enrichment: produces task-specific user-flow and state coverage instead of staying a generic prompt file.

### Designer

- Crew role: visual implementation translator.
- Enrichment: turns UX direction into implementation-ready visual/state instructions inside the workflow.

### Engineer

- Crew role: implementation owner.
- Enrichment: receives prior role outputs as task context instead of starting from a flat task description.

### QA

- Crew role: release confidence gate.
- Enrichment: can verify requirement traceability as a dedicated crew task, not just as human process guidance.

### Safety

- Crew role: risk gate.
- Enrichment: can perform an explicit final review in the same orchestrated chain before release.

## Files Added Or Updated

- `server/warships/agentic/personas.py`
- `server/warships/agentic/artifacts.py`
- `server/warships/agentic/policy.py`
- `server/warships/agentic/router.py`
- `server/warships/agentic/runlog.py`
- `server/warships/agentic/crewai_runner.py`
- `server/warships/management/commands/run_agent_crew.py`
- `server/warships/management/commands/run_agent_workflow.py`
- `server/scripts/run_agent_crew.py`
- `server/scripts/run_agent_workflow.py`
- `server/warships/tests/test_agentic_crewai.py`
- `server/warships/tests/test_agentic_router.py`
- `server/requirements.txt`
- `server/Pipfile`

See also `agents/runbooks/runbook-agent-orchestrator-selection.md` for engine choice guidance.

## Commands

### Dry-run the CrewAI workflow

```bash
cd server
python manage.py run_agent_crew "plan CrewAI integration" --dry-run --json
```

### Dry-run with a subset of personas

```bash
cd server
python manage.py run_agent_crew \
  "plan CrewAI integration" \
  --roles project_coordinator,project_manager,architect,engineer \
  --process sequential \
  --dry-run
```

### Standalone script usage

```bash
cd server
python scripts/run_agent_crew.py "plan CrewAI integration" --dry-run --json
```

### Hybrid router usage

```bash
cd server
python scripts/run_agent_workflow.py \
  "plan and implement CrewAI integration" \
  --engine hybrid \
  --json
```

### Live kickoff when an LLM is configured

```bash
cd server
CREWAI_LLM=gpt-4o-mini python manage.py run_agent_crew \
  "design and validate a new ranked-player workflow" \
  --process hierarchical
```

## Validation Commands

```bash
cd server
LANGGRAPH_CHECKPOINT_POSTGRES_URL='' DB_ENGINE=sqlite3 DJANGO_SETTINGS_MODULE=battlestats.settings DJANGO_SECRET_KEY=test-secret PYTHONPATH=$PWD /home/august/code/archive/battlestats/.venv/bin/python -m pytest warships/tests/test_agentic_graph.py warships/tests/test_agentic_crewai.py warships/tests/test_agentic_router.py
```

```bash
cd server
python manage.py run_agent_crew "plan CrewAI integration" --dry-run --json
```

## Rollout Notes

- LangGraph remains the safer default for implementation tasks that need explicit verification gates and checkpoint durability.
- CrewAI is best introduced first for planning, design synthesis, risk review, and structured handoff generation.
- Keep live CrewAI kickoff behind environment-based model configuration until the team standardizes provider, cost controls, and trace retention.

## Rollback

- Remove the `run_agent_crew` command and `crewai_runner` module.
- Remove the `crewai` dependency from `requirements.txt` and `Pipfile`.
- Keep the persona registry if it remains useful to LangGraph or other tooling.

## Suggested Adoption Order

1. Use CrewAI dry-run mode to review persona routing and output contracts.
2. Trial live CrewAI runs for planning-only tasks.
3. Add provider and prompt governance.
4. Evaluate whether CrewAI should own planning while LangGraph keeps implementation verification.
