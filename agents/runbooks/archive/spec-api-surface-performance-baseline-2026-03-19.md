# Spec: API Surface Performance Baseline

_Captured: 2026-03-19_

_Status: Baseline measurement after Docker restart_

_Update: targeted follow-up fixes implemented and remeasured on 2026-03-19_

_Update: player-detail synchronous read work trimmed further on 2026-03-19; targeted behavior tests and smoke passed, but no new canonical latency table has been recorded yet for that branch-specific pass_

## Goal

Establish a current performance baseline for the public Battlestats API surface after a fresh Docker restart, then identify the highest-value backend optimization targets.

This spec is measurement-first. It does not propose broad refactors without a demonstrated hotspot.

## Environment And Method

- Stack restart: `bounce` alias (`docker compose down && docker compose up -d`)
- Readiness gate: wait for `GET /api/stats/` to return `200`
- Validation pass: `docker compose exec -T server python scripts/smoke_test_site_endpoints.py --timeout 60`
- Raw artifact: `logs/api_benchmark_2026-03-19.json`
- Probe set:
  - 27 smoke-script cases from `server/scripts/smoke_test_site_endpoints.py`
  - 6 additional public routes not covered by the smoke script
- Measurement rule:
  - `cold ms` = first request after restart/readiness
  - `warm median ms` = median of the next 3 requests
- Caveats:
  - Landing warmers run shortly after startup, so `cold` here means first request after the app became ready, not a pre-warm zero-cache state for every landing route.
  - Four low-volume public routes initially returned `429` because they were benchmarked at the end of a dense request burst under DRF anon throttling (`120/minute`). Their canonical timings below come from isolated reruns after a 65-second cooldown.
  - One endpoint probe was duplicated in the raw JSON (`/api/fetch/player_correlation/tier_type/1014916452/`). This spec deduplicates it and uses the corrected isolated rerun timing.

## Executive Summary

- Unique public method+path probes measured: `33`
- Cold median across the API surface: `60.95 ms`
- Warm median across the API surface: `14.16 ms`
- Warm latency distribution:
  - `11/33` endpoints were at or below `5 ms`
  - `25/33` endpoints were at or below `25 ms`
  - `29/33` endpoints were at or below `100 ms`
  - `31/33` endpoints were at or below `250 ms`
- Cold-start outliers are concentrated in a small set of cache-building or sync-refresh paths.
- Steady-state outliers are concentrated in four places:
  - empty ranked-history reads
  - missing-player 404 lookups
  - player detail
  - clan member roster reads

## Follow-Up Result

The two highest-priority fixes from this spec were implemented and verified on the live stack:

- `fetch_ranked_data()` now treats `ranked_json=[]` as a valid fresh cache hit when `ranked_updated_at` is fresh.
- `PlayerViewSet.get_object()` now stores a short negative cache for missing player-name lookups.

Post-fix targeted measurements after `bounce`:

| Endpoint                                    | Before cold ms | Before warm median ms | After cold ms | After warm median ms | Delta                                          |
| ------------------------------------------- | -------------: | --------------------: | ------------: | -------------------: | ---------------------------------------------- |
| `GET /api/fetch/ranked_data/1001712582/`    |         807.32 |                767.31 |       1560.60 |                13.70 | Warm path fixed; cold still pays first refresh |
| `GET /api/player/PlayerThatWillNeverExist/` |         755.28 |                615.28 |        790.65 |               141.78 | Warm 404 path improved by ~`77%`               |

Interpretation:

- The ranked-empty fix solved the real bug. The first request after restart still performs the initial refresh and persists `[]`, but subsequent reads are now cheap.
- The negative-cache fix materially reduced repeated 404 misses, though the route is still slower than ideal and remains a candidate for a deeper pass.
- A later player-detail pass removed two synchronous read-time repairs for existing local players:
  - existing clanless players now serve stored payloads and enqueue refresh instead of force-refreshing inline
  - players with `efficiency_json=None` but already-populated `actual_kdr` now enqueue repair instead of forcing a synchronous player refresh

## Primary Findings

### 1. Empty ranked reads were the clearest steady-state bug

`GET /api/fetch/ranked_data/1001712582/` returned an empty `[]` payload in `767.31 ms` warm median.

This is not a payload-size issue. The response body is 2 bytes.

Root cause is in `fetch_ranked_data()` in `server/warships/data.py`:

- fresh cache reuse is gated by `if player.ranked_json and player.ranked_updated_at ...`
- an empty list is falsey
- therefore a player with known empty ranked history always falls through to `update_ranked_data()`
- the read path keeps redoing ranked maintenance work even when the correct answer is still `[]`

This was the strongest optimization candidate because it was a correctness-shaped performance bug, not just an expensive query.

Status: fixed and remeasured. Warm latency dropped to `13.70 ms`.

### 2. Missing-player 404s were paying remote lookup cost on every request

`GET /api/player/PlayerThatWillNeverExist/` returned `404` in `615.28 ms` warm median.

Root cause is in `PlayerViewSet.get_object()` in `server/warships/views.py`:

- local `name__iexact` lookup misses
- code then calls `_fetch_player_id_by_name()` against the upstream WG API
- only after upstream returns no match does the endpoint emit `404`

That made repeated misses expensive and network-coupled. A short negative cache for name misses removed most of that repeated cost.

Status: fixed and remeasured. Warm latency dropped to `141.78 ms`, but the first miss still pays upstream lookup cost.

### 3. Player detail still has meaningful synchronous work left

`GET /api/player/Shinn000/` measured `148.75 ms` warm median with a `211821` byte payload.

The detail read path currently combines:

- conditional player repair
- conditional clan repair
- possible battle-data repair
- efficiency repair
- `last_lookup` writeback
- recent-landing invalidation
- explorer summary refresh checks
- async task enqueue checks
- clan battle refresh checks

The endpoint is functional, but it is not a pure read path. Any additional synchronous repair logic will keep this route expensive.

Status: partially improved after the baseline pass, but not yet re-benchmarked with a refreshed canonical latency table.

### 4. Clan roster reads are acceptable but still heavy

`GET /api/fetch/clan_members/1000055908/` measured `115.49 ms` warm median.

That is not catastrophic, but it remains one of the slowest steady-state reads. The payload is moderate (`25557` bytes), so the cost is largely server-side work rather than transfer size.

### 5. Landing clans is dominated by payload size and cold cache build cost

`GET /api/landing/clans/` measured:

- cold: `1604.94 ms`
- warm: `96.17 ms`
- payload size: `5239760` bytes

Warm latency is still under 100 ms, so the main issue is not CPU after cache fill. The endpoint is mostly paying for building and serving a very large response.

### 6. Several cold outliers are warming costs, not steady-state costs

These routes were cold-slow but warm-fast:

- `randoms_maraxus1`: `1933.59 ms` cold, `15.50 ms` warm
- `player_summary_shinn000`: `1915.59 ms` cold, `41.20 ms` warm
- `activity_fourgate`: `1104.28 ms` cold, `13.12 ms` warm
- `wr_distribution`: `707.95 ms` cold, `4.14 ms` warm
- `player_distribution_survival_rate`: `795.46 ms` cold, `3.94 ms` warm

Those are valid restart-time concerns, but they do not justify the same priority as the ranked-empty and 404 issues because their steady-state behavior is already acceptable.

## Recommended Optimization Order

1. Reduce synchronous repair work on player detail GETs. Prefer durable background repair over repeated inline mutations where the payload can still be served from existing data.
2. Revisit clan roster shaping only after player detail is tightened.
3. Treat landing-clans as a payload-budget problem rather than a query-latency problem.
4. If missing-player traffic remains hot, consider a stronger local miss path than short TTL negative caching alone.

## Targeted Improvement Ideas

### Ranked empty-path fix

Change the ranked cache freshness gate from truthiness to nullability.

Current shape:

- fresh cache reuse only happens when `player.ranked_json` is truthy

Desired shape:

- reuse cached ranked data when `player.ranked_json is not None` and `ranked_updated_at` is fresh
- let `[]` be a valid cached result

Measured impact:

- collapsed the `ranked_empty_kevik70` warm path from `767.31 ms` to `13.70 ms`

### Missing-player 404 negative cache

Add a short cache key keyed by normalized player name for upstream negative results.

Suggested behavior:

- on upstream no-match, cache a negative result for 5 to 15 minutes
- skip `_fetch_player_id_by_name()` while the negative cache is fresh

Measured impact:

- repeated typo or bot traffic against nonexistent player names dropped from `615.28 ms` warm median to `141.78 ms`
- first misses still pay upstream lookup cost, so this is a partial but worthwhile improvement

### Player detail read-path tightening

Focus areas:

- avoid repeated `refresh_from_db()` churn unless the preceding sync mutation is mandatory for the current response
- prefer enqueueing repair over synchronous refresh when current durable data is already renderable
- separate read concerns from maintenance concerns where possible

Expected impact:

- should reduce steady-state player-detail latency and lower tail variability

### Landing clans payload budget

The route is already cache-backed. The remaining issue is that the response is about `5.24 MB`.

Possible levers:

- trim fields that are not used at initial paint
- shrink default result count
- split “featured” vs “full” clan surfaces if the client does not need the full blob immediately

Expected impact:

- mainly improves restart-time cache fill, transfer size, and browser/network cost

### Player suggestions search path

`GET /api/landing/player-suggestions/?q=sh` measured `65.66 ms` warm median.

This route uses `name__icontains` ordering logic in `player_name_suggestions()`. If suggestions become a hot path, the next targeted DB improvement is consistent with prior repo notes:

- add trigram support for substring search
- preserve prefix-first ranking, but let the index carry the search predicate

## Benchmarks By API Area

These tables are the original baseline probe set unless a row explicitly says it was remeasured later.

### Landing / Discovery

| Endpoint                                    | Cold ms | Warm median ms | Status | Notes                                       |
| ------------------------------------------- | ------: | -------------: | -----: | ------------------------------------------- |
| `GET /api/landing/clans/`                   | 1604.94 |          96.17 |    200 | Very large payload (~5.24 MB)               |
| `GET /api/landing/players/`                 |    4.21 |           3.98 |    200 |                                             |
| `GET /api/landing/recent/`                  |    2.76 |           2.96 |    200 |                                             |
| `GET /api/landing/recent-clans/`            |   28.15 |           3.21 |    200 |                                             |
| `GET /api/landing/player-suggestions/?q=sh` |   66.40 |          65.66 |    200 | Search path worth indexing if this gets hot |
| `GET /api/landing/activity-attrition/`      |   60.95 |           4.00 |    200 |                                             |

### Player Router

| Endpoint                                    | Cold ms | Warm median ms | Status | Notes                                                                      |
| ------------------------------------------- | ------: | -------------: | -----: | -------------------------------------------------------------------------- |
| `GET /api/player/Shinn000/`                 |  175.53 |         148.75 |    200 | Large payload (~212 KB); sync maintenance-heavy                            |
| `GET /api/player/PlayerThatWillNeverExist/` |  790.65 |         141.78 |    404 | Remeasured after negative-cache fix; first miss still pays upstream lookup |

### Player Fetch Endpoints

| Endpoint                                                | Cold ms | Warm median ms | Status | Notes                                                           |
| ------------------------------------------------------- | ------: | -------------: | -----: | --------------------------------------------------------------- |
| `GET /api/fetch/player_summary/1000270433/`             | 1915.59 |          41.20 |    200 | Cold path cascades activity + ranked + battles work             |
| `GET /api/fetch/randoms_data/1000954803/`               | 1933.59 |          15.50 |    200 |                                                                 |
| `GET /api/fetch/tier_data/1000663088/`                  |   15.25 |          14.41 |    200 |                                                                 |
| `GET /api/fetch/type_data/1014916452/`                  |   14.20 |          12.59 |    200 |                                                                 |
| `GET /api/fetch/activity_data/1014916452/`              | 1104.28 |          13.12 |    200 |                                                                 |
| `GET /api/fetch/ranked_data/1001243015/`                |  797.44 |          20.97 |    200 |                                                                 |
| `GET /api/fetch/ranked_data/1001712582/`                | 1560.60 |          13.70 |    200 | Remeasured after empty-cache fix; cold still pays first refresh |
| `GET /api/fetch/player_clan_battle_seasons/1000270433/` |   25.58 |          21.00 |    200 |                                                                 |

### Clan Endpoints

| Endpoint                                         | Cold ms | Warm median ms | Status | Notes |
| ------------------------------------------------ | ------: | -------------: | -----: | ----- |
| `GET /api/clan/1000055908/`                      |   13.45 |          12.96 |    200 |       |
| `GET /api/fetch/clan_data/1000055908:active`     |  154.11 |          94.80 |    200 |       |
| `GET /api/fetch/clan_members/1000055908/`        |  139.72 |         115.49 |    200 |       |
| `GET /api/fetch/clan_battle_seasons/1000055908/` |   13.82 |          14.16 |    200 |       |
| `GET /api/fetch/clan_data/1000055908:bogus`      |    2.35 |           2.38 |    400 |       |

### Explorer / Stats / Trace

| Endpoint                                                      | Cold ms | Warm median ms | Status | Notes                                  |
| ------------------------------------------------------------- | ------: | -------------: | -----: | -------------------------------------- |
| `GET /api/ship/1/`                                            |   11.04 |          10.30 |    200 |                                        |
| `GET /api/players/explorer/?page_size=5&min_pvp_battles=1000` |  231.17 |           2.76 |    200 |                                        |
| `GET /api/stats/`                                             |    2.96 |           2.57 |    200 |                                        |
| `GET /api/agentic/traces/`                                    |   13.34 |          13.34 |    200 | Isolated rerun after throttle cooldown |

### Population Distributions / Correlations

| Endpoint                                                  | Cold ms | Warm median ms | Status | Notes                                  |
| --------------------------------------------------------- | ------: | -------------: | -----: | -------------------------------------- |
| `GET /api/fetch/wr_distribution/`                         |  707.95 |           4.14 |    200 |                                        |
| `GET /api/fetch/player_distribution/win_rate/`            |    3.66 |           3.99 |    200 |                                        |
| `GET /api/fetch/player_distribution/survival_rate/`       |  795.46 |           3.94 |    200 |                                        |
| `GET /api/fetch/player_distribution/battles_played/`      |  429.30 |           3.35 |    200 |                                        |
| `GET /api/fetch/player_correlation/win_rate_survival/`    |  571.83 |          23.20 |    200 |                                        |
| `GET /api/fetch/player_correlation/tier_type/1014916452/` |   20.74 |          20.74 |    200 | Isolated rerun after throttle cooldown |

### Analytics

| Endpoint                                                                                      | Cold ms | Warm median ms | Status | Notes                                  |
| --------------------------------------------------------------------------------------------- | ------: | -------------: | -----: | -------------------------------------- |
| `GET /api/analytics/top-entities/?entity_type=player&period=7d&metric=views_deduped&limit=10` |   21.56 |          21.56 |    200 | Isolated rerun after throttle cooldown |
| `POST /api/analytics/entity-view/`                                                            |   20.36 |          20.36 |    201 | Isolated rerun after throttle cooldown |

## Validation Result

After restart and after the benchmark pass, the API smoke suite passed with the repo's existing script:

```bash
docker compose exec -T server python scripts/smoke_test_site_endpoints.py --timeout 60
```

Result: all smoke cases passed.

## Recommended Next Implementation Pass

1. Record a fresh benchmark for the trimmed player-detail branches using synthetic local fixtures, not only `Shinn000`.
2. Re-benchmark `GET /api/player/Shinn000/` and `GET /api/fetch/clan_members/1000055908/` after any further read-path reductions.
3. Leave landing-clans for a separate payload-budget pass.
