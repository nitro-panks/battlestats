# Runbook: Clan Battle Shield Precompute — Implementation

**Date:** 2026-03-18  
**Spec:** `agents/runbooks/spec-clan-battle-shield-precompute.md`  
**QA Review:** `agents/reviews/qa-clan-battle-shield-precompute-review.md`  
**Scope:** Eliminate on-demand hydration pattern for clan battle shields. Serve shield data from DB, refresh lazily on invocation.

## Superseded Note

This runbook documents the original shield-precompute rollout. The current implementation policy is in `agents/work-items/clan-list-shield-badge-durability-spec.md`.

Current behavior differs in two ways:

1. `clan_members()` is now a pure read path for shield badges and does not enqueue stale shield refresh work.
2. Shield freshness is governed by the slower `CLAN_BATTLE_BADGE_REFRESH_DAYS` cadence and refreshed by slow producer lanes, not hot clan-list reads.

---

## Prerequisites

- [ ] Read the full spec: `agents/runbooks/spec-clan-battle-shield-precompute.md`
- [ ] Read the QA review: `agents/reviews/qa-clan-battle-shield-precompute-review.md`
- [ ] Confirm Docker stack is running (`docker compose ps`)
- [ ] Confirm existing tests pass: `docker compose exec -T server python manage.py test warships.tests --keepdb`
- [ ] Confirm current shield rendering works (load a clan page, verify shields appear)

---

## Step 1: Add `clan_battle_summary_is_stale()` and `maybe_refresh_clan_battle_data()` to `data.py`

These are new utility functions that the rest of the changes depend on.

**File:** `server/warships/data.py`

### 1a. Add staleness constant

Near the existing `CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT` constant (~L61):

```python
CLAN_BATTLE_SUMMARY_STALE_DAYS = max(
    1, int(os.getenv('CLAN_BATTLE_SUMMARY_STALE_DAYS', '7')))
```

### 1b. Add `clan_battle_summary_is_stale()`

```python
def clan_battle_summary_is_stale(player: Player) -> bool:
    """Return True if the player's clan battle summary needs a refresh."""
    summary = getattr(player, 'explorer_summary', None)
    if summary is None:
        return True
    updated_at = summary.clan_battle_summary_updated_at
    if updated_at is None:
        return True
    return (django_timezone.now() - updated_at).days >= CLAN_BATTLE_SUMMARY_STALE_DAYS
```

### 1c. Add `maybe_refresh_clan_battle_data()`

This is the shared entry point used by both views. Uses a function-level import for `queue_clan_battle_data_refresh` to avoid circular imports (matching the existing pattern in data.py ~L477, ~L529).

```python
def maybe_refresh_clan_battle_data(player: Player) -> None:
    """Enqueue a background CB refresh if the player's summary is stale."""
    from warships.tasks import queue_clan_battle_data_refresh
    if player.is_hidden:
        return
    if not clan_battle_summary_is_stale(player):
        return
    queue_clan_battle_data_refresh(player.player_id)
```

**Verify:** Run the test suite to confirm no regressions.

```bash
docker compose exec -T server python manage.py test warships.tests --keepdb
```

---

## Step 2: Simplify `get_published_clan_battle_summary_payload()` in `data.py`

**File:** `server/warships/data.py` (~L1317)

Remove the `fallback_summary` parameter. Read exclusively from `PlayerExplorerSummary`.

**Before:**

```python
def get_published_clan_battle_summary_payload(
    player: Optional[Player],
    fallback_summary: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = {
        'seasons_participated': int((fallback_summary or {}).get('seasons_participated') or 0),
        'total_battles': int((fallback_summary or {}).get('total_battles') or 0),
        'win_rate': (fallback_summary or {}).get('win_rate'),
        'updated_at': None,
    }
    # ... checks explorer_summary to override ...
```

**After:**

```python
def get_published_clan_battle_summary_payload(
    player: Optional[Player],
) -> dict[str, Any]:
    payload = {
        'seasons_participated': 0,
        'total_battles': 0,
        'win_rate': None,
        'updated_at': None,
    }
    # Keep the existing explorer_summary override block unchanged.
    # Remove the fallback_summary initialization — zeros/None are the default.
```

All callers passing `fallback_summary=` must be updated (views.py list comprehension, serializers.py, landing.py).

---

## Step 3: Update `clan_members()` in `views.py`

**File:** `server/warships/views.py` (~L469)

### 3a. Add `select_related('explorer_summary')` to members queryset

Find the members query (~L475):

```python
members = clan.player_set.exclude(name='').order_by(...)
```

Change to:

```python
members = clan.player_set.select_related('explorer_summary').exclude(name='').order_by(...)
```

Do the same for the re-query after `update_clan_members()` (~L480).

### 3b. Remove `queue_clan_battle_hydration()` call

Remove:

```python
clan_battle_hydration_state = queue_clan_battle_hydration(members)
pending_clan_battle_player_ids = clan_battle_hydration_state['pending_player_ids']
```

### 3c. Rewrite member row clan battle fields

Replace the `get_player_clan_battle_summary()` list comprehension pattern:

**Before:**

```python
for clan_battle_summary in [get_player_clan_battle_summary(member.player_id, allow_fetch=False)]
```

**After:** Read directly from `member.explorer_summary` (available via `select_related`):

```python
# In the member row dict:
'is_clan_battle_player': is_clan_battle_enjoyer(
    getattr(getattr(member, 'explorer_summary', None), 'clan_battle_total_battles', None),
    getattr(getattr(member, 'explorer_summary', None), 'clan_battle_seasons_participated', None),
),
'clan_battle_win_rate': getattr(getattr(member, 'explorer_summary', None), 'clan_battle_overall_win_rate', None),
```

Remove the `for clan_battle_summary in [...]` generator from the list comprehension.

### 3d. Remove `clan_battle_hydration_pending` from member row

Remove:

```python
'clan_battle_hydration_pending': member.player_id in pending_clan_battle_player_ids,
```

### 3e. Remove `X-Clan-Battle-Hydration-*` response headers

Remove all four headers:

```python
response['X-Clan-Battle-Hydration-Queued'] = ...
response['X-Clan-Battle-Hydration-Deferred'] = ...
response['X-Clan-Battle-Hydration-Pending'] = ...
response['X-Clan-Battle-Hydration-Max-In-Flight'] = ...
```

### 3f. Add fire-and-forget stale dispatch

After constructing the response (but before returning), dispatch background refreshes for stale members. The `[:MAX_IN_FLIGHT]` slice is for **throttling** — limiting dispatches per request. `maybe_refresh_clan_battle_data()` also checks staleness internally, but the external filter avoids calling into tasks for members that don't need it.

```python
from warships.data import maybe_refresh_clan_battle_data, CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT, clan_battle_summary_is_stale

stale_members = [m for m in members if clan_battle_summary_is_stale(m)]
for member in stale_members[:CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT]:
    maybe_refresh_clan_battle_data(member)
```

This uses the existing in-flight limit but is fire-and-forget — no pending tracking in the response.

### 3g. Remove unused imports

Remove `queue_clan_battle_hydration` and `get_player_clan_battle_summary` imports if no longer used in this file.

**Verify:** Run the test suite.

---

## Step 4: Update Player Detail Read Path in `views.py`

**File:** `server/warships/views.py` — `PlayerViewSet.get_object()`

The routed player detail endpoint is served by `PlayerViewSet`, not the unused `PlayerDetail` class.

Add the clan-battle stale-check dispatch at the end of `get_object()` so the returned player instance is already available and no second view-level lookup is required:

```python
from warships.data import maybe_refresh_clan_battle_data
maybe_refresh_clan_battle_data(obj)
```

This keeps the behavior attached to the actual `/api/player/<name>/` route.

---

## Step 5: Simplify `PlayerSerializer._get_clan_battle_header_payload()` in `serializers.py`

**File:** `server/warships/serializers.py` (~L92)

Remove the `fallback_summary=get_player_clan_battle_summary(...)` call:

**Before:**

```python
summary = get_published_clan_battle_summary_payload(
    obj,
    fallback_summary=get_player_clan_battle_summary(
        obj.player_id,
        allow_fetch=False,
    ),
)
```

**After:**

```python
summary = get_published_clan_battle_summary_payload(obj)
```

Remove the `get_player_clan_battle_summary` import if no longer used.

---

## Step 6: Remove `clan_battle_hydration_pending` from `ClanMemberSerializer`

**File:** `server/warships/serializers.py` (~L299)

Remove:

```python
clan_battle_hydration_pending = serializers.BooleanField()
```

And remove it from the `fields` list in `Meta` if explicitly listed.

---

## Step 7: Update `landing.py`

**File:** `server/warships/landing.py`

### 7a. Replace cache-based reads with DB reads (using existing `players_by_id`)

Both `_serialize_landing_player_rows()` (~L305) and `_build_recent_players()` (~L617) already load full Player objects with `select_related('explorer_summary')` into a `players_by_id` dict keyed by WG account ID. **No separate `PlayerExplorerSummary` query is needed.**

In each function, remove the `get_player_clan_battle_summaries()` call and the `get_published_clan_battle_summary_payload(fallback_summary=...)` call. Replace with direct reads from the existing `players_by_id` dict:

```python
# Replace the old pattern:
#   clan_battle_summary = get_published_clan_battle_summary_payload(
#       players_by_id.get(player_id),
#       fallback_summary=clan_battle_summaries.get(player_id, {...}),
#   )
# With:
player_obj = players_by_id.get(player_id)
es = getattr(player_obj, 'explorer_summary', None) if player_obj else None
row['is_clan_battle_player'] = is_clan_battle_enjoyer(
    getattr(es, 'clan_battle_total_battles', None),
    getattr(es, 'clan_battle_seasons_participated', None),
)
row['clan_battle_win_rate'] = getattr(es, 'clan_battle_overall_win_rate', None)
```

Do this in both `_serialize_landing_player_rows()` (~L335) and `_build_recent_players()` (~L623).

### 7b. Remove `get_player_clan_battle_summaries()` variable and import

Remove the `clan_battle_summaries = get_player_clan_battle_summaries(...)` calls (~L301, ~L611). Remove the `fallback_summary=` usage. Remove the import if no longer used.

**Verify:** Load the landing page and confirm shields render correctly.

---

## Step 8: Extend `_refresh_player()` for Passive CB Backfill

**File:** `server/warships/management/commands/incremental_player_refresh.py` (~L168)

After `save_player()` completes, check if clan battle data needs backfill:

```python
from warships.data import fetch_player_clan_battle_seasons
from warships.models import PlayerExplorerSummary

def _refresh_player(player_id: int) -> None:
    # ... existing code: fetch, save_player, efficiency, achievements ...
    # ... player.refresh_from_db() already happens here ...

    # Passive CB backfill: populate if never hydrated
    # player.refresh_from_db() has already run above, but explorer_summary
    # was not in the original select_related. Access via reverse relation
    # (triggers a single query if not cached).
    try:
        es = player.explorer_summary
        if es.clan_battle_summary_updated_at is None:
            fetch_player_clan_battle_seasons(player.player_id)
    except PlayerExplorerSummary.DoesNotExist:
        pass  # No explorer summary yet — will be created on next cycle
    except Exception:
        pass  # Non-critical — will be retried on next refresh cycle
```

**Important:** Use `player.explorer_summary` (the OneToOneField reverse relation) rather than `PlayerExplorerSummary.objects.filter(player_id=...)`. The FK column `player_id` on `PlayerExplorerSummary` references `Player.id` (the DB primary key), NOT `Player.player_id` (the WG account ID) — these are different fields with the same name. Using the relation avoids this confusion.

**Verify:** Run Phase 1 tests.

```bash
docker compose exec -T server python manage.py test warships.tests.test_incremental_player_refresh --keepdb
```

---

## Step 9: Remove Dead Code from `data.py`

**File:** `server/warships/data.py`

Remove the following functions:

- `clan_battle_player_hydration_needs_refresh()` (~L468)
- `queue_clan_battle_hydration()` (~L476)

These are no longer called from any view or task.

Also remove `get_player_clan_battle_summaries()` (~L3421). Step 7 eliminates all callers (both `_serialize_landing_player_rows()` and `_build_recent_players()` in landing.py now read from `players_by_id` with `select_related('explorer_summary')` directly).

**Verify:** Run full test suite. Grep for removed function names to confirm no remaining callers.

```bash
docker compose exec -T server python manage.py test warships.tests --keepdb
grep -rn "queue_clan_battle_hydration\|clan_battle_player_hydration_needs_refresh" server/warships/ --include="*.py" | grep -v "\.pyc"
```

---

## Step 10: Remove Dead Code from `tasks.py`

**File:** `server/warships/tasks.py`

Remove `is_clan_battle_data_refresh_pending()` (~L143) if no longer referenced.

Verify no callers remain:

```bash
grep -rn "is_clan_battle_data_refresh_pending" server/warships/ --include="*.py"
```

---

## Step 11: Update Client Types and Components

### 11a. Remove `clan_battle_hydration_pending` from type

**File:** `client/app/components/clanMembersShared.ts` (~L16)

Remove:

```typescript
clan_battle_hydration_pending: boolean;
```

### 11b. Clean up `ClanMembers.tsx`

**File:** `client/app/components/ClanMembers.tsx`

Search for any references to `clan_battle_hydration_pending`. Based on QA review, the field is defined in the type but never actually read or rendered in this component — so the only change is the type removal in 11a.

Verify no TypeScript errors:

```bash
cd client && npx tsc --noEmit
```

---

## Step 12: Write Tests

**File:** `server/warships/tests/test_clan_battle_shield_precompute.py` (new)

### Test cases:

1. **`test_clan_members_returns_shield_data_from_db`** — Create members with populated `PlayerExplorerSummary` clan battle fields. Call `clan_members()`. Assert `is_clan_battle_player` and `clan_battle_win_rate` are correct. Assert no `clan_battle_hydration_pending` in response. Assert no `X-Clan-Battle-Hydration-*` headers.

2. **`test_clan_members_no_shield_when_never_hydrated`** — Member with no clan battle data in `PlayerExplorerSummary`. Assert `is_clan_battle_player: false`, `clan_battle_win_rate: null`.

3. **`test_clan_members_dispatches_refresh_for_stale`** — Member with `clan_battle_summary_updated_at` older than 7 days. Assert `queue_clan_battle_data_refresh` was called. Mock the task dispatch.

4. **`test_clan_members_no_dispatch_for_fresh`** — Member with recent `clan_battle_summary_updated_at`. Assert no dispatch.

5. **`test_player_detail_dispatches_refresh_when_stale`** — Player with stale summary. Load detail view. Assert task dispatched.

6. **`test_player_detail_no_dispatch_when_fresh`** — Player with recent summary. Assert no dispatch.

7. **`test_clan_battle_summary_is_stale_null`** — `updated_at` is None → True.

8. **`test_clan_battle_summary_is_stale_old`** — `updated_at` is 8 days ago → True.

9. **`test_clan_battle_summary_is_stale_recent`** — `updated_at` is 3 days ago → False.

10. **`test_serializer_reads_from_db_only`** — `PlayerSerializer` with populated explorer summary. Assert `clan_battle_header_*` fields populated. Assert `get_player_clan_battle_summary` was NOT called.

11. **`test_landing_shield_from_db`** — Landing payload uses explorer summary, not cache.

12. **`test_incremental_refresh_backfills_cb_when_null`** — Player with `clan_battle_summary_updated_at` is None. Run `_refresh_player()`. Assert `fetch_player_clan_battle_seasons` was called.

13. **`test_incremental_refresh_skips_cb_when_populated`** — Player with populated CB data. Assert `fetch_player_clan_battle_seasons` was NOT called.

```bash
docker compose exec -T server python manage.py test warships.tests.test_clan_battle_shield_precompute --keepdb -v2
```

---

## Step 13: Integration Smoke Test

1. Restart the Docker stack:

   ```bash
   docker compose restart server task-runner
   ```

2. Load a clan page with known CB-active members. Verify:
   - Shields render immediately (no blank/loading state)
   - No `X-Clan-Battle-Hydration-*` headers in response (check Network tab)
   - Response time is equal or faster than before

3. Load a player detail page. Verify:
   - `clan_battle_header_*` fields present in API response
   - Stale player triggers a background task (check Celery worker logs)

4. Load the landing page. Verify:
   - Shield icons render for featured/recent players
   - No errors in console

5. Run the smoke test task:
   ```bash
   docker compose exec -T server python scripts/smoke_test_site_endpoints.py
   ```

---

## Step 14: Clean Up and Commit

1. Run full test suite one final time:

   ```bash
   docker compose exec -T server python manage.py test warships.tests --keepdb
   ```

2. Run client type check:

   ```bash
   cd client && npx tsc --noEmit
   ```

3. Grep for any remaining references to removed functions:

   ```bash
   grep -rn "queue_clan_battle_hydration\|clan_battle_player_hydration_needs_refresh\|clan_battle_hydration_pending\|is_clan_battle_data_refresh_pending" server/ client/ --include="*.py" --include="*.ts" --include="*.tsx" | grep -v node_modules | grep -v __pycache__
   ```

4. Commit with a descriptive message:

   ```bash
   git add -A
   git commit -m "Precompute clan battle shield data: DB-only read path, lazy refresh on invocation

   - Serve shield data from PlayerExplorerSummary (DB), not Redis cache
   - Remove queue_clan_battle_hydration() orchestration
   - Remove clan_battle_hydration_pending field and X-Clan-Battle-Hydration-* headers
   - Add stale-check dispatch: refresh CB data after 7 days on invocation
   - Simplify PlayerSerializer to read exclusively from DB
   - Update landing.py to use DB reads instead of cache
   - Extend incremental player refresh with passive CB backfill
   - Remove dead code: hydration checks, pending status, cache fallbacks

   Spec: agents/runbooks/spec-clan-battle-shield-precompute.md
   QA: agents/reviews/qa-clan-battle-shield-precompute-review.md"
   ```

---

## Rollback Plan

If something goes wrong after deployment:

1. **Partial rollback:** Re-add the `fallback_summary` parameter to `get_published_clan_battle_summary_payload()` and the cache reads. This restores the dual-source behavior without needing to reinstate the full hydration machinery.

2. **Full rollback:** Revert the commit. The `queue_clan_battle_hydration()` function and all cache-based reads are restored. No data migration to undo — `PlayerExplorerSummary` fields were never removed.

---

## Files Changed (Summary)

| File                                                                | Action                                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `server/warships/data.py`                                           | Add `clan_battle_summary_is_stale()`, `maybe_refresh_clan_battle_data()`, `CLAN_BATTLE_SUMMARY_STALE_DAYS`. Simplify `get_published_clan_battle_summary_payload()`. Remove `queue_clan_battle_hydration()`, `clan_battle_player_hydration_needs_refresh()`, `get_player_clan_battle_summaries()`. |
| `server/warships/views.py`                                          | `clan_members()`: add `select_related`, remove hydration call/headers/pending, add stale dispatch. `PlayerViewSet.get_object()`: add stale dispatch and keep the routed player detail flow aligned with DB-backed clan-battle data.                                                               |
| `server/warships/serializers.py`                                    | `PlayerSerializer`: remove cache fallback. `ClanMemberSerializer`: remove `clan_battle_hydration_pending`.                                                                                                                                                                                        |
| `server/warships/landing.py`                                        | Replace cache reads with `PlayerExplorerSummary` DB reads.                                                                                                                                                                                                                                        |
| `server/warships/tasks.py`                                          | Remove `is_clan_battle_data_refresh_pending()`.                                                                                                                                                                                                                                                   |
| `server/warships/management/commands/incremental_player_refresh.py` | Add passive CB backfill in `_refresh_player()`.                                                                                                                                                                                                                                                   |
| `client/app/components/clanMembersShared.ts`                        | Remove `clan_battle_hydration_pending` from type.                                                                                                                                                                                                                                                 |
| `server/warships/tests/test_clan_battle_shield_precompute.py`       | New test file (13 test cases).                                                                                                                                                                                                                                                                    |
