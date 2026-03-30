# Runbook: Database Optimization

**Status:** Implemented (v1.2.11) — pending production deploy
**Date:** 2026-03-29

## Current State

### Infrastructure
- **Database:** DigitalOcean Managed PostgreSQL (Basic Premium AMD, Shared CPU, 1 vCPU, 2 GB RAM)
- **`max_connections`:** 50 (DO managed, not configurable)
- **Connection limit for app role:** 47 (3 reserved for DO management)
- **Connection pools:** None configured (DO pgBouncer not enabled)

### Table Sizes
| Table | Rows | Total Size |
|-------|------|-----------|
| `warships_player` | ~275K | 861 MB |
| `warships_playerachievementstat` | ~2.5M | 591 MB |
| `warships_playerexplorersummary` | ~295K | 188 MB |
| `warships_clan` | ~21K | 10 MB |

### Connection Budget (Production Droplet)

| Process | Concurrency | Max DB Connections |
|---------|------------|-------------------|
| Gunicorn (2 CPU → 5 workers) | 5 | 5 |
| Celery default queue | 3 | 3 |
| Celery hydration queue | 4 | 4 |
| Celery background queue | 2 | 2 |
| Startup warmer (subprocess) | 1 | 1 |
| **Total** | **15** | **15** |

Current `CONN_MAX_AGE=300` keeps connections alive 5 minutes. Under normal load: ~4 active connections observed. Peak theoretical: 15. Well within the 47 limit.

### PostgreSQL Server Settings
| Setting | Value | Notes |
|---------|-------|-------|
| `shared_buffers` | 196 MB | ~10% of 2 GB RAM (conservative; 25% = 512 MB would be better) |
| `work_mem` | 2 MB | Default; analytical queries override to 8 MB |
| `effective_cache_size` | 588 MB | Conservative for 2 GB RAM (1.5 GB would be typical) |
| `random_page_cost` | 1.0 | SSD-optimized (good) |
| `seq_page_cost` | 1.0 | SSD-optimized (good) |
| `default_statistics_target` | 100 | Default; consider 200 for skewed columns |

---

## Findings

### 1. Sequential Scans on Large Tables

Production `pg_stat_user_tables` shows:

| Table | Seq Scans | Seq Rows Read | Index Scans |
|-------|-----------|---------------|-------------|
| `warships_playerexplorersummary` | 19 | 2.2M | 149K |
| `warships_player` | 10 | 2.2M | 145K |
| `warships_clan` | 5 | 106K | 413 |

The 19 seq scans on `playerexplorersummary` (295K rows × 19 = 5.6M tuples read) come from distribution/correlation warming queries and `score_best_clans()`. These are analytical queries that legitimately scan the full table — but could benefit from materialized views.

### 2. Unused Indexes (341 MB wasted)

| Index | Size | Scans |
|-------|------|-------|
| `unique_player_achievement_source` | 145 MB | 0 |
| `player_ach_slug_idx` | 115 MB | 0 |
| `warships_playerachievementstat_pkey` | 56 MB | 0 |
| `warships_playerachievementstat_player_id_*` | 25 MB | 0 |
| `explorer_score_idx` | 22 MB | 0 |
| `achievement_slug_idx` | 16 MB | 0 |
| `player_last_fetch_idx` | 14 MB | 0 |
| `player_clan_battle_idx` | 11 MB | 0 |
| `explorer_ranked_idx` | 7.7 MB | 0 |
| `explorer_battles29_idx` | 7.0 MB | 0 |
| `explorer_active29_idx` | 7.0 MB | 0 |
| `explorer_ships_idx` | 6.6 MB | 0 |
| `player_hidden_battle_idx` | 6.1 MB | 0 |

**341 MB of indexes that have never been used.** These consume RAM (competing with `shared_buffers`), slow down writes, and bloat WAL. On a 2 GB managed instance with only 196 MB shared_buffers, this is significant.

**Safe to drop** (after confirming since stats reset):
- All `playerachievementstat` indexes except the unique constraint — if achievement data is only written/read in bulk by player_id, the FK index suffices
- `player_last_fetch_idx` — not used by any current query path
- `player_hidden_battle_idx` — not used (queries filter `is_hidden` but don't use this composite index)
- `player_clan_battle_idx` — 0 scans; clan-member queries use the FK index `warships_player_clan_id_*` instead
- `explorer_*` indexes (score, ranked, battles29, active29, ships) — 0 scans each

**Caution:** `pg_stat_user_indexes` resets on server restart. The managed DB has been running since stats were last reset. Verify that no periodic task uses these before dropping.

### 3. Hot Index: `player_battles_surv_idx` Reading 1.15M Tuples in 8 Scans

```
player_battles_surv_idx | 8 scans | 1,150,560 tuples read
```

This is the `(pvp_battles, pvp_survival_rate)` composite index used by distribution queries. 8 scans reading 1.15M rows means each scan reads ~144K rows — essentially a full index scan. This pattern suggests the query planner is using an index scan when a seq scan would be more efficient, or the filter selectivity is too low.

### 4. N+1 Query Patterns in Cache Warming

**`warm_player_entity_caches()`** (data.py:4681-4722):
Each player triggers 8-12 separate DB operations in a loop:
```
for player_id in player_ids:           # 20 iterations default
    Player.objects.filter(...).first()  # 1 query
    update_player_data()               # 2-3 queries + WG API
    update_battle_data()               # 1-2 queries + WG API
    update_snapshot_data()             # 1 query
    update_activity_data()             # 1 query
    update_tiers_data()                # 1 query
    update_type_data()                 # 1 query
    update_randoms_data()              # 1 query
    ...
```
**Total:** ~200 DB operations for 20 players. Not fixable by batching since each player also requires WG API calls — but the initial Player fetch and the data-staleness checks could be batched.

**`warm_clan_entity_caches()`** (data.py:4725-4746):
```python
clan.player_set.exclude(name='').count()  # Separate COUNT per clan!
```
This is a pure N+1 — the count could be annotated on the initial query.

### 5. `score_best_clans()` — Double Scan

`score_best_clans()` (data.py:4938-5068) runs three separate queries:
1. **Candidate query** with `annotate(tracked_count=Count('player'))` — scans clan→player join
2. **Member stats query** on `PlayerExplorerSummary` — scans explorer summaries for all candidate clan members
3. **CB recency query** on `PlayerExplorerSummary` — another scan for max CB dates

All three could be consolidated into a single query with conditional aggregation, or the first query's `tracked_count` annotation could use cached fields.

### 6. Efficiency Rank Computation — 5 Raw SQL Statements

`compute_efficiency_rank_snapshot()` (data.py:1137-1396) executes 5 separate `cursor.execute()` calls in sequence:
1. Population stats (avg strength, suppressed counts)
2. Main UPDATE with window functions and shrinkage estimators
3. Clear stale ranks UPDATE
4. Tier count query
5. Distribution query

This is already well-optimized raw SQL. The main UPDATE (statement 2) is a single-pass computation across ~275K rows. No changes recommended here — the complexity is inherent.

### 7. Snapshot Saves in Loop

`update_snapshot_data()` (data.py:~2691-2703) creates/updates individual Snapshot records in a loop (~29 days):
```python
for day_data in daily_snapshots:
    Snapshot.objects.update_or_create(...)  # 29 individual upserts
```
Could use `bulk_create(..., update_conflicts=True)` for a single INSERT...ON CONFLICT.

### 8. Missing: DO Connection Pooler

DigitalOcean managed Postgres offers a built-in pgBouncer connection pooler (separate port, e.g., 25061). Benefits:
- Multiplexes many short-lived Django connections over fewer persistent DB connections
- Reduces connection overhead (each PG connection costs ~5-10 MB RAM)
- Enables higher `CONN_MAX_AGE` or even persistent connections
- Transaction-mode pooling is compatible with Django (session-mode features like SET LOCAL need care)

**Current state:** Not configured. All connections go directly to port 25060.

---

## Recommended Actions

### Priority 1 — Quick Wins (No Schema Changes)

#### 1a. Enable DO Connection Pooler
Create a connection pool via the DigitalOcean API:
```bash
curl -X POST "https://api.digitalocean.com/v2/databases/<db-id>/pools" \
  -H "Authorization: Bearer $DO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "battlestats-pool",
    "mode": "transaction",
    "size": 15,
    "db": "defaultdb",
    "user": "doadmin"
  }'
```
- **Mode:** `transaction` — releases server connection after each transaction (best for Django)
- **Size:** 15 — matches max app connections, leaves 32 for direct/admin access
- **Django config change:** Point `DB_HOST` and `DB_PORT` at the pool endpoint (port 25061 typically)
- **Caveat:** `SET LOCAL work_mem` in `_elevated_work_mem()` won't persist across statements in transaction pooling. Wrap analytical queries in `transaction.atomic()` (already done) to ensure SET LOCAL stays within one server connection.

#### 1b. Drop Unused Indexes
Migration to drop 341 MB of dead indexes:
```sql
DROP INDEX IF EXISTS player_last_fetch_idx;
DROP INDEX IF EXISTS player_hidden_battle_idx;
DROP INDEX IF EXISTS player_clan_battle_idx;
DROP INDEX IF EXISTS explorer_score_idx;
DROP INDEX IF EXISTS explorer_ranked_idx;
DROP INDEX IF EXISTS explorer_battles29_idx;
DROP INDEX IF EXISTS explorer_active29_idx;
DROP INDEX IF EXISTS explorer_ships_idx;
```
**Achievement stat indexes** (341 MB total) — evaluate whether the achievement feature is still actively queried before dropping. If only loaded in bulk by player_id, keep the FK index and drop the rest.

#### 1c. Annotate Clan Member Count in `warm_clan_entity_caches()`
Replace the N+1 `clan.player_set.count()` with a single annotated query:
```python
clans = Clan.objects.filter(clan_id__in=clan_ids).annotate(
    tracked_member_count=Count('player', filter=Q(player__name__gt=''))
)
```

#### 1d. Bulk Upsert Snapshots
Replace 29 individual `update_or_create()` calls with:
```python
Snapshot.objects.bulk_create(
    [Snapshot(player_id=pid, date=d, ...) for d in daily_snapshots],
    update_conflicts=True,
    unique_fields=['player_id', 'date'],
    update_fields=['battles', 'wins', ...],
)
```

### Priority 2 — Materialized Views for Analytical Queries

The population distribution and correlation queries perform full-table scans of `warships_player` (275K rows, 861 MB) and `warships_playerexplorersummary` (295K rows, 188 MB). These are already cached in Redis with 2-hour TTL, but the underlying DB queries are expensive when the cache misses.

#### 2a. Player Distribution Materialized View
```sql
CREATE MATERIALIZED VIEW mv_player_distribution_stats AS
SELECT
    player_id,
    pvp_ratio,
    pvp_survival_rate,
    pvp_battles,
    pvp_avg_damage,
    is_hidden
FROM warships_player
WHERE is_hidden = FALSE
  AND pvp_battles >= 100
  AND pvp_ratio IS NOT NULL;

CREATE INDEX ON mv_player_distribution_stats (pvp_ratio);
CREATE INDEX ON mv_player_distribution_stats (pvp_battles);
```
- **Size:** ~25 MB (6 columns × 194K rows vs 861 MB full table)
- **Refresh:** `REFRESH MATERIALIZED VIEW CONCURRENTLY` every 55 min (alongside landing warmer)
- **Benefit:** Distribution binning queries scan 25 MB instead of 861 MB

#### 2b. Clan Scoring Materialized View
```sql
CREATE MATERIALIZED VIEW mv_clan_scoring AS
SELECT
    c.clan_id,
    c.cached_clan_wr,
    c.cached_active_member_count,
    c.cached_total_battles,
    c.members_count,
    COUNT(p.id) FILTER (WHERE p.name > '') as tracked_count,
    AVG(es.player_score) as avg_member_score,
    AVG(es.clan_battle_total_battles) as avg_cb_battles,
    MAX(es.clan_battle_summary_updated_at) as latest_cb_update
FROM warships_clan c
LEFT JOIN warships_player p ON p.clan_id = c.id
LEFT JOIN warships_playerexplorersummary es ON es.player_id = p.id
WHERE c.name IS NOT NULL AND c.name != ''
GROUP BY c.clan_id, c.cached_clan_wr, c.cached_active_member_count,
         c.cached_total_battles, c.members_count;
```
- **Benefit:** Replaces 3 queries in `score_best_clans()` with a single read
- **Refresh:** Every 12h (alongside bulk cache loader)

### Priority 3 — Connection and Query Tuning

#### 3a. Increase `default_statistics_target` for Skewed Columns
```sql
ALTER TABLE warships_player ALTER COLUMN pvp_ratio SET STATISTICS 200;
ALTER TABLE warships_player ALTER COLUMN pvp_battles SET STATISTICS 200;
ALTER TABLE warships_playerexplorersummary ALTER COLUMN player_score SET STATISTICS 200;
ANALYZE warships_player;
ANALYZE warships_playerexplorersummary;
```
Better statistics → better query plans for distribution/correlation queries on skewed data.

#### 3b. Reduce `CONN_MAX_AGE` with Connection Pooler
Once pgBouncer is enabled, set `CONN_MAX_AGE=0` (close after each request) — the pooler handles connection reuse more efficiently than Django's per-process caching.

#### 3c. Batch Player Staleness Checks in `warm_player_entity_caches()`
Instead of checking each player individually:
```python
# Current: 20 individual queries
for pid in player_ids:
    player = Player.objects.filter(player_id=pid).first()
    if player_detail_needs_refresh(player): ...

# Proposed: 1 query + Python filter
players = Player.objects.filter(player_id__in=player_ids).select_related('explorer_summary', 'clan')
players_by_id = {p.player_id: p for p in players}
for pid in player_ids:
    player = players_by_id.get(pid)
    if not player: continue
    if player_detail_needs_refresh(player): ...
```

### Priority 4 — Longer Term

#### 4a. Dedicated Read Replica
If the managed DB plan supports it, add a read replica for analytical queries (distributions, correlations, explorer page). Django's database routers can direct read-heavy queries to the replica.

#### 4b. Table Partitioning for `warships_playerachievementstat`
At 591 MB and growing, this table could benefit from partitioning by player_id ranges or achievement_slug. Most queries filter by player_id, so range partitioning would improve pruning.

#### 4c. VACUUM/ANALYZE Schedule
Verify DO's autovacuum settings are appropriate for the write patterns. The `warships_player` table (861 MB, ~275K rows) has `n_live_tup=0` in pg_stat — this means autovacuum hasn't run recently or stats were reset. Run `ANALYZE` manually after any bulk operations.

---

## Implementation Order

1. ~~**Drop unused indexes**~~ ✅ — Migration 0034: drops 10 unused indexes (~341 MB reclaimed)
2. **Enable DO connection pooler** — deferred (manual DO console step)
3. ~~**Fix N+1 in `warm_clan_entity_caches()`**~~ ✅ — Batch annotated query replaces per-clan `count()`
4. ~~**Bulk update snapshots**~~ ✅ — `Snapshot.objects.bulk_update()` replaces per-snapshot `save()` loop
5. ~~**Batch player staleness checks**~~ ✅ — Single `filter(player_id__in=...)` replaces per-player query
6. ~~**Create distribution materialized view**~~ ✅ — Migration 0034: `mv_player_distribution_stats` with 4 indexes; `warm_player_distributions()` refreshes concurrently before warming
7. ~~**Distribution/correlation queries use MV**~~ ✅ — `fetch_player_population_distribution()` and `fetch_player_wr_survival_correlation()` now query `MvPlayerDistributionStats` (~25 MB) instead of `Player` (861 MB)
8. ~~**Statistics target tuning**~~ ✅ — Migration 0034: `SET STATISTICS 200` on pvp_ratio, pvp_battles, pvp_survival_rate, player_score + ANALYZE
9. **Create clan scoring materialized view** — deferred (lower priority, requires more complex refresh logic)
