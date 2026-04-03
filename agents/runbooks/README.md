# Active Runbooks

This directory should contain only current operational references, active implementation guides, and still-relevant architectural policies.

If a runbook is mainly historical, incident-specific, or completed, move it to `archive/`.

## Start Here

When an agent is told to review project docs, the default runbook read order is:

1. `runbook-api-surface.md` for public API surface, smoke coverage, and request/response expectations.
2. The deploy runbook for the surface you are touching.
3. The architecture or feature-specific runbook that matches the task.

Do not start in `archive/`, `../reviews/`, or `../work-items/` unless an active runbook points there.

## Core Operations

- `runbook-api-surface.md`: public endpoints, smoke coverage, and response-shape notes.
- `runbook-backend-droplet-deploy.md`: backend deployment, runtime env, and service behavior on the droplet.
- `runbook-client-droplet-deploy.md`: client deployment and frontend runtime notes.
- `runbook-db-target-switching.md`: local vs cloud database target switching.
- `runbook-cache-audit.md`: cache design and current operational expectations.
- `runbook-deploy-oom-startup-warmers.md`: startup warmers, OOM tradeoffs, and warm-cache deploy expectations.
- `runbook-droplet-memory-tuning-2026-04-02.md`: current droplet memory sizing and worker tuning snapshot.
- `runbook-flower-observability-2026-04-02.md`: production Flower plan for Celery queue visibility on the droplet.
- `runbook-enrichment-crawler-2026-04-02.md`: enrichment crawler architecture, operations, battles_json usage, and product roadmap.
- `runbook-deleted-account-purge.md`: purge flow and safety notes for deleted accounts.
- `runbook-dependency-audit.md`: dependency hygiene policy and current audit posture.

## Architecture And Product Policy

- `spec-cache-first-lazy-refresh-policy-2026-03-19.md`: cache-first and lazy-refresh contract.
- `spec-multi-realm-eu-support.md`: multi-realm architecture, rollout status, and migration behavior.
- `spec-production-data-refresh-strategy.md`: data refresh and maintenance intent.
- `runbook-contract-strategy-implementation.md`: payload and contract maintenance expectations.
- `runbook-best-clan-eligibility.md`: composite best-clan ranking rules and exclusions.
- `runbook-seo.md`: metadata, sitemap, structured data, and analytics notes.
- `runbook-recently-viewed-player-warming.md`: recent-visit warming strategy and tuning knobs.

## Agentic Platform

- `runbook-agent-orchestrator-selection.md`: choose LangGraph, CrewAI, or hybrid.
- `runbook-langgraph-opinionated-workflow.md`: guarded LangGraph workflow behavior.
- `runbook-crewai-integration.md`: CrewAI platform shape and execution model.
- `runbook-agentic-memory-review.md`: review loop for durable memory.
- `runbook-agentic-next-steps-2026-04-02.md`: current follow-up plan for the agentic stack.
- `runbook-langsmith-trace-dashboard.md`: `/trace` dashboard, LangSmith wiring, and validation notes.
- `spec-langmem-agentic-memory-pilot-2026-03-26.md`: memory pilot scope and current limitations.

## Active Maintenance And Quality

- `runbook-client-test-hardening.md`: frontend regression and test harness guidance.
- `runbook-cicd-harness-rollout.md`: CI and validation rollout status.
- `runbook-codebase-improvement.md`: evergreen maintenance heuristics.
- `runbook-efficiency-rank-qa-2026-04-02.md`: current QA posture for efficiency-rank behavior.
- `runbook-mobile-player-detail-charts.md`: mobile chart rendering behavior on player detail.
- `runbook-mobile-routing-bugs.md`: mobile route-loading regressions and known fixes.
- `runbook-multi-realm-hardening.md`: recent multi-realm cleanup and remaining hardening notes.

## Task-Specific Active Guides

Open these only when the task matches them directly:

- `runbook-clan-tier-distribution-recovery-2026-04-02.md`
- `runbook-eu-best-player-population-2026-04-02.md`
- `runbook-eu-heatmap-rollout-2026-04-02.md`
- `runbook-eu-profile-chart-population-2026-04-02.md`
- `runbook-icon-analysis.md`
- `runbook-player-achievements-data-lane.md`
- `runbook-kdr-backfill.md`
- `spec-landing-best-by-class.md`
- `spec-clan-battle-seasons-chart.md`: D3 multi-series chart for clan CB performance vs realm averages
- `spec-clan-battles-by-tier.md`
- `spec-github-build-status-badge.md`
- `spec-mobile-player-detail-ux-2026-03-28.md`
- `spec-player-route-follow-up-improvements-2026-03-19.md`

## Archive Rule

Move a runbook to `archive/` when any of these are true:

- it documents a fixed incident
- it is a dated performance snapshot or comparison
- it is implemented and no longer the active source of truth
- a newer runbook or spec supersedes it
