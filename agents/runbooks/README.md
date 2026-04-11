# Active Runbooks

This directory should contain only current operational references, active implementation guides, and still-relevant architectural policies.

If a runbook is mainly historical, incident-specific, or completed, move it to `archive/`.

## Metadata System

Active docs in this directory are indexed in `../doc_registry.json`.

For every active runbook or spec, keep these fields current there:

- `owner`: the current team lane responsible for the doc
- `section`: retrieval-facing bucket such as `operations`, `agentic`, `architecture`, `feature-recovery`, or `spec`
- `lifecycle`: `evergreen`, `dated-active`, `active-spec`, or `support-index`
- `aliases`: short alternate names an agent or operator might actually ask for
- `tags`: topic hints that are stronger than filename matching alone
- `archive_on`: the condition that should move the doc out of the active set

If a doc no longer deserves an active registry entry, it probably belongs in `archive/`.

## Start Here

When an agent is told to review project docs, the default runbook read order is:

1. `runbook-api-surface.md` for public API surface, smoke coverage, and request/response expectations.
2. The deploy runbook for the surface you are touching.
3. The architecture or feature-specific runbook that matches the task.

Do not start in `archive/`, `../reviews/`, or `../work-items/` unless an active runbook points there.

## Evergreen Operational Guides

- `runbook-celery-queue-strategy.md`: current queue assessment for crawlers, warmers, and request-driven refresh tasks.
- `runbook-droplet-memory-tuning-2026-04-02.md`: current droplet memory sizing and worker tuning snapshot.
- `runbook-enrichment-crawler-2026-04-03.md`: enrichment progress log. Enrichment runs on the droplet's Celery `background` worker via `enrich_player_data_task`, re-seeded by the `player-enrichment-kickstart` Beat schedule.
- `runbook-deleted-account-purge.md`: purge flow and safety notes for deleted accounts.
- `runbook-dependency-audit.md`: dependency hygiene policy and current audit posture.
- `runbook-post-deploy-post-bounce-operations-2026-04-05.md`: required post-redeploy verification, post-bounce behavior, and bounded warm sequencing.
- `runbook-daily-data-refresh-schedule-2026-04-05.md`: daily refresh cadences and periodic task windows (note: the DO Functions enrichment schedule referenced inside was reverted 2026-04-08 — see the status banner at the top of that runbook).
- `runbook-abs-deprecation-2026-04-07.md`: implemented ABS and clan-CB best-sort retirement plan, plus remaining deploy-time artifact cleanup steps.

## Evergreen Architecture And Policy Guides

- `spec-cache-first-lazy-refresh-policy-2026-03-19.md`: cache-first and lazy-refresh contract.
- `spec-multi-realm-eu-support.md`: multi-realm architecture, rollout status, and migration behavior.
- `spec-production-data-refresh-strategy.md`: data refresh and maintenance intent (partially implemented; enrichment runs on the droplet's Celery `background` worker).
- `runbook-contract-strategy-implementation.md`: payload and contract maintenance expectations.
- `runbook-best-clan-eligibility.md`: composite best-clan ranking rules and exclusions.
- `runbook-seo.md`: metadata, sitemap, structured data, and analytics notes.
- `runbook-recently-viewed-player-warming.md`: recent-visit warming strategy and tuning knobs.

## Evergreen Agentic Guides

- `runbook-agent-orchestrator-selection.md`: choose LangGraph, CrewAI, or hybrid.
- `runbook-langgraph-opinionated-workflow.md`: guarded LangGraph workflow behavior.
- `runbook-crewai-integration.md`: CrewAI platform shape and execution model.
- `runbook-agentic-memory-review.md`: review loop for durable memory.
- `runbook-memory-layering-2026-04-10.md`: agentic memory layering — SuperLocalMemory at the guidance seam.
- `runbook-langsmith-trace-dashboard.md`: `/trace` dashboard, LangSmith wiring, and validation notes.
- `spec-langmem-agentic-memory-pilot-2026-03-26.md`: memory pilot scope and current limitations.

## Evergreen Maintenance And Quality Guides

- `runbook-client-test-hardening.md`: frontend regression and test harness guidance.
- `runbook-codebase-improvement.md`: evergreen maintenance heuristics.
- `runbook-efficiency-rank-qa-2026-04-02.md`: current QA posture for efficiency-rank behavior.
- `runbook-mobile-player-detail-charts.md`: mobile chart rendering behavior on player detail.
- `runbook-mobile-routing-bugs.md`: mobile route-loading regressions and known fixes.
- `runbook-multi-realm-hardening.md`: recent multi-realm cleanup and remaining hardening notes.

## Dated Feature And Recovery Docs

Open these only when the task matches them directly:

- `runbook-agentic-next-steps-2026-04-02.md`
- `runbook-clan-tier-distribution-recovery-2026-04-02.md`
- `runbook-droplet-memory-tuning-2026-04-02.md`
- `runbook-efficiency-rank-qa-2026-04-02.md`
- `runbook-enrichment-crawler-2026-04-03.md`: progress log for the active enrichment crawl pass (batches, disruptions, check-ins)
- `runbook-landing-best-player-subsort-materialization-2026-04-05.md`: current Best-player snapshot materialization and cache behavior
- `runbook-abs-deprecation-2026-04-07.md`: implemented retirement of ABS and clan-CB best sorts, with deploy cleanup steps for stale snapshots and caches
- `runbook-streamer-twitch-icon-2026-04-07.md`: static streamer flag and Twitch badge rollout plan
- `runbook-streamer-submission-feature-2026-04-07.md`: streamer submission queue (footer modal + admin moderation), with deferred approval-side promotion
- `runbook-security-audit-2026-04-05.md`: Wapiti production audit findings and remediation plan (nginx headers, input validation)
- `runbook-icon-analysis.md`
- `runbook-player-achievements-data-lane.md`
- `runbook-kdr-backfill.md`
- `runbook-mobile-player-detail-charts.md`
- `runbook-mobile-routing-bugs.md`
- `runbook-multi-realm-hardening.md`
- `runbook-asia-realm-data-load-2026-04-05.md`: Asia realm data load — clan crawl + enrichment backfill operative plan
- `runbook-search-toggle.md`: header search toggle between player and clan search, with new clan suggestions endpoint

## Active Specs And Open Design Docs

These stay active only while they still shape implementation or operations:

- `spec-landing-best-by-class.md`
- `spec-clan-battle-seasons-chart.md`: D3 multi-series chart for clan CB performance vs realm averages
- `spec-best-clan-subfilters.md`: Best clan sub-filters (Overall, WR) on the landing page
- `spec-best-player-subfilters.md`: Best player sub-sorts (Overall, Ranked, Efficiency, WR, CB) on the landing page
- `spec-clan-battles-by-tier.md`
- `spec-cache-first-lazy-refresh-policy-2026-03-19.md`
- `spec-github-build-status-badge.md`
- `spec-langmem-agentic-memory-pilot-2026-03-26.md`
- `spec-mobile-player-detail-ux-2026-03-28.md`
- `spec-multi-realm-eu-support.md`
- `spec-player-route-follow-up-improvements-2026-03-19.md`
- `spec-cb-seasons-chart-redesign-2026-04-05.md`: CB seasons chart layered redesign spec

## Archive Rule

Move a runbook to `archive/` when any of these are true:

- it documents a fixed incident
- it is a dated performance snapshot or comparison
- it is implemented and no longer the active source of truth
- a newer runbook or spec supersedes it

Archive hygiene checklist:

1. Move the file to `archive/`.
2. Remove it from this README's active sections.
3. Remove or demote its entry in `../doc_registry.json`.
4. Update the successor doc, if one exists, so agents know where to go next.
