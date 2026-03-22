# Spec: Cache-First Lazy Refresh Policy

_Captured: 2026-03-19_

_Status: active policy; implemented tranche through 2026-03-20_

## Goal

Make stale-but-fast data the default product behavior across Battlestats.

The application should prefer serving cached data over recomputing or calling the WoWS API on the request path. Data in this product is not time-sensitive enough to justify blocking users on freshness work.

## Product Decision

Adopt the following global policy:

1. The default cache TTL is 12 hours.
2. If any cache entry already exists for a request, serve it.
3. Never call the WoWS API on behalf of a read request when cached data already exists.
4. Never recompute any derived payload that is already cached just because its TTL expired.
5. Only recompute derived payloads after new underlying source data has been stored locally.
6. Refresh work happens lazily in the background, never synchronously on the user request path.

This is a stronger contract than generic stale-while-revalidate. The cache is not merely a performance hint. It is the primary read model.

## Current Implemented Scope

The current repo implements the following parts of this policy:

1. landing player and clan published surfaces now default to a 12-hour cache TTL
2. public random landing players and clans read from the published cache path rather than consuming queue-pop reads on every request
3. landing cache invalidation now marks cache families dirty and schedules republish work instead of deleting the currently served payloads immediately
4. player derived chart payloads and explorer summaries now remain in place until newer source timestamps exist locally
5. clan battle season reads now prefer cached results and queue background refreshes instead of synchronously rebuilding stale empty caches

This leaves the document active as the durable policy source while the archived tranche specs capture the narrower planning history that led here.

## Motivation

Current behavior still allows several expensive paths to do one of the following:

1. bypass an existing cache because it is considered stale
2. rebuild a derived payload because a cache entry expired
3. synchronously fetch upstream data while handling a user request
4. invalidate broad cache families aggressively enough that the product falls back into cold-cache behavior too often

Those behaviors are reasonable for highly time-sensitive data, but they are the wrong fit here. The user preference is explicit: stale is acceptable, latency is not.

## Definitions

### Source data

Data fetched from WoWS or persisted local tables that directly mirror upstream state.

Examples:

1. `Player` core fields from `account/info/`
2. `Player.battles_json`
3. `Player.ranked_json`
4. `Clan` core fields and roster membership
5. snapshot rows used to derive activity

### Derived data

Any payload computed from source data already stored locally.

Examples:

1. `tiers_json`
2. `type_json`
3. `randoms_json`
4. `activity_json`
5. landing best-player lists
6. landing best-clan lists
7. population distributions and correlations
8. clan plot payloads

### Freshness event

A write of newer source data into local storage.

Examples:

1. a player row updated from WoWS
2. a new `battles_json` payload saved
3. a clan roster refresh persisted
4. a new snapshot row recorded

Only a freshness event may authorize recomputation of dependent derived payloads.

## Required Read-Path Contract

For every API endpoint and page-data fetch:

1. If a cached response or cached model-backed payload exists, return it immediately.
2. If that payload is older than 12 hours, attach metadata indicating staleness if useful, but still return it.
3. Do not synchronously fetch upstream data because the payload is stale.
4. Do not synchronously rebuild derived data because the payload is stale.
5. At most, enqueue background refresh work for the underlying source data.

The user-visible experience should be:

1. fast response now
2. fresher data later
3. no request-thread waiting for WoWS or expensive local recomputation

## Required Recompute Contract

This is the policy change added after the initial draft direction.

If a derived payload already exists in cache or in a model-backed JSON field:

1. do not recompute it merely because it is old
2. do not recompute it merely because a read request touched it
3. do not recompute it in the background unless new source data has first been saved locally

Allowed recompute trigger:

1. source data changed locally and the write path marks the dependent derived payload dirty

Disallowed recompute triggers:

1. TTL expiration alone
2. cache miss for a secondary response wrapper when model-backed derived data still exists
3. repeated page views of a hot entity
4. scheduled maintenance jobs that have no evidence of new source data

This means Battlestats should treat derived caches as versioned views over source data, not as disposable TTL artifacts.

## Cache Classes And Policy

### Class A: source-of-truth local mirrors

Examples:

1. player core row
2. clan core row
3. battles JSON
4. ranked JSON
5. clan roster
6. snapshots

Policy:

1. default TTL target is 12 hours when exposed via cached responses
2. if local source data exists, serve it even when stale
3. if older than 12 hours, enqueue upstream refresh in the background
4. never block the read request on that upstream refresh

### Class B: local derived model fields

Examples:

1. `tiers_json`
2. `type_json`
3. `randoms_json`
4. `activity_json`

Policy:

1. if the field exists, serve it regardless of age
2. do not recompute it on read
3. recompute it only after the upstream-dependent source field changed
4. if the source field did not change, leave the derived field untouched

### Class C: response caches over stable local builders

Examples:

1. landing best clans
2. landing best players
3. landing recent players
4. landing recent clans
5. clan plot responses
6. population distributions
7. correlations

Policy:

1. response-cache TTL defaults to 12 hours
2. if a response cache entry exists, serve it
3. if it expires but the underlying local materialization still exists unchanged, restore or keep serving the last known payload rather than rebuilding on read
4. rebuild only after upstream-backed source data or tracked local inputs changed

### Class D: queue-backed random surfaces

Examples:

1. landing random clans
2. landing random players

Policy:

1. keep serving the most recent generated queue payload until a replacement queue payload is prepared
2. queue rotation is a background refresh concern, not a read concern
3. readers should never force queue regeneration synchronously

## Invalidation Rules

Broad invalidation is the main thing that can sabotage this policy.

Required changes in behavior:

1. stop deleting cached payloads simply because they are older than a short TTL
2. stop bumping entire landing namespaces on routine player or clan updates unless the changed entity actually affects that surface
3. prefer per-entity or per-surface dirty flags over namespace-wide invalidation
4. prefer marking caches stale over removing them outright

Operational rule:

1. stale caches remain readable
2. dirty caches remain readable until a replacement is written
3. delete a cache entry only when its shape is invalid or its source identity changed enough that the old payload is misleading

## Dirty-Flag Model

To enforce "no recompute without new data", each derived family should move to a dirty-flag or source-version model.

Recommended contract:

1. every source write records an updated timestamp or version token
2. every derived payload stores the source version it was built from
3. a derived payload is eligible for rebuild only when `source_version > derived_source_version`
4. if `source_version == derived_source_version`, keep serving the derived payload regardless of age

Examples:

1. `battles_json_updated_at` advances, so `tiers_json`, `type_json`, and `randoms_json` become dirty
2. `ranked_json` does not change, so ranked heatmap payload stays as-is even after 12 hours
3. clan roster timestamp advances, so clan plot payload becomes dirty
4. no new snapshot rows arrive, so `activity_json` is not regenerated

## Endpoint-Level Expectations

### Player summary and player page

1. if player row exists, return it
2. if battles, ranked, activity, or explorer summary caches exist, return them
3. if any source payload is older than 12 hours, queue refresh tasks only for the source payloads
4. derived payloads wait until those source refresh tasks actually write new data

### Tier, type, randoms, activity, and other player charts

1. if derived JSON exists, return it
2. if derived JSON is missing but source data exists locally, compute once and persist
3. if derived JSON exists and source data is unchanged, never recompute on read
4. if source data is stale, queue source refresh and keep serving current derived JSON

### Clan members and clan plot

1. if current roster rows exist, return them
2. if clan plot payload exists, return it
3. if roster or clan metadata is older than 12 hours, queue refresh in background
4. do not rebuild plot from unchanged roster data just because the plot cache aged out

### Landing best and recent surfaces

1. cached best/recent surfaces should be treated as durable published views
2. serve the last published payload until a replacement payload is prepared
3. only republish after relevant player or clan source rows actually changed

### Landing random surfaces

1. keep the last queue payload live until the next queue payload is ready
2. do not clear random queues on read-path expiry
3. do not regenerate synchronously because the queue payload is old

## Default TTL Change

The global default timeout should move from 60 seconds to 12 hours for cache families that do not specify a stronger reason to differ.

Recommended default:

1. `CACHE_DEFAULT_TIMEOUT = 60 * 60 * 12`

Exceptions should remain explicit and rare.

Likely exceptions:

1. short-lived task locks and dispatch dedupe keys
2. trace and operational dashboards
3. rate-limit and coordination keys

Non-exceptions:

1. landing surfaces
2. player detail response wrappers
3. clan detail response wrappers
4. analytics payloads
5. best/recent/random published landing payloads

## Background Refresh Rules

Background refresh jobs may do two things:

1. fetch newer source data from WoWS when the local source is older than 12 hours
2. recompute derived payloads whose source version changed

Background refresh jobs may not do these things:

1. bypass a valid local source payload during a read request
2. recompute derived payloads whose source version did not change
3. delete the currently served payload before the replacement payload is ready

Required sequencing:

1. fetch source data
2. compare with previous local version
3. if unchanged, stop there
4. if changed, write source data
5. mark dependent derived payloads dirty
6. recompute dirty derived payloads
7. atomically publish replacement response caches

## Migration Implications For Current Code

The current repo already has pieces of this model, but several areas need to move further:

1. `server/warships/landing.py`
   - raise current 1-hour landing TTLs to 12 hours by default
   - stop treating landing payload expiry as a reason to rebuild on read
   - preserve last published best/random payloads until replacements are ready
2. `server/warships/data.py`
   - replace broad landing invalidation with dirty marking or selective republish
   - stop synchronous recompute fallbacks for known entities and known charts
3. `server/warships/views.py`
   - ensure read paths always prefer cached payloads over refresh work
   - keep cache metadata headers, but treat them as observability, not permission to block
4. hot-entity and landing warmers
   - republish only when source rows changed
   - skip rebuilds when the newly fetched source data is identical

## Acceptance Criteria

This policy is complete when all of the following are true:

1. default cache TTL for product data is 12 hours unless explicitly exempted
2. no read path calls WoWS when a cached payload already exists locally
3. no derived payload is recomputed solely because of TTL expiry or page traffic
4. derived payloads recompute only after newer source data has been written locally
5. stale caches stay readable until replacement payloads are published
6. broad cache-family invalidation is replaced by dirty marking or targeted republish flows

## Recommended Follow-On Work

1. implement a source-version model for player, clan, and landing derived payloads
2. convert current landing invalidation helpers to publish/dirty helpers
3. update the detail stale-while-revalidate tranche to match this stricter contract
4. add regression tests proving that repeat reads of stale cached entities do not trigger WoWS fetches or derived recomputes
