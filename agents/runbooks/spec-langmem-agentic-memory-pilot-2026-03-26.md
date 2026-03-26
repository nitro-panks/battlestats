---
title: LangMem Pilot For Agentic Development
doc_type: spec
status: pilot-spec-with-initial-implementation
last_updated: 2026-03-26
implementation_status: partially_implemented
integration_scope: internal agentic development only
write_owner: langgraph
read_scope:
  - langgraph
  - dashboard
write_policy:
  candidate_generation: post-run only
  durable_write: review-gated
  crewai_write_access: disabled
memory_backends:
  - file
  - langgraph_memory
  - langgraph_postgres
namespace_scheme:
  - battlestats
  - <environment>
  - <memory_type>
environments:
  - local
  - staging
  - prod-agentic
memory_types:
  - procedural
operator_surface:
  command: python manage.py review_agent_memory
  supports:
    - list pending candidates
    - approve candidates
    - reject candidates
    - supersede prior memories
observability:
  dashboard_payload: enabled
  trace_endpoint: disabled_by_design_at_present
  run_log_fields:
    - memory_store_activity
    - memory_retrieval_count
    - memory_candidate_count
salient_code_paths:
  - server/warships/agentic/memory.py
  - server/warships/agentic/router.py
  - server/warships/agentic/dashboard.py
  - server/warships/management/commands/review_agent_memory.py
validation_artifacts:
  - server/warships/tests/test_agentic_memory.py
  - server/warships/tests/test_agentic_router.py
  - server/warships/tests/test_agentic_dashboard.py
  - server/warships/tests/test_agentic_memory_command.py
---

# Spec: LangMem Pilot For Agentic Development

_Last updated: 2026-03-26_

_Status: Proposed pilot with initial battlestats integration landed._

## Implementation Metadata

- Current write path is LangGraph-only and runs after workflow completion.
- Durable memory writes are review-gated; candidate memories are queued first and promoted only after explicit approval.
- Current supported backends are `file`, `langgraph_memory`, and `langgraph_postgres`.
- Current operator surface is the `review_agent_memory` management command for listing, approving, rejecting, and superseding candidates.
- Current dashboard content includes memory-store totals, recent reviewed memories, recent candidates, provenance, and supersession state.
- Current trace endpoint content contract includes memory-store fields, but the endpoint itself is intentionally down by design at present.
- Current intended use is battlestats internal development memory, not user-facing product memory and not generic application runtime state.

## Recommendation

LangMem is a reasonable addition to battlestats only as a scoped memory layer for internal agentic development workflows.

It should not be introduced as a user-facing product dependency or as a generic memory substrate for the main Django and Next.js application.

The repo already has:

- LangGraph orchestration
- CrewAI orchestration
- routed engine selection
- checkpoint support
- run logs and trace summaries
- repo-backed doctrine and runbook retrieval

That means the useful LangMem question here is narrow:

Can the agentic workflow stop rediscovering the same repo-specific implementation and validation lessons across runs?

The answer is likely yes, but only if the first pilot is:

1. LangGraph-first
2. background-written, not hot-path-written
3. procedural-memory-heavy, not personalization-heavy
4. evidence-linked and reviewable
5. isolated from product runtime paths

## Non-Goals

This pilot should not:

1. add end-user conversational memory to battlestats features
2. let agents freely rewrite their prompts in production without review
3. replace runbooks, tests, or repo knowledge files as the source of truth
4. store arbitrary chat transcripts as durable memory
5. mix memory persistence with cache keys or public API response paths

## 1. Memory Scope

The first pilot should only persist memory types that are already valuable in battlestats agentic work and are low-risk to reuse.

### In scope for phase 1

1. Procedural memory
   Examples:
   - which validation commands matter for a workflow
   - which focused tests tend to catch regressions in a subsystem
   - which rollback or cache invalidation steps are required for a known pattern
   - which files are the durable source of truth for a topic

2. Episodic implementation memory
   Examples:
   - a recent run found that a cold-path endpoint returned `200 []` while warming
   - a previous fix failed because a browser-level test did not cover a pending header path
   - a route was sensitive to API throttling and required paced browser checks

3. Repo-operational memory
   Examples:
   - local environment quirks that affect agent execution
   - known validation commands for agentic routing, traces, or checkpoint behavior

### Explicitly out of scope for phase 1

1. User preference memory for battlestats visitors
2. long-lived memory about arbitrary feature requests without evidence
3. autonomous prompt optimization or self-editing system prompts
4. memory derived from failed runs unless it has explicit human-reviewed value
5. CrewAI write-path memory

### Practical scope rule

If a memory would be unsafe, embarrassing, or hard to verify when quoted back in a future implementation run, it should not be persisted.

## 2. Storage Layout

The pilot should use LangMem with LangGraph storage semantics, but keep battlestats memory logically separate from checkpoints and application data.

### Recommended backend

1. Local development:
   - in-memory or sqlite-backed store for fast iteration

2. Shared or durable environments:
   - Postgres-backed LangGraph store
   - separate logical namespace from checkpoint state
   - separate retention policy from application tables and Redis caches

### Namespace plan

Use explicit namespaces so memory remains queryable and reviewable by workflow type.

Recommended namespace hierarchy:

1. `("battlestats", env, "procedural")`
2. `("battlestats", env, "episodic")`
3. `("battlestats", env, "operational")`
4. later only if justified: `("battlestats", env, "prompt_optimization")`

Where `env` is one of:

1. `local`
2. `staging`
3. `prod-agentic`

Phase 1 should not read across environments.

### Memory record shape

Each stored memory should carry explicit metadata so future retrieval can be filtered by trust level.

Recommended fields:

1. `memory_type`
2. `summary`
3. `detail`
4. `source_run_id`
5. `engine`
6. `workflow_kind`
7. `evidence`
8. `confidence`
9. `created_at`
10. `review_status`
11. `supersedes`

Recommended `evidence` payload:

1. file paths touched
2. validation commands run
3. tests passed or failed
4. trace URL or local run-log path

### Storage separation rule

Do not store LangMem records in Redis caches or piggyback on app-level JSON cache keys. This memory is development workflow state, not request-serving state.

## 3. Update Policy

The update policy should be conservative.

### Phase 1 write policy

1. Write memory only in the background after a LangGraph run completes.
2. Write only from runs that reached a success or review-complete terminal state.
3. Do not write from abandoned or clearly failed runs by default.
4. Extract memory from structured run summaries, touched files, and validation evidence, not raw transcript dumps.
5. Consolidate similar memories instead of appending duplicates indefinitely.

### Phase 1 read policy

1. Retrieve procedural and operational memories before planning.
2. Retrieve episodic memories only for matching workflow categories.
3. Cap retrieval to a small bounded set such as top 3 to 5 memories.
4. Prefer high-confidence, reviewed entries.
5. Fall back cleanly when no memory is relevant.

### Suggested workflow categories

1. `api_contract_change`
2. `cache_behavior`
3. `client_route_smoke`
4. `agentic_workflow`
5. `upstream_contract_review`
6. `performance_regression`

### Consolidation rules

When a new memory is materially the same as an existing one:

1. update evidence and timestamp
2. raise or lower confidence based on fresh validation
3. mark older contradictory entries as superseded

### Deliberate phase-1 restriction

Do not let the live agent decide unilaterally that a memory is important enough to persist in the hot path. Background extraction is safer and aligns better with battlestats doctrine.

## 4. Review Guardrails

LangMem will only help if it does not become a pile of plausible folklore.

### Guardrails for memory creation

1. Every durable memory must point to evidence.
2. Every durable memory must be tied to a run id or review artifact.
3. Memories should be compact statements, not essay-length transcripts.
4. Memories that mention endpoint behavior must be backed by code, tests, or direct validation.
5. Memories that contradict existing runbooks or repo notes should be flagged for review, not silently preferred.

### Guardrails for memory retrieval

1. Retrieved memory should be shown to the workflow as advisory context, not as unquestioned truth.
2. The workflow summary should say when memory was used.
3. If a retrieved memory materially changes a plan, the run summary should record that fact.
4. Retrieved memory should never suppress required tests or documentation updates.
5. Memory should not bypass doctrine-aware review gates.

### Guardrails for sensitive content

Do not persist:

1. secrets or credentials
2. raw user-provided private content
3. large code excerpts unless the value is impossible to express as a short operational fact
4. unreviewed prompt edits intended to silently alter future agent behavior

### Human review expectation

For the first pilot tranche, new memory classes and consolidation behavior should be reviewed by maintainers through logs or a lightweight dashboard surface before broader rollout.

## 5. First Integration Points

The first integration points should stay inside the existing LangGraph-heavy path.

### First code integration targets

1. `server/warships/agentic/graph.py`
   Use retrieved procedural memory as bounded pre-plan context.

2. `server/warships/agentic/router.py`
   Gate LangMem usage behind explicit engine and config checks.

3. `server/warships/agentic/runlog.py`
   Persist enough structured summary detail to support background extraction.

4. new module: `server/warships/agentic/memory.py`
   Own LangMem adapters, namespaces, filtering, and consolidation policy.

5. `server/warships/agentic/dashboard.py`
   Show recent memory retrieval and write activity in a compact operator-facing view.

### First workflow hooks

1. Pre-plan retrieval hook
   - fetch top reviewed procedural memories for the workflow kind

2. Post-run extraction hook
   - build candidate memories from summary, touched files, and validation commands

3. Review-aware write hook
   - write only when the run finished with acceptable verification posture

### Engine scope

Phase 1 should be LangGraph-only.

CrewAI should only consume retrieved memory later, after the LangGraph pilot proves useful and stable. CrewAI should not be allowed to write durable memory in the first tranche.

## Pilot Rollout Plan

### Phase 0: instrumentation only

1. define namespace contract
2. define memory record schema
3. log candidate memories without storing them
4. compare candidate memories against existing repo notes and runbooks

### Phase 1: reviewed procedural memory

1. enable background writes for reviewed procedural memories only
2. retrieve top memories before LangGraph planning
3. record retrieval and usage in run summaries

### Phase 2: episodic memory

1. add bounded episodic retrieval for matching workflow kinds
2. allow supersession and consolidation
3. surface memory provenance in the trace dashboard

### Phase 3: optional CrewAI read path

1. allow routed or CrewAI runs to consume reviewed memory
2. keep durable writes LangGraph-owned unless later evidence justifies expansion

## Success Criteria

The pilot is useful only if it improves repeat work quality without adding ambiguity.

Success should be judged by:

1. fewer repeated rediscoveries of repo-specific operational facts
2. better reuse of focused validation commands across similar runs
3. run summaries that explicitly show when memory altered or improved a plan
4. no noticeable increase in undocumented or incorrect behavior being repeated by the workflow
5. no coupling to request-serving runtime paths

## Validation Plan

### Code-level validation once implemented

1. unit tests for namespace selection and filtering
2. unit tests for consolidation and supersession logic
3. agentic graph tests proving retrieval is bounded and optional
4. dashboard tests showing memory activity summaries
5. router tests confirming the feature is disabled cleanly when config is absent

### Manual validation

1. run two similar cache-related tasks and confirm the second run reuses procedural memory
2. run a task that previously required a special validation command and confirm the command is suggested from memory
3. verify the workflow summary identifies which memory entries were used
4. verify a contradictory or stale memory can be superseded cleanly

## Recommended Initial Decision

Proceed only with a small LangGraph-only pilot focused on reviewed procedural memory.

Do not start with:

1. full transcript memory
2. hot-path self-writing memory tools
3. end-user personalization
4. CrewAI write access
5. automatic prompt optimization

That keeps the pilot aligned with battlestats doctrine: smallest safe vertical slice, reversible rollout, explicit observability, and minimal risk of memory drift.
