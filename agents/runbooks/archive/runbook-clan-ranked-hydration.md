# Runbook: Clan Ranked Hydration

_Last updated: 2026-03-15_

## Status

Implemented on 2026-03-15.

Validation status:

1. Focused ranked-hydration backend tests passed.
2. Clean local `client` production build passed.
3. Site endpoint smoke task passed.
4. A broader Django suite covering `test_views`, `test_data`, and `test_crawl_scheduler` exposed unrelated pre-existing failures outside the ranked hydration lane.

## Purpose

Implement clan-driven ranked hydration so both clan detail and the player-detail embedded clan roster can gain ranked stars after first paint, while explicitly controlling:

- client performance,
- server performance,
- memory growth,
- request-path bottlenecks,
- local API load,
- WG upstream API load.

## Source Artifact

- [agents/work-items/clan-ranked-hydration-spec.md](agents/work-items/clan-ranked-hydration-spec.md)

## Problem Statement

Clan roster stars currently reflect whatever ranked cache already exists on each player. That causes under-reporting on both the full clan page and the player-detail clan list until individual players are opened elsewhere.

The intended fix is to let clan roster reads queue ranked refresh in the background, then let the shared roster component poll briefly and re-render when fresh ranked state arrives.

## Agent Responsibilities

### Project Manager

1. Keep scope constrained to ranked-star hydration on clan rosters.
2. Prevent unrelated ranked-detail or player-detail feature creep.
3. Hold the line on non-blocking page reads.

### Architect

1. Ensure the request path remains read-mostly and bounded.
2. Reuse existing task-lock, cache, and throttle patterns.
3. Review whether stale gating and polling budgets are conservative enough.

### Engineer-Web-Dev

1. Implement a dedicated ranked refresh task lane.
2. Keep queue selection linear in roster size.
3. Keep the client to one shared poll loop per mounted clan list.

### QA

1. Validate correctness of star hydration and stop conditions.
2. Validate the network/request shape for bounded polling.
3. Validate that repeated page visits do not imply runaway local or WG API load.

### Safety / Reliability

1. Confirm stale local data remains renderable during failures.
2. Confirm the feature does not leak backend internals or queue state beyond what the client needs.
3. Confirm failure behavior degrades to stale roster data rather than page failure.

## Preconditions

1. Current clan roster API still returns immediately from local data without forcing ranked refresh completion.
2. Celery, cache, and broker locking remain available through the existing task infrastructure.
3. Ranked cache freshness semantics are defined well enough to distinguish `fresh`, `stale`, and `missing` rows.
4. The shared `ClanMembers.tsx` component remains the only clan-roster fetch owner for both clan detail and player detail.

## Proposed Delivery Shape

### Backend

1. Add a dedicated `update_ranked_data_task(player_id)` Celery wrapper around `warships.data.update_ranked_data`.
2. Add a helper to inspect a clan roster and queue ranked refresh only for stale-or-missing members.
3. Extend the clan-members response with lightweight ranked-hydration metadata:
   - `ranked_hydration_pending`
   - `ranked_updated_at`
4. Keep `/api/fetch/clan_members/<clan_id>/` as the single read path.

### Frontend

1. Update `ClanMembers.tsx` to read the new metadata.
2. Poll the clan-members endpoint only while at least one row is pending.
3. Reuse the existing cadence precedent from clan hydration in `PlayerSearch.tsx`.
4. Abort in-flight requests on unmount or clan change.

## Performance And Load Guardrails

### Client Performance

1. No per-member ranked fetches from the browser.
2. At most one active polling loop per mounted clan list.
3. Polling state stays local to `ClanMembers.tsx`; do not introduce extra parent-level coordination state.
4. Do not mount additional ranked detail charts or hidden fetches as part of the hydration flow.

### Server Performance

1. The clan-members endpoint must return current roster data immediately and must not wait for ranked task completion.
2. Queue selection should be a single pass over roster members.
3. Repeated poll reads should mostly become no-ops, fresh skips, or lock hits.
4. A per-roster in-flight budget should cap how many new ranked refreshes can be admitted at once.

### Memory

1. Do not cache duplicate ranked-history payloads solely for hydration state.
2. Keep per-request helper state to compact player-id lookups and pending flags.
3. Avoid attaching large debug metadata to response rows.

### Bottlenecks

Primary expected bottlenecks:

1. local DB roster reads,
2. Celery task fan-out,
3. WG ranked upstream calls.

Mitigation order:

1. conservative stale gating,
2. task locks,
3. bounded polling,
4. optional future per-clan queue budget if profiling proves necessary.

### API Load

1. Local API load should remain one clan-members request per poll cycle.
2. WG API load should remain proportional to stale-or-missing ranked members only.
3. Lock-hit and fresh-cache skip outcomes should be considered expected load shedding, not failures.
4. Lightweight response headers may be used to expose queued, deferred, pending, and budget counts for validation.

## Suggested Implementation Sequence

### Phase 1: Backend Queue Lane

1. Add `update_ranked_data_task(player_id)` in `server/warships/tasks.py` using `_run_locked_task(...)`.
2. Add queue-selection helper in `server/warships/data.py` or the nearest orchestration layer already responsible for ranked freshness decisions.
3. Extend serializer support in `server/warships/serializers.py` for `ranked_hydration_pending` and `ranked_updated_at`.
4. Update `server/warships/views.py` clan-members read path to attach queueing and return the additive metadata.
5. Add a per-roster in-flight admission budget so repeated polls drain large stale rosters in bounded batches.

### Phase 2: Shared Client Polling

1. Update `client/app/components/ClanMembers.tsx` to parse the new row fields.
2. Add one bounded polling loop with the current cadence baseline:
   - interval: `2500ms`
   - max attempts: `6`
3. Use `AbortController` for request cleanup.
4. Ensure clan change resets the loop cleanly without keeping stale timers or late responses.

### Phase 3: Targeted Validation

1. Backend tests for queue gating, metadata shape, and lock behavior.
2. Frontend/manual verification for both clan detail and player-detail clan list.
3. Network verification that request shape stays bounded.
4. Local/WG load sanity check through logs or counters if instrumentation already exists.
5. Header verification that queued, deferred, pending, and budget values are internally consistent.

## Implementation Checklist

- [x] Dedicated ranked refresh task added
- [x] Task uses per-player lock
- [x] Clan roster queue helper added
- [x] Queue helper gates on stale-or-missing ranked cache only
- [x] Clan-members response extended additively
- [x] Shared `ClanMembers.tsx` polling added
- [x] Poll loop bounded and abortable
- [x] No per-member client ranked fetches introduced
- [x] No ranked-history payload embedded into roster response
- [x] Manual and automated validation plan updated

## Executed Validation

### Backend Tests Executed

1. Focused validation passed for the ranked hydration lane:
   - `warships.tests.test_views.ClanMembersEndpointTests`
   - `warships.tests.test_data.RankedDataRefreshTests`
2. Broader validation was then run across:
   - `warships.tests.test_views`
   - `warships.tests.test_data`
   - `warships.tests.test_crawl_scheduler`
3. That broader suite found unrelated failures outside the ranked hydration change:
   - `warships.tests.test_views.ApiContractTests.test_landing_activity_attrition_returns_monthly_cohorts`
   - `warships.tests.test_views.PlayerViewSetTests.test_clan_members_lookup_updates_clan_last_lookup_timestamp`
   - `warships.tests.test_data.PlayerExplorerSummaryTests.test_refresh_player_explorer_summary_persists_denormalized_metrics`

### Frontend Validation Executed

1. A clean `next build` was run after deleting `client/.next` and passed.
2. The passing build confirmed the shared `ClanMembers.tsx` changes did not break production compilation.
3. The earlier `/trace` build error reproduced only with stale Next build artifacts and cleared after a clean cache rebuild.

### Endpoint Smoke Validation Executed

1. `Smoke Test Site Endpoints` passed.
2. The smoke suite included:
   - clan detail,
   - clan data,
   - clan members,
   - ranked endpoints,
   - player detail and summary,
   - players explorer,
   - distribution and correlation APIs,
   - site stats.

## Remaining Validation Gaps

1. Manual browser verification of bounded polling behavior on both clan detail and player detail still remains useful, but it is no longer a blocker for documenting implementation status.
2. The unrelated broader-suite failures should be resolved separately before claiming the entire backend suite is green.

## Commands Used

1. `python manage.py test warships.tests.test_views --keepdb`
2. `python manage.py test warships.tests.test_data --keepdb`
3. `python manage.py test warships.tests.test_crawl_scheduler --keepdb`
4. `rm -rf client/.next && npm run build`
5. `docker compose exec -T server python scripts/smoke_test_site_endpoints.py`

## Rollback Plan

If the feature causes load or correctness regressions, roll back in this order:

1. disable client polling while keeping additive response fields,
2. disable server-side queue trigger while leaving response serialization intact,
3. remove the additive metadata only if necessary.

This sequence preserves the least risky parts of the implementation while shrinking operational load quickly.

## Exit Criteria

1. Clan detail and player-detail clan rosters both gain ranked stars after background hydration.
2. Request-path latency remains bounded because ranked refresh does not run inline.
3. Polling is bounded, abortable, and single-endpoint.
4. Local queue pressure and WG upstream load are demonstrably gated by stale-member selection and task locks.
5. QA signs off on correctness, completeness, and load behavior before execution.
6. Large stale rosters are drained in bounded batches rather than a single unbounded enqueue wave.
