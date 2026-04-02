# Agent Documentation Map

This directory is the shortest useful path for agents that are told to review project documentation before working.

## First-Read Order

1. `../CLAUDE.md`
   Use this for the current architecture, deployment/runtime commands, multi-realm shape, caching strategy, and repo doctrine summary.
2. `knowledge/agentic-team-doctrine.json`
   This is the authoritative repo decision rule set for planning, implementation, tests, documentation, and runbook hygiene.
3. `runbooks/README.md`
   This is the active runbook index. Use it to select the few runbooks relevant to the task instead of scanning the whole directory.

## Default Mental Model

- Battlestats is a Django + Next.js stats platform with a cache-first, background-hydration architecture.
- The browser should not call the Wargaming API directly.
- Realm-aware behavior matters. `na` and `eu` are both active product concerns.
- Operational changes usually involve scheduled warmers, Celery queue behavior, deploy scripts, and published-cache fallbacks, not just endpoint code.
- The repo has an internal agentic platform. LangGraph is the guarded implementation lane; CrewAI is the persona-oriented planning lane; hybrid routing combines them.

## Task-To-Doc Routing

- App architecture or runtime behavior:
  Start with `../CLAUDE.md`.
- Which runbooks are current:
  Start with `runbooks/README.md`.
- API contracts or smoke coverage:
  Read `runbooks/runbook-api-surface.md`.
- Multi-realm behavior, crawl/warmup, or EU migration:
  Read `runbooks/spec-multi-realm-eu-support.md` and then the related operational runbooks from the active index.
- Deploy, droplet runtime, or memory tuning:
  Read `runbooks/runbook-backend-droplet-deploy.md` or `runbooks/runbook-client-droplet-deploy.md`.
- Agentic workflow behavior:
  Read `runbooks/runbook-agent-orchestrator-selection.md`, `runbooks/runbook-langgraph-opinionated-workflow.md`, and `langgraph-usage-note.md`.
- Agentic memory or review flow:
  Read `runbooks/runbook-agentic-memory-review.md` and `runbooks/spec-langmem-agentic-memory-pilot-2026-03-26.md`.

## Documentation Rules

- Keep durable facts in `knowledge/`.
- Keep current operational or implementation guides in `runbooks/`.
- Move completed, historical, incident-specific, or superseded runbooks to `runbooks/archive/`.
- Prefer a small number of maintained entry docs over large narrative duplicates.

## Commit Gate

Before each commit:

- reconcile changed docs with code and tests
- update the durable docs that describe shipped behavior
- keep focused coverage aligned with the change
- archive runbooks whose status no longer matches the live repo state

## Roles

The role files in this directory remain the persona source material for the agentic stack:

- Project Coordinator
- Project Manager
- Architect
- UX
- Designer
- Engineer (Web Dev)
- QA
- Safety
