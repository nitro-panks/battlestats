# Runbook: Pinned Player Cache Warming

**Created**: 2026-03-27
**Status**: Implemented and deployed

## Purpose

Keep designated "VIP" player profiles always warm in cache so they never experience cold-start latency. The site creator's account (`lil_boots`) is the primary use case.

## Mechanism

Pinned players are injected into the existing **hot entity cache warmer** — the same periodic task that keeps the most-visited players warm. No new task, no new schedule, no new infrastructure.

### How it works

1. **Env var**: `HOT_ENTITY_PINNED_PLAYER_NAMES` (comma-separated player names, default: `lil_boots`)
2. **Resolution**: `_get_pinned_player_ids()` in `data.py` resolves names → player IDs via DB lookup
3. **Injection**: Pinned IDs are prepended to the hot player candidate list in `_get_hot_player_ids()`, before visit-analytics and recency candidates
4. **Warming**: `warm_player_entity_caches()` refreshes all data layers for each player:
   - Player data (personal info, account status)
   - Battle data (PvP stats, battle JSON)
   - Activity data (snapshots + activity chart)
   - Tier/type/randoms derived data
   - Ranked data
   - Explorer summary (efficiency rank, player score)
   - Clan battle seasons
5. **Schedule**: Every 30 minutes via `hot-entity-cache-warmer` periodic task (Celery Beat)
6. **Staleness checks**: Force-refresh is off by default — only stale fields are re-fetched from WG API, keeping API call volume low

### Harmonization with existing warming

| Warmer | Schedule | What it warms | Pinned player overlap |
|--------|----------|---------------|-----------------------|
| **Hot entity cache warmer** | Every 30 min | Top-visited + recent + top-scored players/clans | **Pinned players injected here** |
| **Landing page warmer** | Every 55 min | Landing page payloads (best, random, sigma, popular, clans) | Pinned player may appear in "best" if they qualify |
| **Landing best entity warmer** | On-demand (dispatched after landing warm) | Detail caches for landing "best" players/clans | Same — if pinned player is in best list |
| **Incremental player refresh** | 2x daily (5am, 3pm) | All known players by staleness tier | Pinned player refreshed here too (hot tier = 12h stale) |
| **Clan battle summary warmer** | Every 30 min | Configured clan IDs | Independent — clan-level, not player-level |

### Restart / deploy behavior

- Celery Beat re-registers all periodic tasks via `post_migrate` signal in `signals.py`
- Hot entity warmer starts its first run within 30 minutes of worker startup
- Redis cache survives service restarts (persistent across deploys)
- If Redis is flushed, pinned player data will be re-warmed within 30 minutes automatically

## Configuration

### Env var location
- **Bootstrap template**: `server/deploy/bootstrap_droplet.sh` (line in `/etc/battlestats-server.env` block)
- **Live server**: `/etc/battlestats-server.env`

### Adding or removing pinned players
```bash
# On the live server, edit the env file:
ssh root@battlestats.online
vi /etc/battlestats-server.env
# Change: HOT_ENTITY_PINNED_PLAYER_NAMES=lil_boots,another_player

# Restart the task runner to pick up the new env:
systemctl restart battlestats-task-runner
```

No code change or deploy needed — just env var + service restart.

### Code locations
- **Constant**: `HOT_ENTITY_PINNED_PLAYER_NAMES` in `server/warships/data.py` (~line 110)
- **Resolver**: `_get_pinned_player_ids()` in `server/warships/data.py` (~line 4502)
- **Injection point**: `_get_hot_player_ids()` in `server/warships/data.py` (~line 4510)
- **Log line**: `warm_hot_entity_caches()` logs pinned player IDs each run

## Observability

The hot entity warmer logs pinned player IDs on each run:
```
INFO Hot entity warm includes 1 pinned player(s): [1234567890]
```

Check with:
```bash
ssh root@battlestats.online "journalctl -u battlestats-task-runner --since '30 min ago' | grep 'pinned player'"
```
