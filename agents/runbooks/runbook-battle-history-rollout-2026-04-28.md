# Runbook: Battle History Rollout (Playerbase, Longitudinal)

_Created: 2026-04-28_
_Context: Take the lil_boots incremental-battle PoC (`runbook-incremental-battle-poc-2026-04-27.md`) playerbase-wide as a longitudinal "your last week of battles" feature, surfaced per ship per day for any player on the site. Reuses existing refresh paths so no new WG calls are introduced._
_Status: Design draft. Depends on the PoC runbook landing first (its migration + `incremental_battles.py` orchestrator are prerequisites)._

## Purpose

Battlestats today shows running totals only. The PoC proved that diffing two consecutive snapshots of WG `account/info/` + `ships/stats/` yields per-ship per-match deltas (battles, wins, frags, damage, xp, planes_killed, survived). This rollout takes that mechanism playerbase-wide as a longitudinal record — "show me my last 7 days of battles, by ship and by day" — for any player on the site. Multi-match collapse between observations is acceptable; what matters is that **daily totals per ship are stable and trend over time**.

The PoC's "poll every 60 s" model does not scale: applied to even a small fraction of the 274 K-player base it would saturate the WG `application_id` rate budget. The rollout instead **piggybacks capture on the WG calls the site already makes** during visit-driven and incremental-crawl refreshes, then layers a denormalized daily roll-up table optimized for longitudinal reads.

## Premise: capture is a side-effect, not a poll

The Wargaming public API has no per-match endpoint. Per-battle deltas are still computed by diffing successive aggregate snapshots, exactly as in the PoC. The change vs. the PoC is **when** snapshots are taken:

- **PoC**: dedicated `poll_tracked_player_battles_task` issues 2 WG calls per tick per tracked player. Stays in place for `lil_boots` and tests.
- **Rollout**: snapshots are recorded as a side effect of `update_battle_data` (`server/warships/data.py:2365`), which already fetches `ships/stats/` and is the chokepoint for both visit-driven refreshes (`update_battle_data_task`) and the incremental crawl path (`refresh_player_detail_payloads` → `update_battle_data`). At the tail of that function the WG payload is already in scope and the `Player` row has the freshest aggregates from the most recent `update_player_data`.

Result: every player whose page is visited or whose tier rotates through the incremental crawl gets a `BattleObservation` with **no incremental WG cost**. Resolution is whatever the existing refresh cadence is (instant on visit, ~3 h via incremental crawl).

## Dependencies

This runbook is a follow-on to the PoC. Before the rollout tranche can land:

1. The PoC migration (`0051_battleobservation_battleevent.py` or equivalent) must be generated and applied. The PoC scaffolds `BattleObservation` and `BattleEvent` in `server/warships/models.py:436,473` but no migration exists yet.
2. `server/warships/incremental_battles.py` must exist with `record_observation_and_diff(player_id, realm)`. It is referenced from `server/warships/tasks.py:1339` but the file is not in tree today.
3. The PoC's `BattleObservation.ships_stats_json` shape must be widened (see "Storage shape" below) before any data is written, since the rollout's `BattleEvent.damage_delta` / `xp_delta` / `planes_killed_delta` / `survived` columns require it.

The PoC's 60-second poll loop and `BATTLE_TRACKING_PLAYER_NAMES` env var stay intact. The rollout coexists with it; they share the same orchestrator function.

## Design

### Capture: piggyback hook in `update_battle_data`

Refactor the orchestrator into two callable forms in `server/warships/incremental_battles.py`:

- `record_observation_from_payloads(player, player_data, ship_data)` — **new**. Writes a `BattleObservation` from the in-memory WG payloads, loads the previous observation, computes per-ship deltas, and writes `BattleEvent` rows. Does not issue any WG calls.
- `record_observation_and_diff(player_id, realm)` — **existing wrapper**. Fetches `account/info/` + `ships/stats/`, then calls the new function. Used by the lil_boots PoC poll task and by tests.

Hook point: tail of `update_battle_data` (`server/warships/data.py:2365`), placed after `player.save()` and `refresh_player_explorer_summary(...)`. At that point:

- `ship_data` is the raw WG payload that's already been fetched (line 2391).
- `player.pvp_battles` / `pvp_wins` / etc. are fresh on the row from the most recent `update_player_data` (which always runs before `update_battle_data` on every entry path).

Gated by env flag `BATTLE_HISTORY_CAPTURE_ENABLED` (default off). Off ⇒ the function returns immediately and the system is byte-for-byte identical to today.

```python
# at end of update_battle_data, after refresh_player_explorer_summary(...)
if os.getenv("BATTLE_HISTORY_CAPTURE_ENABLED", "0") == "1":
    from warships.incremental_battles import record_observation_from_payloads
    try:
        record_observation_from_payloads(player, player_data=None, ship_data=ship_data)
    except Exception:
        logging.exception("battle-history capture failed for %s", player.player_id)
```

(The function reads `pvp_battles` etc. from the `player` row, so the second arg can be `None` — keep the parameter for the PoC wrapper which has the raw `account/info/` payload in scope.)

The hook never raises into the refresh path: failures are logged and swallowed so a capture bug cannot regress the existing `update_battle_data` contract.

### Storage shape

#### Widened observation (modifies the PoC schema before any data lands)

The PoC's `ships_stats_json` was specced as compact `{ship_id: {battles, wins, losses, frags}}`. To support the rollout's full delta vocabulary, **before the PoC migration lands** widen the per-ship JSON shape to:

```json
{
  "<ship_id>": {
    "battles": int,
    "wins": int,
    "losses": int,
    "frags": int,
    "damage_dealt": int,
    "original_xp": int,
    "planes_killed": int,
    "survived_battles": int
  }
}
```

Stored only for ships where `pvp.battles > 0`. Most accounts have touched <100 of the ~548 ships in the game, so this filter cuts the JSON 4–8× vs. storing every ship.

No DB migration is required for this change — `ships_stats_json` is already a `JSONField`. The PoC orchestrator just writes a richer dict.

#### New table: `PlayerDailyShipStats`

Denormalized daily roll-up, optimized for "last N days per ship per player":

```python
class PlayerDailyShipStats(models.Model):
    player           = models.ForeignKey(Player, on_delete=models.CASCADE,
                                         related_name='daily_ship_stats')
    date             = models.DateField(db_index=True)
    ship_id          = models.BigIntegerField(db_index=True)
    ship_name        = models.CharField(max_length=200, blank=True, default='')
    battles          = models.IntegerField(default=0)
    wins             = models.IntegerField(default=0)
    losses           = models.IntegerField(default=0)
    frags            = models.IntegerField(default=0)
    damage           = models.BigIntegerField(default=0)
    xp               = models.BigIntegerField(default=0)
    planes_killed    = models.IntegerField(default=0)
    survived_battles = models.IntegerField(default=0)
    first_event_at   = models.DateTimeField(null=True, blank=True)
    last_event_at    = models.DateTimeField(null=True, blank=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'date', 'ship_id'],
                name='unique_player_daily_ship_stats',
            ),
        ]
        indexes = [
            models.Index(fields=['player', '-date'],
                         name='daily_ship_player_date_idx'),
            models.Index(fields=['player', 'ship_id', '-date'],
                         name='daily_ship_player_ship_date_idx'),
            models.Index(fields=['date', '-battles'],
                         name='daily_ship_date_battles_idx'),
        ]
```

This is the table the UI reads. A 7-day query for any player is `7 × ~handful-of-ships` rows, sub-millisecond at any scale.

### Aggregation: dual-writer, both idempotent

Two writers, both gated by `BATTLE_HISTORY_ROLLUP_ENABLED`, both idempotent on the `(player, date, ship_id)` unique key:

1. **On-write incremental.** When `record_observation_from_payloads` creates `BattleEvent` rows, also `update_or_create` the matching `PlayerDailyShipStats` row using `event.detected_at::date` and `+= delta` semantics. Inside the same `transaction.atomic()` block as the `BattleEvent` insert so a partial failure cannot produce phantom events.
2. **Nightly sweeper** — `roll_up_player_daily_ship_stats_task`, Celery Beat at 04:30 UTC. Walks `BattleEvent` rows for the _previous calendar day_ and rebuilds `PlayerDailyShipStats` from scratch for that date. Catches anything the on-write path missed (e.g. events whose detected_at crossed a date boundary, observations that arrived late from a delayed worker). Idempotent because it deletes-then-rewrites rows for the target date.

Both writers share a helper `_apply_event_to_daily_summary(event)` so the math lives in one place.

### Pruning

`BattleObservation` rows are heavy: each one carries a per-ship JSON (~30–60 KB). At playerbase scale this is the dominant cost.

- `cleanup_old_battle_observations_task` — Celery Beat daily, deletes `BattleObservation` rows older than `BATTLE_OBSERVATION_RETENTION_DAYS` (default 14).
- `BattleEvent` rows are small (one per detected match per ship) and stay 90 days, then prune.
- `PlayerDailyShipStats` rows are small and retained indefinitely (the durable artifact).

Pruning is the **last** rollout step, enabled only after 14 days of data exist — otherwise the cleanup task would prune the only data we have.

### API

New DRF endpoint: `GET /api/player/<player_name>/battle-history?days=7` in `server/warships/views.py` via `@api_view(['GET'])`. Kept as a separate, cacheable surface — **not** folded into the existing player-detail payload — so it can be paged, parameterized, and cached independently.

Reads only `PlayerDailyShipStats`, joined to `Ship` for tier/type display. No WG calls on the read path.

Response shape:

```json
{
  "window_days": 7,
  "as_of": "2026-04-28T04:30:00Z",
  "totals": {
    "battles": 23,
    "wins": 12,
    "losses": 11,
    "win_rate": 52.2,
    "damage": 1145200,
    "avg_damage": 49791,
    "frags": 41,
    "xp": 28411,
    "planes_killed": 3,
    "survival_rate": 47.8
  },
  "by_ship": [
    {
      "ship_id": 3761157328,
      "ship_name": "Dalian",
      "ship_tier": 9,
      "ship_type": "Destroyer",
      "battles": 6,
      "wins": 4,
      "win_rate": 66.7,
      "damage": 287400,
      "avg_damage": 47900,
      "frags": 12,
      "xp": 8203,
      "planes_killed": 0,
      "survived_battles": 3
    }
  ],
  "by_day": [
    {
      "date": "2026-04-28",
      "battles": 4,
      "wins": 2,
      "damage": 197200,
      "frags": 7
    }
  ]
}
```

Cached in Redis at `player:{realm}:{name}:battle-history:{days}`, TTL 5 min. Gated by `BATTLE_HISTORY_API_ENABLED` (default off) — when off the endpoint returns 404 so the absence is indistinguishable from a missing route.

### Frontend (deferrable)

- New `client/app/components/BattleHistoryCard.tsx`, mounted in `client/app/components/PlayerDetail.tsx` only when the response has `totals.battles > 0`.
- Renders: top-line week summary, per-ship table sorted by battles, sparkline of `by_day` with damage / win-rate dual axis.
- Reuses `client/app/lib/chartTheme.ts` palette + `client/app/lib/wrColor.ts` for the win-rate accent.

If this tranche lands backend-only first, data accumulates while the frontend is in flight — no migration headache later.

## Migration safety

- One additive migration: `CreateModel('PlayerDailyShipStats')`. No `AlterField` on existing tables, no `NOT NULL` columns added.
- Stacks on top of the PoC migration. If both tranches deploy together, the PoC migration runs first, then the rollout migration.
- Cloud-DB-safe under the same logic as the PoC: the deployed code never references the new tables until the corresponding env flag flips, so applying the migration ahead of the code rollout is safe.
- Backfill: management command `python manage.py rebuild_player_daily_ship_stats --since 2026-04-28` rebuilds rows from `BattleEvent`. No historical backfill is needed for the first week (events only exist from when capture turned on).
- Rollback: drop `PlayerDailyShipStats` (single reverse migration). No FKs from existing tables point into it.

## Rollout staging (gated, reversible)

Each stage is gated by an independent env flag so the team can stop or roll back any individual step.

1. **Capture-only.** Deploy the `record_observation_from_payloads` hook. Flip `BATTLE_HISTORY_CAPTURE_ENABLED=1` on the droplet. Observations + events accumulate; no UI yet. Watch for 2 days under real load to confirm that `BattleObservation` write volume matches expected refresh volume and that `BattleEvent` rows appear for known-active players.
2. **Roll-up.** Deploy nightly task + on-write incremental + the new table migration. Flip `BATTLE_HISTORY_ROLLUP_ENABLED=1`. Watch the table grow; spot-check daily totals against `BattleEvent` aggregates with the validation queries below.
3. **API + UI.** Flip `BATTLE_HISTORY_API_ENABLED=1` to expose `/api/player/.../battle-history`. Ship `BattleHistoryCard.tsx` in the same deploy or defer.
4. **Pruning.** Enable `cleanup_old_battle_observations_task` after 14 days of data exist.

Kill switch: unset any of the env flags. Tables remain (harmless).

## Validation

1. **Capture-only.** `BattleObservation.objects.filter(observed_at__gte=now-1h).count()` is in the hundreds (matches site refresh volume); `BattleEvent.objects.count()` grows when known-active players play.
2. **Daily aggregation correctness.** For a sample player (e.g. lil_boots), assert:
   ```sql
   SELECT SUM(battles_delta), SUM(damage_delta) FROM warships_battleevent
     WHERE player_id=? AND detected_at::date = '2026-04-28';
   -- equals --
   SELECT SUM(battles), SUM(damage) FROM warships_playerdailyshipstats
     WHERE player_id=? AND date = '2026-04-28';
   ```
   Add this as a pytest covering the on-write incremental and the nightly sweeper independently.
3. **Idempotency.** Re-running the nightly sweeper twice produces identical row counts and values (the `update_or_create` path is the only writer per `(player, date, ship_id)`).
4. **Read latency.** `/api/player/lil_boots/battle-history?days=7` returns p95 < 50 ms warm cache, < 200 ms cold.
5. **Backfill rebuild.** Drop a day's `PlayerDailyShipStats` rows for one player; run `rebuild_player_daily_ship_stats --since <day>`; confirm rows return to identical state.
6. **Pruning safety.** `cleanup_old_battle_observations_task` with retention=14 days, run with `--dry-run` first; verify it does not touch `BattleEvent` rows.

## WG API budget

No new calls. Capture is a pure side-effect of fetches that already happen for `update_player_data` / `update_battle_data`. Rate budget is unchanged from the current site.

The one cost is **storage and write amplification**: every refresh now writes a `BattleObservation` (~30–60 KB JSON). Mitigations:

- 14-day retention on `BattleObservation`.
- `ships_stats_json` only includes ships where `pvp.battles > 0` (4–8× shrink).
- Postgres `jsonb` is already efficient; if size becomes a problem, a follow-on can compress to `bytea` + zstd.

## Out of scope

- Per-match-level resolution (the WG API has no per-match endpoint; multi-match collapse is acceptable).
- Co-op / scenario / operations battles — only `pvp.battles` is tracked; non-PvP modes are silent.
- Authenticated "log in to see _your_ history" — battlestats has no auth surface today; the read path is `/api/player/<name>/battle-history`, addressable to anyone who knows a name. Privacy is identical to the existing player-detail page.
- Replacing the PoC's 60 s loop for `lil_boots` — the rollout coexists.

## File map (touch list for the implementation tranche)

| File                                                                     | Change                                                                                                                                                                                                              |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `server/warships/incremental_battles.py`                                 | Add `record_observation_from_payloads`; refactor `record_observation_and_diff` to wrap it; add `_apply_event_to_daily_summary`. Widen `ships_stats_json` shape to include damage/xp/planes_killed/survived_battles. |
| `server/warships/models.py`                                              | Add `PlayerDailyShipStats`.                                                                                                                                                                                         |
| `server/warships/migrations/00XX_player_daily_ship_stats.py`             | Generated.                                                                                                                                                                                                          |
| `server/warships/data.py:2365`                                           | Call `record_observation_from_payloads` at tail of `update_battle_data`, gated by `BATTLE_HISTORY_CAPTURE_ENABLED`. Failures logged and swallowed.                                                                  |
| `server/warships/tasks.py`                                               | Add `roll_up_player_daily_ship_stats_task`, `cleanup_old_battle_observations_task`.                                                                                                                                 |
| `server/warships/signals.py`                                             | Register nightly Beat schedules for both new tasks. Gate via env flags.                                                                                                                                             |
| `server/warships/views.py`                                               | Add `@api_view` `battle_history` endpoint.                                                                                                                                                                          |
| `server/warships/management/commands/rebuild_player_daily_ship_stats.py` | New.                                                                                                                                                                                                                |
| `client/app/components/BattleHistoryCard.tsx`                            | New (deferrable).                                                                                                                                                                                                   |
| `client/app/components/PlayerDetail.tsx`                                 | Mount `BattleHistoryCard` when totals.battles > 0 (deferrable).                                                                                                                                                     |
| `client/app/lib/chartTheme.ts`, `client/app/lib/wrColor.ts`              | Reused as-is.                                                                                                                                                                                                       |
| `CLAUDE.md` (env section)                                                | Document `BATTLE_HISTORY_CAPTURE_ENABLED`, `BATTLE_HISTORY_ROLLUP_ENABLED`, `BATTLE_HISTORY_API_ENABLED`, `BATTLE_OBSERVATION_RETENTION_DAYS`.                                                                      |

## References

- PoC runbook: `agents/runbooks/runbook-incremental-battle-poc-2026-04-27.md`.
- Snapshot precedent for daily aggregates: `server/warships/data.py:2518` (`update_snapshot_data`) — same delta-from-previous-row pattern, but at player level.
- Refresh path entry points: `server/warships/data.py:4696` (`update_player_data`), `:2365` (`update_battle_data`), `:199` (`refresh_player_detail_payloads`).
- Incremental crawl path: `server/warships/management/commands/incremental_player_refresh.py:180` (calls `fetch_players_bulk` + `save_player`, then `refresh_player_detail_payloads`).
- Visit-driven dispatch sites: `server/warships/views.py:143,254,261,268,295`.
- Lock helper precedent: `server/warships/tasks.py:329` (`_run_locked_task`).

## Next step

User reviews this runbook. On approval, the implementation tranche begins — gated by the four `BATTLE_HISTORY_*` env flags so the deploy is a no-op until each stage is flipped on.
