# Runbook: DB-optimization follow-ups (post size-reclaim)

_Created: 2026-05-26_
_Context: The DB size-optimization session (`runbook-db-size-optimization-2026-05-26.md`) reclaimed ~5 GB (`defaultdb` 24 → 19 GB) and made keep=1 BattleObservation compaction durable + live. It left three deferred follow-ups and surfaced one pre-existing bug. This runbook captures and executes them._
_Status: **COMPLETE** (2026-05-26). FU-1 (rollup OOM rewrite) + FU-2 (serializer wire-trim) shipped in release `20260526125032`; release gate 377 passed; FU-1 smoke-tested on the droplet (yearly rebuild 622,942 rows, peak memory flat — no OOM). FU-3 `VACUUM FULL warships_player` run in a confirmed window: pgstattuple showed heap 45.8% free + TOAST 34% free; table **11 GB → 7.4 GB**, **`defaultdb` 19 GB → 16 GB** (~3.6 GB to OS). Net across both DB sessions: `defaultdb` 24 → 16 GB._

## The three follow-ups

### FU-1 — Nightly-rollup OOM (pre-existing bug; highest value)

**Symptom:** `rebuild_period_rollups_for_date()` (`incremental_battles.py`) OOM-killed at **7.2 GB RSS** on
the 7.8 GB droplet during the 2026-05-26 monthly/yearly rebuild. Root cause: `_aggregate_into_period_table`
loads the **entire period's** `PlayerDailyShipStats` rows into Python (`for d in daily_qs:` building a
`rows` dict) — ~1.1 M rows for a month, ~1.2 M for a year. As the daily table grew, the nightly
`roll_up_player_daily_ship_stats_task` (`tasks.py:1532`) has been **silently failing** on the
monthly/yearly tiers (this is why the pre-truncate monthly/yearly counts were partial: 123 K/119 K vs the
true 617 K/620 K rebuilt via server-side SQL).

**Fix:** push the GROUP BY to the database instead of aggregating in Python. Rewrite
`_aggregate_into_period_table` to use ORM aggregation streamed in batches:

```python
from django.db.models import Sum, Min, Max
agg = (PlayerDailyShipStats.objects
       .filter(date__gte=target_period_start, date__lte=period_end_inclusive,
               mode=PlayerDailyShipStats.MODE_RANDOM)
       .values("player_id", "ship_id")
       .annotate(battles=Sum("battles"), wins=Sum("wins"), losses=Sum("losses"),
                 frags=Sum("frags"), damage=Sum("damage"), xp=Sum("xp"),
                 planes_killed=Sum("planes_killed"), survived_battles=Sum("survived_battles"),
                 first_event_at=Min("first_event_at"), last_event_at=Max("last_event_at"),
                 ship_name=Max("ship_name"))
       .order_by())
# delete-first stays (idempotent); then bulk_create in batches via .iterator(chunk_size=…)
```

- Peak memory is bounded by the bulk_create batch (e.g. 5 000), not the period size.
- **DB-portable** (Postgres + the sqlite used in tests) — required because
  `test_rebuild_period_rollups_writes_weekly_monthly_yearly` (`test_incremental_battles.py:2891`) runs on
  sqlite. `ship_name=Max(...)` matches "non-empty wins" since `''` sorts first; `updated_at` stays
  auto-set by `bulk_create` (no raw SQL).
- `rebuild_daily_ship_stats_for_date` (`incremental_battles.py:986`) uses the same Python-load pattern but
  only over **one day** of `BattleEvent` rows (~40 K) — far lower risk; leave as-is this round (note only).

**Verify:** `test_incremental_battles.py` green; then a prod rebuild of the current month/year completes
without OOM (`free -h` stays healthy on the droplet) and row counts match the daily layer.

### FU-2 — Trim unused JSON from the player-detail wire (perf/efficiency; 0 disk)

**Finding:** `PlayerSerializer` (`serializers.py:100`, `fields = '__all__'`) ships **all 8** JSON columns
in every `/api/players/<name>` payload and into the Redis `get_cached_player_detail` dict, but the
frontend reads **none** of `battles_json`, `tiers_json`, `type_json`, `activity_json`,
`achievements_json` (grep of `client/app`: 0 refs each; only `randoms_json`, `ranked_json`,
`efficiency_json` are consumed). The detail payload is **not** ODCS-contract-governed (the contracts cover
`PlayerSummarySerializer` / `PlayerExplorerRowSerializer`, not this serializer), so no contract YAML
change is needed. Server-side reads of `battles_json` (landing `.values()`, `get_kill_ratio`,
battle-history baseline, randoms fallback) all read the **model attribute**, unaffected by the serialized
field set.

**Fix:** switch `PlayerSerializer.Meta` from `fields = '__all__'` to
`exclude = ['battles_json', 'tiers_json', 'type_json', 'activity_json', 'achievements_json']` (surgical;
declared `SerializerMethodField`s like `kill_ratio` are unaffected). Saves ~24 KB/player-page on the wire
**and** shrinks the Redis-cached payload (eases the 3 GB `allkeys-lru` cache).

**Verify:** release gate (`test_views.py` + frontend `npm test`) green — update any test that asserts a
dropped key in a *response* (most references are fixture setup, not assertions); load a player page in the
app and confirm every chart still renders (they use the dedicated endpoints + `randoms/ranked/efficiency`).

### FU-3 — Return the pruned `battles_json` ~2 GB to the OS (Tier 2; needs a window)

The inactive-`battles_json` prune freed ~2 GB into **reusable** space inside `warships_player` (11 GB,
growth capped) but a regular `VACUUM` does not return it to the OS. To reclaim it:

1. `CREATE EXTENSION IF NOT EXISTS pgstattuple;` (contrib, DO-supported) and measure true bloat:
   `SELECT * FROM pgstattuple('warships_player');` — look at `dead_tuple_percent` / `free_percent`.
2. If reclaimable space is material, run a **windowed** `VACUUM (FULL, ANALYZE) warships_player;` with
   `lock_timeout='2min'`. **This takes ACCESS EXCLUSIVE on the 11 GB hot table** — player writes
   (request-driven refreshes, capture) block for the duration (minutes). Schedule a low-traffic window;
   captures retry via `acks_late`. Otherwise prefer tuning per-table autovacuum scale factors and leave the
   space reusable (no lock).

**Verify:** `pg_total_relation_size('warships_player')` drops by ~the freed amount; `defaultdb` shrinks
accordingly; API healthy after the lock releases.

## Execution order & sequencing

1. **FU-1 + FU-2 together** (both code): implement, run the lean release gate, commit, backend-deploy.
2. **FU-3** standalone: measure with pgstattuple; the `VACUUM FULL` only in a confirmed low-traffic window.

## Critical files
- `server/warships/incremental_battles.py` — `_aggregate_into_period_table` (~1282), `rebuild_period_rollups_for_date` (~1353), `rebuild_daily_ship_stats_for_date` (986, note-only).
- `server/warships/serializers.py:100-107` — `PlayerSerializer.Meta`.
- `server/warships/tests/test_incremental_battles.py:2891` — rollup test (sqlite; portability gate).
- `server/warships/tests/test_views.py` — player-detail response assertions (update if any reference a dropped key).

## Rollback
- FU-1/FU-2 are code; revert the commit + redeploy. FU-1 is behaviour-preserving (same rows, lower memory);
  FU-2 only removes payload fields the frontend ignores.
- FU-3 `VACUUM FULL` is not reversible but is safe (rewrites the table); the only risk is the lock window.
