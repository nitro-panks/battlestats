# Spec: Player Route HAR Performance Findings And Next Steps

_Captured: 2026-03-19_

_Status: HAR-based diagnosis and execution plan_

## Goal

Capture the concrete performance findings from the Firefox HAR for the player route and define the next engineering steps that should reduce time-to-usable-content, backend load, and duplicate client work.

This spec is specific to the observed navigation flow for `http://localhost:3001/player/LemmingTheGreat` and the downstream API traffic recorded in `network-archive.har`.

## Artifact And Method

- Route inspected: `GET /player/LemmingTheGreat`
- Frontend origin: `http://localhost:3001`
- Backend origin: `http://localhost:8888`
- Source artifact: `network-archive.har`
- Request count in capture: `35`
- Status mix in capture:
  - `23` successful `200` responses
  - `2` redirects `301`
  - `1` successful `201` analytics write
  - `1` failing `500` response
  - `8` status `0` aborted or cancelled requests

Method:

- Extract each request URL, start offset, total duration, and HAR timing phases.
- Group by exact URL and normalized path to identify duplicate work.
- Separate frontend shell latency from backend API fan-out.
- Use HAR `wait` time as the primary indicator of server-side processing delay.

## Executive Summary

- The page shell is fast. The initial Next request to `/player/LemmingTheGreat` completed in about `91 ms`.
- The route then becomes backend-bound almost immediately. The player payload request to `/api/player/LemmingTheGreat/` took `6030 ms` on one copy and `2941 ms` on another, indicating duplicate fetch work before the page stabilizes.
- Roughly `9.2 s` after navigation start, the page launches a second burst of heavy API requests in parallel. Several of those calls take `5 s` to `10 s`, and one fails after `33.3 s`.
- The capture is dominated by server wait time, not transfer or connection overhead:
  - cumulative HAR `wait`: about `107.9 s`
  - cumulative HAR `receive`: about `9 ms`
  - DNS and connect overhead are negligible
- The route is doing too much repeated work:
  - repeated player detail fetches
  - repeated clan roster fetches
  - repeated clan and ranked panel fetches
  - repeated type-data requests with avoidable `301` redirects
- The most urgent defect is `GET /api/fetch/player_correlation/ranked_wr_battles/1018847016/`, which took `33279 ms` and returned `500`.

Interpretation:

- This route is not network-limited.
- This route is not primarily blocked by the initial Next render.
- The dominant problem is a late client-triggered API fan-out hitting several expensive backend reads at once, with duplicate or retried requests amplifying the cost.

## Primary Findings

### 1. Initial route HTML is not the bottleneck

The first request in the HAR is the Next route payload on port `3001`:

- `GET /player/LemmingTheGreat?_rsc=...`: `91 ms`

That is fast enough that the user-visible delay must be attributed to subsequent client-side API activity rather than route shell generation.

### 2. Player detail is fetched twice and remains slow

The route issues two copies of:

- `GET /api/player/LemmingTheGreat/`

Observed timings:

- `6030 ms`
- `2941 ms`

Aggregate cost:

- `8971 ms` total across `2` requests

Interpretation:

- There is duplicate work before downstream panels even begin their heavy fetches.
- If this is a development-only double-fetch, it still obscures profiling and increases backend pressure locally.
- If it can happen in production, it is a direct UX and infrastructure problem.

### 3. The page launches a late heavy fan-out burst

Most expensive follow-on requests begin around `9157 ms` to `9265 ms` after navigation start:

- `GET /api/fetch/clan_data/1000044008:active`: `7727 ms`
- `GET /api/fetch/player_clan_battle_seasons/1018847016/`: `8030 ms`
- `GET /api/fetch/randoms_data/1018847016/?all=true`: `10001 ms`
- `GET /api/fetch/ranked_data/1018847016/`: `7277 ms`
- `GET /api/fetch/player_correlation/tier_type/1018847016/`: `6046 ms`
- `GET /api/fetch/clan_members/1000044008/`: `7546 ms`
- `GET /api/fetch/player_correlation/ranked_wr_battles/1018847016/`: `33279 ms`, status `500`

Interpretation:

- The route is not progressively settling after player detail arrives.
- Instead, it appears to defer many expensive sections until after the initial player fetch, then fire them concurrently.
- That creates both a slow page and concentrated backend contention.

### 4. The worst endpoint is a failing long-tail blocker

The slowest request in the capture is:

- `GET /api/fetch/player_correlation/ranked_wr_battles/1018847016/`

Observed behavior:

- duration `33279 ms`
- response status `500`
- HAR wait share `97.1%`

Interpretation:

- This endpoint is not merely slow; it is doing expensive server-side work and still failing.
- It should be treated as a correctness bug first and a performance bug second.
- Any route section depending on this endpoint should degrade safely rather than holding the page in an unstable state.

### 5. Duplicate fetches materially amplify the cost

Exact-URL duplicates observed in the HAR:

- `GET /api/fetch/clan_members/1000044008/`: `8` requests, `22477 ms` total
- `GET /api/fetch/clan_data/1000044008:active`: `3` requests, `14471 ms` total
- `GET /api/fetch/ranked_data/1018847016/`: `2` requests, `12806 ms` total
- `GET /api/fetch/randoms_data/1018847016/?all=true`: `2` requests, `12251 ms` total
- `GET /api/fetch/type_data/1018847016/`: `2` redirects without slash, `12796 ms` total
- `GET /api/fetch/type_data/1018847016/`: `2` successful slash-form requests, `9827 ms` total
- `GET /api/player/LemmingTheGreat/`: `2` requests, `8971 ms` total
- `GET /api/fetch/player_clan_battle_seasons/1018847016/`: `2` requests, `9312 ms` total

Interpretation:

- The page is paying for multiple copies of several expensive panels.
- `clan_members` is the clearest repeated-work hotspot.
- There is likely overlap between polling, retry behavior, component remounts, and redirect-triggered duplicate paths.

### 6. Redirect waste is present and easy to remove

The HAR shows two slow redirects for the no-slash type endpoint:

- `GET /api/fetch/type_data/1018847016` -> `301`
- `GET /api/fetch/type_data/1018847016` -> `301`

Timings:

- `6048 ms`
- `6748 ms`

Relevant repository note:

- `clan_data` is intentionally the unusual no-trailing-slash route shape, but adjacent `fetch` endpoints are expected to use their canonical path form.

Interpretation:

- This is low-effort waste.
- The client should always request the canonical slash form for `type_data`.
- Fixing this does not solve the route, but it removes several seconds of needless latency from the observed session.

### 7. Server processing time dominates the capture

Aggregate HAR timing totals:

- `wait`: `107910 ms`
- `blocked`: `36223 ms`
- `receive`: `9 ms`
- `connect`: `11 ms`
- `dns`: `3 ms`

Interpretation:

- Transfer size is not the primary issue in this session.
- Connection setup is not the primary issue.
- The backend is spending significant time computing or waiting on data, and the browser is then accumulating blocked time due to the volume of concurrent work against the same origin.

### 8. The route keeps doing work long after first paint

`clan_members`, `clan_data`, and `type_data` continue to fire again well past the initial post-render burst:

- additional `clan_members` requests start at about `19.4 s`, `27.3 s`, `31.7 s`, `37.1 s`, `40.5 s`, and `44.2 s`
- additional `clan_data` and `type_data` calls also appear later in the session

Interpretation:

- This route does not converge quickly.
- Some combination of polling, hydration follow-up, repeated mounts, and failed/aborted fetch recovery is extending the tail.
- Even if above-the-fold content is acceptable, the page remains operationally expensive for too long.

## Root-Cause Hypotheses To Validate In Code

1. `PlayerRouteView` or adjacent client entry code is issuing duplicate player fetches during development mounts or suspense re-entry.
2. The player detail page is mounting several heavy data sections concurrently instead of sequencing them by priority.
3. Clan roster polling is retriggering more often than intended, likely due to hydration-pending state or component remount churn.
4. Some panel fetch hooks are not deduping in-flight requests by cache key.
5. The ranked-vs-battles correlation endpoint is missing either a warm cache, a fast-path for sparse data, or a failure guard around an expensive query path.
6. The client is mixing canonical and redirected endpoint URLs for `type_data`.

## Recommended Next Steps

### Priority 0: stop the failing long-tail request

Target:

- `GET /api/fetch/player_correlation/ranked_wr_battles/<player_id>/`

Actions:

- reproduce the `500` from the backend directly
- capture the exception, query path, and data shape for player `1018847016`
- add a fast failure mode or empty-state fallback if the data is incomplete or too expensive to derive synchronously
- ensure the player page does not block or thrash when this panel fails

Success criteria:

- endpoint returns either a valid payload or a cheap explicit empty-state response
- request latency is reduced from `33.3 s` to an acceptable steady-state budget
- the player route no longer emits a failed request for this panel in the HAR

### Priority 1: eliminate duplicate player and panel fetches

Targets:

- `GET /api/player/<name>/`
- `GET /api/fetch/clan_members/<clan_id>/`
- `GET /api/fetch/clan_data/<clan_id>:active`
- `GET /api/fetch/ranked_data/<player_id>/`
- `GET /api/fetch/randoms_data/<player_id>/?all=true`
- `GET /api/fetch/player_clan_battle_seasons/<player_id>/`

Actions:

- audit the player-route client components and shared hooks for effect-driven duplicate fetches
- verify whether dev-mode Strict Mode behavior is the only source or whether route state changes can retrigger the same request in normal use
- add in-flight dedupe or stable request caching at the hook level where missing
- verify clan-member polling terminates once hydration settles and does not restart on harmless rerenders

Success criteria:

- one canonical request per panel on initial page load unless a deliberate refresh is required
- `clan_members` count collapses from `8` requests to the minimum needed for hydration UX
- duplicate `api/player` fetches disappear from the HAR

### Priority 2: remove canonical-path mistakes and redirect overhead

Target:

- `GET /api/fetch/type_data/<player_id>` without trailing slash

Actions:

- update the client to always request `/api/fetch/type_data/<player_id>/`
- audit other fetch helpers for inconsistent trailing-slash behavior

Success criteria:

- zero `301` responses for `type_data` in the next HAR

### Priority 3: reduce initial fan-out pressure

Actions:

- rank player-detail sections by user importance above the fold
- keep the header and summary lane highest priority
- defer heavy charts and secondary analytics until viewport entry, idle time, or explicit user intent
- consider serializing some non-critical data loads after the first heavy response completes instead of launching all of them together

Candidate sections to reevaluate first:

- ranked-vs-battles correlation heatmap
- randoms data section
- ranked data section
- clan roster and any follow-up hydration loop
- clan battle seasons section when not immediately visible

Success criteria:

- the post-player-detail burst contains fewer simultaneous `5 s+` requests
- browser blocked time drops materially in the next HAR
- the page reaches a stable usable state sooner even if some secondary panels remain deferred

### Priority 4: remeasure after each fix with the same artifact type

Actions:

- regenerate a HAR for the same route after each meaningful change set
- compare request count, duplicate count, cumulative wait time, and the longest single request
- preserve the artifact alongside this spec for follow-up review

Success criteria:

- fewer total requests than the current `35`
- no `500` responses
- no avoidable `301` responses
- materially lower cumulative wait time
- materially lower request duplication on `clan_members` and `api/player`

## Suggested Implementation Order

1. Trace and fix the `ranked_wr_battles` `500` path on the server.
2. Audit player-route client hooks for duplicate fetches and polling loops.
3. Normalize `type_data` and any similar endpoints to canonical URLs.
4. Defer or sequence secondary panels to reduce the `~9.2 s` fan-out burst.
5. Capture a new HAR and compare against this baseline.

## Open Questions

- Are the duplicate `api/player` requests fully explained by local development behavior, or can production reproduce them?
- Is `clan_members` repetition driven by a deliberate hydration poll, a retry loop, or repeated mounts of the same shared hook?
- Does the `ranked_wr_battles` endpoint fail only for this player fixture, or is it structurally unstable on sparse or partially hydrated ranked data?
- Which panel requests are truly required for first useful paint, and which can move behind idle or interaction without harming the route’s value?

## Exit Criteria For This Spec

This spec is complete when a follow-up HAR for the same player route shows all of the following:

- no failing requests
- no avoidable redirects
- one canonical player-detail request
- a sharply reduced `clan_members` request count
- no single API request above `10 s`
- a visibly smaller post-render request burst
