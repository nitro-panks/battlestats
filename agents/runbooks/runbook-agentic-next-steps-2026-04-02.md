# Runbook: Agentic Next Steps

_Last updated: 2026-04-02_

_Status: Active follow-up runbook with initial implementation tranche landed_

## Purpose

Capture the next implementation steps for the battlestats agentic stack after reviewing the current documentation, the active LangGraph and CrewAI code paths, and the current doctrine and memory setup.

This runbook is intentionally focused on the next tranche of work. It does not replace the existing operator references:

- `agents/runbooks/runbook-langgraph-opinionated-workflow.md`
- `agents/runbooks/runbook-crewai-integration.md`
- `agents/runbooks/runbook-agentic-memory-review.md`
- `agents/work-items/langgraph-opinionated-workflow-roadmap.md`

## Current Strengths

The current agentic stack already has strong foundations that should be preserved:

1. explicit doctrine enforcement via `agents/knowledge/agentic-team-doctrine.json`
2. bounded review loops in LangGraph with deterministic plan remediation in `server/warships/agentic/graph.py`
3. Phase 0 memory support for procedural, episodic, and operational memory with file and Postgres-capable backends in `server/warships/agentic/memory.py`
4. secret redaction in durable run logs via `server/warships/agentic/runlog.py`
5. boundary enforcement and touched-file constraints in the guarded LangGraph workflow

The practical point is that battlestats already has a governed agent runtime. The next work should improve routing quality, retrieval quality, and cross-run reuse without weakening those controls.

## Implementation Status

The first implementation tranche from this runbook has landed.

Delivered in this tranche:

1. layered retrieval scoring now includes doc type, recency, phrase overlap, workflow-kind relevance, and ranking reasons in `server/warships/agentic/retrieval.py`
2. planning now uses a template registry with named templates such as `clan_hydration`, `cache_behavior`, `api_contract_change`, `agentic_workflow`, and `performance_regression` in `server/warships/agentic/graph.py`
3. API review triggering now considers structured signals from touched files, target files, verification commands, workflow kind, and optional explicit override in `server/warships/agentic/graph.py`
4. workflow-kind inference now gives more weight to API-adjacent and agentic file hints in `server/warships/agentic/memory.py`
5. hybrid and CrewAI dry-run flows now surface structured role artifact blueprints that LangGraph can consume during guarded planning

Not delivered in this tranche:

1. true vector-backed semantic retrieval
2. shared cross-workflow learning for CrewAI and hybrid memory writes
3. dashboard cards dedicated to doctrine, retrieval reasoning, and CrewAI artifacts
4. full hybrid CrewAI kickoff as the default mode

## Confirmed Gaps

The following gaps are confirmed by the current docs and implementation:

### 1. Guidance retrieval is simple token overlap

Current retrieval in `server/warships/agentic/retrieval.py` ranks documents by token overlap against a small curated corpus.

This is cheap and predictable, but it is easy to saturate once more runbooks, reviews, and specs accumulate. It also has no notion of semantic similarity, recency, provenance weighting, or workflow-type relevance.

### 2. Clan hydration is still a hardcoded planning branch

`server/warships/agentic/graph.py` contains `_is_clan_hydration_use_case()` and a dedicated `_plan_task()` branch with a one-off plan and target file list.

This works for a narrow battlestats issue, but it does not scale. Similar task families should be expressed as reusable plan templates or workflow classes rather than as inline special cases.

### 3. API review gate activation is still heuristic and prompt-shaped

`server/warships/agentic/graph.py` activates API review through `_task_needs_api_contract_review()` based on task-string keywords such as `api`, `endpoint`, `payload`, `schema`, `response`, `route`, and `fetch`.

`server/warships/agentic/memory.py` uses similar keyword classification for workflow kind inference.

This is better than no gate, but it is easy to miss API-adjacent work when the task wording is weak or indirect.

### 4. Cross-workflow learning is still incomplete

The memory pilot is real and review-gated, but today durable learning is still LangGraph-owned and post-run. CrewAI and hybrid runs do not yet contribute comparable structured lessons, and no shared retrieval layer combines doctrine, reviewed memory, and prior run outcomes into one ranked context package.

### 5. CrewAI hybrid mode is still planning-heavy and output-light

`server/warships/agentic/router.py` runs CrewAI in dry mode for hybrid execution, then hands only planning notes and the crew plan into LangGraph.

This preserves bounded execution, but it means the richer role-shaped output lane is not surfaced as first-class artifacts in hybrid runs.

## Decision Rules For The Next Tranche

Use these rules while addressing the gaps:

1. preserve bounded retries, deterministic remediation, and file-boundary enforcement
2. keep LangGraph as the guarded implementation owner for code-changing runs
3. prefer additive metadata and templates over broad rewrites of the graph
4. improve trigger quality using structured context before introducing heavier model dependencies
5. require focused tests for every new routing, retrieval, or review decision path

## Recommended Sequence

### Phase 1: Replace flat retrieval scoring with layered retrieval

Status: partially implemented.

Goal:

Improve retrieval quality without immediately committing to a full vector-only design.

Implementation steps:

1. keep the current curated corpus boundaries from `agents/knowledge/`, `agents/runbooks/`, and `agents/reviews/`
2. add document metadata scoring for recency, doc type, and workflow relevance
3. add workflow-kind-aware ranking using the inferred task family from `server/warships/agentic/memory.py`
4. add optional semantic ranking behind a feature flag so battlestats can compare simple ranking against embeddings-based retrieval before making it the default
5. store matched-document provenance and ranking reasons in run logs and dashboard payloads

Implemented now:

1. token overlap is no longer the only ranking input
2. retrieval responses now include ranking reasons and score breakdowns
3. workflow-kind-aware ranking is active for curated doctrine documents

Remaining:

1. true vector-backed retrieval remains pending
2. dashboard-specific visualization of retrieval reasoning remains pending

Why first:

This directly addresses the highest-likelihood quality degradation as the docs corpus grows, and it does so without disturbing the existing guarded workflow shape.

Validation:

1. add unit tests for retrieval ranking with overlapping corpora and semantically similar prompts
2. prove that known battlestats tasks retrieve the expected runbooks more reliably than the current token-overlap baseline
3. confirm run logs show ranked guidance provenance without exposing secrets

### Phase 2: Generalize hardcoded planning into workflow templates

Status: implemented for the initial template set.

Goal:

Remove one-off planning branches such as the clan hydration path and replace them with reusable task-family templates.

Implementation steps:

1. introduce a template registry keyed by workflow kind or task family
2. move the clan hydration plan into a named template instead of `_is_clan_hydration_use_case()` inline logic
3. define a small initial template set such as `cache_behavior`, `api_contract_change`, `agentic_workflow`, and `performance_regression`
4. let templates contribute default plan steps, target file hints, and validation expectations
5. keep freeform fallback planning for tasks that do not match a template

Why second:

This removes the most obvious special-case logic without forcing a full planner rewrite.

Validation:

1. add focused tests showing clan hydration now routes through the template registry
2. prove non-template tasks still receive generic planning
3. confirm design review and API review behavior still operate on template-generated plans

### Phase 3: Promote API review triggers from keyword guessing to structured signals

Status: implemented for the initial structured signal set.

Goal:

Reduce missed API review cases and make the trigger explainable.

Implementation steps:

1. derive API review requirements from a structured signal set: touched files, endpoint modules, serializer modules, route files, response-shaping code, and declared task context
2. keep prompt keywords only as a fallback signal, not the primary trigger
3. add an explicit context override such as `api_review_required=true|false` for operator control
4. include the trigger reasons in workflow summary output and run logs
5. align workflow-kind inference in `server/warships/agentic/memory.py` with the same structured signal model

Why third:

This improves review accuracy while staying compatible with the current graph nodes and bounded remediation loops.

Validation:

1. add test cases where API review is required even though the task text omits obvious keywords
2. add test cases where purely internal work no longer triggers API review spuriously
3. confirm dashboard payloads show whether API review was required and why

### Phase 4: Extend reviewed learning across all workflows

Status: not yet implemented.

Goal:

Move from isolated runs toward controlled cross-workflow reuse.

Implementation steps:

1. preserve the current review-gated memory standard from `agents/runbooks/runbook-agentic-memory-review.md`
2. allow CrewAI and hybrid runs to emit memory candidates with clear provenance and engine type
3. unify doctrine retrieval, reviewed memory retrieval, and prior run artifact lookup into one pre-planning context assembly step
4. scope memories by workflow kind and environment so noisy lessons do not leak across unrelated tasks
5. add supersession guidance for outdated operational lessons

Why fourth:

The current memory design is sound, but it is still too LangGraph-centric to count as real cross-workflow learning.

Validation:

1. add tests proving approved memories can influence later hybrid and CrewAI-assisted runs
2. confirm rejected or superseded memories do not continue to reappear in planning context
3. verify memory activity is visible in run logs and dashboard summaries

### Phase 5: Make hybrid CrewAI artifacts first-class

Status: partially implemented.

Goal:

Keep hybrid mode safe while surfacing more useful persona outputs than handoff notes alone.

Implementation steps:

1. keep CrewAI dry-run as the default hybrid entry until model policy and cost controls are stronger
2. emit structured role artifacts from dry-run planning where possible, not just the persona order and task order
3. attach those artifacts to LangGraph state as inputs the planner can cite explicitly
4. expose the crew plan and role artifacts in hybrid run logs and dashboard summaries
5. add an opt-in mode for full CrewAI execution in hybrid workflows when an operator explicitly requests it and an approved model is configured

Implemented now:

1. dry-run CrewAI results expose structured role artifact blueprints
2. hybrid handoff now passes those artifact blueprints into LangGraph planning context
3. hybrid routing supports opt-in CrewAI kickoff via workflow context while keeping dry-run as the default

Remaining:

1. dashboard-specific surfacing of role artifacts is still shallow
2. completed CrewAI kickoff output is not yet parsed back into typed role artifacts

Why fifth:

This improves hybrid usefulness without giving up the current safety model.

Validation:

1. add tests proving hybrid runs surface structured CrewAI artifacts even in dry mode
2. confirm LangGraph summaries mention which role artifacts influenced the guarded plan
3. prove full hybrid kickoff remains opt-in and policy-controlled

## Suggested File Targets

The likely implementation files for the next tranche are:

- `server/warships/agentic/retrieval.py`
- `server/warships/agentic/graph.py`
- `server/warships/agentic/memory.py`
- `server/warships/agentic/router.py`
- `server/warships/agentic/crewai_runner.py`
- `server/warships/agentic/dashboard.py`
- `server/warships/tests/test_agentic_graph.py`
- `server/warships/tests/test_agentic_router.py`
- `server/warships/tests/test_agentic_crewai.py`
- `server/warships/tests/test_agentic_memory.py`

## Non-Goals For This Tranche

Avoid the following while executing these next steps:

1. replacing LangGraph as the guarded execution owner
2. introducing broad uncurated repo retrieval before ranked curated retrieval is working well
3. allowing unreviewed durable memory writes into shared stores
4. making hybrid mode depend on live CrewAI execution by default
5. mixing product-runtime memory with internal development memory

## Exit Criteria

This next-steps runbook is complete when battlestats can show all of the following:

1. retrieval quality improves on known battlestats tasks and is explainable in logs
2. clan hydration and similar work patterns use templates instead of inline hardcoded planning branches
3. API review triggers are driven primarily by structured signals, not only by task phrasing
4. reviewed memory can influence more than LangGraph-only runs
5. hybrid runs surface useful persona artifacts without losing the current bounded execution model

## Related References

- `agents/knowledge/agentic-team-doctrine.json`
- `agents/langgraph-usage-note.md`
- `agents/runbooks/runbook-crewai-integration.md`
- `agents/runbooks/runbook-langgraph-opinionated-workflow.md`
- `agents/runbooks/runbook-agentic-memory-review.md`
- `agents/work-items/langgraph-opinionated-workflow-roadmap.md`
- `server/warships/agentic/graph.py`
- `server/warships/agentic/retrieval.py`
- `server/warships/agentic/router.py`
- `server/warships/agentic/memory.py`
