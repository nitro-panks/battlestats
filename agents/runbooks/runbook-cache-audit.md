# Runbook: Cache Audit and Operations

_Last updated: 2026-03-15_

_Status: Active operational reference_

## Purpose

Operate and validate the battlestats caching layer with an emphasis on:

1. what cache families exist
2. whether they are warm and active
3. whether their TTL and invalidation behavior still match the product surface they support
4. known correctness caveats that operators should not ignore

Use this runbook together with `agents/work-items/archive/cache-analysis-spec.md` and `agents/archive/reviews/qa-cache-analysis-spec-review.md`.

## Primary Cache Families

### Interactive response caches

These exist to keep frequently viewed pages and endpoints responsive:

1. landing clans: `landing:clans:v3`
2. landing recent clans: `landing:recent_clans:last_lookup:v2`
3. landing recent players: `landing:recent_players:last_lookup:v4`
4. landing player lists: `landing:players:v9:n{namespace}:{mode}:{limit}`
5. landing activity attrition: `landing:activity_attrition:v1`
6. DB stats: `db:stats`
7. trace dashboard: `agentic:trace_dashboard:v2`

### Aggregate analytics caches

These protect expensive population-wide chart builders:

1. `players:distribution:v2:{metric}`
2. `players:correlation:v2:{metric}`
3. `players:correlation:v2:ranked_wr_battles:v6`

### Clan-battle caches

1. `clan_battles:seasons:metadata`
2. `clan_battles:player:{account_id}`
3. `clan_battles:summary:v2:{clan_id}`

### Ship metadata cache

1. `ship:{ship_id}`

### Task coordination caches

1. `warships:tasks:{task_name}:{resource_id}:lock`
2. `warships:tasks:update_ranked_data_dispatch:{player_id}`
3. `warships:tasks:update_player_clan_battle_data_dispatch:{player_id}`
4. `warships:tasks:update_ranked_data_dispatch:cooldown`
5. `warships:tasks:update_player_clan_battle_data_dispatch:cooldown`
6. `warships:tasks:crawl_all_clans:lock`
7. `warships:tasks:crawl_all_clans:heartbeat`
8. `warships:tasks:incremental_ranked_data:lock`

## Current TTL Expectations

### 60 seconds

1. landing clans
2. landing recent clans
3. landing recent players
4. landing player lists
5. global default timeout when no explicit timeout is passed

### 15 seconds

1. trace dashboard

### 5 minutes

1. DB stats

### 15 minutes

1. landing activity attrition
2. resource task locks
3. ranked refresh dispatch dedupe
4. clan-battle refresh dispatch dedupe

### 1 hour

1. player distributions
2. player correlations
3. clan-battle summary cache

### 6 hours

1. player clan-battle season stats cache
2. incremental ranked lock

### 24 hours

1. ranked seasons metadata
2. clan-battle seasons metadata
3. ship metadata cache

### 8 hours

1. clan crawl lock
2. clan crawl heartbeat key timeout

## Known Operational Caveats

### 1. Clan crawl heartbeat is task-owned and progress-based

What happens today:

1. `crawl_all_clans_task()` writes the heartbeat at task start
2. the task passes an explicit heartbeat callback into the crawler
3. the crawler refreshes heartbeat during pagination and member processing
4. the task clears both the crawl lock and heartbeat key on exit

Operational consequence:

1. a stale heartbeat is a stronger signal than before, but task logs are still the source of truth before manual intervention
2. if a crawl looks wedged, inspect logs before clearing keys or forcing a restart

### 2. Clan-battle empty responses are ambiguous

An empty clan-battle response can mean:

1. no cached summary yet and a refresh was just enqueued, or
2. there is genuinely nothing to show yet

When checking API behavior, include response headers in your reasoning, especially `X-Clan-Battles-Pending`.

### 3. Analytics caches are intentionally approximate within the hour

The population distributions and correlation charts do not actively invalidate on writes. If a large repair or import just ran, allow for up to one hour of lag unless you explicitly clear or warm those keys.

## Validation Commands

### Confirm the active backend

```bash
cd /home/august/code/archive/battlestats && \
docker compose exec -T server python manage.py shell -c "from django.conf import settings; print(settings.CACHES['default'])"
```

Expected result:

1. Redis in the running stack
2. LocMemCache in test processes

### Sample live key families

```bash
cd /home/august/code/archive/battlestats && \
docker compose exec -T server python manage.py shell -c "from django.core.cache import cache; import json; client = cache._cache.get_client(write=False); patterns=['landing:*','players:*','clan_battles:*','ranked:*','ship:*','warships:tasks:*','db:stats','agentic:*']; results={}; [results.__setitem__(pattern, sum(1 for _ in client.scan_iter(match='*:1:'+pattern, count=1000))) for pattern in patterns]; print(json.dumps(results, indent=2, sort_keys=True))"
```

Use this to distinguish dormant cache families from short-TTL families that simply were not warm at the instant of inspection.

### Exercise landing caches

```bash
cd /home/august/code/archive/battlestats && \
docker compose exec -T server python manage.py shell -c "from warships.landing import get_landing_clans_payload, get_landing_recent_clans_payload, get_landing_players_payload, get_landing_recent_players_payload; print({'clans': len(get_landing_clans_payload()), 'recent_clans': len(get_landing_recent_clans_payload()), 'players_random': len(get_landing_players_payload('random', 40)), 'players_best': len(get_landing_players_payload('best', 40)), 'recent_players': len(get_landing_recent_players_payload())})"
```

### Exercise analytics caches

```bash
cd /home/august/code/archive/battlestats && \
docker compose exec -T server python manage.py shell -c "from warships.data import fetch_player_population_distribution, fetch_player_wr_survival_correlation, fetch_player_ranked_wr_battles_correlation; print(fetch_player_population_distribution('win_rate')['tracked_population']); print(fetch_player_wr_survival_correlation()['tracked_population'])"
```

### Exercise trace cache

```bash
cd /home/august/code/archive/battlestats && \
docker compose exec -T server python manage.py shell -c "from warships.agentic.dashboard import get_agentic_trace_dashboard; print(get_agentic_trace_dashboard(limit=3)['diagnostics'])"
```

### Focused regression suite

```bash
cd /home/august/code/archive/battlestats/server && \
DB_ENGINE=sqlite3 \
LANGGRAPH_CHECKPOINT_POSTGRES_URL='' \
DJANGO_SETTINGS_MODULE=battlestats.settings \
DJANGO_SECRET_KEY=test-secret \
/home/august/code/archive/battlestats/.venv/bin/python -m pytest \
  warships/tests/test_cache.py \
  warships/tests/test_views.py -q
```

## What Good Looks Like

### Backend shape

1. Redis is active in the running stack
2. short-lived landing keys appear after the associated endpoints are exercised
3. ship and clan-battle keys remain warm under normal use

### TTL behavior

1. landing surfaces expire quickly and repopulate without operator action
2. analytics keys stay stable through repeated chart loads inside the hour
3. metadata keys survive repeated reads over a day-scale window

### Invalidation behavior

1. player refreshes clear landing player caches
2. clan lookups and clan refreshes clear landing clan caches
3. clan data or roster changes clear cached clan-battle summary rows

## Operator Guidance

### When not to intervene

1. landing keys missing from Redis during a point-in-time scan is normal if those endpoints have not been hit recently
2. analytics keys persisting for close to an hour is expected
3. trace dashboard keys may disappear quickly because their TTL is only 15 seconds

### When to investigate

1. repeated duplicate clan crawl scheduling while a crawl is visibly still running
2. clan-battle summaries staying empty long after a populated roster exists
3. landing responses staying stale after player or clan refreshes
4. analytics endpoints rebuilding on every request instead of holding keys for an hour

## Cleanup Notes

Landing player invalidation now uses a namespace bump instead of deleting every `{mode, limit}` variant synchronously. Superseded player-list keys are expected to age out on their normal 60-second TTL.

## Companion Artifacts

1. `agents/work-items/archive/cache-analysis-spec.md`
2. `agents/archive/reviews/qa-cache-analysis-spec-review.md`
