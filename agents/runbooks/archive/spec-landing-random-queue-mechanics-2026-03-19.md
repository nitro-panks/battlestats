# Spec: Landing Random Queue Mechanics

_Captured: 2026-03-19_

_Status: partially historical; public landing reads now use published cache surfaces with durable fallback, while queue refill/preview remains background infrastructure_

## Goal

Change the landing-page random surfaces so they stop choosing random players and clans during the user request.

Current runtime note:

- public landing player and clan requests now read the published landing cache surfaces, not queue-pop responses
- the queue lane remains relevant for background refill and preview plumbing, not as the public request contract

Required behavior:

- maintain a queue of `100` eligible random player ids
- maintain a queue of `100` eligible random clan ids
- when the landing page requests random players, pop `25` ids from the head of the player queue and return those rows
- when the landing page requests random clans, pop `30` ids from the head of the clan queue and return those rows
- do not refill the queue while the user is waiting on that request
- after serving a random-player request, trigger background refill of `25` more eligible player ids
- after serving a random-clan request, trigger background refill of `30` more eligible clan ids
- when the clan queue refill happens, warm the landing clan chart payload so the next clan render is ready

This spec applies only to the random landing lanes.

Unchanged lanes:

- `recent players`
- `recent clans`
- `best players`
- `sigma players`

## Current Behavior

### Random players

Current request path in [server/warships/landing.py](/home/august/code/archive/battlestats/server/warships/landing.py):

- `_build_random_landing_players(limit)` builds the random player result on demand
- it queries all eligible player ids
- it calls `random.sample(...)` during the request
- it fetches and serializes those players immediately
- the result is cached for one hour via `get_landing_players_payload_with_cache_metadata(...)`

Implication:

- random player selection happens on the hot request path
- the one-hour payload cache means one sampled set can stay fixed for too long
- the system has no durable notion of a queue or of incremental replenishment

### Random clans

Current request path in [server/warships/landing.py](/home/august/code/archive/battlestats/server/warships/landing.py):

- `_build_landing_clans()` builds a broad clan payload
- `_prioritize_landing_clans(...)` chooses a featured subset with `random.sample(...)`
- the full payload is cached for one hour via `get_landing_clans_payload_with_cache_metadata(...)`

Implication:

- clan randomization also happens on the hot build path
- the landing clan chart depends on the cached clan payload, not on a stable queue-driven featured set
- warmup exists today through `warm_landing_page_content_task`, but it is TTL-driven rather than queue-driven

## Proposed Product Behavior

### Random landing players

For `GET /api/landing/players/?mode=random`:

- response returns the next `25` ids from a Redis-backed random-player queue
- those ids are resolved to player rows after the pop
- the request does not build or refill the next batch synchronously
- after the response payload is determined, the server schedules a background refill of `25` more eligible ids

### Random landing clans

For `GET /api/landing/clans/` in the landing random lane:

- response returns the next `30` ids from a Redis-backed random-clan queue
- those ids are resolved to clan rows after the pop
- the request does not synchronously refill the queue
- after the response payload is determined, the server schedules a background refill of `30` more eligible ids
- the refill task also warms the landing clan chart payload so the next clan landscape render is ready

### Queue intent

The queue makes the landing page behave more like a rotating deck:

- each request consumes a visible tranche
- replenishment happens behind the scenes
- request latency becomes row resolution plus serialization, not candidate discovery plus random sampling plus refill work

## Scope Decisions

### In scope

- random landing player queue
- random landing clan queue
- async refill tasks for each queue
- low-water-mark scheduling after a pop
- clan chart warmup after clan refill
- tests for queue depletion, refill scheduling, duplicate prevention, and warmup sequencing

### Out of scope

- changing best-player scoring
- changing sigma-player selection
- changing recent-player or recent-clan behavior
- changing player-search suggestions
- changing public API shapes for non-random landing endpoints unless needed for metadata

## Queue Model

### Redis keys

Add explicit queue keys in [server/warships/landing.py](/home/august/code/archive/battlestats/server/warships/landing.py) or a nearby queue helper module.

Proposed keys:

- `landing:queue:players:random:v1`
- `landing:queue:players:random:refill-lock:v1`
- `landing:queue:players:random:eligible-cache:v1`
- `landing:queue:clans:random:v1`
- `landing:queue:clans:random:refill-lock:v1`
- `landing:queue:clans:random:eligible-cache:v1`

Optional metadata keys:

- `landing:queue:players:random:last-refill-at:v1`
- `landing:queue:clans:random:last-refill-at:v1`

### Queue size targets

- target depth: `100`
- request pop size: `40`
- background refill tranche: `40`
- refill trigger threshold: queue depth after pop is `< 60`

Rationale:

- a queue of `100` provides two full visible batches plus headroom
- refilling by `40` exactly matches one served tranche
- threshold `< 60` avoids back-to-back requests draining the queue to near-empty before the worker responds

## Eligibility Rules

### Random player queue eligibility

Base eligibility should match current `_build_random_landing_players(...)` behavior:

- `name != ''`
- `is_hidden = False`
- `days_since_last_battle <= 180`
- `pvp_battles > LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES`
- `last_battle_date IS NOT NULL`

This keeps product semantics stable while moving mechanics off-request.

### Random clan queue eligibility

Base eligibility should match the current clan-featured intent in `_prioritize_landing_clans(...)`:

- `name != ''`
- `clan_wr IS NOT NULL`
- `total_battles >= LANDING_CLAN_MIN_TOTAL_BATTLES`

This preserves the current meaning of the featured landing clan strip rather than opening the queue to weak or empty clans.

## Request-time Flow

### Random players request

Proposed flow for `mode=random` in [server/warships/views.py](/home/august/code/archive/battlestats/server/warships/views.py):

1. normalize request mode and limit
2. if mode is not `random`, keep existing code path unchanged
3. pop up to `40` player ids from `landing:queue:players:random:v1`
4. if the queue is empty or underfilled, synchronously bootstrap once to satisfy the current request
5. resolve those ids to player rows and serialize in queue order
6. return response
7. enqueue async refill if post-pop queue depth is below threshold

Important constraint:

- the refill must be scheduled after the response payload is ready, not awaited before returning

### Random clans request

Proposed flow for [server/warships/views.py](/home/august/code/archive/battlestats/server/warships/views.py):

1. pop up to `40` clan ids from `landing:queue:clans:random:v1`
2. if empty or underfilled, synchronously bootstrap once to satisfy the current request
3. resolve those ids to clan rows in queue order
4. return response
5. enqueue async clan-queue refill if post-pop depth is below threshold

## Bootstrap Behavior

### Cold start bootstrap

If a request finds the queue empty, the system may do one synchronous bootstrap only because there is no existing inventory to serve.

Bootstrap target:

- fill queue to `100`
- pop the first `40`
- leave the remainder in Redis

Cold-start bootstrap should be protected by a short lock so two simultaneous requests do not race to fully rebuild the same queue.

### Why synchronous bootstrap is acceptable

The user requirement is specifically to avoid refill while the user is waiting during normal operation.

Cold start is the one exception because there is no queue to serve from. The spec treats that as initialization, not steady-state refill.

## Background Refill Tasks

### New Celery tasks

Add two tasks in [server/warships/tasks.py](/home/august/code/archive/battlestats/server/warships/tasks.py):

- `refill_landing_random_players_queue_task`
- `refill_landing_random_clans_queue_task`

Task requirements:

- lock-protected so only one refill per queue can run at a time
- top up by up to `40` ids, not blindly rebuild to `100` on every request
- avoid pushing duplicates already present in the queue
- skip if another refill for the same queue is already running

### Clan refill extra responsibility

After clan ids are successfully pushed, the clan refill task must warm the landing clan chart payload.

Proposed warm target:

- rebuild the queue-backed landing clan response payload cache for the next request
- if the chart payload is the same payload returned by `GET /api/landing/clans/`, warm that exact surface

Implementation note:

- warming should happen after the queue top-up is committed, so the warmed chart reflects the new queue head and next visible batch

## Queue Refill Algorithm

### Candidate selection

Each refill task should draw candidates from a cached eligible-id pool rather than recomputing a giant queryset repeatedly inside one refill loop.

Suggested refill algorithm:

1. load current queue ids into a set
2. load eligible ids for the queue type
3. subtract queued ids from eligible ids
4. shuffle the remainder
5. append up to `40` ids to the Redis list tail

### Eligible pool caching

To keep refill cheap, cache the eligible-id list separately for a short TTL.

Suggested TTLs:

- eligible random player ids: `10 minutes`
- eligible random clan ids: `10 minutes`

This gives the queue refill task a stable pool without re-running the full eligibility query on every request-triggered refill.

### Duplicate policy

Rules:

- no duplicate ids inside one queue at the same time
- duplicates across time are allowed after an id has been fully consumed and later becomes eligible again

Reason:

- the queue should feel rotational, not repetitive within adjacent landing visits

## Caching Contract

### Keep payload caching, but change what it caches

Current published landing caches use a 12-hour freshness window and retain the last published payload as a durable fallback so public reads stay hot after first publish.

Under the queue model:

- random player payload caching should be reduced or removed for queue-backed responses because caching the entire popped result defeats queue rotation
- clan queue responses can still have a short-lived response cache only if it is explicitly tied to the current queue head version

Recommended contract:

- random player lane: do not one-hour cache the popped `40` rows
- random clan lane: keep a short cache only for the warmed next batch, keyed by queue version
- best and sigma lanes: keep their current cache model
- recent lanes: keep current cache model

### Queue versioning

If response caching remains for clans, add a queue version key:

- `landing:queue:clans:random:version:v1`

Increment version whenever the clan queue head changes materially after a pop or refill.

This prevents serving a stale warmed clan chart payload after the queue rotates.

## API Behavior

### Existing endpoints stay stable

No endpoint rename is required.

Keep:

- `GET /api/landing/players/?mode=random`
- `GET /api/landing/clans/`

### Response headers

Add queue-oriented response headers for observability:

- `X-Landing-Queue-Type: players-random|clans-random`
- `X-Landing-Queue-Served-Count: 40`
- `X-Landing-Queue-Remaining: <n>`
- `X-Landing-Queue-Refill-Scheduled: true|false`

These are optional for frontend logic but valuable for QA and smoke testing.

## Data Resolution Helpers

### Players

Add helper functions in [server/warships/landing.py](/home/august/code/archive/battlestats/server/warships/landing.py):

- `get_random_landing_player_ids_from_queue(limit: int = 40)`
- `bootstrap_random_landing_player_queue(target_size: int = 100)`
- `refill_random_landing_player_queue(batch_size: int = 40)`
- `resolve_landing_players_by_id_order(player_ids: list[int])`

### Clans

Add parallel helpers:

- `get_random_landing_clan_ids_from_queue(limit: int = 40)`
- `bootstrap_random_landing_clan_queue(target_size: int = 100)`
- `refill_random_landing_clan_queue(batch_size: int = 40)`
- `resolve_landing_clans_by_id_order(clan_ids: list[int])`

This keeps queue mechanics separate from row serialization and helps testability.

## Clan Chart Warmup Contract

The user requirement explicitly ties clan refill to chart warmup.

Define the contract as:

- after a successful clan-queue refill, warm the payload needed by the landing clan visualization
- the warmed payload must correspond to the next queue-driven visible batch, not the previous one
- the refill task owns that warmup, not the user request

If the current chart uses the same `/api/landing/clans/` payload:

- warm exactly that queue-backed payload

If the chart later gets a dedicated endpoint:

- warm both the landing visible rows and the chart payload from the same queue snapshot

## Failure Handling

### Empty queue after bootstrap failure

If bootstrap cannot produce enough ids:

- return as many rows as were resolved
- return `[]` only if no eligible rows exist
- do not fail the landing page with `500` solely because queue depth is low

### Refill failure

If async refill fails:

- log the failure
- leave the current queue untouched
- let the next eligible request or scheduled warm pass enqueue another refill attempt

### Lock contention

If refill lock exists:

- skip duplicate refill scheduling
- do not block the response

## Testing Plan

Add backend tests in [server/warships/tests/test_landing.py](/home/august/code/archive/battlestats/server/warships/tests/test_landing.py) and [server/warships/tests/test_crawl_scheduler.py](/home/august/code/archive/battlestats/server/warships/tests/test_crawl_scheduler.py) as needed.

Required coverage:

- cold bootstrap fills queue to `100` and returns first `40`
- normal request pops `40` and preserves order
- request schedules refill when queue depth falls below threshold
- refill appends up to `40` unique ids
- refill does not duplicate ids already in queue
- refill lock prevents parallel duplicate refills
- clan refill warms the landing clan chart payload after top-up
- empty eligible set returns `[]` cleanly
- best and sigma modes remain unchanged

Optional client coverage:

- existing client tests can remain mostly unchanged if payload shape stays stable
- add one test for any new response headers only if the client starts using them

## Rollout Plan

### Phase 1

- implement queue helpers and tasks
- switch random players to queue-backed serving
- keep clan path unchanged except for helper scaffolding

### Phase 2

- switch random clans to queue-backed serving
- add clan chart warmup on refill
- expose queue observability headers

### Phase 3

- trim or remove obsolete one-hour random-lane payload caches
- tune queue sizes and refill thresholds using real traffic

## Acceptance Criteria

The work is complete when all of the following are true:

- random landing players are served from a prebuilt queue of ids rather than request-time sampling
- random landing clans are served from a prebuilt queue of ids rather than request-time sampling/prioritization
- each request serves `40` rows from the queue head
- each queue targets `100` ids in storage
- normal requests do not wait for the `40`-id refill to finish
- refill happens asynchronously after serving the user
- clan refill warms the landing clan chart payload after updating the queue
- best, sigma, and recent surfaces still behave as before
- test coverage exists for queue serving, refill scheduling, and clan chart warmup sequencing

## Recommended Implementation Notes

- prefer Redis list operations for queue storage and Redis locks for refill/bootstrap exclusion
- keep query eligibility logic in one place so queue mechanics do not drift from current product filters
- isolate queue-backed random lanes from the cached `best` and `sigma` lanes to avoid unnecessary cache invalidation coupling
- treat the clan queue and warmed clan chart as one coherent unit so the next landing visit does not pay for both rotation and chart preparation
