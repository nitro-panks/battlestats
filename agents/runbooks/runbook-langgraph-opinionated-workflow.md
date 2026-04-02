# Runbook: Opinionated LangGraph Workflow

_Last updated: 2026-04-02_

_Status: Active workflow reference_

## Purpose

Operate, validate, and extend the battlestats LangGraph workflow now that it carries explicit team doctrine, curated guidance retrieval, and doctrine-aware review gates.

This runbook is the practical companion to the longer roadmap in `agents/work-items/langgraph-opinionated-workflow-roadmap.md`.

## What Exists Today

The LangGraph workflow under `server/warships/agentic/graph.py` now includes:

1. repo-backed doctrine loading from `agents/knowledge/agentic-team-doctrine.json`
2. runtime doctrine overrides from workflow context
3. curated guidance retrieval from `agents/knowledge/`, `agents/runbooks/`, and `agents/reviews/`
4. a `design_pattern_review` gate with a bounded plan-revision loop
5. an `api_contract_review` gate with a bounded plan-revision loop
6. the existing boundary and verification gates, including real command re-execution on bounded verification retries

The goal is not to mimic model fine-tuning. The goal is to make battlestats preferences visible, testable, and enforceable at runtime.

## Doctrine Sources

### Repo-default doctrine

- file: `agents/knowledge/agentic-team-doctrine.json`
- owner: repo maintainers
- use: shared battlestats defaults for patterns, anti-patterns, review priorities, and decision rules

### Runtime overrides

- context key: `team_doctrine`
- use: per-run adjustments without changing the repo default

### Runtime style snippets

- context key: `team_style_snippets`
- use: lightweight opinion nudges that should be appended to review priorities for one run

### Curated guidance retrieval

- sources:
  - `agents/knowledge/*.md`
  - `agents/runbooks/*.md`
  - `agents/reviews/*.md`
- use: pull battlestats-specific guidance into state before planning

### Planner handoff notes

- context key: `planning_notes`
- use: carry persona-shaped planning guidance from hybrid routing into LangGraph so implementation planning can preserve the intended role sequence and handoff order

## When To Use This Workflow

Use the opinionated LangGraph flow when the request needs any of the following:

1. explicit architecture or contract review
2. rollback or bounded-load thinking
3. API or payload changes
4. a battlestats-specific guidance pass before implementation
5. a durable run summary that explains not only what changed, but how the plan cleared doctrine-aware review gates

## Execution Patterns

### Basic run

```bash
cd server
python scripts/run_agent_graph.py \
  "change player summary API response payload without breaking current consumers" \
  --json
```

### Run with explicit doctrine overrides

Use a context file when you want to strengthen or test a particular engineering opinion:

```bash
cd server
python scripts/run_agent_graph.py \
  "change player summary API response payload without breaking current consumers" \
  --context-file scripts/agent_context.example.json \
  --json
```

### Example override payload

```json
{
  "team_doctrine": {
    "preferred_patterns": [
      "Prefer feature-flagged rollout for user-visible cache changes."
    ],
    "decision_rules": ["Prefer reversible cache invalidation changes."]
  },
  "team_style_snippets": [
    "Bias toward additive diagnostics when touching agent workflows."
  ],
  "max_design_review_retries": 1,
  "max_api_review_retries": 1
}
```

## Expected Graph Behavior

### Design-pattern review

This gate is meant to reject or revise plans that do not include:

1. explicit validation or regression work
2. rollback, guardrail, or bounded-load planning for riskier tasks
3. concrete implementation steps instead of empty or overly generic planning

Typical fix-up behavior:

- append validation steps
- append rollback and load-control steps
- loop once through plan revision before implementation

### API-contract review

This gate activates only for API-facing tasks.

It is meant to require:

1. explicit contract or payload compatibility planning
2. documentation updates in the same tranche
3. regression tests in the same tranche

Typical fix-up behavior:

- append serializer or backward-compatibility checks
- append API docs and payload regression steps
- loop once through plan revision before implementation

## Validation

### Focused doctrine and graph tests

```bash
cd server && \
DB_ENGINE=sqlite3 \
DJANGO_SETTINGS_MODULE=battlestats.settings \
DJANGO_SECRET_KEY=test-secret \
PYTHONPATH=$PWD \
/home/august/code/archive/battlestats/.venv/bin/python -m pytest \
  warships/tests/test_agentic_doctrine.py \
  warships/tests/test_agentic_graph.py -q
```

### Broader agentic package slice

```bash
cd server && \
DB_ENGINE=sqlite3 \
DJANGO_SETTINGS_MODULE=battlestats.settings \
DJANGO_SECRET_KEY=test-secret \
PYTHONPATH=$PWD \
/home/august/code/archive/battlestats/.venv/bin/python -m pytest \
  warships/tests/test_agentic_dashboard.py \
  warships/tests/test_agentic_crewai.py \
  warships/tests/test_agentic_doctrine.py \
  warships/tests/test_agentic_graph.py \
  warships/tests/test_agentic_router.py -q
```

## Manual Checks

1. run a task with `api`, `payload`, or `endpoint` in the prompt and confirm the summary reports `API review: pass` or `fail`
2. run a risky task like cache or hydration work and confirm the plan is amended with rollback or bounded-load steps before implementation
3. run a task that matches an existing runbook and confirm retrieved guidance paths are mentioned in the summary or logs
4. confirm the graph still reaches `completed` on safe tasks when verification passes
5. confirm failed verification commands are rerun on retry instead of only rechecking stale state

## Extension Guidance

If you add more review nodes, keep these constraints:

1. every review node should check one concern area clearly
2. every review node should have bounded retries
3. remediation steps should be appended deterministically
4. tests should prove both pass and fail-or-reroute behavior
5. if a new node changes user-visible summary behavior, update this runbook and `agents/langgraph-usage-note.md`

## Suggested Next Additions

1. `rollback_readiness_review` for migrations and cache invalidation work
2. `observability_review` for metrics, logging, and traceability expectations
3. scoped override files with provenance instead of only per-run context snippets
4. richer retrieval ranking if the current token-overlap approach becomes too noisy

## Rollback

If the new opinionated gates become too noisy or block too many tasks:

1. keep file-backed doctrine loading
2. disable the review nodes by wiring `plan_task` directly to `implement_task`
3. keep the tests and doctrine artifact so the structure is easy to reintroduce later

## Residual Limitations

1. guidance retrieval is intentionally simple and curated, not a full vector search system
2. runtime overrides are still request-scoped; they are not yet persisted as long-lived team decisions
3. summary output reflects gate outcomes, but the `/trace` dashboard does not yet break doctrine and guidance data out into dedicated cards
