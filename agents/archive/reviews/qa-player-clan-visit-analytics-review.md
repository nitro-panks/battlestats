# QA Review — Player And Clan Visit Analytics Spec

## Verdict

Proceed with implementation.

The spec is sound for a measured Phase 1 as long as the first release stays narrow:

1. route-level page-view tracking only,
2. first-party storage as the canonical source,
3. daily aggregate reporting for top players and clans,
4. graceful failure when analytics submission fails.

## QA Findings

### 1. High: Count only routed detail-page renders, not API lookups

The current backend already updates `Player.last_lookup` and `Clan.last_lookup` from API reads. If implementation accidentally reuses those lookup paths as visit counters, rankings will be polluted by nested fetches and non-browser consumers.

Required action:

1. Emit only from `PlayerRouteView` and `ClanRouteView` after successful entity load.
2. Keep analytics writes on a separate endpoint.

### 2. Medium: Dedupe behavior must be testable and visible in stored data

The 30-minute visitor cooldown is reasonable, but it will be easy to break later unless the raw event row records whether it counted toward deduped rankings.

Required action:

1. Persist a boolean such as `counted_in_deduped_views` on raw events.
2. Add explicit tests for first view, repeated view inside cooldown, and duplicate `event_uuid` replay.

### 3. Medium: Client-side analytics must not break routed detail rendering

The client routes already do one critical job: load player or clan data and render the detail view. Analytics must be fire-and-forget.

Required action:

1. Swallow analytics transport failures.
2. Keep route rendering successful even if analytics POST fails.
3. Ensure tests still assert the original route behavior.

### 4. Medium: Top-entities reporting should read daily aggregates, not raw events

The product question is ranking-oriented. Reporting directly from raw events in Phase 1 would work briefly, then become the wrong query surface.

Required action:

1. Add a daily aggregate table in Phase 1.
2. Make the top-entities endpoint read aggregate rows.

## Required QA Checks

1. A successful player route load emits exactly one analytics write attempt.
2. A successful clan route load emits exactly one analytics write attempt.
3. Failed player or clan route loads emit no analytics write.
4. The analytics endpoint accepts a valid payload and stores a raw event row.
5. The analytics endpoint increments daily aggregates correctly.
6. A second visit from the same visitor to the same entity inside the cooldown window increments raw views but not deduped views.
7. Replaying the same `event_uuid` does not create duplicate rows or inflated aggregates.
8. Bot user agents are ignored.
9. The top-entities endpoint returns ranked rows for players and clans over supported time windows.
10. Existing routed detail tests still pass.

## Regression Risks

1. Double-counting caused by route re-renders or nested fetch components.
2. Silent analytics failures leaking console noise or breaking UI tests.
3. Ranking inaccuracies if aggregates drift from raw rows.
4. Polluting analytics with smoke tests, bots, or repeated page refreshes.

## QA Recommendation

Ship Phase 1 only after backend tests cover idempotency and cooldown rules, and client tests prove that route-level analytics does not change the existing player/clan page behavior.
