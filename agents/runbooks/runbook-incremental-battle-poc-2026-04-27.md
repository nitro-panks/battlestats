# Runbook: Incremental Battle Capture PoC (lil_boots)

_Created: 2026-04-27_
_Context: Prove out per-battle delta capture for a single tracked player (`lil_boots`) by frequently polling WG aggregate stats and diffing successive observations. Side-channel PoC, additive-only schema, gated off in production._
_Status: Design approved (Ultraplan, 2026-04-27). Implementation not started._

## Purpose

Battlestats today refreshes player stats on a 3-hour incremental cycle and surfaces only running totals. This PoC tests a tighter loop: poll the WG API every ~60 seconds for `lil_boots`, detect when running totals advance, compute the per-ship delta, and show it on the player page as a "latest battle" card. Validation is human-in-the-loop — the user plays one PvP match, then refreshes the page and verifies that the card shows the right ship and W/L outcome within ~2 minutes.

The PoC must run **alongside the live site without disturbing it**: prod on DigitalOcean must be safe to deploy without picking up the new schedule, and any DB migration applied locally must remain safe for the deployed prod release. All new behavior is gated by a single env var (`BATTLE_TRACKING_PLAYER_NAMES`); when unset, the system is byte-for-byte identical to today.

## Premise: WG has no per-match endpoint

The Wargaming public API exposes only aggregate running totals — `account/info/` (player) and `ships/stats/` (per-ship). There is no per-match feed. Per-battle deltas must therefore be **computed by diffing successive snapshots** of those aggregates. This makes the PoC a pure pull-and-diff exercise:

- Observation = a single successful poll of both endpoints.
- Event = a detected positive delta in `pvp_battles` between two observations, attributed to the ship whose per-ship `pvp.battles` advanced.

Durability comes from snapshot persistence and idempotent diffing, not transactional ingest. A failed poll writes nothing and the next tick retries; deltas are tolerant of arbitrary gaps between successful observations.

## Design

### Data model (additive only)

Two new tables in `server/warships/models.py`. **No changes** to `Player`, `Snapshot`, or `PlayerExplorerSummary`.

**`BattleObservation`** — one row per successful poll.

| Field | Type | Notes |
|---|---|---|
| `player` | FK → Player | indexed |
| `observed_at` | DateTimeField | server-side timestamp of the poll |
| `pvp_battles` | IntegerField | from `account/info/.statistics.pvp.battles` |
| `pvp_wins` | IntegerField | |
| `pvp_losses` | IntegerField | |
| `pvp_frags` | IntegerField | |
| `pvp_survived_battles` | IntegerField | |
| `last_battle_time` | DateTimeField, null | from WG payload |
| `ships_stats_json` | JSONField | compact `{ship_id: {battles, wins, losses, frags}}` |
| `source` | CharField | `"poll"` or `"manual"` |

Indexes: `(player, observed_at desc)`. `unique_together (player, observed_at)`.

**`BattleEvent`** — one row per detected battle.

| Field | Type | Notes |
|---|---|---|
| `player` | FK → Player | |
| `detected_at` | DateTimeField | when diff was computed |
| `ship_id` | BigIntegerField | |
| `ship_name` | CharField | denormalized for display |
| `battles_delta` | IntegerField | usually 1; >1 when multiple matches between polls |
| `wins_delta` | IntegerField | |
| `losses_delta` | IntegerField | |
| `frags_delta` | IntegerField | |
| `survived` | BooleanField, null | inferred from `survived_battles` delta |
| `from_observation` | FK → BattleObservation | |
| `to_observation` | FK → BattleObservation | |

Dedup key: `(from_observation, to_observation, ship_id)` unique.

### Capture pipeline

New Celery task in `server/warships/tasks.py`:

```
poll_tracked_player_battles_task(player_id)
```

- Queue: `background`.
- Reuses the WG client at `server/warships/api/client.py:35` (already retries 429/5xx with backoff).
- Wrapped in the existing `_run_locked_task` Redis-lock helper (`tasks.py:329`), keyed `warships:tasks:poll_tracked_player_battles:{player_id}:lock`, **5-min timeout** (shorter than the default 15 min so we don't lock out the next tick).
- Steps:
  1. Fetch `account/info/` and `ships/stats/`.
  2. If both succeed, `BattleObservation.objects.create(...)`.
  3. Load the previous observation for this player.
  4. If `current.pvp_battles > previous.pvp_battles`, walk per-ship deltas and create one `BattleEvent` per ship whose battle count advanced.
- Idempotency: re-running on identical WG state inserts a fresh observation row but produces no new events because the delta is computed from the immediately-prior row, which already reflects the same totals.

New Celery Beat schedule registered in `server/warships/signals.py`:

```
poll-tracked-player-battles  →  every 60s (configurable)
```

The schedule wakes once per minute, reads `BATTLE_TRACKING_PLAYER_NAMES` from env, resolves each name to a `Player`, and dispatches one `poll_tracked_player_battles_task` per resolved id. **If the env var is empty/unset, the wake-up is a no-op.** This is the same gating pattern as `HOT_ENTITY_PINNED_PLAYER_NAMES`.

### Frontend surfacing

Backend: extend the existing `/api/player/{playerName}/` payload (assembled in `server/warships/views.py` / `server/warships/data.py`) with an optional `latest_battle` block:

```json
{
  "ship_name": "Yamato",
  "ship_tier": 10,
  "frags_delta": 2,
  "won": true,
  "observed_at": "2026-04-27T18:14:02Z",
  "observation_lag_seconds": 47
}
```

Absent for non-tracked players → contract is purely additive.

Frontend: new `client/app/components/LatestBattleCard.tsx`, mounted at the top of `PlayerDetail.tsx` only when `latest_battle` is present. Styling reuses `client/app/lib/chartTheme.ts` and `client/app/lib/wrColor.ts` for the win/loss accent.

### Migration safety

- `python manage.py makemigrations warships` produces e.g. `0067_battleobservation_battleevent.py` — two `CreateModel` operations, no `AlterField` on existing tables, no `NOT NULL` columns added to existing tables.
- **Cloud-DB safety:** because the migration is additive only, applying it locally against the cloud DB does not break the deployed prod release — prod's running code never references the new tables. The `migrate --noinput` step in `server/deploy/deploy_to_droplet.sh:437` is a no-op once the tables exist.
- **Rollback:** a single reverse migration (`DROP TABLE` for both) is sufficient. Both tables are PoC-only with no FKs pointing into them from existing code.

### Durability against WG flakiness

- WG client retry policy (`api/client.py:35`) handles 429/5xx with backoff — no change needed.
- A failed poll writes nothing — no partial observation. Next tick (60s later) retries.
- Deltas are computed from any two successful observations, not a continuous chain. Arbitrary gaps are tolerated.
- If WG returns stale data (totals unchanged across many polls), no event is emitted — correct behavior.
- Per-player rate: 2 WG calls/min for one account, well under the per-key rate cap.
- Observations older than 7 days are pruned by lazy-adding a sweep to an existing periodic cleanup task in `tasks.py` (prevents unbounded growth; not implemented in the initial PoC if storage stays small).

### Kill switch

1. Unset `BATTLE_TRACKING_PLAYER_NAMES` (or remove from env) → next Beat tick the schedule short-circuits with no work dispatched.
2. Or comment out the Beat registration in `server/warships/signals.py` and restart workers.
3. Tables remain (harmless, empty for non-tracked players).

## Validation plan

1. Apply migration locally: `cd server && python manage.py migrate`.
2. Set `BATTLE_TRACKING_PLAYER_NAMES=lil_boots` in local env; restart Celery beat + the `background` worker.
3. Confirm baseline:
   ```
   python manage.py shell -c "from warships.models import BattleObservation; print(BattleObservation.objects.count())"
   ```
   Count increments at ~1/min.
4. **User plays one PvP game in WoWS.**
5. Within ~2 min after the post-battle results screen, a `BattleEvent` row should appear; the player page should show the `LatestBattleCard` with the correct ship and W/L outcome.
6. Sanity checks:
   - `BattleEvent.battles_delta == 1` for a single match (or `>1` if multiple games happened between polls — acceptable; card shows the most recent ship).
   - `observation_lag_seconds` on the card matches `now − latest BattleObservation.observed_at`.
   - Page refresh shows the same battle, no flicker, no duplicate event row.
7. Failure-mode probe: stop local Redis briefly (~30s); confirm the task logs the lock failure and recovers on the next tick without writing a duplicate observation.

## Out of scope for this PoC

- Multi-player tracking, public rollout, opt-in UX.
- A per-match endpoint (none exists in WG).
- Historical backfill (forward-looking only — first observation defines the baseline).
- Any modification to `Snapshot`, `PlayerExplorerSummary`, daily aggregates, or the existing 3-hour refresh path.
- Production deployment of the schedule. Prod env stays unset.

## File map (touch list for implementation tranche)

| File | Change |
|---|---|
| `server/warships/models.py` | Add `BattleObservation`, `BattleEvent` |
| `server/warships/migrations/00XX_battleobservation_battleevent.py` | Generated |
| `server/warships/tasks.py` | Add `poll_tracked_player_battles_task`; reuse `_run_locked_task` (line 329) |
| `server/warships/signals.py` | Register `poll-tracked-player-battles` Beat schedule, gated on `BATTLE_TRACKING_PLAYER_NAMES` |
| `server/warships/api/players.py` | Reused as-is (line 25 — `account/info/` fetcher) |
| `server/warships/api/ships.py` | Reused as-is (line 231 — `ships/stats/` fetcher) |
| `server/warships/api/client.py` | Reused as-is (line 35 — retry policy) |
| `server/warships/data.py` | Extend player API payload with optional `latest_battle` block |
| `server/warships/views.py` | Wire `latest_battle` through the player detail view |
| `client/app/components/LatestBattleCard.tsx` | New |
| `client/app/components/PlayerDetail.tsx` | Conditionally mount `LatestBattleCard` |
| `client/app/lib/chartTheme.ts`, `client/app/lib/wrColor.ts` | Reused as-is for styling |
| `CLAUDE.md` (env section) | Document `BATTLE_TRACKING_PLAYER_NAMES` |

## References

- Existing single-player refresh tasks (template for the new task): `server/warships/tasks.py:524,549,593`.
- Delta-from-previous-row precedent: `update_snapshot_data` at `server/warships/data.py:2518`.
- Deploy migrate step (proves additive migration is prod-safe): `server/deploy/deploy_to_droplet.sh:437`.
- Player models touched/untouched: `server/warships/models.py:15` (Player), `:160` (Snapshot), `:179` (PlayerExplorerSummary).

## Next step

User plays a game, then we iterate on the runbook (or kick off the implementation tranche) based on what the validation surfaces.
