# Runbook: API Performance Pass

_Last updated: 2026-03-18_

_Status: Active operational reference_

## Purpose

Document the March 18, 2026 API performance pass:

1. what was measured
2. which hotspots were real in the live stack
3. which optimization shipped
4. what residual performance risks remain
5. what to do next if latency becomes a product issue again

## Scope

This pass focused on the public API surfaces exercised by the endpoint smoke suite and the landing page:

1. landing endpoints
2. player summary and activity endpoints
3. player explorer
4. aggregate population charts
5. stats endpoint

## Measurement Method

Primary measurement methods used during this pass:

1. repeated live HTTP timing against `http://localhost:8888`
2. direct Django shell timing for explorer queryset count and first-page fetch
3. smoke suite verification through `scripts/smoke_test_site_endpoints.py`
4. server log inspection for repeated synchronous refresh work and worker timeout behavior

Representative commands from the workspace root:

```bash
cd /home/august/code/archive/battlestats && \
python - <<'PY'
import time, urllib.request
base='http://localhost:8888'
for path in [
    '/api/fetch/player_summary/1000270433/',
    '/api/fetch/activity_data/1014916452/',
    '/api/players/explorer/?page_size=5&min_pvp_battles=1000',
    '/api/landing/clans/',
]:
    start=time.perf_counter()
    with urllib.request.urlopen(base + path, timeout=120) as response:
        response.read()
    print(path, round(time.perf_counter() - start, 3))
PY
```

```bash
cd /home/august/code/archive/battlestats && \
docker compose exec -T server python manage.py shell -c "from warships.data import _build_player_explorer_queryset, _build_player_explorer_ordering; import time; qs=_build_player_explorer_queryset(min_pvp_battles=1000, apply_ranked_filter=False); start=time.perf_counter(); count=qs.count(); mid=time.perf_counter(); page=list(qs.order_by(*_build_player_explorer_ordering('player_score','desc'))[:5]); end=time.perf_counter(); print({'count': count, 'count_seconds': round(mid-start, 3), 'page_seconds': round(end-mid, 3)})"
```

```bash
cd /home/august/code/archive/battlestats && \
curl -sD - -o /dev/null -H 'Accept-Encoding: gzip' 'http://localhost:8888/api/landing/clans/' | grep -i 'content-encoding\|vary\|content-length'
```

```bash
cd /home/august/code/archive/battlestats && \
for run in 1 2; do \
    echo "=== explorer request ${run} ==="; \
    curl -sD - -o /dev/null 'http://localhost:8888/api/players/explorer/?q=ExplorerCacheCaptain&page_size=5' | grep -i 'x-players-explorer-cache\|x-players-explorer-cache-ttl-seconds'; \
done
```

## Findings

### 1. The strongest live hotspot was repeated synchronous activity refresh work

Observed behavior before the change:

1. `fetch_activity_data()` treated an all-zero 29-day activity series as empty
2. requests for inactive players repeatedly called `update_snapshot_data()` and `update_activity_data()` even when `activity_updated_at` was fresh
3. this inflated both `/api/fetch/activity_data/<player_id>/` and `/api/fetch/player_summary/<player_id>/`

For this runbook, "fresh" means `activity_updated_at` is still inside the existing 15-minute freshness window used by `fetch_activity_data()`.

Log signature before the fix:

1. `Activity data refresh required (stale=False, empty=True, cumulative_spike=False)`
2. immediate follow-on account, clan membership, and efficiency refresh work on the same request path

### 2. Explorer was no longer a database-query problem

The current explorer path had already been improved before this pass, but it remained worth validating.

Measured Django-side explorer costs for `min_pvp_battles=1000`:

1. count over about 98,538 rows: about 0.05 seconds
2. first-page ordered fetch of 5 rows: about 0.14 seconds
3. `build_player_summary()` for the 5 returned rows: effectively 0 seconds in the shell sample

Conclusion:

1. explorer is no longer dominated by whole-population Python materialization
2. remaining explorer latency is mostly first-hit process/cache warmup, not a bad queryset shape

### 3. Landing clans is operationally acceptable but payload-heavy

Observed characteristics:

1. warm response times are reasonable
2. first-hit latency after restart is noticeably higher
3. response size remains large, around 5.2 MB in the logs

Conclusion:

1. the current cache strategy is working
2. payload size, not backend compute, is the remaining concern on this route

## Change Shipped

### Activity cache handling

Updated [server/warships/data.py](server/warships/data.py):

1. `fetch_activity_data()` no longer treats a fresh all-zero activity series as a cache miss
2. the function still refreshes when activity data is structurally missing, stale, or shows the known cumulative-spike anomaly

Regression coverage added in [server/warships/tests/test_data.py](server/warships/tests/test_data.py):

1. fresh zero-activity cache stays cached
2. cumulative-spike cache still refreshes

### Landing response compression

Updated [server/battlestats/settings.py](server/battlestats/settings.py):

1. enabled `django.middleware.gzip.GZipMiddleware`
2. kept the landing clans API shape stable instead of trimming client-required fields

Operational result:

1. `landing_clans` now responds with `Content-Encoding: gzip` when requested
2. a live validation sample showed compressed response length around 1,353,546 bytes, down from the prior uncompressed payload logged at about 5.2 MB

### Explorer response caching

Updated [server/warships/views.py](server/warships/views.py):

1. added a parameter-order-independent cache key for `/api/players/explorer/` derived from the validated request parameter set
2. cached serialized explorer responses for 60 seconds
3. added `X-Players-Explorer-Cache` and `X-Players-Explorer-Cache-TTL-Seconds` headers for observability

Regression coverage added in [server/warships/tests/test_views.py](server/warships/tests/test_views.py):

1. repeated explorer calls reuse the cached payload
2. landing clans advertises gzip for large JSON responses

### Backend coverage tooling

Updated [server/requirements.txt](server/requirements.txt) and [server/Pipfile](server/Pipfile):

1. added `coverage`
2. confirmed the active venv can run backend coverage reports directly

## Measured Results

### Before this pass

Representative live timings before the activity fix:

1. `player_summary`: about 1.055 seconds
2. `activity_data`: about 0.865 seconds
3. `players_explorer`: about 4.329 seconds in one representative run, later validated as an already-fixed warmup-sensitive path

### After the activity fix and restart

Representative live timings:

1. `player_summary`: about 1.489 seconds on first hit, then about 0.039 to 0.042 seconds steady-state
2. `activity_data`: about 0.014 to 0.015 seconds steady-state
3. `players_explorer`: about 0.876 seconds on first hit, then about 0.234 to 0.240 seconds steady-state
4. `landing_clans`: about 1.487 seconds on first hit, then about 0.115 to 0.121 seconds steady-state
5. `stats`: about 0.046 seconds on first hit, then about 0.003 seconds steady-state

### After the explorer response cache and gzip follow-up

Operational validation showed:

1. `landing_clans` returns `Content-Encoding: gzip` with `Vary: Accept, origin, Cookie, Accept-Encoding`
2. explorer returns `X-Players-Explorer-Cache: miss` on the first request and `hit` on the second request for the same normalized query
3. the full smoke suite still passes after both changes

Interpretation:

1. the shipped change materially removed repeated synchronous refresh cost for inactive-player activity and summary endpoints
2. the remaining first-hit latency on some routes is dominated by warmup and cache state, not obvious application inefficiency

## Validation

Validation completed during this pass:

1. `manage.py test --keepdb warships.tests.test_data warships.tests.test_views`
2. full smoke suite via `docker compose exec -T server python scripts/smoke_test_site_endpoints.py`

Smoke status after the change:

1. all endpoint checks passed

## Recommendations

### Priority 1: keep the activity fix in place and watch for similar false cache misses

What to watch:

1. request paths that treat valid zero-value denormalized data as absent
2. log lines that claim a refresh is required while freshness timestamps are recent

Reason:

1. this class of bug creates hidden synchronous upstream work and is easy to miss in correctness-only testing

### Priority 2: add short-lived response caching for explorer if traffic rises

Status:

1. implemented at 60 seconds for normalized explorer query responses

Next refinement if needed:

1. add targeted invalidation when denormalized explorer summaries are refreshed in bulk
2. watch cache hit rate before increasing TTL further

### Priority 3: reduce landing clans payload size

Status:

1. field-level trimming was intentionally not shipped because the current landing client consumes the existing shape
2. transport-level reduction was shipped through gzip compression

Potential next options:

1. trim unused fields from the payload if the client does not render them
2. split heavyweight detail into a follow-up request if the landing page only needs a preview row
3. verify compression is enabled end to end on the deployed stack

Reason:

1. warm compute is fine, but transfer size is still large enough to affect first-hit latency and client parse cost

### Priority 4: add a cold-start warm command to performance checks

Suggested operational check:

1. after `bounce`, exercise `landing_clans`, `landing_players`, `player_summary`, and `players_explorer`

Reason:

1. several endpoints are meaningfully slower on first hit than steady-state
2. this is acceptable operationally, but it should be measured deliberately rather than inferred from random browsing

### Priority 5: install Python coverage before the next backend performance pass

Status:

1. completed in the active backend venv

Current report sample from the touched files:

1. `battlestats/settings.py`: 87%
2. `warships/views.py`: 86%
3. `warships/data.py`: 78%

Reason to keep using it:

1. backend tests passed and quantitative coverage reporting now works in the active backend venv
2. future optimization passes should show both latency impact and coverage impact together

## Execution Sequence

Use this sequence to re-run the March 18 pass from the workspace root.

### 1. Regression validation

```bash
cd /home/august/code/archive/battlestats/server && \
/home/august/code/archive/battlestats/.venv/bin/python manage.py test --keepdb warships.tests.test_data warships.tests.test_views warships.tests.test_landing
```

### 2. Cold-start and warm-state timing

```bash
cd /home/august/code/archive/battlestats && bounce
```

Wait until the server is ready before measuring:

```bash
cd /home/august/code/archive/battlestats && \
until curl -sf 'http://localhost:8888/api/stats/' >/dev/null; do sleep 1; done
```

Then measure the first-hit and steady-state timings from the host:

```bash
cd /home/august/code/archive/battlestats && \
python - <<'PY'
import time, urllib.request
base='http://localhost:8888'
paths=[
    '/api/landing/clans/',
    '/api/fetch/player_summary/1000270433/',
    '/api/fetch/activity_data/1014916452/',
    '/api/players/explorer/?page_size=5&min_pvp_battles=1000',
]
for label in ('cold', 'warm'):
    print(f'[{label}]')
    for path in paths:
        start=time.perf_counter()
        with urllib.request.urlopen(base + path, timeout=120) as response:
            response.read()
        print(path, round(time.perf_counter() - start, 3))
PY
```

### 3. Header-level validation

Verify landing compression:

```bash
cd /home/august/code/archive/battlestats && \
curl -sD - -o /dev/null -H 'Accept-Encoding: gzip' 'http://localhost:8888/api/landing/clans/' | grep -i 'content-encoding\|vary\|content-length'
```

Verify explorer cache miss then hit:

```bash
cd /home/august/code/archive/battlestats && \
for run in 1 2; do \
  echo "=== explorer request ${run} ==="; \
    curl -sD - -o /dev/null 'http://localhost:8888/api/players/explorer/?q=ExplorerCacheCaptain&page_size=5' | grep -i 'x-players-explorer-cache\|x-players-explorer-cache-ttl-seconds'; \
done
```

### 4. Coverage report for touched backend files

```bash
cd /home/august/code/archive/battlestats/server && \
/home/august/code/archive/battlestats/.venv/bin/coverage run --source=warships,battlestats manage.py test --keepdb warships.tests.test_views warships.tests.test_data warships.tests.test_landing && \
/home/august/code/archive/battlestats/.venv/bin/coverage report -m battlestats/settings.py warships/views.py warships/data.py
```

### 5. Smoke suite

```bash
cd /home/august/code/archive/battlestats && \
docker compose exec -T server python scripts/smoke_test_site_endpoints.py
```

## Operator Checklist

When investigating future API latency complaints:

1. time the endpoint twice before drawing conclusions about steady-state cost
2. inspect logs for synchronous refresh work on the request path
3. distinguish cold-start latency from true hot-path inefficiency
4. prefer denormalized or cached read paths over recomputation on public GETs
5. verify smoke still passes after any optimization
