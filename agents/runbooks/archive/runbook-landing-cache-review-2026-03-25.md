# Runbook: Landing Cache Review 2026-03-25

_Reviewed: 2026-03-25_

_Status: reviewed and partially implemented on 2026-03-25; durable published landing fallbacks now keep public player/clan reads hot after first publish_

## Scope

Review the current landing-page caching strategy for the published player and clan surfaces and answer one operational question:

Do players and clans stay hot after they have been loaded once?

This review covers:

1. published landing player payloads
2. published landing clan payloads
3. recent-player and recent-clan landing payloads
4. landing warmup tasks and scheduler wiring
5. random landing queue refill behavior only where it affects hot published surfaces

## Current Runtime Shape

### Published landing players

The public landing-player endpoint reads through `get_landing_players_payload_with_cache_metadata(...)` in `server/warships/landing.py` and returns cached payloads plus cache headers from `server/warships/views.py`.

Key properties today:

1. TTL is `12` hours via `LANDING_PLAYER_CACHE_TTL`
2. the public route reads the published cache path, not the queue-pop path
3. invalidation marks the landing-player family dirty and schedules a republish task instead of deleting the current payload immediately
4. the scheduled/task warm path uses `force_refresh=True`, so it rebuilds all published player surfaces eagerly when it runs

### Published landing clans

The public landing-clan endpoint reads through `get_landing_clans_payload_with_cache_metadata(...)` or `get_landing_best_clans_payload_with_cache_metadata(...)` and slices the returned payload in `server/warships/views.py`.

Key properties today:

1. TTL is `12` hours via `LANDING_CLAN_CACHE_TTL`
2. the public route reads the published cache path, not the random-queue payload path
3. invalidation marks the clan family dirty and schedules a republish task instead of deleting the current payload immediately
4. the scheduled/task warm path uses `force_refresh=True`, so it rebuilds published clan surfaces eagerly when it runs

### Recent surfaces

The recent-player and recent-clan payloads behave differently from the published surfaces.

1. they explicitly honor dirty keys on read
2. if dirty, the next read rebuilds the recent payload and clears the dirty marker
3. they still preserve the currently cached payload until that next rebuild happens

### Warm path

The repo currently warms landing caches in three places:

1. server startup: `docker-compose.yml` runs `python manage.py warm_landing_page_content` a few seconds after boot when `WARM_LANDING_PAGE_ON_STARTUP=1`
2. beat schedule: `server/warships/signals.py` provisions a `landing-page-warmer` periodic task
3. write-side invalidation: landing invalidation helpers call `queue_landing_page_warm()` in `server/warships/tasks.py`

## Conclusion

The original review finding was correct at capture time: published landing player and clan payloads were still able to go cold on cache miss.

This tranche closes that gap for the public player and clan landing surfaces by adding durable published fallback payloads that are served when the TTL-bound primary key is missing.

The landing player and clan surfaces are now much closer to a hard cache-first contract after first publish.

They stay hot operationally only while all of the following remain true:

1. the startup warm ran successfully
2. Celery worker dispatch is available for republish tasks
3. the beat-driven `landing-page-warmer` continues to refresh the payloads before TTL expiry
4. the underlying cache store is not flushed

If the TTL-bound primary key expires or is missing, the public read now serves the durable published fallback and schedules a republish in the background instead of rebuilding synchronously on the request path.

## Findings

### 1. High at review time, now reduced: published landing player and clan payloads rebuilt synchronously on total miss

Evidence:

1. `server/warships/landing.py` calls `_build_landing_clans()` when `LANDING_CLANS_CACHE_KEY` is missing in `get_landing_clans_payload_with_cache_metadata(...)`
2. `server/warships/landing.py` calls `_build_random_landing_players`, `_build_best_landing_players`, or `_build_sigma_landing_players` when the selected player cache key is missing in `get_landing_players_payload_with_cache_metadata(...)`
3. `server/warships/views.py` serves those helpers directly on the public request path

Why it mattered:

1. the active cache-first policy says cached data should remain the primary read model and derived payloads should not be rebuilt on reads merely because TTL elapsed
2. once the cache entry expires or the cache is flushed, the next user request can pay the full rebuild cost
3. this means “stay hot once loaded” is an operational convention, not an enforced runtime contract

Original impact:

1. beat/worker outages or cache flushes could turn the next public landing request cold
2. this risk affected both player and clan published surfaces

Current status:

1. public landing player/clan reads now serve a durable published fallback when the TTL-bound primary key is missing
2. the remaining synchronous rebuild risk is now limited to true first-publish or full-never-warmed cases where neither the primary key nor the durable published fallback exists

Recommended follow-up:

1. completed in this tranche: published landing player and clan payloads now retain a non-expiring published fallback so reads do not block on rebuild after first publish
2. remaining follow-up: decide whether startup/bootstrap should also guarantee pre-first-publish warm coverage in environments where the initial warm task may be disabled or unavailable

### 2. Medium: the periodic landing warmer cadence is still much shorter than the current 12-hour landing TTL

Evidence:

1. `server/warships/landing.py` sets `LANDING_PLAYER_CACHE_TTL` and `LANDING_CLAN_CACHE_TTL` to `12` hours
2. `server/warships/signals.py` provisions the `landing-page-warmer` every `55` minutes
3. `server/warships/tasks.py` runs `warm_landing_page_content(force_refresh=True, ...)`, so every scheduled warm fully rebuilds the landing surfaces rather than only refreshing near actual expiry

Why it matters:

1. the current cadence is much more aggressive than the actual TTL requires
2. unnecessary force-refresh churn raises avoidable DB work for payloads the product explicitly allows to be stale-but-fast

Recommended follow-up:

1. align the periodic warm interval and its description with the actual 12-hour policy, or
2. if the shorter cadence is intentional, document that the warmer is freshness-driven rather than TTL-driven

### 3. Resolved in this tranche: active tests and specs had stale landing-cache assumptions that weakened regression coverage

Evidence:

1. `server/warships/tests/test_views.py` had stale warm-cache assertions at `limit=40`
2. `server/warships/tests/test_data.py` had stale landing-player cache key assertions at `limit=40`
3. `server/warships/signals.py` described a one-hour landing TTL even though runtime was already 12 hours
4. `agents/runbooks/spec-landing-random-queue-mechanics-2026-03-19.md` still described the old one-hour request-built cache model

Why it matters:

1. the runtime player limit is now `25` and clan limit is `30`
2. the active cache policy says landing payloads use a 12-hour freshness window with durable published fallback
3. stale tests and docs would have made it easier to miss regressions in the hot-cache behavior because the assertions no longer described production reality

Recommended follow-up:

1. completed: stale landing cache tests were updated to current player/clan key shapes and limits
2. completed: active landing cache docs were reconciled to the current published-cache model

### 4. Low: the random landing queue preview path looks operationally disconnected from the public clan/player read path

Evidence:

1. public landing views in `server/warships/views.py` read published cache payloads, not queue payload helpers
2. `server/warships/tasks.py` still warms a random-clan preview after queue refill
3. `get_random_landing_clan_queue_payload(...)` and `get_random_landing_player_queue_payload(...)` appear to be used only in helper tests, not in public views

Why it matters:

1. queue maintenance may still be useful for future or internal surfaces
2. but today it does not appear to be the mechanism that keeps the public landing page hot
3. that separation should be explicit so maintainers do not overestimate the queue’s role in public cache freshness

Recommended follow-up:

1. either document the queue lane as background-only infrastructure, or
2. remove/reduce preview warming work if it no longer supports any public or operational consumer

## What Currently Keeps Landing Hot

In practice, landing stays hot today because of the combination of:

1. startup warm in `docker-compose.yml`
2. periodic warm provisioning in `server/warships/signals.py`
3. invalidation that preserves current payloads while enqueuing a republish in `server/warships/landing.py`

That is good enough for normal operation when beat, worker, and cache are healthy.

For published landing player and clan surfaces, the durable fallback added in this tranche now closes the main post-first-publish cold-read gap.

The remaining hard-guarantee gap is narrower:

1. true first publish, before any landing payload has ever been built
2. environments where startup warm and worker-backed republish are both unavailable long enough that no initial publish occurs

## Suggested Next Tranche

If the product requirement is to tighten the remaining edge cases after this tranche, the next implementation tranche should do the following:

1. decide whether non-warmed environments should bootstrap an initial landing publish synchronously or through a stronger startup guarantee
2. tune the `landing-page-warmer` cadence so it matches the 12-hour freshness policy more closely, unless the shorter interval is intentionally freshness-driven
3. document the queue lane explicitly as background-only infrastructure if it will remain disconnected from public reads

## Validation Notes

This review was based on code and test inspection of:

1. `server/warships/landing.py`
2. `server/warships/views.py`
3. `server/warships/tasks.py`
4. `server/warships/signals.py`
5. `docker-compose.yml`
6. `server/warships/tests/test_landing.py`
7. `server/warships/tests/test_views.py`
8. `server/warships/tests/test_data.py`
9. `agents/runbooks/spec-cache-first-lazy-refresh-policy-2026-03-19.md`
10. `agents/runbooks/spec-landing-random-queue-mechanics-2026-03-19.md`
