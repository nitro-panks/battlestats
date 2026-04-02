# Agent Orchestrator Selection Runbook

_Last updated: 2026-03-10_

_Status: Active workflow reference_

## Purpose

Choose the right orchestration engine for battlestats work:

- LangGraph
- CrewAI
- Hybrid routing

## Decision Rule

Use LangGraph when the task is implementation-heavy and needs explicit guardrails.

Use CrewAI when the task is coordination-heavy and benefits from persona-shaped synthesis.

Use Hybrid when the task needs both persona-driven planning and guarded execution.

## LangGraph Best Fit

- Fixes, refactors, migrations, and implementation tasks.
- Tasks that need verification commands, boundary checks, or retries.
- Workflows that benefit from checkpointed state and deterministic gates.

Typical prompt shape:

```bash
cd server
python scripts/run_agent_graph.py "fix clan hydration bug" --json
```

## CrewAI Best Fit

- Planning, design synthesis, release readiness review, and persona coordination.
- Requests where PM, Architect, UX, QA, and Safety should each contribute a shaped artifact.
- Early-stage work packets where explicit role sequencing matters more than code execution.

Typical prompt shape:

```bash
cd server
python scripts/run_agent_crew.py "plan CrewAI integration" --dry-run --json
```

## Hybrid Best Fit

- Tasks that start as planning/design work and end in implementation.
- Requests like: "plan, implement, test, and summarize".
- Work that benefits from CrewAI shaping the work packet before LangGraph runs guarded execution.
- The current runtime hands CrewAI's planned role sequence and task handoff order into LangGraph as planning notes so implementation planning can honor the persona pass instead of running independently.

Typical prompt shape:

```bash
cd server
python scripts/run_agent_workflow.py "plan and implement a ranked player workflow" --engine hybrid --json
```

## Router Behavior

`run_agent_workflow` chooses engines with these heuristics:

- Planning-only signals: prefer CrewAI.
- Implementation or verification signals: prefer LangGraph.
- Mixed planning and implementation signals: prefer Hybrid.
- Explicit `--engine` always wins.

In hybrid mode, CrewAI still runs as a dry planning pass by default, but its generated plan now informs LangGraph's planning stage rather than being logged and ignored.

## Provider Policy

CrewAI model resolution follows the provider policy layer:

- `CREWAI_LLM_MODEL`
- `CREWAI_LLM`
- `OPENAI_MODEL`
- `MODEL`

Allowed providers default to:

- `openai`
- `anthropic`
- `ollama`

Override with:

```bash
export CREWAI_ALLOWED_PROVIDERS=openai,ollama
```

## Structured Artifacts

CrewAI personas now map to structured artifact contracts:

- Coordinator -> routing plan
- PM -> product requirements
- Architect -> architecture note
- UX -> UX brief
- Designer -> design brief
- Engineer -> engineering handoff
- QA -> QA summary
- Safety -> safety review

These schemas exist to keep persona outputs shaped and testable.

## Durable Run Logs

Agentic runs are written under:

- `server/logs/agentic/crewai/`
- `server/logs/agentic/langgraph/`
- `server/logs/agentic/hybrid/`

Use these logs to review prior routing decisions and outputs.

## Validation

```bash
cd server
LANGGRAPH_CHECKPOINT_POSTGRES_URL='' DB_ENGINE=sqlite3 DJANGO_SETTINGS_MODULE=battlestats.settings DJANGO_SECRET_KEY=test-secret PYTHONPATH=$PWD /home/august/code/archive/battlestats/.venv/bin/python -m pytest warships/tests/test_agentic_graph.py warships/tests/test_agentic_crewai.py warships/tests/test_agentic_router.py
```
