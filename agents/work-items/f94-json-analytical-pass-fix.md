# F9.4 — JSON-element analytical pass: identification + rebuild-interval floor

Status: implemented 2026-07-20 (code-only; no prod mutation in this tranche).
Parent: `agents/runbooks/runbook-db-table-audit-2026-07-19.md` finding F9, recommendation 4.

## The query

`pg_stat_statements` (2026-07-20): **59 calls × 398.3 s mean** — the heaviest
standing analytical statement after the F9.1 fixes.

```sql
WITH qualifying AS (
    SELECT p.player_id,
           CASE WHEN btrim(elem->>'ship_type') = 'AirCarrier'
                THEN 'Aircraft Carrier'
                ELSE btrim(elem->>'ship_type') END AS ship_type,
           trunc((elem->>'ship_tier')::numeric)::int   AS ship_tier,
           trunc((elem->>'pvp_battles')::numeric)::int AS pvp_battles
    FROM warships_player p
    CROSS JOIN LATERAL jsonb_array_elements(p.battles_json) AS elem
    WHERE p.realm = %s AND p.is_hidden = false AND p.pvp_battles >= %s
      AND p.battles_json IS NOT NULL
      AND jsonb_typeof(...) checks ... AND element tier/battles > 0
)
SELECT ship_type, ship_tier, SUM(pvp_battles)::bigint, COUNT(DISTINCT player_id)
FROM qualifying WHERE ship_type <> 'Unknown'
GROUP BY GROUPING SETS ((ship_type, ship_tier), ())
```

## Owner

- SQL literal: `_TIER_TYPE_POPULATION_SQL`, `server/warships/data.py` (~line 3059).
- Executor: `_aggregate_tier_type_population_sql` → called with `force_rebuild=True`
  by `_fetch_player_tier_type_population_correlation` →
  `warm_player_tier_type_population_correlation` → `warm_player_correlations`
  (`data.py`) → Celery `warm_player_correlations_task` (`tasks.py`).
- Schedule: Beat `player-correlation-warmer-{realm}` (`signals.py`, daily —
  `CORRELATION_WARM_MINUTES=1440`, per-realm striped, base_minute=45). 3 realms
  × daily ≈ the observed 52–59 calls over the statements window. Secondary
  triggers: gunicorn startup warmer (`startup_warm_all_caches`) and the
  cold-cache request-path dispatch (`_dispatch_async_correlation_warm`,
  fires only when both fresh and durable published keys are absent).
- Surface fed: the player-page Population tier-type heatmap
  (`fetch_player_tier_type_correlation`) — **live**, not a decommission candidate.

## Why it ran daily despite skip-if-fresh

`warm_player_tier_type_population_correlation` short-circuits on a fresh,
non-empty TTL'd cache key — but the fresh-key TTL is
`PLAYER_CORRELATION_CACHE_TTL` (12 h) while the Beat is daily, so the key had
always expired by the next run and every daily warm re-ran the full ~400 s scan
per realm (~20 min/day of DB time on the 2-vCPU managed PG).

## Fix chosen: (b) bound

- **(a) relational rewrite — rejected**: the payload is *career* per-(type,tier)
  battle sums. `PlayerDailyShipStats`/`BattleEvent` hold only ~30–92 d window
  deltas; `PlayerExplorerSummary` has spreads/counts, no per-cell data. No
  relational source reproduces the contract. The runbook's write-time
  side-table extraction (~per-player parsed cells maintained on every
  `battles_json` write — the floor rewrites these constantly) was rejected as
  far larger than the smallest safe slice.
- **(c) removal — rejected**: the Population heatmap is a live surface.
- **(b) bound — chosen**: rebuild-interval floor. A marker key
  (`_tier_type_rebuild_marker_key`, TTL `TIER_TYPE_POPULATION_REBUILD_HOURS`,
  default **72 h**, env-tunable) is set only after a successful non-empty
  rebuild. While the marker is present and the durable `:published` payload is
  non-empty, the warmer serves the published payload without scanning. The
  empty-population rescue (the historical asia `tracked_population=0` freeze)
  still rebuilds straight through the marker; a marker lost to Redis
  `allkeys-lru` eviction merely rebuilds early. Payload contract unchanged.

## Expected cost after

~400 s × 3 realms drops from daily to once per 72 h: **~20 → ~6.7 min/day** of
DB time (set `TIER_TYPE_POPULATION_REBUILD_HOURS=168` for ~2.9). Worst-case
staleness of the career population baseline grows from ~1 to ~3 days —
imperceptible for a 200K-player career aggregate. Verify after ~a week:
`pg_stat_statements` call rate for the `WITH qualifying … btrim` statement
should fall to ~1 per realm per 3 days.

## Files

- `server/warships/data.py` — constant, marker-key builder, floor in
  `warm_player_tier_type_population_correlation`.
- `server/warships/signals.py` — Beat comment reconciled.
- `server/warships/tests/test_player_correlation_warm.py` —
  `TierTypeRebuildIntervalFloorTests` (4 tests, written failing-first).
- `agents/runbooks/ops-env-reference.md` — env catalog entry.
- `agents/runbooks/runbook-db-table-audit-2026-07-19.md` — Applied log + pickup pointer.
