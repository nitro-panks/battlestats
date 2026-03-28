# Runbook: Bulk Entity Cache Loader

**Created**: 2026-03-28
**Status**: Implemented

## Purpose

Pre-load serialized player and clan detail API responses into Redis so most page loads skip the DB entirely. Low overhead: single DB query per entity type, no WG API calls, no Celery task fan-out.

## How it works

1. **Single DB query** fetches top N players (by player_score, then pvp_ratio) with `select_related('clan', 'explorer_summary')`
2. **Serializes** each through `PlayerSerializer.to_representation()` — the same serializer the API uses
3. **Writes all payloads** via `cache.set_many()` (pipelined on Redis backends)
4. Same pattern for clans (top by cached_clan_wr, min 10K battles)
5. **PlayerViewSet.retrieve()** checks cache before running the full `get_object()` + serializer path

### Cache key patterns
- Players: `player:detail:v1:{player_id}` (TTL: 24h default)
- Clans: `clan:detail:v1:{clan_id}` (TTL: 24h default)

### Invalidation
- `update_player_data()` calls `invalidate_player_detail_cache()` after saving
- `update_clan_data()` calls `invalidate_clan_detail_cache()` after saving
- Any data refresh from WG API automatically clears the cached response

## Relationship to other warmers

| Task | Purpose | Frequency | Entities | API calls? |
|------|---------|-----------|----------|------------|
| **bulk-entity-cache-loader** | Pre-load serialized responses into Redis | Every 12h | 500 players, 100 clans | **No** — DB reads only |
| hot-entity-cache-warmer | Refresh source data from WG API | Every 30 min | 20 players, 10 clans | Yes |
| landing-page-warmer | Refresh landing page payloads | Every 55 min | 25 per mode | Yes (indirectly) |
| warm_landing_best_entity_caches | Refresh source data for best cohort | On-demand | 25 players, 25 clans | Yes |

The bulk loader reads what's already in the DB. The other warmers keep the DB data fresh from the WG API. They're complementary.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `BULK_CACHE_PLAYER_LIMIT` | 500 | Max players to cache |
| `BULK_CACHE_CLAN_LIMIT` | 100 | Max clans to cache |
| `BULK_CACHE_PLAYER_TTL` | 86400 (24h) | Redis TTL for player payloads |
| `BULK_CACHE_CLAN_TTL` | 86400 (24h) | Redis TTL for clan payloads |
| `BULK_CACHE_LOAD_HOURS` | 12 | Periodic task interval (hours) |

## Running manually

```bash
# Via management command
docker compose exec server python manage.py bulk_load_entity_caches
docker compose exec server python manage.py bulk_load_entity_caches --player-limit 1000 --clan-limit 200

# Via Celery task
docker compose exec server python -c "from warships.tasks import bulk_load_entity_caches_task; bulk_load_entity_caches_task.delay()"

# On production
ssh root@battlestats.online
cd /opt/battlestats-server/current/server
/opt/battlestats-server/venv/bin/python manage.py bulk_load_entity_caches
```

## Observability

Cache hit/miss is visible via `X-Player-Cache` response header:
```bash
curl -sI https://battlestats.online/api/player/lil_boots | grep X-Player-Cache
# X-Player-Cache: hit
```

Bulk load results are logged:
```
INFO bulk_load_player_cache: loaded 500 player detail payloads into cache (limit=500)
INFO bulk_load_clan_cache: loaded 87 clan detail payloads into cache (limit=100)
```

## Estimated Redis memory

- ~1-2 KB per player payload (scalar fields + explorer summary)
- ~500 bytes per clan payload
- 500 players + 100 clans ~ 1 MB total Redis usage
- Large JSON fields (battles_json, ranked_json, etc.) are included in the serialized payload but represent the bulk of the size

## Code locations

- Bulk load functions: `server/warships/data.py` — `bulk_load_player_cache()`, `bulk_load_clan_cache()`, `bulk_load_entity_caches()`
- Cache helpers: `server/warships/data.py` — `get_cached_player_detail()`, `invalidate_player_detail_cache()`, etc.
- View cache check: `server/warships/views.py` — `PlayerViewSet.retrieve()`
- Task: `server/warships/tasks.py` — `bulk_load_entity_caches_task`
- Periodic schedule: `server/warships/signals.py` — `bulk-entity-cache-loader`
- Management command: `server/warships/management/commands/bulk_load_entity_caches.py`
