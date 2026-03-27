# Runbook: Profile Charts Stuck in Warming State

**Status:** Active
**Last verified:** 2026-03-27
**Affected surface:** Player detail → Profile tab (`/player/<name>`)
**Observed player:** B277 (`player_id=1025693496`)

---

## Symptom

The Profile tab shows:

> Profile charts are still warming. Try again in a moment.

The message never resolves. Reloading repeats the same cycle. No profile charts (Tier vs Type Profile, Performance by Ship Type, Performance by Tier) ever render.

---

## Root Cause

Three bugs combine to make this permanent:

### Bug 1 — `views.py`: Battle data task is never dispatched when `battles_json` is null

`PlayerViewSet.get_object` dispatches `update_battle_data_task` on player load:

```python
# views.py:200
if not obj.is_hidden and obj.battles_json is not None and player_battle_data_needs_refresh(obj):
    _delay_task_safely(update_battle_data_task, player_id=obj.player_id)
```

The `battles_json is not None` guard means that for any player whose `battles_json` has never been populated, the task is **never queued from the player detail path**. B277 has `battles_json: null` and `battles_updated_at: null` (never fetched successfully), so this dispatch never fires on page load — the primary hydration trigger is silently skipped.

### Bug 2 — `data.py`: Failed `ships/stats` fetches leave `battles_updated_at` null, creating an unbounded retry loop

`update_battle_data` short-circuits without recording an attempt timestamp when the WG API returns empty:

```python
# data.py:2386
ship_data = _fetch_ship_stats_for_player(player_id)
if not ship_data:
    logging.warning('No ship stats returned...; leaving battles_json unchanged.')
    return player.battles_json  # battles_updated_at is NOT written
```

Because `battles_updated_at` stays null, `player_battle_data_needs_refresh()` always returns True (null timestamp = always stale). Every call to `fetch_player_tier_type_correlation` (triggered by the Profile tab) re-dispatches `update_battle_data_task` through `_dispatch_async_refresh`. If the WG `ships/stats` endpoint is returning null for this player, the task fires on every profile tab request, always fails, and never stops.

### Bug 3 — `PlayerDetailInsightsTabs.tsx`: No recovery path from warming state

After exhausting `PROFILE_PENDING_RETRY_LIMIT` (5) retries with `X-Tier-Type-Pending: true` + empty `player_cells`, the client sets `profileChartState = 'warming'` and shows the message. The effect guard added in the recent commit:

```tsx
if (isLoading || activeTab !== 'profile' || profileChartPayload ||
    profileChartState === 'error' || profileChartState === 'warming') {
    return;
}
```

prevents the effect from re-running within the session. A full page reload resets state to `'idle'`, so the cycle repeats: 5 retries → warming → stuck. There is no in-page "Try again" path and no timed recovery retry.

---

## Diagnosis Steps

```bash
# 1. Confirm battles_json and battles_updated_at are null
curl -s "https://battlestats.online/api/player/B277/" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); \
   print('battles_json:', d.get('battles_json') is not None); \
   print('battles_updated_at:', d.get('battles_updated_at')); \
   print('pvp_battles:', d.get('pvp_battles'))"

# 2. Confirm the tier_type endpoint returns X-Tier-Type-Pending: true with empty player_cells
curl -si "https://battlestats.online/api/fetch/player_correlation/tier_type/<player_id>/" \
  | head -20

# 3. Confirm player_cells is empty in the response body
curl -s "https://battlestats.online/api/fetch/player_correlation/tier_type/<player_id>/" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('player_cells:', d.get('player_cells'))"
```

A player is in this stuck state when:
- `battles_json` is null
- `battles_updated_at` is null
- `pvp_battles` is nonzero (has real battle history)
- `X-Tier-Type-Pending: true` is set on the tier_type response
- `player_cells` is empty

---

## Suggested Fix

Three targeted patches, one per bug:

### Fix 1 — `views.py`: Dispatch the battle data task even when `battles_json` is null

Change line 200 from:

```python
if not obj.is_hidden and obj.battles_json is not None and player_battle_data_needs_refresh(obj):
    _delay_task_safely(update_battle_data_task, player_id=obj.player_id)
```

to:

```python
if not obj.is_hidden and (obj.battles_json is None or player_battle_data_needs_refresh(obj)):
    _delay_task_safely(update_battle_data_task, player_id=obj.player_id)
```

This ensures that the first player detail fetch (the natural trigger on every page load) queues the hydration task even for players whose `battles_json` has never been populated.

### Fix 2 — `data.py`: Record a failure timestamp so the retry loop has backoff

In `update_battle_data`, when `_fetch_ship_stats_for_player` returns empty, write `battles_updated_at` before returning so subsequent staleness checks provide a 15-minute cooldown:

```python
ship_data = _fetch_ship_stats_for_player(player_id)
if not ship_data:
    logging.warning(
        'No ship stats returned for player_id=%s; recording attempt timestamp to avoid tight retry loop.',
        player_id,
    )
    player.battles_updated_at = datetime.now()
    player.save(update_fields=['battles_updated_at'])
    return
```

This prevents the task from being queued on every tier_type request for players whose `ships/stats` API call persistently returns empty. The 15-minute staleness window (`PLAYER_BATTLE_DATA_STALE_AFTER`) provides the backoff.

### Fix 3 — `PlayerDetailInsightsTabs.tsx`: Add a timed recovery retry from warming state

Replace the hard warming guard with a timed re-attempt so the profile tab self-heals if the backend catches up:

Remove `profileChartState === 'warming'` from the early-return guard and restore a bounded delayed retry from warming state (distinct from the pending-retry loop):

```tsx
// When profileChartState reaches 'warming', schedule one final retry after
// a longer delay (e.g. 30s) so the tab self-heals if the backend catches up,
// rather than requiring a full page reload.
```

The PROFILE_WARMING_RETRY_DELAY_MS constant (5000ms) that was removed in the recent commit was the retry mechanism for this. It should be restored at a longer interval (e.g. 30,000ms) to avoid the tight-loop behavior that motivated its removal, while still providing a recovery path.

Alternatively, surface a "Retry" button in the warming state UI so users can trigger a single fresh attempt without a full reload.

---

## Recovery for B277 Specifically

Fix 1 will not immediately resolve B277 — the task will queue and then fail at the WG `ships/stats` step if the API is returning empty for this player. To force a fresh attempt against the WG API:

1. Apply Fix 2 first (so the task has backoff on failure).
2. Apply Fix 1 (so the next page load queues the task).
3. Visit `/player/B277/` to trigger the `update_player_data_task` (which in turn triggers `update_battle_data_task`).
4. Check Django logs or the Celery worker logs for the `update_battle_data` call for `player_id=1025693496` to see whether the WG `ships/stats/` endpoint is returning data.

If the WG API is returning null for this player's ships (e.g., their public ship stats are unavailable), the profile chart cannot be populated from the current data path. In that case, the correct resolution is to show a clear "Ship data unavailable for this player" state rather than the warming message.

---

## Pre-Commit Requirements

Before committing any fix from this runbook:

- [ ] Update the `fetch_player_tier_type_correlation` and `update_battle_data` inline comments to describe the new failure-stamp behavior
- [ ] Add or update focused tests covering:
  - `update_battle_data` with empty `ship_data` now writes `battles_updated_at` (Fix 2)
  - `PlayerViewSet.get_object` dispatches `update_battle_data_task` when `battles_json is None` (Fix 1)
- [ ] Verify no existing tests rely on `battles_updated_at` staying null after a failed `ships/stats` response

---

## Related Files

- `server/warships/views.py` — `PlayerViewSet.get_object`, line 200
- `server/warships/data.py` — `update_battle_data` (line ~2360), `fetch_player_tier_type_correlation` (line ~3162)
- `server/warships/api/ships.py` — `_fetch_ship_stats_for_player`
- `client/app/components/PlayerDetailInsightsTabs.tsx` — profile chart load effect
