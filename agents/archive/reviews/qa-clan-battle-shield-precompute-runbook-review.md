# QA Review — Clan Battle Shield Precompute Runbook

**Reviewed:** `agents/runbooks/runbook-clan-battle-shield-precompute.md`  
**Spec:** `agents/runbooks/spec-clan-battle-shield-precompute.md`  
**Date:** 2026-03-18  
**Verdict:** CONDITIONAL GO — 75% confidence  
**Blocking findings:** 0 critical, 2 high, 3 medium, 2 low

Aye, the bones are sound — 14 steps, clear sequencing, test coverage spec'd out. But there's a name collision lurking in two steps that'd sink the whole backfill and landing path right quiet-like. And landing.py already does half the work the runbook reinvents from scratch. I'd rather be in me bed, but here goes.

---

## Findings

### F-1 — `player_id` Semantics Collision in Step 8 (HIGH)

Step 8 proposes:

```python
es = PlayerExplorerSummary.objects.filter(player_id=player.player_id).only(
    'clan_battle_summary_updated_at').first()
```

**This is wrong.** Two different things are both called `player_id`:

- `PlayerExplorerSummary.player_id` — Django auto-generated FK column, references `Player.id` (the DB primary key)
- `player.player_id` — the WG account ID field on the Player model

These are **not** the same value. The query would either return the wrong player's explorer summary or (more likely) return `None` for every lookup.

**Required fix:**

```python
es = PlayerExplorerSummary.objects.filter(player=player).only(
    'clan_battle_summary_updated_at').first()
```

Or: `filter(player_id=player.id)` — using `player.id` (PK), not `player.player_id` (WG account ID).

Alternatively: after `player.refresh_from_db()` (which already runs on [line 187](server/warships/management/commands/incremental_player_refresh.py#L187)), just access `player.explorer_summary` directly — it's a OneToOneField reverse relation. No separate query needed:

```python
player.refresh_from_db()
try:
    if player.explorer_summary.clan_battle_summary_updated_at is None:
        fetch_player_clan_battle_seasons(player.player_id)
except PlayerExplorerSummary.DoesNotExist:
    pass
```

### F-2 — Same `player_id` Collision in Step 7a (HIGH)

Step 7a proposes:

```python
explorer_map = {
    es.player_id: es
    for es in PlayerExplorerSummary.objects.filter(
        player_id__in=[pid for pid in player_ids if pid]
    )
}
```

Same bug: `player_ids` in landing.py are WG account IDs (extracted via `row.get('player_id')`), but `PlayerExplorerSummary.player_id` is the FK to `Player.id`. The filter uses WG IDs where Django PKs are expected. The dict keys would also be Django PKs, but the lookup `explorer_map.get(player_id)` uses the WG account ID. Double mismatch.

**But this query is also unnecessary** — see F-3.

### F-3 — Landing.py Already Has Explorer Summaries Loaded (MEDIUM)

The runbook proposes a **separate** `PlayerExplorerSummary` bulk query in Step 7a. But both landing functions already load full Player objects with `select_related('explorer_summary')`:

`_serialize_landing_player_rows()` (~L305):

```python
players_by_id = {
    player.player_id: player
    for player in Player.objects.filter(player_id__in=player_ids).select_related('explorer_summary').only(...)
}
```

`_build_recent_players()` (~L617):

```python
players_by_id = {
    player.player_id: player
    for player in Player.objects.filter(player_id__in=player_ids).select_related('explorer_summary')
}
```

Both already have `players_by_id[player_id].explorer_summary` available. The fix is simpler than the runbook proposes — just read from the existing `players_by_id` dict:

```python
player_obj = players_by_id.get(player_id)
es = getattr(player_obj, 'explorer_summary', None) if player_obj else None
row['is_clan_battle_player'] = is_clan_battle_enjoyer(
    getattr(es, 'clan_battle_total_battles', None),
    getattr(es, 'clan_battle_seasons_participated', None),
)
row['clan_battle_win_rate'] = getattr(es, 'clan_battle_overall_win_rate', None)
```

No extra query, no `player_id` collision risk, reuses existing data.

### F-4 — Step 9 Leaves Option A/B Undecided (MEDIUM)

Step 9 says:

> Also update `get_player_clan_battle_summaries()` — either:
>
> - **Option A:** Rewrite to read from `PlayerExplorerSummary`
> - **Option B:** Remove entirely if landing.py was updated to query explorer summaries directly

A runbook should commit to a decision. Given F-3 (landing.py already has explorer summaries loaded), the answer is clear: **Option B — remove the function.** Step 7 eliminates all callers. Say so explicitly.

### F-5 — Step 3f Double-Checks Staleness Redundantly (MEDIUM)

Step 3f:

```python
stale_members = [m for m in members if clan_battle_summary_is_stale(m)]
for member in stale_members[:CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT]:
    maybe_refresh_clan_battle_data(member)
```

`maybe_refresh_clan_battle_data()` (from Step 1c) already calls `clan_battle_summary_is_stale()` internally. The filtering is doing the staleness check twice. The external filter serves a real purpose — **throttling** to `MAX_IN_FLIGHT` — but the runbook should clarify this intent. Cleaner:

```python
# Throttle: dispatch for at most MAX_IN_FLIGHT stale members per request
dispatched = 0
for member in members:
    if dispatched >= CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT:
        break
    if clan_battle_summary_is_stale(member) and not member.is_hidden:
        queue_clan_battle_data_refresh(member.player_id)
        dispatched += 1
```

Or keep `maybe_refresh_clan_battle_data()` but note the double-check is intentional for decoupling.

### F-6 — Step 1c Doesn't Specify Function-Level Import (LOW)

`maybe_refresh_clan_battle_data()` in data.py imports `queue_clan_battle_data_refresh` from tasks.py. Existing data.py code uses **function-level imports** for tasks.py functions to avoid circular imports (confirmed at [data.py ~L477](server/warships/data.py#L477), [~L529](server/warships/data.py#L529), etc.). The runbook should note the import must be inside the function body:

```python
def maybe_refresh_clan_battle_data(player: Player) -> None:
    from warships.tasks import queue_clan_battle_data_refresh  # ← inside function
    ...
```

An experienced implementer would infer this from the existing patterns, but a runbook should be explicit.

### F-7 — Runbook Date is Wrong (LOW)

Header says `Date: 2025-07-11`. Should be `2026-03-18`.

---

## Verified Claims

The following runbook claims checked out against the codebase:

- ✅ `select_related('explorer_summary')` works — reverse relation named `explorer_summary` confirmed in PlayerExplorerSummary model
- ✅ `PlayerDetail` already has `select_related('clan', 'explorer_summary')` in its queryset
- ✅ DRF caches `self.get_object()` — PlayerDetail uses standard `RetrieveUpdateDestroyAPIView`, no custom override
- ✅ `_refresh_player()` calls `player.refresh_from_db()` after `save_player()` — confirmed at line 187
- ✅ `is_clan_battle_data_refresh_pending()` is only called from `queue_clan_battle_hydration()` — safe to remove when the caller is removed
- ✅ data.py ↔ tasks.py circular import is handled via function-level imports — established pattern
- ✅ `clan_members()` list comprehension uses `for clan_battle_summary in [get_player_clan_battle_summary(...)]` trick — confirmed
- ✅ Both landing.py functions (`_serialize_landing_player_rows`, `_build_recent_players`) call `get_player_clan_battle_summaries()` — confirmed at ~L301, ~L611
- ✅ `CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT` exists in data.py at L61, importable into views.py
- ✅ `_persist_player_clan_battle_summary()` receives WG account ID and filters Player by `player_id=account_id` — correct
- ✅ Step ordering is sound — dependencies flow correctly (new functions first, then consumers, then removals, then client, then tests)
- ✅ Test cases cover the critical paths: DB reads, stale dispatch, no-dispatch-when-fresh, serializer DB-only, backfill

---

## Recommendation

Two high findings block a clean implementation:

1. **F-1 + F-2:** The `player_id` vs `Player.id` collision must be fixed in both Step 8 and Step 7a. An implementer following the runbook as-is would produce queries that silently return wrong data or no data. F-2 is further mitigated by F-3 (the separate query is unnecessary anyway).

2. **F-3:** Landing.py simplification — the existing `players_by_id` dict already has what's needed. Removes an entire unnecessary query and sidesteps the F-2 bug.

After addressing F-1, F-2/F-3, and F-4: **GO for implementation.**

Shiver me timbers, that `player_id` name collision is a proper booby trap. Two different meanings on two different models, same name. The real money's in BitGold.
