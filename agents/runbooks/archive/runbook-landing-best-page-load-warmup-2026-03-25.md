# Runbook: Landing Best Page-Load Warmup 2026-03-25

_Implemented: 2026-03-25_

## Goal

Ensure that loading the landing page triggers warming for the top `25` Best players and top `25` Best clans so their detail surfaces are hot shortly after page entry.

## Shipped Design

Page load now triggers a dedicated landing warmup request:

1. the landing client calls `GET /api/landing/warm-best/` once per mount
2. that request is kicked off independently of the recent-clans and recent-players fetch results, so partial landing-data failures do not suppress best-detail warmup
3. the API does not warm inline on the request thread
4. the API deduplicates and queues `warm_landing_best_entity_caches_task`
5. the task resolves the current Best landing cohorts and warms their detail caches using the same player/clan warming steps already used by the hot-entity warmer

## Runtime Contract

What becomes warm:

1. top `25` Best players from the current published Best landing player payload
2. top `25` Best clans from the current published Best landing clan payload

What the warm task does for players:

1. refreshes stale player detail
2. refreshes battle/activity-derived caches when stale
3. refreshes tiers, types, randoms, ranked, explorer summary, and clan-battle season detail lanes

What the warm task does for clans:

1. refreshes stale clan detail
2. refreshes clan members when incomplete or stale
3. refreshes clan-battle seasons and both clan plot payload variants

## Files

Server:

1. `server/warships/data.py`
2. `server/warships/tasks.py`
3. `server/warships/views.py`
4. `server/battlestats/urls.py`

Client:

1. `client/app/components/PlayerSearch.tsx`

Focused tests:

1. `server/warships/tests/test_views.py`
2. `server/warships/tests/test_crawl_scheduler.py`
3. `client/app/components/__tests__/PlayerSearch.test.tsx`

## Operational Notes

1. This is an asynchronous warm path and depends on Celery worker dispatch being available.
2. Repeated landing loads are deduplicated through a short dispatch cache key so the page can be refreshed without fanning out duplicate best-warm jobs.
3. The request is fire-and-forget from the client and does not block landing render.
4. If the warmup request reaches Django but worker dispatch is unavailable, landing still renders normally and the endpoint returns a skipped enqueue result instead of warming detail caches inline.
