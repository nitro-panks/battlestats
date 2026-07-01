# Runbook: Tier-Type Population Correlation Rework — Incremental Aggregate (Scope/Spec)

_Created: 2026-07-01_
_Context: With the `score_best_clans` bulk-loader prewarm gated off (2026-07-01, `BULK_CACHE_BEST_PREWARM_ENABLED=0`), the tier-type population correlation warmer is now the single largest remaining scheduled DB event on the shared 2-vCPU managed PG: a measured **324.7s/realm** (na, 2026-07-01 18:0x UTC) full `CROSS JOIN LATERAL` over every qualifying player's `battles_json`. This scopes an incremental replacement that keeps the payload byte-identical._
_QA: scope only — no code written. Current-state facts traced in `data.py` / `views.py` / `serializers.py` / the frontend, 2026-07-01. Not yet implemented._

## Purpose

Design a rework of the tier-type population correlation aggregation so it stops re-exploding the whole population's `battles_json` on every warm. Read this before implementing; it fixes the approach (incremental normalized aggregate + watermark-delta maintainer), the parity boundary, and the rollout, and records the two alternatives that were rejected and why. **This is a real slice (new table + migration + maintainer + parity), not a toggle** — bigger than the prewarm gate that preceded it.

## Current state (what we're replacing)

- **The query** — `_TIER_TYPE_POPULATION_SQL` (`data.py:3144`), run by `_aggregate_tier_type_population_sql(realm, min_population_battles)` (`data.py:3177`) inside `_fetch_player_tier_type_population_correlation` (`data.py:3216`). It `CROSS JOIN LATERAL jsonb_array_elements(p.battles_json)` over `warships_player` filtered to `realm`, `is_hidden=false`, `pvp_battles >= 100` (`PLAYER_TIER_TYPE_CORRELATION_CONFIG['min_population_battles']`, `data.py:2600`), then `GROUP BY GROUPING SETS ((ship_type, ship_tier), ())`. A Python fallback (`_aggregate_tier_type_population_python`, `data.py:3193`) does the same by streaming rows.
- **Cost** — ~325s/realm (matches the ~340s `pg_stat_statements` mean), spread across parallel PG workers; runs at most once per `PLAYER_CORRELATION_CACHE_TTL` (12h) per realm via the skip-if-fresh guard in `warm_player_tier_type_population_correlation` (`data.py:3313`). So ~2×/day/realm worst case, striped.
- **Cache** — version-keyed fresh + `:published` durable fallback (`_fetch_player_tier_type_population_correlation`).

### The parity boundary (this is what makes the rework safe)

`_aggregate_tier_type_population_sql` returns exactly two things:
- `tile_counts`: `dict[(ship_type, ship_tier)] -> SUM(lifetime pvp_battles)`
- `tracked_population`: `COUNT(DISTINCT player_id)` of qualifying players

**Everything else in the payload is derived in pure Python from those two** (`data.py:3254-3310`): `tiles`, `trend` (`avg_tier = Σ tier·battles / Σ battles`), `x_labels`, `y_values`, `tracked_population`. So the rework only has to reproduce `(tile_counts, tracked_population)` bit-for-bit; the entire downstream payload is then unchanged.

### The contract to preserve (do not touch)

- Endpoint: `GET /api/fetch/player_correlation/tier_type/<player_id>/` (`urls.py:141`, view `player_correlation_distribution` `views.py:1509`). It overlays a **per-player** `player_cells` onto the shared population `tiles`/`trend`. The warmer computes only the population part; `player_cells` is per-request and cheap — **out of scope**.
- Serializer: `PlayerTierTypeCorrelationSerializer` (`serializers.py:571`) — `metric,label,x_label,y_label,tracked_population,x_labels,y_values,tiles,trend,player_cells`.
- Frontend: `TierTypePayload` (`client/app/components/playerProfileChartData.ts`), rendered by the "Tier vs Type Profile" tab (`PlayerDetailInsightsTabs.tsx`), e2e-covered (`client/e2e/tier-type-heatmap.spec.ts`).

## Rejected alternatives

1. **Read from `PlayerDailyShipStats` / `BattleEvent`.** Rejected — **semantics mismatch**. Those are *windowed* (per-day within the ~32-day capture window) and *delta*; `battles_json` is *lifetime cumulative*. Swapping the source silently changes the metric from "lifetime population distribution" to "recent-window activity". No existing lifetime per-player-ship table exists (confirmed).
2. **Materialized view over the current query, refreshed daily.** Rejected — **no CPU win**. The warmer already runs the scan ~once/12h/realm and caches to Redis; a matview refreshed on the same cadence *is* the same 325s scan, just relabeled. `REFRESH CONCURRENTLY` only buys non-blocking reads, which the Redis `:published` fallback already provides.

## Proposed design — incremental normalized aggregate + watermark-delta maintainer

**Core idea:** move the jsonb explosion from an all-at-once batch to *incremental per-player*, so the warm becomes a cheap indexed `GROUP BY`.

### 1. New table — per-player normalized ship (type,tier) sums

```
PlayerShipTierType
  player_id  BigInteger   # FK-ish to warships_player.player_id (+ realm)
  realm      CharField
  ship_type  CharField    # alias-resolved (AirCarrier -> Aircraft Carrier), 'Unknown' excluded at derive
  ship_tier  SmallInt     # > 0
  pvp_battles BigInteger  # SUM of lifetime pvp_battles across this player's ships of (type,tier)
  unique_together: (player_id, realm, ship_type, ship_tier)
  index: (realm, ship_type, ship_tier)   # for the read GROUP BY
```

Grain = one row per (player, type, tier) — the same collapse the current query does per player before the population `GROUP BY`. Size estimate: ~qualifying players × ~20-40 (type,tier) combos ≈ 2-4M rows/realm; an indexed `GROUP BY` over that is seconds, not minutes.

Derive each player's rows with the **existing** `_extract_tier_type_battle_rows(battles_json)` (`data.py:3069`) — it already does the alias resolution, `ship_tier>0 / pvp_battles>0` filters, and Unknown exclusion, so parity with the current SQL's normalization is by-construction if we reuse it.

### 2. Rewritten read (replaces the body of `_aggregate_tier_type_population_sql`)

```sql
SELECT t.ship_type, t.ship_tier,
       SUM(t.pvp_battles)::bigint AS battles,
       COUNT(DISTINCT t.player_id) AS players
FROM   player_ship_tier_type t
JOIN   warships_player p ON p.player_id = t.player_id AND p.realm = t.realm
WHERE  t.realm = %s
  AND  p.is_hidden = false
  AND  p.pvp_battles >= %s
GROUP BY GROUPING SETS ((t.ship_type, t.ship_tier), ())
```

**Key insight — player-level filters stay a fresh JOIN.** `is_hidden` and the `pvp_battles >= 100` gate are read from `warships_player` at read time, so a player toggling hidden or crossing the battle floor needs **no** table update; only a change to their `battles_json` *content* does. This shrinks the maintenance surface to exactly one trigger: `battles_json` changed.

### 3. Maintenance — a delta batch keyed on `battles_updated_at`

All five `battles_json` writers set `battles_updated_at` in the same `update_fields` save (verified): enrichment `_process_player_ship_data` (`enrich_player_data.py:226,293`); on-view `update_battle_data` (`data.py:2225,2278`); floor `apply_battles_json` (`data.py:2225`, gated `FLOOR_REFRESH_BATTLES_JSON_ENABLED`); the hidden-blank path `update_player_data` (`data.py:4894`, sets `battles_json=None`). So a single watermark cleanly captures "who changed":

```
maintain_player_ship_tier_type(realm):
    for player in Player.objects.filter(realm=realm,
                                        battles_updated_at__gt=watermark[realm]):
        rows = _extract_tier_type_battle_rows(player.battles_json)   # [] if None/empty
        with transaction.atomic():
            PlayerShipTierType.objects.filter(player_id=player.player_id, realm=realm).delete()
            PlayerShipTierType.objects.bulk_create(collapse_to_type_tier(rows))
    watermark[realm] = max(battles_updated_at seen)   # persist per realm
```

- Runs on a Beat cadence *before* the correlation warm (or continuously on `background`). Most players don't change `battles_json` daily → tiny delta → cheap. **First run = one-time full backfill** (~a single 325s-class explosion, or chunked).
- Prefer the **batch-on-watermark** over per-writer hooks: it's self-contained (no surgery across five write paths) and can't half-update. The trade is freshness = last batch, which is fine for a 12h-cadence chart.

### 4. Migration

- Create `PlayerShipTierType` + its index.
- Add an index on `warships_player (realm, battles_updated_at)` — the field is currently **not indexed** (`models.py:47`); the delta scan needs it.

## Parity & rollout

1. **Shadow-parity gate (load-bearing).** Add a flag `TIER_TYPE_AGG_SOURCE = jsonb|table` (default `jsonb`). Run both paths and assert `(tile_counts, tracked_population)` are **identical per realm** before cutover — mirror the `record_observations_bulk` shadow-parity pattern (`test_observations_bulk.py` ShadowParity*). Any mismatch blocks cutover.
2. Keep the current SQL + Python aggregators intact as the fallback behind the flag; do not delete until the table path has run clean for ≥1 week across all realms.
3. Cut over per realm (na first) once shadow parity is clean and the backfill has completed.

## Risks & mitigations

- **Watermark drift** — if any future `battles_json` writer forgets `battles_updated_at`, its players silently go stale in the table. Mitigation: (a) the parity shadow-run catches it pre-cutover; (b) a slow full re-derive (weekly) as a safety net; (c) a code comment on `battles_json` writers. All *current* writers are covered.
- **Backfill cost** — one-time full explosion. Chunk it by player-id range; run off-peak; it's a one-time 325s-class cost, not recurring.
- **Deletions / GDPR** — a deleted/blocked player must lose their rows. Add an FK `on_delete=CASCADE` (or handle in the `DeletedAccount` path). The read-time JOIN already hides `is_hidden` players, so stale rows for hidden players are harmless until cleaned.
- **Unknown / AirCarrier normalization** — must happen at *derive* time (reuse `_extract_tier_type_battle_rows`) so the stored `ship_type` matches what the current SQL emits. Do not re-normalize at read time.
- **Table growth** — 2-4M rows/realm. Monitored under the existing DB-growth runbook; far smaller than `BattleEvent`.

## Estimated slices

1. Model + migration (`PlayerShipTierType` + both indexes) + `collapse_to_type_tier` helper reusing `_extract_tier_type_battle_rows`. Unit test the derive/collapse.
2. `maintain_player_ship_tier_type` task + watermark storage + Beat registration + backfill command. Test the delta selection.
3. Table-path aggregator behind `TIER_TYPE_AGG_SOURCE` + shadow-parity test asserting identical `(tile_counts, tracked_population)`.
4. Cutover (per-realm flag), then retire the jsonb scan after a clean week.

## Verification

- **Parity**: shadow-run asserts identical `(tile_counts, tracked_population)` per realm; the existing tier-type e2e (`client/e2e/tier-type-heatmap.spec.ts`) stays green; the API payload diff is empty against a captured baseline.
- **Cost**: the correlation warm's tier-type portion drops from ~325s to a seconds-class indexed `GROUP BY` (measure via `pg_stat_statements` mean + the `warm_player_correlations_task` journal duration); the maintainer's per-cycle delta stays small (log rows-processed).
- **Backend gate**: `cd server && python -m pytest warships/tests/ --tb=short`.

## Related

- `runbook-landing-featured-boards-decommission-2026-06-22.md` + `runbook-db-write-efficiency-eval-2026-07-01.md` — the preceding PG-headroom work (`score_best_clans` gate) that made this the top remaining sink.
- `runbook-bulk-battle-observation-capture-2026-06-06.md` — the shadow-parity pattern to mirror for cutover.
