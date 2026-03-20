# Spec: Detail Cache Stale-While-Revalidate

_Captured: 2026-03-19_

_Status: implementation tranche_

## Goal

Reduce player and clan detail latency by making repeat reads serve cached model-backed data immediately while scheduling background refresh work instead of recomputing on the request thread.

This tranche is specifically about already-known players and clans.

## Problem Statement

The repo now has several good cache foundations:

- player detail is largely model-backed
- clan detail is largely model-backed
- landing surfaces are warmed in the background
- hot player/clan warming now exists for top entities
- some chart fetchers already return stale cache and enqueue refresh

But there are still slow paths on repeat reads:

- `GET /api/fetch/clan_members/<clan_id>/` still performs synchronous clan or roster hydration when the clan record is incomplete
- `GET /api/fetch/clan_data/<clan_id>:<filter>` still builds the clan plot synchronously on cache miss
- some chart endpoints still rebuild their JSON synchronously when the derived cache is missing
- `GET /api/fetch/player_summary/<player_id>/` still bootstraps multiple caches synchronously when the summary inputs are empty

The result is that the site feels slower than it should even though most of the underlying data is relatively static.

## Desired Behavior

For already-known players and clans:

- serve whatever cached model-backed or response-cached data already exists
- if the cached data is stale or incomplete, enqueue background refresh work
- avoid blocking the response on WG upstream calls or expensive cache rebuilds

More concretely:

- repeat reads should prefer stale data over a slow fresh recompute
- missing derived chart JSON should return `[]` or current partial data and trigger async refresh
- hot landing entities should be proactively warmed so their detail pages are already primed

## Non-Goals

This tranche does not change the API contract for first-ever lookups of brand-new entities.

Out of scope:

- returning `202 Accepted` or a new pending payload shape for player/clan detail
- redesigning the frontend around explicit hydration states for every chart
- changing the semantics of the first unknown player lookup by name

Reason:

- first lookup of a never-seen entity still needs some bootstrap path, and changing that contract is a larger product/API decision than this caching tranche

## Targeted Request Paths

### Player summary

Current issue:

- `fetch_player_summary()` still synchronously bootstraps battles, activity, ranked, and explorer summary when all caches are absent

Required change:

- return the best currently available summary immediately
- queue background refreshes for battles, activity, ranked, and player detail when the summary inputs are absent or stale
- only recompute explorer summary synchronously when enough local cached inputs already exist

### Tier / type / randoms charts

Current issue:

- chart fetchers still call synchronous local rebuilds when their derived JSON is absent
- some paths synchronously fetch battle data first when `battles_json` is empty

Required change:

- if battle data is missing, queue battle refresh and return current derived JSON or `[]`
- if battle data is present but derived JSON is missing, queue the derived refresh and return `[]`
- if battle data is stale, queue battle refresh and let that battle refresh repopulate the dependent chart caches

### Activity chart

Current issue:

- missing activity cache still triggers synchronous snapshot and activity rebuild

Required change:

- return cached activity rows when present
- if the activity cache is missing or stale, queue snapshot refresh and let snapshot refresh rebuild activity downstream
- return `[]` on cold activity cache instead of blocking

### Clan members endpoint

Current issue:

- `clan_members()` still calls `update_clan_data()` and `update_clan_members()` synchronously when the clan record or roster is incomplete

Required change:

- always return currently stored roster rows immediately
- if clan metadata is incomplete, queue `update_clan_data_task`
- if roster membership is incomplete, queue `update_clan_members_task`
- preserve the current ranked/efficiency/clan-battle hydration headers and member annotations for whatever rows are currently available

### Clan plot endpoint

Current issue:

- `fetch_clan_plot_data()` still builds the plot synchronously on miss and may synchronously hydrate clan or roster first

Required change:

- if a cached plot exists, return it and queue stale/incomplete refreshes in the background
- if no cached plot exists and clan data is incomplete, queue refresh tasks and return `[]`
- only build and cache a plot synchronously when the local clan and roster data are already complete enough to do so without upstream work

## Refresh Cascade Rules

To avoid racing derived refreshes against missing base data:

- battle refresh should republish `tiers_json`, `type_json`, and `randoms_json`
- snapshot refresh should republish `activity_json`

This lets read paths enqueue the base refresh task and rely on that task to repopulate dependent caches.

## Hot Entity Warming

The existing hot-entity warmer becomes the proactive lane for detail performance.

Required guarantees:

- top visited players are warmed
- top recently viewed clans are warmed
- top best-surface players/clans are warmed
- the warmer must populate detail-adjacent caches, not just top-level model rows

That means warming:

- player detail core row
- battle JSON
- activity JSON
- tier/type/randoms derived charts
- ranked JSON
- player clan battle summary rows
- clan detail core row
- clan members
- clan plot payloads
- clan battle season summary cache

## Tests Required

Required regression coverage:

- `fetch_player_summary()` returns a partial cached summary and queues refresh instead of bootstrapping synchronously when caches are empty
- `fetch_tier_data()` returns `[]` and queues battle refresh on cold cache
- `fetch_type_data()` returns `[]` and queues battle or derived refresh as appropriate
- `fetch_activity_data()` returns `[]` and queues snapshot refresh on cold cache
- `fetch_clan_plot_data()` returns `[]` and queues clan/roster refresh on incomplete local state
- `clan_members()` returns current rows and queues clan/roster refresh instead of calling synchronous refresh helpers
- hot entity warmer task remains scheduled and lock-protected

## Acceptance Criteria

This tranche is complete when all of the following are true:

- repeat reads of known players and clans no longer trigger synchronous upstream refresh work on the request thread
- missing derived player chart caches return quickly and refill asynchronously
- clan roster and clan plot requests return quickly even when the local clan cache is incomplete
- the hot entity warmer keeps the hottest detail surfaces primed in advance
- targeted tests cover the stale-while-revalidate behavior and pass
