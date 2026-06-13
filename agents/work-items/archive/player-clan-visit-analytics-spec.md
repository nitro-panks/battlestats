# Player And Clan Visit Analytics Spec

_Drafted: 2026-03-16_

## Objective

Enable battlestats to answer these product questions reliably:

1. Which player detail pages get the most visits?
2. Which clan detail pages get the most visits?
3. Over what time window are those visits happening: today, 7 days, 30 days, custom range?
4. How many of those visits are total page views versus unique visitors?

This spec proposes a first-party analytics layer that works inside the current Django + Next.js stack and leaves a clean path for future Google Analytics integration.

## Current State

Today the app has partial lookup signals but no durable visit history:

1. Player page loads hit `/api/player/<name>/`, and `PlayerViewSet.get_object()` updates `Player.last_lookup`.
2. Clan-related fetches such as `/api/clan/<id>/`, `/api/fetch/clan_members/<id>/`, `/api/fetch/clan_data/<id>:<filter>`, and `/api/fetch/clan_battle_seasons/<id>/` update `Clan.last_lookup`.
3. Those timestamps are useful for freshness and landing-page invalidation, but they only preserve the most recent lookup.
4. There is no event table, no aggregate table, no reporting endpoint, and no browser analytics script in the app shell.

Consequence: battlestats cannot answer "most visited players" or "most visited clans" without adding explicit visit tracking.

## Product Recommendation

Implement this in two layers:

1. First-party entity visit tracking as the canonical product data source.
2. Optional GA4 emission using the same event semantics for cross-checking, acquisition analysis, and future external reporting.

Reasoning:

1. The product question is entity-specific. The canonical store must preserve `player_id` and `clan_id` exactly.
2. Google Analytics is useful, but it is not a dependable primary source for operational rankings because of ad blockers, consent choices, sampling, and export dependencies.
3. First-party storage keeps the product answer available even when GA is misconfigured or absent.
4. A shared event contract lets the team add GA4 later without redefining what a visit means.

## Core Definitions

### Canonical Entity Visit

A canonical entity visit is counted when all of the following are true:

1. The user is on a routed player or clan detail page.
2. The route has resolved to a valid entity payload.
3. The detail view is visible in the browser, not just prefetched.
4. The client emits one analytics event for that entity view.

### Counted Surfaces

Included:

1. `/player/<playerName>` after `PlayerRouteView` successfully loads.
2. `/clan/<clanSlug>` after `ClanRouteView` successfully loads.

Excluded:

1. Landing lists, search suggestions, explorer rows, and hover states.
2. Background data fetches inside detail pages such as ranked, randoms, clan members, or clan battles.
3. Route prefetches, crawler traffic, smoke tests, and internal batch jobs.
4. Direct API hits to detail endpoints that are not paired with a real page view.

### Metrics

The system should support at least these metrics:

1. `views`: total counted entity visits.
2. `unique_visitors`: distinct visitor keys within the selected date range.
3. `unique_sessions`: distinct session keys within the selected date range.

Product default for the question "most visits" should be `views`, with `unique_visitors` shown beside it so rankings are interpretable.

## Scope

### In Scope

1. Canonical visit event definition for player and clan detail routes.
2. First-party storage for raw events and daily aggregates.
3. A reporting API that returns top players and clans by date range.
4. Lightweight browser instrumentation in the current Next.js route views.
5. Bot suppression and dedupe rules.
6. A future-compatible GA4 event shape.

### Out Of Scope

1. Full marketing attribution or campaign analytics.
2. Cohort retention, funnels, or cross-page user journey analytics.
3. Real-time dashboards with sub-second freshness.
4. Personal user accounts or user-level behavior profiles.
5. Public-facing rankings UI on the main site. This spec focuses on answerability first.

## Event Semantics

### Event Name

Canonical event name: `entity_detail_view`

### Required Event Fields

Each event should include:

1. `event_uuid`: client-generated UUID for idempotency.
2. `occurred_at`: client event timestamp.
3. `entity_type`: `player` or `clan`.
4. `entity_id`: numeric `player_id` or `clan_id`.
5. `entity_slug`: route segment used by the browser.
6. `entity_name`: best-known display name at event time.
7. `route_path`: browser pathname.
8. `referrer_path`: same-origin referrer pathname when present.
9. `source`: initially `web_first_party`.
10. `session_key`: first-party session identifier.
11. `visitor_key`: first-party long-lived visitor identifier.

### Optional Event Fields

Optional now, useful later:

1. `ga_client_id`
2. `user_agent_family`
3. `is_bot_suspected`
4. `site_section`

## Counting Rules

### Dedupe

To avoid inflating counts from refresh loops and route churn:

1. Accept each `event_uuid` once.
2. Additionally suppress duplicate visits from the same `visitor_key` to the same `entity_type + entity_id` within a 30-minute cooldown window.
3. Preserve two counters in aggregates:
   1. `raw_views`: all accepted events.
   2. `deduped_views`: cooldown-suppressed count used for ranking.

Recommendation: use `deduped_views` as the default ranking metric and keep `raw_views` available for diagnostics.

### Bot Filtering

The first release should exclude obvious non-user traffic:

1. Ignore requests with known bot user agents.
2. Ignore events from smoke tests and local scripts by sending a custom header or disabling client instrumentation in test mode.
3. Ignore same-event replays via `event_uuid` uniqueness.

### Hidden Profiles

Hidden players should still be countable if their routed detail page is viewable, because the question is about user interest, not data completeness.

## Architecture

### Client Instrumentation

Recommended hook points:

1. `client/app/components/PlayerRouteView.tsx`
2. `client/app/components/ClanRouteView.tsx`

Behavior:

1. Emit the event only after the route payload resolves successfully.
2. Do not emit from nested chart components.
3. Do not emit on loading skeletons or not-found states.
4. Use `navigator.sendBeacon()` when available, with `fetch(..., { keepalive: true })` fallback.

Why these files:

1. They are the canonical routed detail entry points.
2. They already know whether the route resolved to a valid player or clan.
3. Instrumenting here avoids double counting from internal fetch components inside `PlayerDetail` and `ClanDetail`.

### First-Party Server Endpoint

Add a lightweight endpoint:

`POST /api/analytics/entity-view/`

Responsibilities:

1. Validate the payload.
2. Normalize timestamps and route fields.
3. Derive hashed storage keys from `visitor_key` and `session_key`.
4. Apply idempotency and cooldown rules.
5. Write raw event rows.
6. Upsert daily aggregate rows.

This should live outside the hot player/clan read endpoints so analytics failures do not block detail page rendering.

### Storage Model

Use two persistence layers.

#### 1. Raw Event Table

Recommended model: `EntityVisitEvent`

Fields:

1. `event_uuid` unique
2. `occurred_at`
3. `event_date`
4. `entity_type`
5. `entity_id`
6. `entity_name_snapshot`
7. `entity_slug_snapshot`
8. `route_path`
9. `referrer_path`
10. `source`
11. `visitor_key_hash`
12. `session_key_hash`
13. `dedupe_bucket_started_at`
14. `counted_in_deduped_views`
15. `created_at`

Retention recommendation:

1. Keep 90 days of raw events.
2. Add a cleanup management command or periodic Celery task.

#### 2. Daily Aggregate Table

Recommended model: `EntityVisitDaily`

Fields:

1. `date`
2. `entity_type`
3. `entity_id`
4. `entity_name_snapshot`
5. `views_raw`
6. `views_deduped`
7. `unique_visitors`
8. `unique_sessions`
9. `last_view_at`
10. `source_first_party_views`
11. `source_ga4_views`
12. `updated_at`

Unique constraint:

1. `(date, entity_type, entity_id)`

Why a daily aggregate is required:

1. The product question is ranking-oriented.
2. Top-N queries over raw events will become unnecessary overhead.
3. GA4 imports later can merge into the same daily reporting shape.

### Identifier Strategy

The system should not store raw IPs or personally identifying data.

Recommended identifiers:

1. `visitor_key`: random UUID stored in a first-party cookie with a long TTL.
2. `session_key`: random UUID stored in `sessionStorage` and rotated when the browser session ends.
3. Server stores only a salted hash of each key.

This gives enough fidelity for unique counts without creating a user identity system.

## Reporting Surface

### API

Add internal read endpoints:

1. `GET /api/analytics/top-entities/?entity_type=player&period=7d&metric=views_deduped&limit=25`
2. `GET /api/analytics/top-entities/?entity_type=clan&period=30d&metric=unique_visitors&limit=25`
3. `GET /api/analytics/entity-visits/<entity_type>/<entity_id>/?period=30d`

Response for top entities should include:

1. `entity_type`
2. `entity_id`
3. `entity_name`
4. `views_raw`
5. `views_deduped`
6. `unique_visitors`
7. `unique_sessions`
8. `last_view_at`

### Consumer Options

The first consumer can be any of:

1. Django admin table view.
2. Internal-only JSON endpoint for manual querying.
3. A small trace-style internal dashboard later.

This spec does not require public UI exposure to be useful.

## Google Analytics Path

### Recommendation

Add GA4 only after the first-party event contract exists.

### GA4 Event Shape

Emit the same semantic event:

1. `event_name = entity_detail_view`
2. `entity_type`
3. `entity_id`
4. `entity_slug`
5. `entity_name`
6. `route_path`

### Integration Points

Likely future touch points:

1. `client/app/layout.tsx` for GA script bootstrap.
2. Shared analytics helper in `client/app/lib/` for event dispatch.
3. Route views for actual event emission.

### Why GA4 Is Still Helpful

1. It provides cross-checking against first-party counts.
2. It can answer broader traffic questions such as referrers and acquisition.
3. BigQuery export can support richer analysis later.

### Why GA4 Should Not Be The Only Source

1. Pageview loss from blockers and privacy controls will distort rankings.
2. GA4 reporting is not designed around battlestats entity IDs as a first-class product model.
3. Joining GA exports back to current Player and Clan records is avoidable if first-party tracking already exists.

## Data Flow

1. User lands on `/player/<name>` or `/clan/<slug>`.
2. `PlayerRouteView` or `ClanRouteView` loads canonical entity data.
3. On successful render, the client emits `entity_detail_view`.
4. Django validates and records the raw event.
5. Django upserts the daily aggregate row.
6. Reporting endpoints read from `EntityVisitDaily`.
7. Future GA4 emission uses the same event contract in parallel.

## Implementation Plan

### Phase 1: Canonical First-Party Tracking

1. Add analytics models and migrations.
2. Add serializers and `POST /api/analytics/entity-view/`.
3. Add a small analytics client helper in the Next app.
4. Instrument `PlayerRouteView` and `ClanRouteView`.
5. Add reporting endpoint for top entities.
6. Add tests for idempotency, cooldown dedupe, and aggregate updates.

### Phase 2: Internal Reporting

1. Add an admin view or simple internal dashboard.
2. Support `1d`, `7d`, `30d`, and custom date range queries.
3. Add a detail endpoint for a single player or clan visit timeline.

### Phase 3: GA4 Parallel Emission

1. Add GA4 bootstrap to the app shell.
2. Emit `entity_detail_view` in parallel with first-party tracking.
3. Optionally add GA4 import or reconciliation later.

## Acceptance Criteria

### Functional

1. The system can return the top visited players for 1-day, 7-day, and 30-day windows.
2. The system can return the top visited clans for the same windows.
3. Rankings are based on persisted aggregates, not transient cache state.
4. A repeated reload loop from the same browser session does not inflate rankings without bound.

### Technical

1. Player and clan detail pages remain functional if analytics submission fails.
2. The analytics endpoint is append-safe and idempotent.
3. Reporting queries stay fast at dataset scale because they read daily aggregates.
4. No existing `last_lookup` freshness behavior is removed or repurposed.

### Data Quality

1. The same page view does not generate multiple counted visits from nested API calls.
2. Bot and smoke-test traffic are excluded from ranked counts.
3. The system can distinguish raw views from deduped views and unique visitors.

## Risks And Mitigations

### Risk: Counting API lookups instead of page views

Mitigation:

1. Emit only from route views after successful entity resolution.
2. Keep analytics out of nested fetch components.

### Risk: Overcounting due to reloads or SPA rerenders

Mitigation:

1. Enforce `event_uuid` uniqueness.
2. Add cooldown-based dedupe per visitor and entity.

### Risk: Underreporting because GA4 is blocked

Mitigation:

1. Use first-party tracking as canonical.
2. Treat GA4 as additive, not authoritative.

### Risk: Privacy creep

Mitigation:

1. Store hashed visitor and session keys only.
2. Do not store raw IP addresses in analytics tables.
3. Keep the event schema intentionally narrow.

### Risk: Aggregate drift

Mitigation:

1. Write aggregate updates transactionally with event persistence when feasible.
2. Add a rebuild management command that can recompute daily aggregates from raw events.

## File-Level Implementation Targets

Likely touched files when this is built:

1. `client/app/components/PlayerRouteView.tsx`
2. `client/app/components/ClanRouteView.tsx`
3. `client/app/layout.tsx`
4. `server/warships/models.py`
5. `server/warships/views.py`
6. `server/battlestats/urls.py`
7. `server/warships/tests/test_views.py`
8. new analytics-focused tests under `server/warships/tests/`

## Final Recommendation

Build the first-party tracking layer first, and treat GA4 as a parallel downstream analytics lane.

That gives battlestats an exact, entity-aware answer to "which players and clans get the most visits" without depending on marketing tooling, while still keeping a clean path to broader Google-based reporting later.
