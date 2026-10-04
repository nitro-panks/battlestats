# Active Runbooks

This directory contains only current operational references, active implementation guides, and still-relevant architectural policies. If a runbook is mainly historical, incident-specific, or completed, move it to `archive/` (skill: `runbook-archive`).

**This index is complete.** All 103 `.md` files in this directory are listed in the sections below (`runbook-api-surface.md` also appears in Start Here, as the default first read). It was 48 of 113 until 2026-08-15, and the gap was not random — it was nearly all recent operational work, so the newest knowledge was the least reachable. If you add a runbook, add its line here in the same commit; if you archive one, remove its line. `../doc_registry.json` is the machine index and is checked against this directory by the two-part done-gate.

## Metadata System

Active docs here are indexed in `../doc_registry.json`. Keep these fields current:

- `owner` — the team lane responsible · `section` — retrieval bucket · `lifecycle` — `evergreen`, `dated-active`, `active-spec`, `support-index`
- `aliases` — what an agent or operator would actually ask for · `tags` — topic hints stronger than the filename
- `archive_on` — the condition that should move this doc out of the active set

If a doc no longer deserves an active registry entry, it probably belongs in `archive/`.

## Start Here

1. `runbook-api-surface.md` — public API surface, smoke coverage, request/response expectations.
2. The deploy runbook for the surface you are touching.
3. The architecture or feature runbook matching the task.

Do not start in `archive/`, `../archive/`, or `../work-items/` unless an active doc points there.

## Agentic Tooling

The in-process LangGraph/CrewAI runtime and its LangSmith/LangMem memory layer were **retired in v1.12.1**; those runbooks are in `archive/` (tagged `retired-runtime`). There is no in-app agentic runtime. Current workflows run through Claude Code: `../knowledge/agentic-team-doctrine.json` (authoritative decision rules and pre-commit checklist), `../../.claude/skills/`, and `../../CLAUDE.md`.

- `runbook-claude-md-durability.md` — keeps `CLAUDE.md` a thin dispatch file; the re-slim procedure and the `scripts/check_claude_md.sh` word/line caps.

---

## Environment, Deploy, Release

- `ops-env-reference.md` — **the env-var catalog**: env files, every runtime var with defaults, Umami, Docker ports.
- `ops-infra-resources.md` — **authoritative production sizing** (droplet 2 vCPU / 8 GB, managed PG 2 vCPU / 4 GB) and the re-verify recipe. Do not plan against a 1-vCPU DB.
- `runbook-env-value-authority-2026-08-05.md` — why a code default is not a live value, and the authority-carrying form for quoting one.
- `runbook-backend-droplet-deploy.md` · `runbook-client-droplet-deploy.md` — bare-droplet setup for the Django backend and the Next.js client.
- `runbook-worktree-local-prereqs-2026-08-13.md` — deploying and gating from a worktree; how gitignored prerequisites resolve from the main checkout.
- `runbook-post-deploy-post-bounce-operations-2026-04-05.md` — required post-redeploy verification and bounded warm sequencing.
- `runbook-post-deploy-verification-2026-08-07.md` — verifying fixes that depend on a scheduled task that has not run yet.
- `runbook-db-target-switching.md` — switching between cloud and local database targets.
- `runbook-droplet-hardening-2026-04-09.md` — ssh/tls/nginx/systemd security posture.
- `runbook-security-audit-2026-04-05.md` — Wapiti audit findings and remediation.
- `runbook-dependency-audit.md` — dependency hygiene policy and current posture.
- `runbook-django-6-upgrade-2026-07-30.md` — the Django 6 upgrade, the Python 3.12 ceiling, and the `npm audit` production gate.

## Queues, Scheduling, Workers

- `runbook-celery-queue-strategy.md` — queue and routing assessment. **Note: describes a four-queue model with `background -c 2`; live is five queues with `-c 3`.**
- `runbook-realm-schedule-striping-2026-08-15.md` — **the striping mechanism itself** (`REALM_INTERVAL_OFFSETS`, `_realm_crontab_for_cycle`, `REALM_CRAWL_CRON_HOURS`) and an index of the engines riding on it.
- `runbook-incident-celery-zombie-worker-2026-04-12.md` — service `active` with 0 consumers; the watchdog recovery.
- `runbook-crawls-queue-depth-alarm-2026-06-12.md` — crawl queue-depth alarming.
- `runbook-interactive-refresh-lane-2026-06-17.md` — moving on-visit refreshes onto the `hydration` lane.
- `runbook-flower-observability-2026-04-02.md` — Flower on the droplet: queue depth, worker liveness, per-task stats.
- `analysis-feed-schedule-optimization-2026-06-08.md` · `analysis-update-process-cost-map-2026-06-06.md` — what writes to Postgres, how often, and what it costs.

## Capture Engines

- `runbook-floor-throughput-tuning-2026-06-13.md` — **canonical current-state entry for the battle-observation floor** (see its "CURRENT STATE" header): dedicated `floor` worker, self-chaining, recency-first, DB as the binding constraint. Start here, then branch to the family below.
- `runbook-bulk-battle-observation-capture-2026-06-06.md` — the bulk account/info + change-gate capture path.
- `runbook-floor-battles-json-refresh-2026-06-14.md` — the `battles_json` rebuild seam. **`FLOOR_REFRESH_BATTLES_JSON_ENABLED=1` in prod, re-enabled 2026-07-08**, pinned in the deploy script.
- `runbook-daily-active-snapshots-2026-06-09.md` — the daily `Snapshot` engine and its delta gate.
- `runbook-recapture-lapsed-players-2026-06-26.md` — the dormant-player sweep the floor structurally cannot see.
- `runbook-recapture-upstream-failure-guard-2026-08-12.md` — the `aborted` axis. **`aborted` is separate from `partial`.**
- `runbook-recapture-soft-limit-budget-2026-08-13.md` — the lever order for `recapture_partial:<realm>`. Read before touching any recapture knob.
- `runbook-hot-players-engagement-queue-2026-06-10.md` — visitor-interest capture queue, **disabled in prod**, revivable.
- `runbook-pause-resume-clan-crawls-2026-06-10.md` — safe pause/resume for a maintenance window; lock and watchdog gotchas.
- `runbook-crawl-upstream-failure-abort-2026-08-11.md` — a WG outage must abort a pass, not complete it. A missing yield snapshot can mean aborted, not in-flight.
- `runbook-enrichment-crawler-2026-04-03.md` — enrichment progress log.
- `runbook-enrichment-pool-maintenance-2026-06-09.md` — keeping the `pending` pool complete. **Note: predates the 2026-08-07 drift/json bucket-family split.**
- `runbook-wg-rate-limiter-token-bucket-2026-06-05.md` — the shared global Redis token bucket in front of the WG application id.
- `../diagrams/be-observation-floor-data-flow.md` · `../diagrams/be-player-enrichment-data-flow.md` — flow diagrams for the above.

## Data Pipeline And Storage

- `runbook-data-lifecycle-architecture-2026-06-21.md` — the consolidated ingest → store → evict reference.
- `runbook-battle-history-rollout-2026-04-28.md` — the `BattleObservation` → `BattleEvent` → `PlayerDailyShipStats` pipeline.
- `runbook-ranked-battle-history-rollout-2026-05-02.md` — ranked-mode capture, season-scoped.
- `runbook-battle-history-rollup-durability-2026-06-06.md` — durability of the derived calendrical layer.
- `runbook-battle-history-archive-prune-2026-06-17.md` — the archive + prune cadence and its env knobs.
- `runbook-battle-history-data-operationalization-2026-06-16.md` — the captured combat fields the UI does not yet read.
- `runbook-db-table-audit-2026-07-19.md` — per-table storage audit. **Never VACUUM FULL a hot table.**
- `runbook-db-disk-remediation-2026-08-05.md` — the disk-growth remediation arc.
- `runbook-db-write-efficiency-eval-2026-07-01.md` — whether bulk/COPY ingest would beat the current write path.
- `runbook-clan-departure-reconciliation-2026-06-15.md` — reconciling clan roster departures.
- `runbook-deleted-account-purge.md` — GDPR purge flow and safety notes.

## Caching And Request Path

- `spec-cache-first-lazy-refresh-policy-2026-03-19.md` — the cache-first / lazy-refresh contract. **No `/api/fetch/*` endpoint blocks the request thread.**
- `runbook-cache-audit.md` — cache families, keys, and TTL expectations.
- `runbook-player-fetch-orchestration-2026-06-21.md` — **canonical client request layer**: `sharedJsonFetch`, the priority queue, backoff, the degradation monitor, whole-page cancellation.
- `runbook-recently-viewed-player-warming.md` — recent-visit warming strategy and knobs.
- `runbook-player-refresh-latency-2026-06-10.md` — the latency tiers and what shipped.
- `runbook-player-refresh-pill-clobber-2026-06-21.md` — the "hung on Updating…" pill report and its cause.
- `runbook-live-update-cooldown-2026-05-27.md` — the on-visit live-update affordance and its cooldown.
- `runbook-hidden-profile-chart-warming-2026-07-11.md` — hidden-profile chart warming (`is None` vs `[]`).
- `runbook-cross-realm-player-fallback-2026-07-20.md` — resolving a player found on another realm.

## Ship Standings And Leaderboards

- `runbook-ship-leaderboard-architecture-2026-06-18.md` — **start here**: the whole standings pipeline and its env knobs.
- `runbook-leaderboard-updates.md` — freshness and snapshot cadence ("is the leaderboard stale?").
- `runbook-ship-leaderboard-window-30d-2026-06-29.md` — the single reference for the window's live value and how to advance it.
- `runbook-shipleaderboard-warm-before-evict-2026-06-18.md` — durable `:published` fallback; **a cold fresh key is not an error**.
- `runbook-top-ships-warm-soft-limit-2026-08-12.md` — the warm orchestrator that never completed, and the dispatcher rewrite.
- `runbook-ship-list-wr-percentile-2026-06-23.md` — the `wr_pct` buckets and their nightly pre-warm.
- `runbook-ship-list-rollup-source-2026-08-14.md` — the all-view moved off `BattleEvent` onto the daily rollup; the two coupled window invariants.
- `runbook-ship-top-player-badges-2026-06-05.md` — `/ship` standings and profile badges.
- `runbook-ship-badges-rolling-2026-06-14.md` — the rolling nightly recompute that replaced fixed seasons.
- `runbook-ship-badge-current-generation-2026-07-08.md` — badges anchor on the realm's current generation, so a displaced player drops immediately.
- `runbook-shiptool-integration-2026-06-22.md` — the shiptool.st deep link; the code is derived, not scraped.
- `runbook-top-random-battle-players-2026-06-16.md` — point-in-time top-players snapshot.
- `runbook-ship-leaderboard-submarine-easter-egg-2026-06-11.md` — the submarine easter egg.

## Frontend

- `runbook-player-page-charts-and-roster-2026-08-15.md` — the tier figure and its sqrt(n) damping, the Ships-tab drill-down nonce, `ClanActivityRoster`, `ActivityIcon`, classification icons.
- `runbook-frontend-final-shape-cleanup-2026-07-15.md` — the 850px single-column doctrine and the final-shape audit. **Authoritative on layout.**
- `runbook-icon-analysis.md` — the classification-icon inventory.
- `runbook-clan-chart-activity-filter-2026-06-18.md` — the clan chart's activity filter and the three-phase taxonomy.
- `runbook-battle-history-treemaps-2026-07-13.md` — the Battle History mini-treemaps and the damage-map baseline.
- `runbook-landing-treemap-filter-correlation-2026-07-01.md` — landing treemap correlated with the leaderboard filters.
- `runbook-mobile-player-detail-charts.md` · `runbook-mobile-routing-bugs.md` — mobile rendering and route-loading regressions.
- `runbook-search-toggle.md` — the header player/clan search toggle.
- `runbook-client-test-hardening.md` — frontend regression and test-harness guidance.
- `runbook-seo.md` — metadata, sitemap, structured data, OG cards.

## Features And Player Classification

- `runbook-cb-icon-current-season-2026-07-15.md` — the CB shield's current-season semantics and the tab gate.
- `runbook-enriched-data-features-2026-04-12.md` — enrichment-backed surfaces (distributions, correlations, explorer summaries).
- `runbook-player-achievements-data-lane.md` — the achievements lane.
- `runbook-streamer-submission-feature-2026-04-07.md` · `runbook-streamer-twitch-icon-2026-04-07.md` — streamer submission queue and the Twitch badge.
- `runbook-landing-featured-boards-decommission-2026-06-22.md` — why the Best/Popular boards were removed and what went with them.
- `runbook-multi-realm-hardening.md` — multi-realm cleanup and remaining hardening notes.

## Analytics, Email, Observability

- `runbook-umami-event-reference-2026-06-18.md` — **the durable Umami reference**: pipeline plus every custom event. **Note: still lists three removed landing events and omits `locale-active`.**
- `runbook-umami-analytics-coverage-2026-06-17.md` — the coverage pass that closed the core-nav blind spots.
- `runbook-umami-hardening-2026-06-02.md` — hosting and attack-surface analysis.
- `runbook-audience-growth-instrumentation-2026-07-29.md` — arrivals, not retention, are the constraint; the durable visitor id.
- `runbook-locale-adoption-measurement-2026-08-10.md` — measuring sustained non-English usage; count `visit_id`, never `session_id`.
- `runbook-ops-email-exception-only-2026-08-09.md` — the exception-only ops digest and its deterministic verdict.
- `runbook-weekly-traffic-email-2026-08-09.md` — the Monday traffic digest, the Umami session-row trap, and why weekly visitors are not the sum of daily visitors.
- `runbook-llm-token-optimization-2026-10-03.md` — the only two Anthropic call sites (both emails), their estimated cost (about a dollar a month), the `[llm]` usage journal line, the heartbeat-only send that skips the model, and why prompt caching and batch do not apply.
- `runbook-droplet-outbound-mail-2026-08-06.md` — the outbound SMTP path. **Never delete the sysop routing rule.**
- `runbook-health-sweep-remediation-2026-08-06.md` — the two-week health sweep and its findings.

## Contracts, Quality, Maintenance

- `runbook-api-surface.md` — public API surface and smoke coverage.
- `runbook-contract-strategy-implementation.md` — payload and contract maintenance expectations.
- `runbook-codebase-improvement.md` — evergreen maintenance heuristics.
- `spec-production-data-refresh-strategy.md` — data refresh and maintenance intent (partially implemented).

## Specs And Open Design Docs

Active only while they still shape implementation:

- `spec-clan-battle-seasons-chart.md` · `spec-cb-seasons-chart-redesign-2026-04-05.md` — the CB seasons chart and its layered redesign.
- `spec-clan-battles-by-tier.md` — clan battles broken out by tier.
- `spec-player-route-follow-up-improvements-2026-03-19.md` — player-route follow-ups.
