# Active Runbooks

This directory should contain only current operational references, active implementation guides, and still-relevant architectural policies.

If a runbook is mainly historical, incident-specific, or completed, move it to `archive/`.

## Start Here

When an agent is told to review project docs, the default runbook read order is:

1. `runbook-agent-orchestrator-selection.md` for how the internal agentic lanes are meant to be used.
2. `runbook-api-surface.md` for the public API surface and smoke coverage.
3. Only then open the task-specific runbooks below.

## Core Operations

- `runbook-api-surface.md`: public endpoints, smoke coverage, and response-shape notes.
- `runbook-backend-droplet-deploy.md`: backend deployment, runtime env, and service behavior on the droplet.
- `runbook-client-droplet-deploy.md`: client deployment and frontend runtime notes.
- `runbook-db-target-switching.md`: local vs cloud database target switching.
- `runbook-cache-audit.md`: cache design and current operational expectations.

## Architecture And Product Policy

- `spec-cache-first-lazy-refresh-policy-2026-03-19.md`: cache-first and lazy-refresh contract.
- `spec-multi-realm-eu-support.md`: multi-realm architecture, rollout status, and migration behavior.
- `spec-production-data-refresh-strategy.md`: data refresh and maintenance intent.
- `runbook-contract-strategy-implementation.md`: payload and contract maintenance expectations.

## Agentic Platform

- `runbook-agent-orchestrator-selection.md`: choose LangGraph, CrewAI, or hybrid.
- `runbook-langgraph-opinionated-workflow.md`: guarded LangGraph workflow behavior.
- `runbook-crewai-integration.md`: CrewAI platform shape and execution model.
- `runbook-agentic-memory-review.md`: review loop for durable memory.
- `runbook-agentic-next-steps-2026-04-02.md`: current follow-up plan for the agentic stack.

## Active Maintenance And Quality

- `runbook-client-test-hardening.md`: frontend regression and test harness guidance.
- `runbook-cicd-harness-rollout.md`: CI and validation rollout status.
- `runbook-codebase-improvement.md`: evergreen maintenance heuristics.
- `runbook-langsmith-trace-dashboard.md`: local trace dashboard behavior and validation.

## Task-Specific Active Guides

Open these only when the task matches them directly:

- `runbook-clan-tier-distribution-recovery-2026-04-02.md`
- `runbook-player-achievements-data-lane.md`
- `runbook-best-clan-eligibility.md`
- `runbook-seo.md`
- `spec-landing-best-by-class.md`
- `spec-langmem-agentic-memory-pilot-2026-03-26.md`

## Archive Rule

Move a runbook to `archive/` when any of these are true:

- it documents a fixed incident
- it is a dated performance snapshot or comparison
- it is implemented and no longer the active source of truth
- a newer runbook or spec supersedes it
