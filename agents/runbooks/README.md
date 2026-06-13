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

Do not start in `archive/`, `../archive/`, or `../work-items/` unless an active runbook points there.

## Evergreen Operational Guides

- `runbook-celery-queue-strategy.md`: current queue assessment for crawlers, warmers, and request-driven refresh tasks.
- `runbook-pause-resume-clan-crawls-2026-06-10.md`: safe procedure to pause/resume clan crawls for a maintenance window (stop the single-slot crawls worker, clear locks while preserving pass markers, watchdog/lock gotchas).
- `runbook-enrichment-crawler-2026-04-03.md`: enrichment progress log. Enrichment runs on the droplet's Celery `background` worker via `enrich_player_data_task`, re-seeded by the `player-enrichment-kickstart` Beat schedule.
- `runbook-deleted-account-purge.md`: purge flow and safety notes for deleted accounts.
- `runbook-dependency-audit.md`: dependency hygiene policy and current audit posture.
- `runbook-post-deploy-post-bounce-operations-2026-04-05.md`: required post-redeploy verification, post-bounce behavior, and bounded warm sequencing.
- `runbook-daily-data-refresh-schedule-2026-04-05.md`: daily refresh cadences and periodic task windows (note: the DO Functions enrichment schedule referenced inside was reverted 2026-04-08 — see the status banner at the top of that runbook).
- `runbook-daily-active-snapshots-2026-06-09.md`: daily `Snapshot` engine for every active player (`snapshot_active_players_task`, coexists with crawls; kill switch `SNAPSHOT_ACTIVE_PLAYERS_ENABLED`).
- `runbook-leaderboard-updates.md`: ship-leaderboard / standings freshness and snapshot cadence ("is the leaderboard stale?").
- `runbook-floor-throughput-tuning-2026-06-13.md`: observation-floor throughput tuning — background-pool contention, enrichment self-chain spin fix, coverage levers.
- `runbook-incident-celery-zombie-worker-2026-04-12.md`: the celery zombie-worker failure mode (service `active` with 0 consumers) and watchdog recovery.
- `runbook-droplet-hardening-2026-04-09.md`: droplet security posture — ssh/tls/nginx/systemd hardening.

## Evergreen Architecture And Policy Guides

- `spec-cache-first-lazy-refresh-policy-2026-03-19.md`: cache-first and lazy-refresh contract.
- `spec-multi-realm-eu-support.md`: multi-realm architecture, rollout status, and migration behavior.
- `spec-production-data-refresh-strategy.md`: data refresh and maintenance intent (partially implemented; enrichment runs on the droplet's Celery `background` worker).
- `runbook-contract-strategy-implementation.md`: payload and contract maintenance expectations.
- `runbook-best-clan-eligibility.md`: composite best-clan ranking rules and exclusions.
- `runbook-seo.md`: metadata, sitemap, structured data, and analytics notes.
- `runbook-recently-viewed-player-warming.md`: recent-visit warming strategy and tuning knobs.
- `runbook-battle-history-rollout-2026-04-28.md`: battle-history pipeline rollout (`BattleObservation`/`BattleEvent`, per-day/period rollups).
- `runbook-ranked-battle-history-rollout-2026-05-02.md`: ranked-mode battle-history capture (season-scoped seasons/shipstats).

## Agentic Tooling (current)

The experimental in-process LangGraph/CrewAI runtime and its LangSmith/LangMem
memory layer were **retired in v1.12.1** (`f0fbbe3`); those runbooks now live in
`archive/` (tagged `retired-runtime`) for historical reference only. There is no
in-app agentic runtime to enable.

Current agent workflows run through Claude Code itself:

- `../knowledge/agentic-team-doctrine.json` — authoritative decision rules, pre-commit checklist, and quality gates.
- `../../.claude/skills/` — the recurring operational workflows (deploy, release-gate, doctrine-precommit, enrichment-status, observation, runbook-author/archive).
- `../../CLAUDE.md` — always-loaded repo defaults and routing.

## Evergreen Maintenance And Quality Guides

- `runbook-client-test-hardening.md`: frontend regression and test harness guidance.
- `runbook-codebase-improvement.md`: evergreen maintenance heuristics.
- `runbook-mobile-player-detail-charts.md`: mobile chart rendering behavior on player detail.
- `runbook-mobile-routing-bugs.md`: mobile route-loading regressions and known fixes.
- `runbook-multi-realm-hardening.md`: recent multi-realm cleanup and remaining hardening notes.

## Dated Feature And Recovery Docs

Open these only when the task matches them directly:

- `runbook-enrichment-crawler-2026-04-03.md`: progress log for the active enrichment crawl pass (batches, disruptions, check-ins)
- `runbook-landing-best-player-subsort-materialization-2026-04-05.md`: current Best-player snapshot materialization and cache behavior
- `runbook-streamer-twitch-icon-2026-04-07.md`: static streamer flag and Twitch badge rollout plan
- `runbook-streamer-submission-feature-2026-04-07.md`: streamer submission queue (footer modal + admin moderation), with deferred approval-side promotion
- `runbook-security-audit-2026-04-05.md`: Wapiti production audit findings and remediation plan (nginx headers, input validation)
- `runbook-icon-analysis.md`
- `runbook-player-achievements-data-lane.md`
- `runbook-mobile-player-detail-charts.md`
- `runbook-mobile-routing-bugs.md`
- `runbook-multi-realm-hardening.md`
- `runbook-asia-realm-data-load-2026-04-05.md`: Asia realm data load — clan crawl + enrichment backfill operative plan
- `runbook-search-toggle.md`: header search toggle between player and clan search, with new clan suggestions endpoint
- `runbook-ship-top-player-badges-2026-06-05.md`: `/ship` standings leaderboard + profile ship badges (`ShipTopPlayerSnapshot`, `SHIP_BADGE_SNAPSHOT_ENABLED`).
- `runbook-ship-award-ledger-2026-06-05.md`: durable per-ship career Ship Honors (`ShipAward` append-only ledger).
- `runbook-ship-banner-ux-pass-2026-06-05.md`: ship-award surfaces UX pass (`ShipTopPlayerBanner` / `ShipHonors` type hierarchy + tokens).
- `runbook-enriched-data-features-2026-04-12.md`: enrichment-backed feature surfaces (distributions, correlations, explorer summaries).

## Active Specs And Open Design Docs

These stay active only while they still shape implementation or operations:

- `spec-landing-best-by-class.md`
- `spec-clan-battle-seasons-chart.md`: D3 multi-series chart for clan CB performance vs realm averages
- `spec-best-clan-subfilters.md`: Best clan sub-filters (Overall, WR) on the landing page
- `spec-best-player-subfilters.md`: Best player sub-sorts (Overall, Ranked, Efficiency, WR, CB) on the landing page
- `spec-clan-battles-by-tier.md`
- `spec-cache-first-lazy-refresh-policy-2026-03-19.md`
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
