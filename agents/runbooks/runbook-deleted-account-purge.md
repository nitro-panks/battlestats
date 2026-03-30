# Runbook: Deleted Account Purge (GDPR / WG Account Deletion Request)

**Created**: 2026-03-30
**Status**: Implemented — pending deploy and execution

## Problem Statement

Wargaming sent a list of 11,839 account IDs (`deleted_accounts.zip` containing `accounts.csv`) whose accounts have been deleted. All data associated with these IDs must be purged from BattleStats, and the IDs must be permanently blocked from re-entering the system.

---

## Scope & Magnitude

### Input
- **File**: `deleted_accounts.zip` -> `accounts.csv` (CSV with `account_id` column)
- **Count**: 11,839 unique Wargaming account IDs (all numeric, all unique)

### Database footprint per player

| Table | Relation | Cascade? | Estimated rows per player |
|-------|----------|----------|--------------------------|
| `warships_player` | Direct | N/A | 1 |
| `warships_snapshot` | FK `player_id` -> Player | CASCADE | 0-365 (daily snapshots) |
| `warships_playerachievementstat` | FK `player_id` -> Player | CASCADE | 0-50 |
| `warships_playerexplorersummary` | OneToOne `player_id` -> Player | CASCADE | 0-1 |
| `warships_entityvisitevent` | `entity_type='player'` + `entity_id` | Manual | 0-hundreds |
| `warships_entityvisitdaily` | `entity_type='player'` + `entity_id` | Manual | 0-hundreds |
| `warships_clan.leader_id` | Integer (not FK) | Manual | 0-1 |

**CASCADE behavior**: Deleting a Player row automatically cascades to Snapshot, PlayerAchievementStat, and PlayerExplorerSummary. EntityVisit* tables use a generic `entity_id` integer field and must be deleted manually.

### Cache keys per player

| Pattern | Storage |
|---------|---------|
| `player:detail:v1:{player_id}` | Redis |
| `clan_battles:player:{player_id}` | Redis |
| `warships:tasks:update_ranked_data_dispatch:{player_id}` | Redis |
| `warships:tasks:update_player_clan_battle_data_dispatch:{player_id}` | Redis |
| `warships:tasks:update_player_efficiency_data_dispatch:{player_id}` | Redis |
| `warships:tasks:update_player_data::{player_id}:lock` | Redis |
| `warships:tasks:update_battle_data::{player_id}:lock` | Redis |
| `player:refresh_dispatched:{player_id}` | Redis |

Also remove from list-type keys: `recently_viewed:players:v1`, `landing:queue:players:random:v1`, `landing:queue:players:random:eligible:v1`.

### Re-entry vectors (must be blocked)

All Player creation flows through these entry points:

1. **`get_or_create_canonical_player(player_id)`** in `player_records.py:149` — used by clan crawl (`clan_crawl.py:147`) and clan member sync (`data.py:4413`)
2. **`Player.objects.get_or_create(player_id=...)`** in `views.py:155` — used by user-initiated player search

Both must check a blocklist before creating.

### Blocklist design

A new `DeletedAccount` model stores purged IDs with a unique constraint on `account_id`. The gatekeeper function and the views.py lookup both check this table (via a cached set) before creating any Player record.

---

## Implementation (completed)

### Phase 1: Blocklist model + migration

- `DeletedAccount` model in `models.py` with `account_id` (IntegerField, unique) and `deleted_at` (DateTimeField, auto_now_add)
- Migration: `0035_deletedaccount.py`
- Cached blocklist in `blocklist.py`: `is_account_blocked()` checks an in-memory set (5-min TTL via Django cache)

### Phase 2: Block re-entry at all 3 ingestion points

1. `player_records.py`: `get_or_create_canonical_player()` raises `BlockedAccountError` before creation
2. `views.py`: `PlayerViewSet.get_object()` returns 404 for blocked IDs before `Player.objects.get_or_create()`
3. `data.py` + `clan_crawl.py`: `try/except BlockedAccountError` with `continue` — silently skips blocked IDs during clan member sync and crawl

### Phase 3: Management command `purge_deleted_accounts`

```bash
python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip
python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip --transcript /path/to/output.jsonl
python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip --dry-run
```

Execution order:
1. Parse CSV from zip or plain CSV file
2. Bulk-create `DeletedAccount` rows (blocklist activated immediately, prevents re-entry during purge)
3. For each account: delete Player (CASCADE), EntityVisitEvent, EntityVisitDaily, null Clan.leader_id, delete cache keys
4. Write per-account JSONL transcript + summary line

### Phase 4: Transcript output

Per-account detail line:
```json
{"account_id": 1063882911, "found": true, "player_name": "SomePlayer", "player_pk": 42, "rows_deleted": {"player": 1, "snapshots": 42, "achievements": 12, "explorer_summary": 1, "visit_events": 7, "visit_daily": 3}, "cache_keys_deleted": 8, "clan_leader_nulled": false, "blocklisted": true}
```

Not-found line:
```json
{"account_id": 999999999, "found": false, "blocklisted": true}
```

Summary line:
```json
{"summary": true, "total_ids": 11839, "found_in_db": 4231, "not_found": 7608, "total_player_rows": 4231, "total_snapshot_rows": 52410, "total_cache_keys_deleted": 33848, "blocked": 11839}
```

---

## Post-purge verification

1. `SELECT COUNT(*) FROM warships_player WHERE player_id IN (...)` — must return 0
2. `SELECT COUNT(*) FROM warships_deletedaccount` — must equal 11,839
3. `SELECT COUNT(*) FROM warships_entityvisitevent WHERE entity_type='player' AND entity_id IN (...)` — must return 0
4. `SELECT COUNT(*) FROM warships_clan WHERE leader_id IN (...)` — must return 0 or leader_id is NULL
5. Confirm transcript file exists and has expected line count

---

## Rollback

The blocklist (`DeletedAccount`) is permanent and should not be rolled back. Player data deletion is irreversible by design — this is a compliance operation.

---

## Files modified

| File | Change |
|------|--------|
| `server/warships/models.py` | Added `DeletedAccount` model |
| `server/warships/blocklist.py` | New — cached blocklist lookup (`is_account_blocked()`) |
| `server/warships/player_records.py` | `BlockedAccountError` + blocklist check in `get_or_create_canonical_player()` |
| `server/warships/views.py` | Blocklist check in `PlayerViewSet.get_object()` before `get_or_create` |
| `server/warships/data.py` | `try/except BlockedAccountError` in `update_clan_data()` and `update_clan_members()` |
| `server/warships/clan_crawl.py` | `try/except BlockedAccountError` in `save_player()` |
| `server/warships/management/commands/purge_deleted_accounts.py` | New management command |
| `server/warships/migrations/0035_deletedaccount.py` | Auto-generated migration |
| `server/warships/tests/test_purge_deleted_accounts.py` | Tests for parsing, blocklist, gates, and full purge flow |
