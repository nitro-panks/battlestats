# Runbook: Profile Charts Permanently Stuck in Warming State (Batch)

**Created**: 2026-03-27
**Status**: Resolved — deployed 2026-03-27, verified against 3 test players
**Priority**: High — affects 264,890 of 265,846 visible players

## Problem

The Profile tab for most players shows "Profile charts are still warming. Try again in a moment." permanently. The tier_type correlation endpoint returns `X-Tier-Type-Pending: true` with zero `player_cells` and never resolves.

The frontend retries 5 times at 1.5s intervals (7.5s total), then shows the warming message. After 30s it resets to idle and retries, entering an infinite warming loop. See `PlayerDetailInsightsTabs.tsx` lines 113-115, 264-298.

## Root Cause

### Corrected Analysis (2026-03-27)

Initial investigation assumed the tier_type view never dispatched a refresh. This was **incorrect** — `fetch_player_tier_type_correlation()` in `data.py` line 3166 DOES dispatch `update_battle_data_task` when `battles_json` is empty. Additionally, `PlayerViewSet.retrieve()` at `views.py` line 200 dispatches `update_battle_data_task` when `battles_json is None`.

**The actual root cause is the bootstrap guard in `fetch_player_summary()`** — it was the only path that controlled whether missing `activity_json` and `ranked_json` fields triggered their respective refresh tasks. The guard required ALL JSON fields to be None AND no `explorer_summary` before dispatching any refresh, so players with partial data (e.g. `ranked_json` set from a bulk import but `battles_json` still None) never triggered hydration from this endpoint.

However, since `battles_json` hydration IS dispatched from both the tier_type data function and `PlayerViewSet.retrieve()`, the stuck state for Profile charts specifically may also involve:
- WG API returning empty ship stats for some players (causes `battles_updated_at` to be set without populating `battles_json`, creating a 15-minute cooldown)
- Rate limiting on the WG API when many players trigger concurrent requests
- The Celery task lock (`_run_locked_task`) preventing concurrent executions for the same player

### The bootstrap guard in fetch_player_summary was too restrictive

`server/warships/data.py` line 2054-2060 (before fix): `fetch_player_summary()` dispatched `update_battle_data_task` only when ALL of these were true:

```python
needs_bootstrap = (
    not player.is_hidden
    and player.battles_json is None
    and player.activity_json is None
    and player.ranked_json is None
    and getattr(player, 'explorer_summary', None) is None
)
```

The stale-data refresh path (line 2069) required `battles_json is not None`, so it also skipped these players.

### Database state (verified 2026-03-27)

| Condition | Count |
|-----------|-------|
| Total visible players | 265,846 |
| `battles_json = NULL` | 264,890 |
| `battles_json = NULL` AND `ranked_json != NULL` | 264,253 |
| `battles_json != NULL` (working) | 956 |
| `activity_json = NULL` | 265,744 |
| `ranked_json = NULL` | 646 |
| All three JSON fields NULL (bootstrap-eligible) | 637 |
| Bootstrap-eligible AND no `explorer_summary` (actually fires) | 637 |

The bootstrap fired only for the 637 players with all three JSON fields null AND no explorer_summary. The remaining 264,253 were blocked because `ranked_json` was set (from a bulk ranked data import). Even if `ranked_json` were null, 264,890 stuck players all had `explorer_summary` set, which independently blocked the bootstrap.

## Affected Endpoints

All of these depend on `battles_json` being populated:
- `/api/fetch/player_correlation/tier_type/<player_id>/` — returns `X-Tier-Type-Pending: true` with 0 cells
- `/api/fetch/randoms_data/<player_id>/` — returns empty array `[]`
- `/api/fetch/tier_data/<player_id>/` — returns empty array `[]`
- `/api/fetch/type_data/<player_id>/` — returns empty array `[]`

Verified against Magnetohydrodynamics (1039062423): all four endpoints return 0 rows.

## Confirmed Working

For the 956 players that have `battles_json` populated:
- All tab endpoints return data within 200-350ms
- Profile charts render correctly
- Ranked data, ranked_wr_battles, activity all functional
- Landing page, clan views, player explorer all functional

## Fix Applied

### 1. Relaxed the bootstrap guard in fetch_player_summary (per-field lazy hydration)

**File**: `server/warships/data.py` `fetch_player_summary()`

Replaced the all-or-nothing bootstrap with per-field lazy hydration. Each JSON field is now checked independently — if a field is None, its refresh task is dispatched; if it exists but is stale, the stale-refresh path fires. This means a player with `ranked_json` set but `battles_json=None` now correctly dispatches `update_battle_data_task`.

```python
if not player.is_hidden:
    if player.battles_json is None:
        _dispatch_async_refresh(update_battle_data_task, player_id=player_id)
    elif player_battle_data_needs_refresh(player):
        _dispatch_async_refresh(update_battle_data_task, player_id=player_id)

    if player.activity_json is None:
        _dispatch_async_refresh(update_snapshot_data_task, player_id)
        _dispatch_async_refresh(update_activity_data_task, player_id)
    elif player_activity_data_needs_refresh(player):
        _dispatch_async_refresh(update_snapshot_data_task, player_id)
        _dispatch_async_refresh(update_activity_data_task, player_id)

    if player.ranked_json is None:
        from warships.tasks import queue_ranked_data_refresh
        queue_ranked_data_refresh(player_id)
    elif player_ranked_data_needs_refresh(player):
        from warships.tasks import queue_ranked_data_refresh
        queue_ranked_data_refresh(player_id)
```

### 2. Tier_type view — NO change needed

The tier_type data function (`fetch_player_tier_type_correlation()` at `data.py` line 3166) already dispatches `update_battle_data_task` when `battles_json` is empty. The initial runbook incorrectly stated the view never dispatched; that analysis only looked at `views.py` and missed the dispatch inside the data function.

### 3. Test coverage added

New regression test: `test_fetch_player_summary_dispatches_battle_refresh_when_only_battles_json_missing` in `test_data.py` — verifies that a player with `battles_json=None` but `ranked_json` set correctly triggers `update_battle_data_task`.

### Not applied: Backfill via management command

A one-time Celery fan-out to populate `battles_json` for all 264,890 affected players was not applied. The lazy hydration fix means players will self-heal on their next profile visit. A backfill could be considered for analytics completeness but carries rate-limit and queue saturation risks.

## Verification

After deploying:
```bash
# 1. Hit a known-stuck player's profile to trigger lazy hydration
curl -s -D- "https://battlestats.online/api/fetch/player_summary/1039062423/" | head -20

# 2. Hit the tier_type endpoint — should return pending and dispatch task
curl -s -D- "https://battlestats.online/api/fetch/player_correlation/tier_type/1039062423/" | grep -i pending
# Expected: X-Tier-Type-Pending: true (dispatches update_battle_data_task)

# 3. Wait 10-15s for Celery to complete, then re-check
sleep 15
curl -s "https://battlestats.online/api/fetch/player_correlation/tier_type/1039062423/" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'cells={len(d.get(\"player_cells\",[]))}')"
# Expected: cells > 0

# 4. Verify battles_json is now populated
docker compose exec -T server python manage.py shell -c "
from warships.models import Player
p = Player.objects.get(player_id=1039062423)
print(f'battles_json: {len(p.battles_json) if p.battles_json else None} rows')
"
```

## Test Players

| Player | ID | Pre-fix Status | Post-fix Status |
|--------|----|----------------|-----------------|
| Magnetohydrodynamics | 1039062423 | Stuck (battles_json=None) | Fixed — 26 tier_type cells |
| B277 | 1025693496 | Stuck (battles_json=None) | Fixed — 20 tier_type cells |
| cicero_jones | 1001006706 | Stuck (battles_json=None) | Fixed — 30 tier_type cells |
| John_The_Ruthless | 1020639850 | Working | Working |
| Noob_CoralSea | 1033630907 | Working | Working |

## Deployment Notes

- The clan crawl watchdog (`ensure_crawl_all_clans_running_task`, every 5 min) can trigger a full crawl on restart if a stale lock remains in Redis. Before restarting Celery services, clear the crawl lock: `redis-cli DEL warships:tasks:crawl_all_clans:lock warships:tasks:crawl_all_clans:heartbeat`
- Also purge the Celery queue to discard stale tasks: `rabbitmqctl purge_queue celery`
- Players self-heal on next profile visit. No backfill needed for organic traffic; consider backfill only for analytics completeness.
