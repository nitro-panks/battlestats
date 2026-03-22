# Ranked Seasons First-Load False Empty

## Scope

This runbook covers the player-detail bug where the Ranked Seasons panel shows:

- `No ranked seasons found for this player.`

on the first load, but a manual refresh shows ranked seasons normally a few seconds later.

## Symptom

On player detail pages, the ranked seasons panel can render the empty-state message immediately even for players that do have ranked history.

Observed user-facing behavior:

1. open player detail
2. Ranked Seasons renders `No ranked seasons found for this player.`
3. refresh the page after a short delay
4. ranked seasons appear normally

## Client Read Path

The panel is driven by:

- `client/app/components/RankedSeasons.tsx`

Current behavior in that component:

1. fetch `/api/fetch/ranked_data/${playerId}/`
2. retry only on fetch failure
3. treat `200 OK` with `[]` as a final empty-state result

Current empty-state text:

- `No ranked seasons found for this player.`

## Server Read Path

The endpoint is:

- `server/warships/views.py` `ranked_data()`

It delegates to:

- `server/warships/data.py` `fetch_ranked_data()`

Current `fetch_ranked_data()` behavior:

1. if `player.ranked_json is not None`, return it immediately
2. if that cached ranked payload is stale, queue `queue_ranked_data_refresh(player_id)` in the background
3. do not block the response on refresh completion
4. if `player.ranked_json is None`, perform the synchronous bootstrap path via `update_ranked_data(player_id)`

That means a cached empty list is treated as a valid read model and is returned immediately, even when it is stale and a background refresh has just been queued.

## Concrete Reproduction

### Natural stale-empty players observed

These players were found in the local DB with stale cached empty ranked payloads:

- `DusttheRegulus` `1017753905`
- `nildesperandum` `1038559897`
- `Dorryza` `1019969895`
- `Old_Man_Rebel_Gaming` `1011518356`
- `Johnny_Storm` `1000032430`

Observed behavior for those naturally stale-empty players:

1. first request returned `200` with `[]`
2. later request updated `X-Ranked-Updated-At`
3. payload still remained `[]`

Conclusion:

- these were true empty histories, not the false-empty bug
- they confirm that the endpoint legitimately serves stale empty caches first and refreshes later

### Forced false-empty reproduction with real ranked players

To reproduce the actual UI bug, three known ranked players were temporarily seeded in the running container with:

- `ranked_json = []`
- `ranked_updated_at = now - 5 days`

Players used:

- `LemmingTheGreat` `1018847016`
- `Punkhunter25` `1001243015`
- `Shinn000` `1000270433`

Observed result:

1. first request returned `200` with `[]`
2. second request a few seconds later returned populated ranked seasons
3. `X-Ranked-Updated-At` advanced from the seeded stale timestamp to a fresh timestamp

Observed sequence:

### `LemmingTheGreat` `1018847016`

1. first: `200`, `X-Ranked-Updated-At=2026-03-15...`, body `[]`
2. second: `200`, fresh `X-Ranked-Updated-At`, populated seasons

### `Punkhunter25` `1001243015`

1. first: `200`, `X-Ranked-Updated-At=2026-03-15...`, body `[]`
2. second: `200`, fresh `X-Ranked-Updated-At`, populated seasons

### `Shinn000` `1000270433`

1. first: `200`, `X-Ranked-Updated-At=2026-03-15...`, body `[]`
2. second: `200`, fresh `X-Ranked-Updated-At`, populated seasons

This reproduces the exact false-empty behavior the user reported.

## Root Cause

This is not a fetch failure. It is a read-contract mismatch.

### Backend contract

`fetch_ranked_data()` intentionally serves cached ranked data immediately, including cached `[]`, and only queues refresh in the background when stale.

This behavior is consistent with the repo's cache-first policy.

### Frontend contract

`RankedSeasons.tsx` only distinguishes between:

1. fetch failed
2. fetch succeeded with data
3. fetch succeeded with empty list

It does not distinguish between:

1. confirmed empty ranked history
2. stale cached empty placeholder while ranked refresh is pending

So when the endpoint returns stale cached `[]` on the first request, the component immediately renders the final no-data message.

## Why Refresh Fixes It

The first request queues ranked refresh work.

By the time the page is manually refreshed:

1. the background task has often finished
2. `player.ranked_json` has been repopulated
3. the same endpoint now returns populated ranked seasons

The UI was not wrong about the response body. It was wrong about what that empty success meant.

## Existing Evidence In Tests

Current backend test coverage already codifies the core server behavior:

- `server/warships/tests/test_data.py`
- `test_fetch_ranked_data_returns_stale_cache_and_queues_refresh`

That test asserts:

1. stale cached ranked data is returned immediately
2. ranked refresh is queued in the background

This is the intended server-side behavior and explains why the frontend bug is reproducible.

## Recommended Fix

Keep the cache-first backend behavior. Do not restore request-thread ranked recompute.

Implemented on 2026-03-20:

1. `/api/fetch/ranked_data/<player_id>/` now sets `X-Ranked-Pending: true` when the ranked payload is empty and a ranked refresh is in flight.
2. `RankedSeasons.tsx` now treats that response as a temporary refresh state instead of a final empty-state verdict.
3. the client performs bounded polling until pending clears or the retry budget is exhausted.

This keeps the non-blocking backend model and adds the missing pending-state contract so the UI can distinguish transient empty cache from durable no-history.

### Backend changes

Add a lightweight pending signal on `/api/fetch/ranked_data/<player_id>/` when all of the following are true:

1. `player.ranked_json == []`
2. ranked data is stale or missing enough that a refresh was queued
3. `is_ranked_data_refresh_pending(player_id)` is true, or the current request just queued refresh work

Implemented response header:

- `X-Ranked-Pending: true`

Optional additive metadata:

- preserve `X-Ranked-Updated-At`
- consider `X-Ranked-Cache-State: pending-empty-refresh`

### Frontend changes

Update `client/app/components/RankedSeasons.tsx` so that:

1. `200 OK` plus `[]` plus `X-Ranked-Pending: true` does not render the final no-data message
2. the component shows a loading or refreshing state instead
3. it performs bounded polling or retries, similar to `ClanBattleSeasons.tsx`
4. only after pending clears and the payload is still `[]` does it render `No ranked seasons found for this player.`

## Preferred UX

The panel should distinguish between these two states:

1. `Refreshing ranked seasons...`
2. `No ranked seasons found for this player.`

The first is a transient hydration state.

The second is a durable empty-state verdict.

Those states are currently conflated.

## Tests Added

### Backend

Added endpoint coverage in:

- `server/warships/tests/test_views.py`

Implemented case:

1. stale cached empty ranked payload returns `200` and `X-Ranked-Pending: true` when refresh is pending
2. confirmed empty ranked payload returns `200` without pending header once refresh completes and still yields `[]`

### Frontend

Added component coverage for:

- `client/app/components/RankedSeasons.tsx`

Implemented cases:

1. empty ranked response with pending header keeps the panel in refreshing state and retries
2. subsequent populated response renders the seasons table
3. empty ranked response without pending header renders the final empty-state message

## Validation Steps

1. Visit a player whose ranked cache is stale-empty but whose upstream ranked history exists.
2. Confirm first load does not show the final no-data message.
3. Confirm the panel stays in a temporary refreshing state while pending.
4. Confirm the table appears without a full page reload once refresh completes.
5. Confirm true no-ranked-history players still render the final empty-state message.

## Files Involved

- `client/app/components/RankedSeasons.tsx`
- `server/warships/data.py`
- `server/warships/views.py`
- `server/warships/tasks.py`
- `server/warships/tests/test_data.py`
- `server/warships/tests/test_views.py`
