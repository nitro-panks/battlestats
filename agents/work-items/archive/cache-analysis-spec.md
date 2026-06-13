# Cache Analysis Spec

_Drafted: 2026-03-15_

## Objective

Document the current caching strategy in battlestats, answer what is cached and whether those caches are still active, and assess whether cache shape, refresh frequency, and TTL values still fit the current product behavior.

This spec has now been executed for the approved near-term changes in the same tranche.

## Scope

Reviewed surfaces:

1. Django cache backend configuration in `server/battlestats/settings.py`
2. data and analytics caches in `server/warships/data.py`
3. landing-page caches in `server/warships/landing.py`
4. task coordination and lock caches in `server/warships/tasks.py`
5. ship metadata cache in `server/warships/api/ships.py`
6. lightweight in-process API session cache in `server/warships/api/client.py`
7. response-layer caches in `server/warships/views.py`
8. cache-focused regression coverage in `server/warships/tests/test_cache.py`

Not treated as app-level cache sources:

1. generated `.next/` build output
2. Docker layer caching
3. browser-native caching outside explicit app code

## Runtime Snapshot

The running server currently resolves Django cache to Redis, not locmem.

- backend: `django.core.cache.backends.redis.RedisCache`
- location: `redis://redis:6379/0`
- global default timeout: `60` seconds

Live key-family sample captured from Redis during this analysis:

- `ship:*` -> `659` warm keys
- `clan_battles:*` -> `43` warm keys
- `players:*` -> `3` warm keys
- `ranked:*` -> `1` warm key
- `warships:tasks:*` -> `2` warm keys
- `landing:*` -> `0` warm keys at sample time
- `agentic:*` -> `0` warm keys at sample time
- `db:stats` -> `0` warm keys at sample time

Interpretation:

1. Redis is actively used in production-like runtime.
2. ship metadata and clan-battle caches are definitely live.
3. landing and trace caches are short-lived enough that a point-in-time scan may miss them.
4. test runs do not exercise the same backend semantics because the settings switch tests to locmem.

## Inventory

### 1. Ship metadata cache

- key family: `ship:{ship_id}`
- code: `server/warships/api/ships.py`
- read path:
  - `_fetch_ship_info()` checks Redis before DB or WG fetch
- write path:
  - `_upsert_ship_from_api_payload()` writes a fully populated `Ship` object
  - `_fetch_ship_info()` also writes when a DB row is already complete
- invalidation:
  - deletes per-ship keys during `sync_ship_catalog()`
  - deletes incomplete cached objects when detected
- TTL: `86400` seconds
- active today: yes

Assessment:

The usage is real and the TTL is broadly sensible because ship metadata is mostly static. The one architectural compromise is that Redis stores full Django model instances rather than a compact serialized dict. That works, but it ties cache value format to Python object serialization and model shape.

Verdict:

- still being used: yes
- TTL fit: reasonable
- shape fit: acceptable but not ideal

### 2. Landing page caches

- code: `server/warships/landing.py`
- global landing TTL: `60` seconds

Surfaces:

1. `landing:clans:v3`
2. `landing:recent_clans:last_lookup:v2`
3. `landing:recent_players:last_lookup:v4`
4. `landing:players:v9:n{namespace}:{mode}:{limit}`

Read paths:

1. `get_landing_clans_payload()`
2. `get_landing_recent_clans_payload()`
3. `get_landing_players_payload()`
4. `get_landing_recent_players_payload()`

Invalidation paths:

1. clan lookup recording invalidates clan caches
2. player profile lookup invalidates recent-player cache
3. `update_clan_data()` invalidates clan caches
4. `update_clan_members()` invalidates clan caches
5. `update_player_data()` invalidates all landing player caches and recent-player cache

Assessment:

This cache family is definitely live. It is the primary shield between frequently loaded landing surfaces and repeated DB aggregation.

The good:

1. TTL is short enough to keep the landing page fresh.
2. invalidation hooks exist for both lookups and upstream data refreshes.
3. versioned keys are already in place for most landing payloads.

The remaining tradeoffs:

1. the landing caches still use `cache.get_or_set(...)` without explicit stampede protection. At the current TTL and traffic shape this is probably acceptable, but it is still a tradeoff rather than a free property.
2. namespace-versioning leaves superseded landing player keys to age out on their normal TTL instead of deleting them synchronously. That is intentional and keeps invalidation cheap.

Verdict:

- still being used: yes
- TTL fit: good
- shape fit: good after key normalization and namespace-version invalidation

### 3. Player population distributions and correlation caches

- code: `server/warships/data.py`
- key families:
  - `players:distribution:v2:{metric}`
  - `players:correlation:v2:{metric}`
  - `players:correlation:v2:ranked_wr_battles:v6`
- TTL:
  - `PLAYER_DISTRIBUTION_CACHE_TTL = 3600`
  - `PLAYER_CORRELATION_CACHE_TTL = 3600`

Covered metrics:

1. win rate distribution
2. survival-rate distribution
3. battles-played distribution
4. win-rate vs survival correlation
5. tier-type population correlation
6. ranked win-rate vs battles correlation

Read paths:

1. chart endpoints in `server/warships/views.py`
2. client charts that fetch those endpoints from the Next app

Invalidation paths:

1. none beyond TTL expiry and key-version changes

Assessment:

These caches are active and important. The underlying queries iterate large populations and would be expensive to rebuild per request. A pure TTL strategy is acceptable here because these are aggregate analytics rather than primary transactional data.

The tradeoff is freshness. After large imports or refresh jobs, the aggregates can lag reality by up to one hour.

That is probably acceptable for trend charts and explorer-adjacent analytics, especially because some keys are explicitly versioned when binning logic changes.

The ranked correlation cache shows the strongest sign of active tuning: `ranked_wr_battles:v6`. That indicates schema and binning changes have happened repeatedly and versioned cache keys were used to avoid payload-shape drift.

Verdict:

- still being used: yes
- TTL fit: reasonable for analytics
- shape fit: good for expensive read-mostly aggregates

### 4. Clan-battle caches

- code: `server/warships/data.py`
- key families:
  - `clan_battles:seasons:metadata`
  - `clan_battles:player:{account_id}`
  - `clan_battles:summary:v2:{clan_id}`
- TTLs:
  - metadata: `86400`
  - player season stats: `21600`
  - clan summary: `3600`

Read paths:

1. player clan-battle seasons endpoint
2. clan-battle seasons endpoint
3. landing and clan-member serializers that enrich rows with clan-battle summary state

Invalidation paths:

1. `_invalidate_clan_battle_summary_cache(clan_id)`
2. clan-data and clan-member refreshes call summary invalidation
3. empty-cache miss triggers background refresh for summary data

Assessment:

This is one of the stronger cache designs in the repo.

The good:

1. metadata and per-player season rows are separated from aggregated clan summaries.
2. clan summary cache has explicit invalidation when clan data or roster changes.
3. cold misses enqueue background work instead of blocking the request path.
4. empty cache values are intentionally used to avoid repeated expensive misses.

Potential concern:

The per-player clan-battle cache has a six-hour TTL with no targeted invalidation when only one player refreshes. That is not necessarily wrong because clan-battle history is slow-moving, but it does mean player-specific freshness can lag even after upstream updates.

Additional concern:

The expensive clan-summary rebuild path has no cache-side cold-miss lock. On concurrent misses, multiple requests can trigger the same aggregation work before Redis is repopulated.

Verdict:

- still being used: yes
- TTL fit: mostly good
- shape fit: strong

### 5. Ranked seasons metadata cache

- key: `ranked:seasons:metadata`
- code: `server/warships/data.py`
- TTL: `86400`
- read path: ranked data enrichment
- invalidation: TTL only
- active today: yes

Assessment:

This is low-risk metadata caching. The 24-hour TTL is appropriate.

Verdict:

- still being used: yes
- TTL fit: good
- shape fit: good

### 6. Landing activity attrition cache

- key: `landing:activity_attrition:v1`
- code: `server/warships/data.py`
- TTL: `900` seconds
- read path: landing attrition endpoint and chart
- invalidation: TTL only

Assessment:

This cache protects a medium-cost cohort query. A 15-minute TTL is a reasonable compromise because the underlying signal moves slowly relative to user interaction.

Verdict:

- still being used: yes
- TTL fit: good
- shape fit: good

### 7. Response-layer caches

- code: `server/warships/views.py`
- keys:
  - `db:stats`
  - `agentic:trace_dashboard:v2`
- TTLs:
  - `db:stats`: `300` seconds
  - trace dashboard: `15` seconds

Assessment:

Both are tiny cache wrappers around cheap or moderate summary builders.

`db:stats` is straightforward and fine.

The trace dashboard cache is intentionally very short. That keeps `/trace` responsive without making the run-log scan happen on every page refresh.

Verdict:

- still being used: yes
- TTL fit: good
- shape fit: good

### 8. Task coordination caches

- code: `server/warships/tasks.py`
- key families:
  - `warships:tasks:{task_name}:{resource_id}:lock`
  - `warships:tasks:update_ranked_data_dispatch:{player_id}`
  - `warships:tasks:update_player_clan_battle_data_dispatch:{player_id}`
  - `warships:tasks:update_ranked_data_dispatch:cooldown`
  - `warships:tasks:update_player_clan_battle_data_dispatch:cooldown`
  - `warships:tasks:crawl_all_clans:lock`
  - `warships:tasks:crawl_all_clans:heartbeat`
  - `warships:tasks:incremental_ranked_data:lock`
- TTLs:
  - per-resource refresh lock: `900` seconds
  - dispatch dedupe: `900` seconds
  - broker failure cooldown: `60` seconds
  - clan crawl lock and heartbeat: `28800` seconds
  - ranked incremental lock: `21600` seconds

Assessment:

These are active, necessary caches used as distributed coordination primitives rather than response caches.

Most of them are well matched to the task model.

The crawl heartbeat contract has now been made explicit at the task boundary:

1. `crawl_all_clans_task()` owns the heartbeat writer and passes it into the crawler as an explicit callback.
2. the crawler refreshes heartbeat progress throughout pagination and member processing.
3. the task now clears both the lock and heartbeat key on exit.

That keeps the watchdog semantics aligned with the actual long-running crawl behavior instead of relying on an implicit cache-side helper.

Verdict:

- still being used: yes
- TTL fit: good
- shape fit: good after making heartbeat ownership explicit

### 9. In-process session cache

- code: `server/warships/api/client.py`
- surface: `@lru_cache(maxsize=1)` around `_get_session()`
- TTL: process lifetime
- purpose: reuse the same configured `requests.Session`

Assessment:

This is a real cache, but it is not a Django/Redis cache. It reduces connection setup overhead and preserves retry adapter configuration. It is still being used and is fine.

Verdict:

- still being used: yes
- TTL fit: good
- shape fit: good

### 10. Client-side application caching

Findings:

1. the current Next app mostly uses plain `fetch(...)` from client components
2. no meaningful use of `unstable_cache`, `revalidateTag`, route-segment caching policy, or explicit fetch cache options appears in app source
3. app-level caching is therefore almost entirely server-side today

Assessment:

This is not a bug by itself. It just means backend caches are doing the real work, and client mounts will still call the API whenever components mount or remount.

Verdict:

- still being used: not really at the application level
- TTL fit: not applicable
- shape fit: intentionally absent

## Findings

### Finding 1: Clan crawl heartbeat ownership needed to be made explicit

Severity: high

The crawler already refreshed heartbeat opportunistically, but it did so through an implicit helper inside `warships.clan_crawl`. That made the watchdog’s safety story too dependent on an implementation detail instead of an explicit task contract.

Why it matters:

1. watchdog behavior was harder to reason about than it needed to be
2. the task boundary did not clearly own heartbeat freshness
3. future crawler refactors could accidentally weaken watchdog guarantees

### Finding 2: Landing cache versioning was only partially normalized

Severity: medium

The landing cache family mixed versioned active reads with legacy unversioned invalidation keys, and the player-list family used delete-many invalidation instead of a cheap namespace bump.

### Finding 3: Landing player invalidation was correct but brute-force

Severity: medium

Every player refresh invalidated all `landing:players:v8:{mode}:{limit}` variants rather than using a cheaper namespace-version approach. With the current scale this was acceptable, but it was not the cleanest shape.

### Finding 4: Aggregate analytics caches are TTL-only and intentionally stale within the hour

Severity: medium

The distribution and correlation caches do not respond to write events. That is likely the right tradeoff for heavy analytics, but the spec should state it explicitly: these charts are approximate-to-recent, not instantly fresh.

### Finding 5: Some empty cache values are semantically overloaded

Severity: medium

In the clan-battle path, an empty list can mean either:

1. no cached summary exists yet and background refresh was just enqueued, or
2. the clan genuinely has no roster-driven result to return.

The API partly mitigates this with `X-Clan-Battles-Pending` on first-miss responses, but the underlying cache-value strategy still deserves to be documented as a tradeoff.

### Finding 6: Ship metadata cache is effective but stores model instances

Severity: low

The `ship:{id}` cache is warm and active, and the 24-hour TTL is sensible. The main tradeoff is storing a full Django model instance in Redis instead of a compact immutable payload.

### Finding 7: Most TTLs are directionally correct

Severity: low

Outside the crawl heartbeat issue, TTLs mostly line up with the freshness needs of each surface:

1. landing payloads: very short and interactive
2. analytics aggregates: hourly
3. season metadata: daily
4. task dedupe and locks: minutes to hours depending on task type

## Recommendations

### Executed near-term changes

1. Made the clan-crawl heartbeat an explicit task-owned callback and cleared heartbeat state on task exit.
2. Normalized landing cache keys so the active read keys and invalidation keys are versioned consistently.
3. Moved landing player cache invalidation from delete-many fan-out to a small namespace-version strategy.

### Later, if caching becomes a bottleneck

1. Consider explicit warmers for the hourly analytics caches after large backfills or repair jobs.
2. Consider serializing ship cache payloads as dicts rather than pickled model instances.
3. Consider documenting freshness expectations on analytics endpoints if users start reading them as real-time signals.

## Bottom Line

The project is absolutely using caching in meaningful ways today.

The cache system is not neglected. Most of it is in workable shape, and several areas are thoughtfully versioned or invalidated. The near-term cache cleanup tranche addressed the crawler heartbeat ownership issue and the landing cache consistency gaps; the remaining tradeoffs are mostly around freshness expectations and optional future stampede protection.

## Proposed Follow-Up Artifacts

1. QA review of this spec
2. operator runbook for auditing and validating cache behavior in battlestats
