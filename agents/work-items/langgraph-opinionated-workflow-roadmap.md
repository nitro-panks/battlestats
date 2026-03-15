# LangGraph Opinionated Workflow Roadmap

_Drafted: 2026-03-15_

## Purpose

Capture a durable set of next steps for making the battlestats agent workflows more opinionated over time, with the goal of moving team preferences out of ad hoc prompt text and into reusable workflow structure.

This document is meant to be revisited as the repo's agentic stack matures.

## Current State

The repo already has useful building blocks:

- role markdown for battlestats personas under `agents/`
- CrewAI persona orchestration under `server/warships/agentic/crewai_runner.py`
- a guarded LangGraph execution flow under `server/warships/agentic/graph.py`
- automatic engine routing under `server/warships/agentic/router.py`
- local run logging and a `/trace` dashboard for inspection

What is still missing is a durable, runtime-visible concept of team doctrine:

- preferred patterns
- discouraged patterns
- review priorities
- decision rules
- human overrides that can evolve over time

## Working Goal

Teach the battlestats agent workflows to express stable engineering opinions without pretending to fine-tune model weights.

The practical target is:

1. battlestats doctrine is explicit,
2. the graph can load it at runtime,
3. planning and review nodes can react to it,
4. later retrieval and feedback loops can evolve it.

## Phased Next Steps

### Phase 1: Structured Doctrine Layer

Status: initial implementation delivered on 2026-03-15.

Goal:

Introduce a structured doctrine payload that can ride in graph state and influence planning without requiring a vector store.

Scope:

1. define battlestats defaults for preferred patterns, discouraged patterns, review priorities, and decision rules
2. allow runtime overrides through workflow context
3. load the merged doctrine into graph state before planning
4. surface doctrine usage in planning and summary notes
5. store the repo-default doctrine in a file-backed artifact so the team can evolve it without editing Python

Why first:

- it creates a stable place to encode team preferences,
- it is cheap to implement and test,
- it makes later feedback and retrieval work additive instead of disruptive.

Implemented so far:

1. repo-backed doctrine file: `agents/knowledge/agentic-team-doctrine.json`
2. doctrine loading and runtime override merging in `server/warships/agentic/doctrine.py`
3. doctrine-aware planning and design review in `server/warships/agentic/graph.py`
4. curated retrieval over `agents/runbooks/` and `agents/reviews/`
5. API contract review as a second doctrine-aware gate
6. operator documentation in `agents/runbooks/runbook-langgraph-opinionated-workflow.md`

### Phase 2: Feedback Loops And Human Overrides

Goal:

Let humans or downstream review steps record explicit preference changes that later runs can consume.

Candidate directions:

1. persist lightweight override files under `agents/` or a dedicated workflow memory folder
2. allow scoped overrides such as `frontend`, `api`, `migration`, or `performance`
3. track provenance for each override: source, date, rationale
4. teach the graph to attach the latest relevant override snippets at run start

Success signal:

The workflow can answer not only what the default doctrine says, but also what the team most recently decided.

### Phase 3: Opinionated Review Nodes

Goal:

Move battlestats opinions into graph behavior instead of leaving them only in context strings.

Candidate nodes:

1. `design_pattern_review`
2. `api_contract_review`
3. `observability_review`
4. `rollback_readiness_review`

Desired behavior:

1. score or flag a proposal against battlestats doctrine
2. route weak plans back to planning or redesign
3. carry structured issues forward into summary output and trace logs

### Phase 4: Selective Multi-Agent Debate

Goal:

Use debate only when the task is high impact enough to justify extra cost and complexity.

Good candidates:

1. migrations
2. upstream API load changes
3. caching strategy changes
4. major UI architecture changes

Suggested debate shape:

1. architect proposal
2. performance-focused critique
3. maintainability-focused critique
4. QA risk summary
5. final decision node that chooses, requests revision, or escalates

### Phase 5: Retrieval-Augmented Doctrine

Goal:

Let agents retrieve battlestats doctrine dynamically from repo artifacts rather than relying only on static defaults.

Good first sources:

1. runbooks under `agents/runbooks/`
2. QA reviews under `agents/reviews/`
3. work-item specs under `agents/work-items/`
4. selected top-level docs such as `README.md`

Guideline:

Do not start with broad repo retrieval. Start with doctrine-bearing documents that already contain real battlestats preferences and tradeoff history.

## Initial Doctrine Candidates

The first battlestats doctrine layer should likely encode preferences like these:

### Preferred patterns

1. incremental evolution over rewrite
2. additive API changes when possible
3. explicit contracts and validation evidence
4. reuse of existing fetch paths and shared components
5. non-blocking background hydration over synchronous page-load fan-out

### Discouraged patterns

1. new browser-triggered upstream WG calls when stored data can be reused
2. unbounded polling or hydration loops
3. hidden schema drift between backend docs and shipped payloads
4. large unscoped refactors during feature delivery

### Review priorities

1. correctness and observability first
2. rollback clarity
3. bounded API and queue load
4. consistency with existing battlestats UX and data contracts

### Decision rules

1. prefer the smallest safe vertical slice
2. prefer reversible changes
3. validate touched areas with focused tests before expanding scope
4. preserve current user-facing behavior unless the task explicitly changes it

## Open Questions

1. Should doctrine remain code-defined for a while, or move quickly to file-backed JSON or YAML?
2. Should overrides be repo-scoped only, or should there also be developer-local preferences?
3. What is the right threshold for triggering multi-agent debate rather than a single review node?
4. Which existing runbooks represent stable team doctrine versus one-off delivery history?

## Recommended Near-Term Sequence

1. add persistent human override capture with provenance
2. add `rollback_readiness_review` and `observability_review`
3. decide whether `/trace` should expose doctrine and guidance details in dedicated cards
4. then consider richer retrieval ranking or a vector-backed retrieval layer if the curated approach stops being enough

## Definition Of Progress

This roadmap is paying off when:

1. workflow outputs start referencing battlestats doctrine explicitly,
2. planning choices become more consistent across runs,
3. review nodes can reject or reroute proposals based on doctrine,
4. newer team decisions can be injected without rewriting persona prompts.