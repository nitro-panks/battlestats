# Runbook: Recent-players landing surface — switch to recency-ordered + battles-floor

_Created: 2026-05-04_
_Status: planned (about to ship in this session)_

## Context

The landing **Recent** pill currently surfaces the top 40 players by trailing 7-day random-battle volume (`week_battles` desc). That shipped on 2026-05-03 (`v1.12.5`/`feat(landing): switch recent-players surface to 7-day random-battles leaders`) replacing the prior `last_random_battle_at`-ordered list. Since then we layered:

- a hidden-stats filter (`v1.12.9`),
- a phantom first-observation guard (week_battles ≤ pvp_battles + 50, ≤ 1500 absolute, `v1.12.5`),
- a 3h durable-cache rebuild via `warm_landing_recent_players_task`.

The user wants the surface to read more like an "active right now" feed: 25 most-recently-active random-battle players who have crossed a real-activity floor. This runbook captures that change end-to-end.

## What changes

| Aspect | Before | After |
|---|---|---|
| Order | `week_battles` desc | `last_random_battle_at` desc (most-recent first) |
| Filter | `0 < week_battles ≤ 1500`, `week_battles ≤ pvp_battles + 50`, `is_hidden=False`, `realm=...` | `week_battles > 10` AND existing filters |
| Limit | 40 | 25 |
| Source columns | `PlayerDailyShipStats(mode='random', date>=today-7)` aggregated → top 40 | Same aggregation produces the *eligible set*; final ordering uses `Player.last_random_battle_at` |
| Cache key | `landing:recent_players:active7d:v3` | `landing:recent_players:recent25:v1` |
| Periodic warmer | `recent-players-warmer-{realm}` every 180 min, unchanged | unchanged |

Frontend continues to render via `LandingPlayer[]` shape; the row contract still includes `week_battles` (downstream JS may surface it). No client redeploy required for the contract — only the on-device cache TTL comment, which already lines up.

## Why these specific values

- **>10 battles** — high enough to filter out one-off "logged in for 3 random matches" sessions while keeping anyone who actually had a session within the week. Single-night casual play is ~5–15 battles; >10 surfaces "had at least a real session in the last 7 days."
- **25** — denser than 40, gives the landing card a punchier feel and avoids long scroll. Mirrors the existing `LANDING_PLAYER_LIMIT = 25` for Best/Random modes.
- **Recency by `last_random_battle_at`** — that column is already maintained by the `BattleEvent` capture hook for randoms (`incremental_battles.py:920`); no new write path needed.

## Phases

| # | Title | Class | Risk | Effort |
|---:|---|---|---|---|
| 1 | Update `_build_recent_players` query + constants | code | low | ~15 min |
| 2 | Update tests under `LandingRecentPlayersWeekActivityTests` (rename → `RecencyFilter`) | test | low | ~10 min |
| 3 | Run lean release gate (4 backend pytest files + frontend `npm test`) | QA | none | ~3 min |
| 4 | Bump cache key to `recent25:v1`; commit, `release.sh patch`, deploy backend + client | ship | low | ~5 min |
| 5 | Live verification — probe `/api/landing/recent/?realm=na`: count == 25, all rows have `week_battles > 10`, no phantom outliers, no hidden players | check | none | ~2 min |

### Phase 1 — code

Edits to `server/warships/landing.py`:

```python
LANDING_RECENT_PLAYERS_LIMIT = 25                     # was 40
LANDING_RECENT_PLAYERS_MIN_WEEK_BATTLES = 10          # NEW
LANDING_RECENT_PLAYERS_CACHE_KEY = 'landing:recent_players:recent25:v1'  # bump from active7d:v3
```

`_build_recent_players` rewrite:

1. SQL aggregation runs unchanged (over `PlayerDailyShipStats`, mode=random, date>=floor, realm match, is_hidden=False, week_battles between MIN+1 and MAX).
2. Pull eligible `player_pk` set.
3. Hydrate Players ordered by `F('last_random_battle_at').desc(nulls_last=True)`, with `last_random_battle_at__isnull=False` filter (a player without a recorded battle timestamp can't be ordered — should be vanishingly rare given they passed the >10 battles filter).
4. Apply phantom-first-observation guard (`week_battles > pvp_battles + 50` → drop) row-by-row.
5. Truncate at LIMIT.

`only(...)` field list extended with `'last_random_battle_at'`.

### Phase 2 — tests

`server/warships/tests/test_landing.py`:

- Rename `LandingRecentPlayersWeekActivityTests` → `LandingRecentPlayersRecencyFilterTests` (the class name should match what it now tests).
- Replace `test_orders_by_total_battles_in_lookback_window_descending` with `test_orders_by_last_random_battle_at_descending` (seed 3 players who all clear the >10 floor, assert recency-desc).
- Replace lookback assertions with min-battles ones; rename `test_excludes_rows_above_absolute_max_week_battles` stays valid as-is.
- Update `_make_player` helper to accept `last_random_battle_at`.
- Update the limit-25 view test in `test_views.py` (`test_landing_recent_players_orders_by_week_battles_desc_and_limits_to_40` → `_orders_by_recency_and_limits_to_25`).

### Phase 3 — release gate

```bash
cd server
DJANGO_SECRET_KEY=... DB_ENGINE=sqlite3 ... python -m pytest --nomigrations \
  warships/tests/test_views.py warships/tests/test_landing.py \
  warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py \
  -x --tb=short
```

Frontend: `cd client && npm test` (no contract change but cheap to confirm).

### Phase 4 — ship

```bash
git add ... && git commit -m "feat(landing): recent-players surface = 25 most-recent active >10/wk"
./scripts/release.sh patch
./server/deploy/deploy_to_droplet.sh battlestats.online
./client/deploy/deploy_to_droplet.sh battlestats.online   # per CLAUDE.md mandatory pragma
```

### Phase 5 — verify live

```bash
curl -s 'https://battlestats.online/api/landing/recent/?realm=na' | python -c "
import json, sys
rows = json.load(sys.stdin)
print('rows:', len(rows))
print('all >10 battles:', all(r['week_battles'] > 10 for r in rows))
print('any hidden:', any(r['is_hidden'] for r in rows))
print('top-3 by order:')
for r in rows[:3]:
    print(' ', r['name'], 'wb=', r['week_battles'])
"
```

Expected: 25 rows, all with week_battles > 10, no hidden players, top-3 are the most-recently active.

## Rollback

Revert the commit, redeploy backend + client. The prior `active7d:v3` cache key still has its 3h warmer registered — last value will repopulate on the next tick.

## Out of scope

- EU + ASIA — same code path serves all realms; the 3h warmer fires per-realm; no additional work.
- Frontend label or copy changes — the button still says "Recent" and "recent" semantically still applies; no visible-text edit.
