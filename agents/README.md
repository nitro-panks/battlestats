# Agent Documentation Map

This directory is the shortest useful path for agents that are told to review project documentation before working.

## First-Read Order

1. `../CLAUDE.md`
   Use this for the current architecture, deployment/runtime commands, multi-realm shape, cache model, and deployment gates.
2. `knowledge/agentic-team-doctrine.json`
   This is the authoritative repo decision rule set for planning, implementation, tests, documentation, and runbook hygiene.
3. `runbooks/README.md`
   This is the active runbook index. Use it to select the few runbooks relevant to the task instead of scanning the whole directory.
4. `knowledge/README.md` or `contracts/README.md`
   Open only if the task depends on upstream behavior, durable research, or payload contracts.
5. `doc_registry.json`
   Use this when you need machine-readable tags, aliases, owners, lifecycle, and section metadata for active docs.

## Default Mental Model

- Battlestats is a Django + Next.js stats platform with a cache-first, background-hydration architecture.
- The browser should not call the Wargaming API directly.
- Realm-aware behavior matters. `na`, `eu`, and `asia` are all active product concerns.
- Operational changes usually involve scheduled warmers, Celery queue behavior, deploy scripts, and published-cache fallbacks, not just endpoint code.
- The `agents/` tree is markdown briefs (personas, knowledge, runbooks) for Claude Code subagents. The experimental in-process LangGraph/CrewAI runtime was retired in v1.12.1 (`f0fbbe3`); there is no agentic runtime to enable.

## Task-To-Doc Routing

- App architecture or runtime behavior:
  Start with `../CLAUDE.md`.
- Which runbooks are current:
  Start with `runbooks/README.md`.
- API contracts or smoke coverage:
  Read `runbooks/runbook-api-surface.md`.
- Frontend player-page fetching, hydration, loading, request cancellation, or perceived latency:
  Read `runbooks/runbook-player-fetch-orchestration-2026-06-21.md` (the canonical client request layer: shared fetch, priority queue, degradation monitor, whole-page cancellation, clan-rail de-waterfall).
- Battle-observation floor, capture coverage/freshness, daily snapshots, or capture throughput:
  Read `runbooks/runbook-floor-throughput-tuning-2026-06-13.md` (canonical current state) and the diagram `diagrams/be-observation-floor-data-flow.md`; branch to the supporting family from the floor entry in `runbooks/README.md`.
- Multi-realm behavior, crawl/warmup, or EU migration:
  Read `runbooks/spec-multi-realm-eu-support.md` and then the related operational runbooks from the active index.
- Deploy, droplet runtime, or memory tuning:
  Read `runbooks/runbook-backend-droplet-deploy.md` or `runbooks/runbook-client-droplet-deploy.md`.
- Verified upstream behavior or expensive rediscovery:
  Read `knowledge/README.md` and then the specific note.
- Structured payload semantics:
  Read `contracts/README.md` and then the specific YAML profile.

## Do Not Read By Default

- `runbooks/archive/` — historical or superseded runbooks.
- `work-items/` — planning specs and tranche scaffolds (shipped/superseded ones under `work-items/archive/`).
- `archive/` — retired persona briefs (`archive/personas/`) and historical QA review snapshots (`archive/reviews/`).

Only open those directories when an active runbook or current debugging task points there.

## Documentation Rules

- Keep durable facts in `knowledge/`.
- Keep current operational or implementation guides in `runbooks/`.
- Keep structured schemas and endpoint contracts in `contracts/`.
- Keep machine-readable doc metadata in `doc_registry.json` for active docs that retrieval should rank well.
- Move completed, historical, incident-specific, or superseded runbooks to `runbooks/archive/`.
- Prefer a small number of maintained entry docs over large narrative duplicates.

## Commit Gate

Before each commit:

- reconcile changed docs with code and tests
- update the durable docs that describe shipped behavior
- keep focused coverage aligned with the change
- archive runbooks whose status no longer matches the live repo state

## Roles (archived)

The persona role briefs (Project Coordinator, Project Manager, Architect, UX,
Designer, Engineer/Web Dev, QA, Safety) were source material for the retired
in-process agentic runtime. They are **not** wired to current workflows and now
live in `archive/personas/`. Current agent behavior is driven by
`knowledge/agentic-team-doctrine.json` (decision rules + gates) and
`../.claude/skills/` (operational workflows); Claude Code uses its built-in
subagent types for delegation.
