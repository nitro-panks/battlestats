# Runbook: Incremental Player Refresh — Phase 1 Implementation

**Date:** 2026-03-18  
**Spec:** `agents/runbooks/spec-production-data-refresh-strategy.md`  
**QA Review:** 2026-03-18 — Conditional GO, Critical/High findings addressed in spec  
**Scope:** Implement `incremental_player_refresh` management command and Celery task

---

## Prerequisites

- [ ] Read the full spec: `agents/runbooks/spec-production-data-refresh-strategy.md`
- [ ] Confirm Docker stack is running (`bounce`)
- [ ] Confirm existing tests pass: `cd server && python manage.py test warships.tests --keepdb`

---

## Step 1: Measure Live Population Per Tier

Before writing code, measure actual tier sizes to validate the spec's capacity assumptions.

```bash
docker compose exec -T server python manage.py shell -c "
from django.utils import timezone
from datetime import timedelta
from warships.models import Player

now = timezone.now()
total = Player.objects.count()
hot = Player.objects.filter(last_lookup__gte=now - timedelta(days=14)).count()
active = Player.objects.filter(
    last_battle_date__gte=(now - timedelta(days=30)).date()
).exclude(last_lookup__gte=now - timedelta(days=14)).count()
warm = Player.objects.filter(
    last_battle_date__gte=(now - timedelta(days=90)).date(),
    last_battle_date__lt=(now - timedelta(days=30)).date()
).count()
dormant = total - hot - active - warm

print(f'Total: {total}')
print(f'Hot (site visitors, 14d): {hot}')
print(f'Active (battled 30d, not hot): {active}')
print(f'Warm (battled 30-90d): {warm}')
print(f'Dormant (>90d or null): {dormant}')
print()
print(f'At ACTIVE_LIMIT=500, 2 cycles/day: {active} active / 1000 = {active/1000:.1f} days to full coverage')
print(f'At WARM_LIMIT=200, 2 cycles/day: {warm} warm / 400 = {warm/400:.1f} days to full coverage')
"
```

Record results. If Active > 1,000, adjust `PLAYER_REFRESH_ACTIVE_LIMIT` upward in the implementation.

---

## Step 2: Add `last_fetch` Index Migration

Create a Django migration adding an index on `Player.last_fetch`:

**File:** `server/warships/migrations/0032_player_last_fetch_index.py`

The migration should add a B-tree index on `last_fetch`. Validate with:

```bash
docker compose exec -T server python manage.py migrate
```

---

## Step 3: Implement `incremental_player_refresh` Management Command

**File:** `server/warships/management/commands/incremental_player_refresh.py`

Model after `incremental_ranked_data.py`. Key implementation notes:

### Candidate Selection

```python
# Tier 1: Hot — site visitors within 14 days, stale > 12 hours
hot_ids = list(
    Player.objects.filter(
        last_lookup__gte=now - timedelta(days=hot_lookback_days),
        # last_fetch is null OR older than hot_stale_hours
    ).filter(
        Q(last_fetch__isnull=True) | Q(last_fetch__lt=now - timedelta(hours=hot_stale_hours))
    ).order_by(
        F('last_lookup').desc(nulls_last=True),
        F('last_fetch').asc(nulls_first=True),
    ).values_list('id', flat=True)
)

# Tier 2: Active — battled within 30 days, stale > 24 hours, excluding Hot
active_ids = list(
    Player.objects.filter(
        last_battle_date__gte=(now - timedelta(days=active_lookback_days)).date(),
    ).filter(
        Q(last_fetch__isnull=True) | Q(last_fetch__lt=now - timedelta(hours=active_stale_hours))
    ).exclude(
        id__in=hot_ids
    ).order_by(
        F('last_battle_date').desc(nulls_last=True),
        F('pvp_battles').desc(nulls_last=True),
    ).values_list('id', flat=True)[:active_limit]
)

# Tier 3: Warm — battled within 90 days, stale > 72 hours
warm_ids = list(
    Player.objects.filter(
        last_battle_date__gte=(now - timedelta(days=warm_lookback_days)).date(),
        last_battle_date__lt=(now - timedelta(days=active_lookback_days)).date(),
    ).filter(
        Q(last_fetch__isnull=True) | Q(last_fetch__lt=now - timedelta(hours=warm_stale_hours))
    ).order_by(
        F('last_battle_date').desc(nulls_last=True),
    ).values_list('id', flat=True)[:warm_limit]
)
```

### Per-Player Refresh

Players are fetched **one at a time** via `fetch_players_bulk([player_id])` — the same single-player pattern used by `incremental_ranked_data.py`. This deliberate choice enables per-player error isolation and fine-grained checkpoint saves (each player is checkpointed immediately). Bulk-batching would reduce API calls (~12 vs ~1200 for a 1200-player run) but complicates checkpoint resume and error attribution. Consider batch optimization in a future pass if wall-clock time becomes a concern.

```python
from warships.clan_crawl import save_player, fetch_players_bulk

def _refresh_player(player_id: int) -> None:
    player = Player.objects.filter(id=player_id).select_related('clan').first()
    if player is None:
        return
    player_map = fetch_players_bulk([player.player_id])
    player_data = player_map.get(str(player.player_id))
    if player_data is None:
        return
    save_player(player_data, clan=player.clan)
    player.refresh_from_db()
    if not player.is_hidden:
        if player_efficiency_needs_refresh(player):
            update_player_efficiency_data(player)
        if player_achievements_need_refresh(player):
            update_achievements_data(player.player_id)
```

### Checkpoint & Error Budget

Follow `incremental_ranked_data.py` patterns exactly:

- JSON state file at `logs/incremental_player_refresh_state.json`
- Track: `pending_player_ids`, `current_index`, `failed_player_ids`, `error_count`, `tier_counts`
- Halt when `error_count >= max_errors`
- Resume from `current_index` on next invocation

### Lock Exclusion

```python
from django.core.cache import cache
from warships.tasks import CLAN_CRAWL_LOCK_KEY

if cache.get(CLAN_CRAWL_LOCK_KEY) is not None:
    self.stdout.write("Clan crawl in progress — skipping this cycle.")
    return
```

### CLI Arguments

```
--limit              Total player limit per cycle (default: env PLAYER_REFRESH_TOTAL_LIMIT or 1200)
--hot-stale-hours    Hot tier staleness (default: env PLAYER_REFRESH_HOT_STALE_HOURS or 12)
--active-stale-hours Active tier staleness (default: env PLAYER_REFRESH_ACTIVE_STALE_HOURS or 24)
--warm-stale-hours   Warm tier staleness (default: env PLAYER_REFRESH_WARM_STALE_HOURS or 72)
--active-limit       Active tier cap (default: env PLAYER_REFRESH_ACTIVE_LIMIT or 500)
--warm-limit         Warm tier cap (default: env PLAYER_REFRESH_WARM_LIMIT or 200)
--max-errors         Error budget (default: env PLAYER_REFRESH_MAX_ERRORS or 25)
--state-file         Checkpoint path (default: logs/incremental_player_refresh_state.json)
--dry-run            Print candidates without refreshing
```

---

## Step 4: Implement Celery Task + Beat Schedule

**File:** `server/warships/tasks.py`

Add:

```python
@app.task(**CRAWL_TASK_OPTS, name="warships.tasks.incremental_player_refresh_task")
def incremental_player_refresh_task():
    from warships.management.commands.incremental_player_refresh import Command
    call_command("incremental_player_refresh")
```

**File:** `server/warships/signals.py`

Add schedule entry in `ensure_daily_clan_crawl_schedule()` (or a new `ensure_incremental_schedules()` function):

```python
PeriodicTask.objects.update_or_create(
    name="incremental-player-refresh-am",
    defaults={
        "task": "warships.tasks.incremental_player_refresh_task",
        "crontab": CrontabSchedule.objects.get_or_create(
            hour=PLAYER_REFRESH_SCHEDULE_HOUR_AM,  # default 5
            minute="0",
        )[0],
        "enabled": True,
    }
)
PeriodicTask.objects.update_or_create(
    name="incremental-player-refresh-pm",
    defaults={
        "task": "warships.tasks.incremental_player_refresh_task",
        "crontab": CrontabSchedule.objects.get_or_create(
            hour=PLAYER_REFRESH_SCHEDULE_HOUR_PM,  # default 15
            minute="0",
        )[0],
        "enabled": True,
    }
)
```

---

## Step 5: Write Tests

**File:** `server/warships/tests/test_incremental_player_refresh.py`

Required test cases:

### Candidate Selection

- [ ] Hot tier: player with `last_lookup` 3 days ago and `last_fetch` 13 hours ago → selected
- [ ] Hot tier: player with `last_lookup` 3 days ago and `last_fetch` 6 hours ago → excluded (fresh)
- [ ] Hot tier: player with `last_lookup` 20 days ago → excluded (not Hot)
- [ ] Active tier: player with `last_battle_date` 10 days ago and `last_fetch` 25 hours ago → selected
- [ ] Active tier: player already in Hot set → excluded from Active
- [ ] Warm tier: player with `last_battle_date` 60 days ago and `last_fetch` 80 hours ago → selected
- [ ] Dormant: player with `last_battle_date` 200 days ago → excluded from all tiers
- [ ] Boundary: player with `last_battle_date` exactly 30 days ago → Active tier (inclusive)
- [ ] Boundary: player with `last_battle_date` exactly 90 days ago → Warm tier boundary

### Ordering & Caps

- [ ] Active tier respects `active_limit` and orders by `last_battle_date DESC`
- [ ] Hot tier is uncapped (all qualifying players included)

### Checkpoint Durability

- [ ] Checkpoint saves after each batch
- [ ] Resumed run skips already-processed players
- [ ] Fresh run ignores stale checkpoint

### Error Budget

- [ ] Stops processing after `max_errors` exceeded
- [ ] Error count carries through checkpoint

### Lock Exclusion

- [ ] Skips cycle when `CLAN_CRAWL_LOCK_KEY` is set
- [ ] Runs normally when lock is absent

### Hidden Players

- [ ] Hidden player is included in candidates
- [ ] `save_player()` clears efficiency/verdict for hidden player

### Integration

- [ ] Full cycle: creates candidates → fetches → saves → updates achievements/efficiency
- [ ] Dry-run mode: logs candidates without API calls

---

## Step 6: Manual Validation

After tests pass:

```bash
# Dry run — confirm candidate selection looks right
docker compose exec -T server python manage.py incremental_player_refresh --dry-run

# Single live run — small limit
docker compose exec -T server python manage.py incremental_player_refresh --limit 20

# Verify updated players
docker compose exec -T server python manage.py shell -c "
from warships.models import Player
from django.utils import timezone
from datetime import timedelta
recent = Player.objects.filter(last_fetch__gte=timezone.now() - timedelta(minutes=5)).values_list('name', 'last_fetch')[:10]
for name, fetch in recent:
    print(f'{name}: {fetch}')
"
```

---

## Step 7: Commit & Push

Pre-commit checklist (from doctrine):

- [ ] Changed behavior has test coverage
- [ ] Spec document is up to date
- [ ] No superseded runbooks to archive
- [ ] Migration is safe and reversible

```bash
cd /home/august/code/archive/battlestats
git add -A
git commit -m "Add incremental player refresh (Phase 1 of prod data refresh strategy)"
git push origin main
```

---

## Rollback

- The incremental player refresh is purely additive — it doesn't modify or disable the existing nightly crawl
- To disable: remove the Celery Beat schedule entries or set `enabled=False` on the PeriodicTask rows
- The `last_fetch` index migration is safe to leave in place (indexes are cheap to keep)

---

## Success Criteria

- [ ] `incremental_player_refresh --dry-run` reports correct tier counts matching Step 1 population measurement
- [ ] Full cycle completes within 30 minutes wall-clock time
- [ ] Hot-tier players are refreshed within 12 hours
- [ ] Active-tier players are refreshed within 24-48 hours (depending on population)
- [ ] Checkpoint file enables clean resume after interruption
- [ ] All tests pass: `python manage.py test warships.tests.test_incremental_player_refresh --keepdb`
- [ ] No increase in 429 errors from WG API during parallel-run with legacy crawl
