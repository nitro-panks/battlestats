# Enrichment Crawler — Architecture, Operations, and Product Roadmap

Created: 2026-04-02

## Purpose

The enrichment crawler fills `battles_json` (and derived fields) for the ~211K players who have core stats from the clan crawler but lack per-ship battle data. It runs continuously on the background Celery worker, prioritizing the highest win-rate players first.

## What battles_json Contains

Each row is one ship the player has sailed:

```python
{
    'ship_id': int,
    'ship_name': str,
    'ship_chart_name': str,      # abbreviated for chart labels
    'ship_tier': int,             # 1-11
    'all_battles': int,           # total (PvP + PvE + other)
    'distance': float,            # nautical miles sailed
    'wins': int,
    'losses': int,
    'ship_type': str,             # Destroyer, Cruiser, Battleship, AirCarrier, Submarine
    'pve_battles': int,
    'pvp_battles': int,
    'win_ratio': float,           # 0.0-1.0
    'kdr': float,                 # kills per PvP battle
}
```

Sorted by `pvp_battles` descending. One WG API call per player (`ships/stats/`).

## What battles_json Powers Today

### Direct Surfaces

| Surface | Component / Endpoint | What it renders |
|---|---|---|
| Profile bar chart | `RandomsSVG.tsx` → `/api/fetch/randoms_data/{id}` | Top 20 most-played ships |
| Tier-type heatmap | `TierTypeHeatmapSVG.tsx` → `/api/fetch/player_correlation/tier_type/{id}` | Per-player WR grid (ship type x tier) |
| Player summary cards | `PlayerSummaryCards.tsx` → `/api/fetch/player_summary/{id}` | Kill ratio, ships played, type spread, tier spread |
| Landing page rows | `landing.py` `_calculate_tier_filtered_pvp_record()` | High-tier (>=5) battle count and WR |
| Population heatmap | `/api/fetch/player_distribution/tier_type` | Aggregate tier-type distribution across all players |

### Derived Fields (Cascaded on Write)

`update_battle_data()` writes `battles_json` and then cascades:

- **tiers_json** — battle count per tier (feeds TierSVG chart)
- **type_json** — battle count per ship type (feeds TypeSVG chart)
- **randoms_json** — top 20 ships extracted from battles_json
- **PlayerExplorerSummary** — kill_ratio, ships_played_total, ship_type_spread, tier_spread, player_score

### Indirect Dependencies

- **Player score** — multi-factor composite used by Best and Sigma landing lists. Falls back to cruder metrics when battles_json is missing.
- **Efficiency rank** — `refresh_player_explorer_summary()` updates fields that feed sigma badge computation.

## Coverage (as of 2026-04-02)

| Realm | Players with battles_json | Total eligible (500+ battles, 48%+ WR) | Coverage |
|---|---|---|---|
| NA | ~1,100 | ~73,600 | ~1.5% |
| EU | ~1,000 | ~137,700 | ~0.7% |

Only populated when a player's profile is viewed directly (user-triggered) or via the hot entity warmer (top ~20 players). The clan crawler does NOT fill it.

## Crawler Design

### Architecture

```
enrich_player_data_task (Celery, background queue)
  ├─ Check clan crawl mutex → defer 5min if active
  ��─ Acquire Redis lock
  ├─ _prewarm_ship_cache() → bulk-load all Ship records to Redis
  ├─ _candidates() per realm → players missing battles_json, ORDER BY pvp_ratio DESC
  ├─ _interleave(na, eu) → alternate realms
  ├─ For each player:
  │   ├─ update_battle_data()         → 1 API call (ships/stats) + cached ship lookups
  │   ├─ update_snapshot_data(refresh_player=False) → 0 API calls (DB only)
  │   └─ update_ranked_data()         → 2 API calls (account rank_info + shipstats)
  ├─ Release lock
  └─ _maybe_redispatch_enrichment() → self-chain with 10s pause
```

### Batch API Optimizations

- **Ship cache pre-warm**: all ~960 Ship records loaded to Redis before the loop. Avoids per-ship DB + API lookups inside `update_battle_data`.
- **`refresh_player=False`** on `update_snapshot_data`: skips 2 redundant API calls per player (`account/info` + `clans/accountinfo`) since the clan crawler already keeps these current.
- **No separate `update_activity_data` call**: `update_snapshot_data` cascades to it internally.
- **Net cost: ~2 API calls per player** (ships/stats + ranked account info), or ~3 if they have ranked data (+ ranked shipstats). Down from ~5 without optimizations.

### Throughput

- ~1.3s per player (including 0.2s inter-player delay)
- 500 players per batch (~11 min/batch)
- Continuous self-chaining when no clan crawl is active
- **Estimated backlog clearance: ~3-4 days** of effective run time

### Durability (Three Layers)

1. **Self-chaining** — after each batch, re-dispatches itself (10s pause). Broker dispatch retries 3x with backoff.
2. **Startup kickoff** — `startup_warm_caches_task` dispatches enrichment on every deploy/restart (30s countdown).
3. **Beat safety net** — every 2 hours, Beat fires the task. Redis lock prevents duplicates.

### Mutual Exclusion

- Checks for active clan crawl locks (any realm) before starting. Defers with 5-minute retry if crawl is running.
- Redis lock (`warships:tasks:enrich_player_data:lock`) prevents concurrent enrichment batches.
- Runs on dedicated `background` queue — never competes with user-facing tasks on `default`/`hydration`.

## Configuration (Env Vars)

| Variable | Default | Description |
|---|---|---|
| `ENRICH_BATCH_SIZE` | 500 | Players per batch before re-dispatch |
| `ENRICH_MIN_PVP_BATTLES` | 500 | Minimum PvP battles to be eligible |
| `ENRICH_MIN_WR` | 48.0 | Minimum overall WR% to be eligible |
| `ENRICH_DELAY` | 0.2 | Seconds between players |
| `ENRICH_PAUSE_BETWEEN_BATCHES` | 10 | Seconds between batch re-dispatches |
| `ENRICH_KICKSTART_MINUTES` | 120 | Beat safety-net interval |

## Key Files

- `server/warships/management/commands/enrich_player_data.py` — core logic, management command
- `server/warships/tasks.py` — `enrich_player_data_task`, `_maybe_redispatch_enrichment`
- `server/warships/signals.py` — Beat schedule registration (`player-enrichment-kickstart`)
- `server/battlestats/settings.py` — task routing to `background` queue
- `server/deploy/deploy_to_droplet.sh` — production env var defaults

## Candidate Selection

```sql
-- Conceptual query (Django ORM in _candidates())
SELECT player_id, name, pvp_ratio, pvp_battles, realm
FROM warships_player
WHERE realm = :realm
  AND is_hidden = FALSE
  AND pvp_battles >= :min_pvp_battles
  AND pvp_ratio >= :min_wr
  AND battles_json IS NULL
  AND name != ''
ORDER BY pvp_ratio DESC NULLS LAST,
         pvp_battles DESC NULLS LAST,
         name
LIMIT :per_realm
```

Design rationale: "Crawl players based on overall WR, filtering out low value new players and noisy accounts, for truly strong players who have lots of stats." — highest-WR players with significant battle counts are enriched first.

## Product Opportunities (Unlocked by Full Coverage)

### Near-term (achievable with current data model)

1. **Population analytics at scale** — Tier-type heatmaps and correlations currently cover ~2K players. Full coverage makes distributions statistically meaningful. "What's the real meta?" becomes answerable.

2. **Better player scoring** — `player_score` composite currently falls back to cruder metrics when battles_json is missing. Full coverage means every player gets the full multi-factor score. Sigma badges and Best lists become more accurate.

3. **Ship-level leaderboards** — battles_json has per-ship WR and KDR. Rank players by specific ship: "Top Shimakaze players NA", "Best Petropavlovsk win rates." Currently impossible without the data.

4. **Ship popularity and win rate rankings** — Aggregate across population: most played ships, highest/lowest WR ships, ships overperforming relative to tier. Meta analysis for the community.

### Medium-term (requires new surfaces or models)

5. **Clan composition analysis** — Aggregate members' ship pools: "this clan specializes in destroyers at tier 10", "this clan has no carrier players." Enhances clan detail page.

6. **"Players like you" recommendations** — With type/tier profiles for everyone, suggest players with similar playstyles or ship preferences.

7. **Ship-type queue composition estimates** — Per-tier type distributions could show estimated matchmaking composition.

## Monitoring

Check enrichment progress:

```bash
# SSH to droplet
ssh root@battlestats.online

# Live logs
journalctl -u battlestats-celery-background -f | grep enrich

# Check remaining candidates
sudo -u battlestats bash -c '
cd /opt/battlestats-server/current/server
/opt/battlestats-server/venv/bin/python manage.py shell -c "
from warships.models import Player
for realm in [\"na\", \"eu\"]:
    total = Player.objects.filter(realm=realm, is_hidden=False, pvp_battles__gte=500, pvp_ratio__gte=48.0, battles_json__isnull=True).exclude(name=\"\").count()
    done = Player.objects.filter(realm=realm, is_hidden=False, pvp_battles__gte=500, pvp_ratio__gte=48.0, battles_json__isnull=False).exclude(name=\"\").count()
    print(f\"{realm.upper()}: {done} done, {total} remaining\")
"'

# Queue depth
rabbitmqctl list_queues name messages | grep background

# Check if enrichment lock is held
redis-cli GET warships:tasks:enrich_player_data:lock
```

## Manual Operation

```bash
# Dry run — see candidates without processing
python manage.py enrich_player_data --dry-run

# Small test batch
python manage.py enrich_player_data --batch 10 --delay 0.3

# NA only, relaxed filters
python manage.py enrich_player_data --realm na --min-pvp-battles 200 --min-wr 45

# Large batch, minimal delay
python manage.py enrich_player_data --batch 5000 --delay 0.1
```
