# Runbook: KDR Field Backfill

**Created**: 2026-03-31
**Status**: Complete — root cause fixed in v1.2.15, backfill executed 2026-03-31

## Context

Players viewed on the site were showing missing KDR (Kill/Death Ratio) numbers, including active players with thousands of battles. Example: `therock86` had no `actual_kdr` despite 10K+ PVP battles.

## Root Cause

The clan crawl (`clan_crawl.py:save_player()`) was the primary ingestion path for ~98% of players. It persisted `pvp_battles`, `pvp_wins`, `pvp_losses` but **never persisted** `pvp_frags` or `pvp_survived_battles` — the two fields needed to compute KDR.

Without `pvp_frags` and `pvp_survived_battles`, `_calculate_actual_kdr()` could not be called, leaving `pvp_deaths` and `actual_kdr` NULL for all crawl-ingested players.

The only players with KDR were those who had been individually refreshed via the player detail API view, which uses a separate code path that did compute KDR.

### KDR formula

```
pvp_deaths = pvp_battles - pvp_survived_battles
actual_kdr = pvp_frags / pvp_deaths  (NULL if pvp_deaths == 0)
```

Implemented in `warships/data.py:_calculate_actual_kdr()`.

## Fix (v1.2.15)

### 1. Clan crawl root cause fix (`clan_crawl.py:save_player()`)

Added field persistence and KDR computation to the crawl ingestion path:

```python
player.pvp_frags = pvp.get("frags", 0)
player.pvp_survived_battles = pvp.get("survived_battles", 0)
# ... existing pvp_ratio and pvp_survival_rate computation ...
from warships.data import _calculate_actual_kdr
player.pvp_deaths, player.actual_kdr = _calculate_actual_kdr(
    player.pvp_battles, player.pvp_frags, player.pvp_survived_battles,
)
```

### 2. On-demand backfill trigger (`views.py`)

Added `needs_kdr_backfill` detection in both cache-hit and cache-miss paths of `PlayerViewSet`. When a player is viewed with `actual_kdr IS NULL` and `pvp_battles > 0` and `is_hidden = False`, a forced refresh task is dispatched to populate KDR immediately.

### 3. Batch backfill management command

Created `server/warships/management/commands/backfill_player_kdr.py` for one-time mass backfill of all affected players.

## Backfill Execution (2026-03-31)

### Command

```bash
python manage.py backfill_player_kdr
python manage.py backfill_player_kdr --batch-size 50 --limit 1000 --dry-run  # test mode
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--batch-size` | 100 | Players per WG API request (max 100) |
| `--limit` | 0 (all) | Max players to process |
| `--dry-run` | off | Report counts without API calls or DB writes |
| `--rate-delay` | 0.25s | Seconds between API batches |

### How it works

1. Queries `Player` rows with `is_hidden=False, actual_kdr IS NULL, pvp_battles > 0`
2. Batches player IDs into groups of 100
3. Calls WG API `account/info/` with `fields=statistics.pvp.frags,statistics.pvp.survived_battles,statistics.pvp.battles,hidden_profile`
4. Computes `pvp_deaths` and `actual_kdr` via `_calculate_actual_kdr()`
5. Uses `Player.objects.bulk_update()` to write `pvp_frags`, `pvp_survived_battles`, `pvp_deaths`, `actual_kdr`

### Results

```
Players eligible for backfill: 247,970
Players successfully updated:  258,661 (includes some updated by concurrent crawl)
Players unfillable:            816 (deleted/hidden accounts returning no API data)
Coverage:                      99.7%
```

The 816 unfillable players are accounts that the WG API returns no data for (deleted or newly hidden). These will be caught by the on-demand backfill trigger if anyone views their profile, or cleaned up by future crawls.

## Verification

```bash
# Confirm KDR populated for a previously-missing player
curl -s https://battlestats.online/api/player/therock86/ | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'actual_kdr: {d.get(\"actual_kdr\")}')"
# Expected: actual_kdr: 1.58

# Count remaining nulls (should be small, mostly hidden/deleted)
python manage.py shell -c "
from warships.models import Player
print(Player.objects.filter(is_hidden=False, pvp_battles__gt=0, actual_kdr__isnull=True).count())"
```

## Files Modified

| File | Change |
|------|--------|
| `server/warships/clan_crawl.py` | Added `pvp_frags`, `pvp_survived_battles` persistence + KDR computation in `save_player()` |
| `server/warships/views.py` | Added `needs_kdr_backfill` detection in cache-hit and cache-miss paths |
| `server/warships/management/commands/backfill_player_kdr.py` | New — one-time batch backfill command |

## Future

The management command can be safely re-run if needed (idempotent — only processes players with `actual_kdr IS NULL`). The clan crawl fix ensures all future ingestions compute KDR automatically. The on-demand trigger in views.py handles any stragglers on first view.
