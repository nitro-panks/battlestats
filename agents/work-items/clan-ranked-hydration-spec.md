# Clan-Driven Ranked Hydration Specification

**Author:** Project Manager Agent
**Date:** 2026-03-15
**Status:** Implemented on 2026-03-15; focused validation and endpoint smoke coverage passed, with unrelated broader-suite failures noted outside this lane
**Scope:** Clan detail page and shared clan-member list behavior in the Next.js client, plus targeted ranked refresh orchestration in the Django backend
**Primary Surfaces:** `ClanDetail`, shared `ClanMembers`, `/api/fetch/clan_members/<clan_id>/`, ranked refresh task lane

## 1. Objective

**Core question this change answers:** _"When a user opens a clan view, can Battlestats actively fill in missing ranked markers for the whole roster instead of waiting for individual player-detail visits?"_

Today, ranked stars in clan member lists depend on whatever `ranked_json` is already cached on each `Player`. That means the clan page and the player-detail embedded clan list can under-report ranked players until someone opens those players one by one.

This change makes clan-driven navigation a ranked-data hydration trigger:

1. When a clan page is opened, Battlestats should queue ranked refreshes for the roster in the background.
2. The clan member list should rehydrate and surface new stars as ranked data lands.
3. The same behavior should apply to the shared clan list shown on player detail pages.
4. The UI should stay responsive and avoid synchronous N+1 ranked API fetches in the request path.
5. The implementation should explicitly control client work, server work, memory growth, bottlenecks, and local plus WG API load.

## 2. PM Recommendation

Implement this as a **server-queued, client-polled hydration flow attached to the existing clan-members endpoint and shared `ClanMembers` component**.

Reasoning:

1. Both clan detail and player detail already render the same `ClanMembers` component, so the hydration behavior should live there once and apply to both surfaces.
2. The existing `/api/fetch/clan_members/<clan_id>/` endpoint already returns the star-driving fields: `is_ranked_player` and `highest_ranked_league`.
3. The current ranked endpoint is synchronous and can hit WG directly; calling it once per member from the browser would create avoidable latency and rate-limit pressure.
4. The backend already has a lock pattern for per-resource Celery tasks, so ranked hydration should follow that pattern instead of inventing a second concurrency model.
5. Rehydrating the list via polling is consistent with the current player search flow, which already polls for clan hydration when player clan info is still being populated.

## 2.1 Cross-Agent Performance Direction

This feature must be reviewed not just as a UX improvement, but as a controlled background-hydration system.

### Architect Focus

1. Keep the request path read-mostly and bounded.
2. Prefer additive metadata and queue-based work over synchronous ranked refresh in page reads.
3. Reuse existing cache-lock and throttling patterns rather than creating a parallel coordination system.

### Engineer Focus

1. Minimize extra client renders and repeated poll requests.
2. Keep queue selection set-based and cheap on the server.
3. Avoid holding large temporary Python structures longer than necessary during clan hydration.

### QA Focus

1. Validate not only correctness of stars and polling, but also absence of runaway requests.
2. Confirm the poll window closes and that repeated page visits do not multiply task load.
3. Verify the flow against both populated and empty-ranked fixtures.

### Safety / Reliability Focus

1. WG-facing load must stay bounded under repeated public requests.
2. Local broker/cache/database pressure must remain proportional to actual stale roster members, not page refresh count.
3. Failure states should degrade to stale-but-usable roster rendering rather than blocking the page.

## 3. User Story

As a user viewing a clan roster, I want Battlestats to quietly refresh ranked data for that roster so the star markers become accurate without forcing me to open every player individually.

As a user already on a player detail page, I want the embedded clan member list to keep doing the same background ranked hydration work, so I can still discover ranked clanmates from that surface.

## 4. Product Scope

### In Scope

1. Queue ranked refresh work for clan roster members when the clan member list is requested.
2. Rehydrate the clan member list so star markers can appear after the initial paint.
3. Apply the same shared behavior to both full clan detail and player-detail clan list views.
4. Expose enough backend state for the client to know whether it should keep polling.
5. Prevent duplicate ranked refresh storms with freshness checks and task locks.
6. Bound client polling, Celery fan-out, memory overhead, and upstream API calls.

### Out of Scope

1. Rendering per-member ranked season detail inside the clan roster.
2. Changing the player-detail ranked sections themselves.
3. Reworking the global incremental ranked refresh scheduler.
4. Forcing a synchronous ranked refresh before the clan page can render.
5. Adding a new visible progress UI for every member row.

## 5. Current State

### Backend

1. `/api/fetch/clan_members/<clan_id>/` returns roster rows with `is_ranked_player` and `highest_ranked_league` derived from cached `Player.ranked_json`.
2. `fetch_ranked_data(player_id)` is synchronous and refreshes `ranked_json` if stale or missing.
3. `update_ranked_data(player_id)` exists in `warships.data`, but there is not yet a dedicated Celery task wrapper for ranked refreshes.
4. The task system already has per-resource locking via `_run_locked_task(...)`.

### Frontend

1. `ClanMembers.tsx` fetches the roster once and renders stars from `is_ranked_player` and `highest_ranked_league`.
2. `ClanMembers.tsx` is used by both `ClanDetail.tsx` and `PlayerDetail.tsx`.
3. There is already a polling precedent in `PlayerSearch.tsx` for clan hydration, but nothing similar yet for ranked hydration in member lists.

## 6. Proposed Behavior

### User-Facing Behavior

1. Opening a clan page should render the roster immediately using currently cached data.
2. That same visit should queue ranked refresh work for roster members whose ranked cache is missing or stale.
3. The roster should then poll for refreshed member rows for a short window.
4. If a member gains ranked history after hydration, their star should appear without a full page reload.
5. The identical behavior should occur inside the player-detail clan roster because it uses the same shared member-list component.

### Freshness Model

1. Clan-driven ranked hydration should not enqueue work for members whose ranked cache is still fresh.
2. Recommended freshness threshold for queue eligibility: align with the durable ranked freshness lane, using a conservative threshold such as `24h` rather than `1h`.
3. The direct ranked endpoint can keep its current request-time freshness behavior for explicit player detail needs.
4. Clan-page visits should act as a discovery and queueing signal, not as a forced immediate re-fetch of already-fresh ranked rows.

### Non-Functional Guardrails

1. Client polling must remain single-endpoint and bounded; do not add per-member fetches.
2. Clan-members reads should enqueue only stale-or-missing ranked members, not the whole roster unconditionally.
3. Queue selection should avoid repeated DB work where possible by using existing related data already loaded for member serialization.
4. Pending-state metadata should remain compact and per-row; do not embed ranked season payloads in clan roster responses.
5. WG API pressure must scale with stale ranked members, not with page impressions.

## 7. Backend Specification

### 7.1 Ranked Refresh Task

Add a dedicated Celery task wrapper for ranked refreshes.

Recommended task:

`update_ranked_data_task(player_id)`

Behavior:

1. Use the existing `_run_locked_task(...)` helper with a resource key of `player_id`.
2. Call `warships.data.update_ranked_data(player_id)` inside the lock.
3. Return a small status payload so logs and manual runs can distinguish `completed` vs `skipped`.

### 7.2 Clan-Triggered Queue Helper

Add a helper that decides which members should be queued for ranked hydration.

Recommended helper:

`queue_clan_ranked_hydration(clan)` or `queue_clan_ranked_hydration(clan_id)`

Behavior:

1. Load the current clan roster from local `Player` rows.
2. Select players whose ranked data is missing or stale according to the queue freshness threshold.
3. Enqueue `update_ranked_data_task.delay(player.player_id)` for each eligible member.
4. Do not block on task completion.
5. Return a lookup structure keyed by `player_id` or `name` so the response can flag which rows are pending hydration.
6. Keep the helper idempotent per request so repeated clan-member polls mostly become lock hits or no-ops rather than new work.

### 7.3 Clan Members Response Additions

Extend the clan member response with ranked-hydration state.

Recommended new fields per member row:

1. `ranked_hydration_pending: boolean`
2. `ranked_updated_at: string | null`

Recommended semantics:

1. `ranked_hydration_pending = true` when the backend queued a ranked refresh for that member during this request or when the member remains stale and eligible.
2. `ranked_updated_at` exposes the current cache timestamp so the client can stop polling once fresh data has landed.
3. `is_ranked_player` and `highest_ranked_league` remain the actual rendering source for the star.
4. The response should not include heavyweight debug or queue metadata beyond what the client needs to poll safely.

### 7.4 Endpoint Trigger Point

Attach queueing to the existing clan-members endpoint rather than adding a second endpoint.

Recommended flow inside `/api/fetch/clan_members/<clan_id>/`:

1. Load clan and roster as today.
2. Queue ranked hydration for eligible members.
3. Serialize the roster with current star state plus `ranked_hydration_pending` metadata.
4. Return immediately.

This preserves a single read path for the client while keeping hydration asynchronous.

## 8. Frontend Specification

### 8.1 Shared Component Ownership

Put the polling behavior in `client/app/components/ClanMembers.tsx`, not in `ClanDetail.tsx` or `PlayerDetail.tsx`.

Reasoning:

1. The same roster behavior is required on both surfaces.
2. The shared component already owns the clan-members fetch.
3. This avoids duplicating timing and retry behavior in two parent screens.

### 8.2 Polling Behavior

Recommended client behavior:

1. Fetch the member list normally on mount or when `clanId` changes.
2. After each response, check whether any row has `ranked_hydration_pending = true`.
3. If none are pending, stop.
4. If some are pending, poll the same endpoint on a bounded interval.
5. Stop polling when either:
   - no rows are pending,
   - the poll limit is reached, or
   - the component unmounts or `clanId` changes.

Recommended initial limits:

1. Interval: `2500ms`
2. Max attempts: `6`

This matches the current clan-hydration cadence already used in `PlayerSearch.tsx` and keeps the UX consistent.

### 8.3 Client Performance Guidance

1. Keep polling state local to `ClanMembers.tsx`; do not push ephemeral poll counters into parent screens.
2. Cancel in-flight requests on unmount or clan change via `AbortController`.
3. Avoid resetting member state if a late response belongs to an abandoned clan id.
4. Prefer replacing the single members array over layering extra per-member React state maps unless profiling shows a need.
5. Do not mount additional ranked-detail charts or hidden fetches as part of the roster hydration flow.

### 8.4 Rendering Rules

1. The star continues to render only from `is_ranked_player` and `highest_ranked_league`.
2. Pending hydration should not show speculative stars.
3. The list may optionally keep the existing `Syncing clan members...` copy for the initial fetch only; no per-row spinner is required for MVP.
4. A newly hydrated ranked player should simply gain the star on the next successful poll response.

## 9. Data Contract

### Existing Endpoint

`GET /api/fetch/clan_members/<clan_id>/`

### Proposed Response Shape

```json
[
  {
    "name": "ExamplePlayer",
    "is_hidden": false,
    "pvp_ratio": 54.8,
    "days_since_last_battle": 3,
    "is_leader": false,
    "is_pve_player": false,
    "is_ranked_player": true,
    "highest_ranked_league": "Silver",
    "activity_bucket": "active_7d",
    "ranked_hydration_pending": false,
    "ranked_updated_at": "2026-03-15T14:18:00+00:00"
  },
  {
    "name": "FreshLookupPending",
    "is_hidden": false,
    "pvp_ratio": 51.3,
    "days_since_last_battle": 9,
    "is_leader": false,
    "is_pve_player": false,
    "is_ranked_player": false,
    "highest_ranked_league": null,
    "activity_bucket": "active_30d",
    "ranked_hydration_pending": true,
    "ranked_updated_at": null
  }
]
```

### Contract Rules

1. `ranked_hydration_pending` is advisory polling metadata, not the source of truth for the star.
2. `ranked_updated_at` may be `null` if ranked data has never been hydrated.
3. The endpoint must continue to tolerate empty clans and missing clans by returning `[]`.
4. New fields should be additive and backward compatible for existing consumers.

## 9.1 Performance, Memory, and Load Requirements

### Client Performance

1. Clan-page hydration must not materially expand the landing route bundle or introduce new always-on player-detail charts.
2. The shared roster should perform at most one active poll loop per mounted clan list.
3. Polling must stop cleanly when the component unmounts, the clan changes, or the pending set clears.

### Server Performance

1. The clan-members endpoint must remain fast enough to serve cached roster data without waiting on ranked refresh completion.
2. Queue selection should stay linear in clan roster size and avoid nested per-member fetch patterns.
3. Repeated poll requests should mostly observe existing lock state or fresh timestamps rather than re-creating real work.
4. Clan-triggered ranked refresh should cap simultaneous in-flight admissions per roster so the first poll cannot fan out unbounded Celery or WG work.

### Memory

1. No new long-lived cache payload should duplicate full ranked season history solely for clan hydration status.
2. Per-request temporary structures should be limited to compact member-id lookups and pending flags.
3. Client memory overhead should remain bounded to the rendered member list and one poll loop.

### Bottlenecks

1. The likely hot spots are Celery fan-out, repeated DB roster reads, and WG upstream ranked calls.
2. The implementation should treat the clan-members endpoint as orchestration only, not as the ranked-data execution bottleneck.
3. If future profiling shows queue bursts are a problem, the first mitigation should be tighter stale gating or per-clan queue budgets, not client-side complexity.

### API Load

1. Local API load must remain one roster endpoint call per poll cycle, not one request per member.
2. WG API load must be bounded by stale ranked members actually admitted to the queue.
3. Lock hits and fresh-cache skips are expected outcomes and should be treated as healthy load-shedding behavior.
4. The roster endpoint may expose lightweight response headers for queued, deferred, pending, and max-in-flight counts to support QA and smoke validation without bloating the JSON payload.

## 10. Acceptance Criteria

### Product and UX

1. Opening a clan page queues ranked hydration work for eligible roster members.
2. The clan page can gain star markers after initial load without a manual refresh.
3. The player-detail embedded clan list behaves the same way.
4. Clan-page load does not block on one ranked fetch per player.
5. The implementation does not create per-member browser fetches or unbounded poll loops.

### Backend

1. There is a dedicated ranked refresh Celery task with per-player locking.
2. `/api/fetch/clan_members/<clan_id>/` returns `ranked_hydration_pending` and `ranked_updated_at` per row.
3. The endpoint only queues ranked refreshes for stale or missing ranked rows.
4. Repeated poll requests do not create duplicate task storms for the same player.
5. Clan-members responses remain lightweight and do not inline ranked history payloads.
6. Clan-triggered admissions are capped by a per-roster in-flight budget.

### Frontend

1. `ClanMembers.tsx` polls only while ranked hydration is pending.
2. Polling stops when hydration settles or the max retry window is reached.
3. Newly hydrated ranked players gain the star in both clan detail and player-detail clan lists.
4. No new hydration warnings or runtime errors are introduced.
5. In-flight requests are cancelled on unmount or clan change.

### Performance and Load

1. A single clan-list mount creates at most one bounded polling loop.
2. The server request path returns cached roster data immediately even while ranked work continues in the background.
3. WG ranked calls are limited to stale-or-missing members actually admitted through queue gating.
4. The feature does not materially regress client bundle size or trigger additional below-the-fold chart fetches.
5. Large rosters do not enqueue an unbounded first-wave burst because in-flight admissions are budgeted per roster.

## 11. Test Plan

### Backend Tests

1. Verify clan-members response includes the new ranked hydration fields.
2. Verify stale or null `ranked_updated_at` members are queued.
3. Verify fresh ranked rows are not queued.
4. Verify the task lock prevents duplicate concurrent ranked refreshes for the same player.

### Frontend Tests / Verification

1. Open a clan with at least one member lacking ranked cache and confirm the roster refetches within the poll window.
2. Confirm a star appears after hydration for a member who gains ranked data.
3. Confirm the same behavior occurs on the player-detail embedded clan list.
4. Confirm polling stops once no members remain pending.
5. Confirm the UI remains usable if some members never produce ranked data.
6. Confirm the browser network panel shows one roster request per poll cycle, not one ranked request per member.
7. Confirm repeated clan-page refreshes during an active hydration window do not create an obvious explosion in local task dispatch or WG upstream calls.

### Suggested Fixtures

1. Use the verified populated clan fixture `Naumachia` (`clan_id=1000055908`) for end-to-end manual testing.
2. Include at least one known ranked player fixture such as `Punkhunter25` or `Shinn000` in targeted backend tests where practical.
3. Include at least one no-ranked fixture such as `Kevik70` or `DOOKJA` to verify that completed hydration can still legitimately end with no star.

## 12. Risks and Mitigations

| Risk                                                                                    | Severity | Mitigation                                                                                                           |
| --------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------- |
| Clan-page visits enqueue too many ranked refreshes                                      | High     | Gate queueing on stale-or-missing ranked data and use per-player task locks                                          |
| Polling causes repeated noisy requests                                                  | Medium   | Bound polling attempts and only continue while at least one member is pending                                        |
| Browser-driven per-member ranked calls recreate N+1 latency                             | High     | Keep queueing server-side and poll the single clan-members endpoint                                                  |
| Freshly hydrated no-ranked players look indistinguishable from not-yet-hydrated players | Medium   | Expose `ranked_hydration_pending` and `ranked_updated_at` so the client can distinguish pending from completed-empty |
| The new metadata breaks existing consumers                                              | Low      | Make the response additive and keep existing fields unchanged                                                        |
| Repeated clan views create unnecessary local broker, cache, or DB churn                 | Medium   | Keep stale gating conservative and rely on lock-hit/no-op behavior on repeated polls                                 |
| WG load rises sharply on active clan browsing                                           | High     | Restrict hydration admission to stale-or-missing members and preserve a single shared upstream client with retries   |

## 13. Implementation Plan

### Phase 1: Backend Queue Lane

1. Add `update_ranked_data_task(player_id)` to `warships.tasks`.
2. Add a helper to select and queue eligible clan members for ranked hydration.
3. Extend `ClanMemberSerializer` and `/api/fetch/clan_members/<clan_id>/` with pending-state fields.
4. Verify the helper uses bounded temporary data structures and conservative stale gating.

### Phase 2: Shared Client Hydration Loop

1. Update `ClanMembers.tsx` to read the new pending-state fields.
2. Add bounded polling with the existing clan-hydration cadence.
3. Ensure cleanup on unmount and `clanId` change.
4. Verify there is only one poll loop and that no per-member ranked fetches were introduced.

### Phase 3: Verification

1. Verify the behavior on full clan detail.
2. Verify the behavior on player-detail embedded clan list.
3. Verify stars appear after hydration without blocking initial render.
4. Verify client performance, server request shape, memory behavior, and local/WG API load against the guardrails above.

## 14. Definition of Done

1. Visiting a clan surface queues ranked hydration for eligible members.
2. The shared clan member list rehydrates and shows new stars when ranked data lands.
3. The same behavior works on both clan detail and player-detail clan lists.
4. Backend and frontend validation confirm no synchronous roster-wide ranked fetch regression was introduced.
5. Validation confirms bounded client polling, bounded queue fan-out, and no obvious local or WG API load regression.
