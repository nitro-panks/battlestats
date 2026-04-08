# Runbook: Clan Members "Updating N members" Wedge

**Status:** Root cause confirmed, fix implemented 2026-04-07 (pending deploy)
**Owner:** august
**Reported:** 2026-04-07, after the sigma-icon fix in `e0273dc`
**Surfaces:** Player detail → Clan Members panel

## Symptom

On the player detail page, the **Clan Members** panel shows a persistent "Updating N members" banner that never decreases, never clears, and gives no indication that anything is happening behind the scenes. The number sticks across reloads.

The bug appeared *after* `e0273dc fix: stop blanking efficiency rank when snapshot inputs advance` shipped, which is why it's been mentally associated with the sigma icon work — but the code paths are independent (see "Why the sigma fix is structurally innocent" below).

## How the counter is supposed to work

End-to-end flow on each `GET /api/clans/<id>/members/`:

1. **`server/warships/views.py:708-716`** loads members and calls `queue_clan_efficiency_hydration(members, realm)`.
2. **`server/warships/data.py:556-585`** `queue_clan_efficiency_hydration`:
   - Computes `eligible = [p for p in players if player_efficiency_needs_refresh(p)]`
   - Calls `_queue_limited_player_hydration(...)` with `max_in_flight = CLAN_EFFICIENCY_HYDRATION_MAX_IN_FLIGHT` (default **8**, env override `CLAN_EFFICIENCY_HYDRATION_MAX_IN_FLIGHT`)
3. **`server/warships/data_support.py:92-143`** `_queue_limited_player_hydration`:
   - Marks players whose `is_efficiency_data_refresh_pending(pid)` (Redis dispatch key) is set as already-pending.
   - Fills remaining `max_in_flight` slots by enqueuing `update_player_efficiency_data_task`.
   - **`data_support.py:135`** — *deferred players (eligible but no slot) are merged into `pending_player_ids`*. This means a clan with 30 stale members reports `pending = 30` on the first request, not 8.
4. **`views.py:740`** stamps each member with `efficiency_hydration_pending: member.player_id in pending_efficiency_player_ids`.
5. **`views.py:752-756`** — response is **not** cached when `has_pending`, so the next poll always re-runs the queue logic.
6. Frontend (`useClanMembers`) polls every 3-6s (6s when chart fetches are in flight, see `runbook-player-page-load-priority.md`).
7. Each completed task updates `Player.efficiency_updated_at`, dropping that player out of `eligible` on the next request. Counter is supposed to drain ~8 per poll cycle.

The "Updating N members" UI counts members where `efficiency_hydration_pending === true` in the most recent response.

## Why the sigma fix is structurally innocent

`e0273dc` only modified `_get_published_efficiency_rank_payload` (`server/warships/data.py:819-849`) and an unrelated WR/survival correlation axis flip. It does **not** touch:

- `player_efficiency_needs_refresh` (the eligibility predicate)
- `Player.efficiency_updated_at` (the freshness clock)
- `update_player_efficiency_data_task` / `_fetch_efficiency_badges_for_player`
- `queue_clan_efficiency_hydration` or `_queue_limited_player_hydration`
- The dispatch-key cache or the per-task lock

The pending counter reads `Player.efficiency_updated_at`; the sigma icon reads `PlayerExplorerSummary.efficiency_rank_updated_at` via the snapshot path. The fix changed only the second path. So the wedge is its own bug — it was either preexisting and masked by the icon flicker that drew the user's eye, or it became newly visible because the icons now stay rendered while the counter sticks underneath them.

## Root cause (confirmed via QA review)

Two compounding bugs, one frontend, one backend:

### 1. Frontend poll cap leaves stale `pending` flags in React state

`client/app/components/useClanMembers.ts:7` defines `HYDRATION_POLL_LIMIT = 12`. Lines 124-133 only schedule the next poll when `attempt < HYDRATION_POLL_LIMIT`. After 12 attempts (~36-72s at 3-6s intervals) the polling loop simply stops — but the last response's `members` array (with `efficiency_hydration_pending: true` for any unfinished members) remains in state. `client/app/components/ClanMembers.tsx:29,39` computes the banner count as `members.filter((m) => m.efficiency_hydration_pending).length`, so N freezes at whatever the 12th poll reported and never moves again. Reloads only re-mount the component; if the server still reports the same eligible players within the next 12 polls, the wedge re-appears immediately.

### 2. Backend amplifier: deferred players reported as pending

`server/warships/data_support.py:135` previously merged `deferred_player_ids` into `pending_player_ids` before returning. With `CLAN_EFFICIENCY_HYDRATION_MAX_IN_FLIGHT = 8`, a clan with 30 stale members reported `pending = 30` on every poll instead of `pending = 8`. The drain rate (~8 per cycle) was correct, but because the *reported* count started at the full eligible count, the banner showed an alarmingly large N that took many cycles to drop — and frequently exceeded the 12-poll cap before reaching zero, triggering bug #1.

The two together produced the user-visible symptom: a stable, non-decreasing N that never clears.

## Hypotheses ruled out by QA review

- **Stuck `_run_locked_task` lock**: even an orphaned lock is harmless because the task's own `finally` always clears the dispatch key (`tasks.py:672`), and the next poll re-enqueues. Churn, not wedge.
- **Stuck dispatch key after worker SIGKILL**: real but narrow window (only between `.delay()` returning and the task entering its try block); recovers within 15 min via `EFFICIENCY_REFRESH_DISPATCH_TIMEOUT`. Not the cause of the reported persistent wedge.
- **PvE member wedge via Hypothesis A**: ruled out — `update_player_efficiency_data` (`server/warships/data.py:371-375`) stamps `efficiency_updated_at = now()` for any `pvp_battles <= 0` player before any upstream call, so PvE-only members cannot stick.

Hypothesis A (silent upstream `_fetch_efficiency_badges_for_player` failures) remains a *possible* secondary contributor for genuinely poison-pilled players, but is not the primary cause and is not addressed in this fix. It should be tracked separately if the wedge re-appears post-deploy.

## Original hypotheses (preserved for diagnostic context)

### Hypothesis A — silent failure of `_fetch_efficiency_badges_for_player`

`server/warships/data.py:359-383` `update_player_efficiency_data` only advances `efficiency_updated_at` after `_fetch_efficiency_badges_for_player` returns. If the WG upstream call raises (timeout, 5xx, throttle), the task exits in the `finally` of `update_player_efficiency_data_task` (`tasks.py:671-672`), the dispatch key is cleared, the lock is released — but the player is still stale. Next poll re-queues the same player, fails again. The pending count never drops.

This matches the symptom exactly: count is *stable* (not zero, not decreasing, not growing). The hydration loop is busy but accomplishing nothing.

**Diagnostic:**
```bash
ssh root@battlestats.online '
  cd /opt/battlestats-server/current/server &&
  /opt/battlestats-server/venv/bin/python manage.py shell -c "
from warships.models import Player
from warships.data import player_efficiency_needs_refresh
from django.utils import timezone
from datetime import timedelta
# Replace 1000040518 with the wedged clan id
ids = list(Player.objects.filter(clan_id=1000040518, realm=\"na\").values_list(\"player_id\", flat=True))
players = list(Player.objects.filter(player_id__in=ids))
stale = [p for p in players if player_efficiency_needs_refresh(p)]
print(f\"total={len(players)} stale={len(stale)}\")
for p in stale[:10]:
    age = (timezone.now() - p.efficiency_updated_at) if p.efficiency_updated_at else None
    print(f\"  {p.name}: efficiency_updated_at={p.efficiency_updated_at} age={age}\")
"
'
```

If the same names appear stale across multiple runs minutes apart, hypothesis A is confirmed.

```bash
# Check celery worker logs for repeated efficiency task errors on the same player_ids
ssh root@battlestats.online 'journalctl -u celery-hydration -n 500 --no-pager | grep -E "efficiency|update_player_efficiency" | tail -100'
```

### Hypothesis B — stuck dispatch key with no in-flight task

`tasks.py:35` defines `EFFICIENCY_REFRESH_DISPATCH_TIMEOUT = 15 * 60` (15 minutes). The dispatch key is `cache.add`-ed before `.delay()` and `cache.delete`-ed in the task's `finally` (`tasks.py:671-672`). If a worker is killed (OOM, SIGKILL, deploy bounce) between `.delay()` enqueue and task pickup, the key persists for 15 minutes with no task ever running. During that window, `_queue_limited_player_hydration` sees the player as already-pending and never re-enqueues — the player counts toward the wedged number but no work is being done.

This is a softer wedge than A: it self-heals after 15 minutes. But if multiple bounces stack (e.g. memory pressure causing repeated worker restarts during clan crawl), the wedge can be effectively permanent.

**Diagnostic:**
```bash
ssh root@battlestats.online '
  cd /opt/battlestats-server/current/server &&
  /opt/battlestats-server/venv/bin/python manage.py shell -c "
from django.core.cache import cache
from warships.tasks import _efficiency_refresh_dispatch_key
# spot-check a few stale player_ids from hypothesis A diagnostic
for pid in [12345678, 23456789]:
    key = _efficiency_refresh_dispatch_key(pid, realm=\"na\")
    print(f\"{pid}: dispatch_key={key} value={cache.get(key)}\")
"
'
```

```bash
# Check celery-hydration queue depth — if dispatch keys exist but the queue is empty, that's hypothesis B
ssh root@battlestats.online 'rabbitmqctl list_queues name messages consumers | grep -E "hydration|default"'
```

### Hypothesis C — `_run_locked_task` lock leak

`tasks.py:329-346` `_run_locked_task` uses `cache.add(lock_key, ..., timeout=RESOURCE_TASK_LOCK_TIMEOUT)` with a `try/finally cache.delete(lock_key)`. A redis flush, key eviction, or SIGKILL between `cache.add` and the `finally` would leave the lock orphaned for `RESOURCE_TASK_LOCK_TIMEOUT`. Subsequent dispatches return `{"status": "skipped", "reason": "already-running"}`, the dispatch key gets cleared in the `finally`, and the player stays stale forever — until the lock TTL expires.

Less likely than A/B because `RESOURCE_TASK_LOCK_TIMEOUT` is generally short, but worth checking.

**Diagnostic:**
```bash
ssh root@battlestats.online '
  cd /opt/battlestats-server/current/server &&
  /opt/battlestats-server/venv/bin/python manage.py shell -c "
from django.core.cache import cache
from warships.tasks import _task_lock_key
# cross-reference with stale player_ids
for pid in [12345678, 23456789]:
    key = _task_lock_key(\"update_player_efficiency_data\", pid)
    print(f\"{pid}: lock_key={key} value={cache.get(key)}\")
"
'
```

### Hypothesis D — frontend stops polling but the banner persists

The banner could be sticky in the React state if the polling hook errors out or unmounts/remounts in a way that snapshots a stale `pending` count without scheduling further requests. Worth ruling out by:

1. Open DevTools Network on a wedged player page.
2. Watch `/api/clans/<id>/members/` — does it fire every 3-6s?
3. Inspect response headers `X-Efficiency-Hydration-Queued / Deferred / Pending` — do they decrease?

If the request fires and the headers show a constant non-zero pending, the wedge is server-side (A/B/C). If requests stop firing, the wedge is in `useClanMembers` polling state.

## Workarounds (no code change required)

1. **Force-clear the relevant dispatch keys and re-warm**:
   ```bash
   ssh root@battlestats.online '
     cd /opt/battlestats-server/current/server &&
     /opt/battlestats-server/venv/bin/python manage.py shell -c "
   from django.core.cache import cache
   from warships.tasks import _efficiency_refresh_dispatch_key, _task_lock_key
   from warships.models import Player
   ids = Player.objects.filter(clan_id=<CLAN_ID>, realm=\"na\").values_list(\"player_id\", flat=True)
   for pid in ids:
       cache.delete(_efficiency_refresh_dispatch_key(pid, realm=\"na\"))
       cache.delete(_task_lock_key(\"update_player_efficiency_data\", pid))
   print(f\"cleared {len(ids)} dispatch+lock keys\")
   "
   '
   ```
2. **Force-stamp efficiency_updated_at** for the wedged clan to take members out of `eligible` (last-resort, masks the underlying upstream failure if any):
   ```python
   from warships.models import Player
   from django.utils import timezone
   Player.objects.filter(clan_id=<CLAN_ID>, realm="na", efficiency_json__isnull=False).update(efficiency_updated_at=timezone.now())
   ```
3. **Bounce the hydration worker** if a poison-pilled task is stuck:
   ```bash
   ssh root@battlestats.online 'systemctl restart celery-hydration'
   ```

## Implemented fix (2026-04-07)

Two minimal changes addressing the two compounding bugs:

1. **`server/warships/data_support.py:135`** — removed the `pending_player_ids.update(deferred_player_ids)` line. `pending` now reflects only the players with refresh work actually in flight (≤ `max_in_flight`). Deferred-but-eligible players will become pending on subsequent polls as slots free up. The `X-Efficiency-Hydration-Deferred` and `X-Efficiency-Hydration-Pending` headers continue to be exposed separately so server-side observability is unchanged.
2. **`client/app/components/useClanMembers.ts`** — when the poll cap is reached with hydration still pending, scrub `ranked_hydration_pending` and `efficiency_hydration_pending` from the in-state `members` array. The banner drops to zero instead of freezing. Polling resumes naturally on next mount or when `clanId`/`realm`/`enabled` changes.

The mocked clan_members tests in `test_views.py` are unaffected because they patch `queue_clan_efficiency_hydration` at the data.py boundary (the change is inside `_queue_limited_player_hydration`).

## Fixes considered but not implemented

- **Track and surface upstream failures** — write a per-player failure cookie with backoff in `update_player_efficiency_data_task` so genuinely poison-pilled players are skipped instead of churning. Worth doing if Hypothesis A re-surfaces post-deploy.
- **Shorten `EFFICIENCY_REFRESH_DISPATCH_TIMEOUT`** from 15 min to 2-3 min for faster orphaned-key recovery.
- **`X-Efficiency-Hydration-Stale-Player-Ids` debug header** — observability nice-to-have, not load-bearing.

## Verification once a fix lands

1. Reproduce on the previously-wedged clan: open the player detail page, watch the banner.
2. Banner should decrement to zero within 3-4 poll cycles (15-30s) for a clan with ~30 stale members.
3. `X-Efficiency-Hydration-Pending` header should monotonically decrease across polls.
4. Celery worker logs should show `update_player_efficiency_data_task` succeeding for the same player_ids the previous poll reported as pending.

## Related runbooks

- `runbook-player-page-load-priority.md` — chart-fetch coordination and 6s poll backoff
- `runbook-enrichment-crawler-2026-04-03.md` — worker memory pressure and OOM history (relevant to hypothesis B/C)
