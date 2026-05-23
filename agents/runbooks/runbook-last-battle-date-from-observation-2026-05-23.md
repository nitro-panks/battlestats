# Runbook: Refresh `Player.last_battle_date` from observation deltas

_Created: 2026-05-23_
_Context: User flagged `lil_boots` profile showing "Last played 10 days ago" in the page header and on the clan-list left rail at the same time the battle-history chart correctly displayed 8 battles played today. Root cause is that `Player.last_battle_date` is owned exclusively by `_update_player_personal_data` (the WG `account/info/` call), while the battle-history pipeline updates from a different path (`update_battle_data` → `record_observation_from_payloads` → `BattleEvent`). Battles can be observed and rolled up into `PlayerDailyShipStats` while the player row's `last_battle_date` still reflects the prior account-info refresh. Compounded by the bulk player-detail cache (`player:detail:v1`, 24h TTL) and clan-members cache (`clan:members:v3`, 5-min TTL), which both freeze the derived `days_since_last_battle` value at write time._
_Status: shipped 2026-05-23. Fix landed in `record_observation_from_payloads` (`server/warships/incremental_battles.py`); 5 new tests in `RecordObservationFromPayloadsTests`; full incremental-battles suite 123/123 green and curated release gate 243/243 green. Backend-only deploy._

## Symptom

For `lil_boots` at the moment of the bug report (verified live via API on 2026-05-23):

- `/api/player/lil_boots/battle-history?window=day` → `totals.battles: 9` (recent 24h, true)
- Player profile header → "Last played 10 days ago"
- Left-rail clan list (`/api/fetch/clan_members/1000055908`) → `days_since_last_battle: 10` for lil_boots

Both stale surfaces ultimately read `Player.last_battle_date` (directly via the serializer or via the read-time derivation in `views.py:_days_since_last_battle`, `server/warships/views.py:1455`). The battle-history chart reads `PlayerDailyShipStats`, which is populated by a different lane that doesn't touch `last_battle_date`.

## The bug class

Two refresh pipelines, one shared piece of state, no write coupling:

1. **Account-info refresh** (`server/warships/data.py:4821` `update_player_data` → `_update_player_personal_data` at `server/warships/data.py:4859-4866`) is the **only** writer of `Player.last_battle_date` and `Player.days_since_last_battle`. Triggered by `PlayerViewSet.get_object` on cache-miss (`server/warships/views.py:280-298`) and by the periodic incremental refresh.
2. **Battle-history capture** (`server/warships/data.py:2539-2575` invoking `record_observation_from_payloads` at `server/warships/incremental_battles.py:694`) writes `BattleObservation` / `BattleEvent` / `PlayerDailyShipStats` rows. Triggered by every visit-driven `update_battle_data` call (15-min freshness floor at `server/warships/data.py:2456`) and by the daily floor sweep.

Lane (2) confidently knows when battles happened — it just diffed per-ship battle counts against the previous observation and emitted events. Lane (1) refreshes on a different cadence (hot-tier 12h, active-tier 24h, warm-tier 72h per `PLAYER_REFRESH_*_STALE_HOURS`). The two lanes can drift by hours-to-days, and the user-visible "Last played N days ago" reads lane (1).

The clean fix is to make lane (2) update the shared field whenever it has primary-source evidence that the player just played.

## Fix

Inside `record_observation_from_payloads` (`server/warships/incremental_battles.py:694`), in the existing `transaction.atomic()` block (`server/warships/incremental_battles.py:747`), after the per-ship and per-(ship,season) event lists are computed:

```python
if events or ranked_events:
    today_utc = datetime.now(timezone.utc).date()
    Player.objects.filter(pk=player.pk).update(
        last_battle_date=today_utc,
        days_since_last_battle=0,
    )
```

Then, outside the atomic block (cache invalidation should not run inside the DB transaction), call:

```python
from warships.data import invalidate_player_detail_cache
invalidate_player_detail_cache(player.player_id, realm=getattr(player, "realm", None) or DEFAULT_REALM)
```

`invalidate_player_detail_cache` lives at `server/warships/data.py:5243` and is already the canonical invalidator used by `_update_player_personal_data` (`server/warships/data.py:4938`). Reusing it keeps cache-eviction policy in one place.

### Conditions

- **Only fire when `events or ranked_events` is non-empty.** If the per-ship and per-(ship,season) deltas are both empty, the player has not played since the prior observation. Don't touch the row.
- **Implicitly skip on baseline observations.** `previous is None` returns at `server/warships/incremental_battles.py:768-776` before event computation, so no update path runs.
- **Implicitly skip on hidden profiles.** `_snapshot_from_player_row` returns `None` for hidden players (`server/warships/incremental_battles.py:412-413`), which surfaces as `record_observation_from_payloads` returning `{"status": "skipped", ...}` (`server/warships/incremental_battles.py:732-733`) before the atomic block opens. The capture path in `update_battle_data` itself only runs after `player.is_hidden` is set and `update_battle_data` writes `battles_json = []` and returns early for hidden players at `server/warships/data.py:2466-2473` (verified: the hidden short-circuit fires when `ship_data` is empty; hidden players have `ships/stats/` returning nothing, so the capture hook below is unreachable).
- **Bypass random-prior-broken / ranked-prior-broken guards.** When `random_prior_broken` is true (`server/warships/incremental_battles.py:785-796`), `events = []` is set explicitly. The condition `events or ranked_events` will be false unless ranked events are present; we will not falsely bump the date from a broken-prior artifact.

### Why `today_utc` and not the snapshot's `last_battle_time`

The rollout-piggyback path doesn't have a `player_data` dict (the `account/info/` payload), and `_snapshot_from_player_row` sets `last_battle_time=None` at `server/warships/incremental_battles.py:420`. The per-ship `ships/stats/` rows do not include a player-level last-battle timestamp at parse time in our snapshot dataclass (`server/warships/incremental_battles.py:36-63` — `ShipSnapshot` doesn't carry `last_battle_time`).

We don't need it. The fact that the current observation **just** diffed against a prior observation and found new ship-level activity is by itself proof that the player played between `previous.observed_at` and `now`. Observation cadence is at most ~12 hours for active players (hot-tier staleness + daily floor), so "today (UTC)" is the correct calendar day to within edge-of-day rounding. Edge case at the UTC midnight boundary (battle played at 23:55Z, observed at 00:05Z next day) records `today` as the new day — bounded to a few minutes of imprecision, acceptable.

If the PoC poll path (which does pass `player_data`) wants ground-truth precision, the existing `_update_player_personal_data` lane already updates `last_battle_date` from the actual `last_battle_time`. We're only fixing the rollout piggyback path, which has no access to that field.

### Why update `days_since_last_battle = 0` too

`PlayerSerializer` (`server/warships/serializers.py:80`) uses `fields = '__all__'` with no field-level override for `days_since_last_battle`, so the API returns the stored DB column directly. Setting `last_battle_date=today_utc` without also setting `days_since_last_battle=0` would leave the serialized response inconsistent until the next `_update_player_personal_data` run. Update both.

The published-frontend derivation (`server/warships/data.py:1873` `derive_days_since_last_battle`) and the clan-members derivation (`server/warships/views.py:1455`) both read `last_battle_date` and would converge anyway, but the `PlayerSerializer` path is what the `/api/player/<name>` endpoint and the bulk-cached `player:detail:v1` payload use.

### Cache invalidation scope

- **`player:detail:v1:{player_id}`** — invalidated explicitly by reusing `invalidate_player_detail_cache`. Next page load re-serializes from the updated row. Without this, the bulk cache (24h TTL via `BULK_CACHE_PLAYER_TTL`) would continue serving the stale snapshot until the next periodic warmer cycle.
- **`clan:members:v3:{clan_id}`** (`server/warships/views.py:1427`) — **not** addressed by this fix. It has a 5-min TTL and no targeted invalidation hook anywhere in the codebase. After this fix the left-rail clan list will still lag the player's true status by up to 5 minutes, then converge naturally on the next cache rebuild (which derives from the now-updated `Player.last_battle_date`). This is a >10× improvement over the multi-hour lag that motivated the bug report; further clan-cache invalidation is out of scope here and tracked as a follow-up.
- **Published landing caches** — covered transitively by `_update_player_personal_data`'s downstream invalidations, which still run on the existing schedule. No change needed.

## Implementation

### Code change

One file: `server/warships/incremental_battles.py`.

Inside `record_observation_from_payloads`, place the update block **after** the existing no-events early return at `server/warships/incremental_battles.py:824-831`. At that point execution has confirmed `events or ranked_events` is non-empty. Do the DB write inside the existing `transaction.atomic()` block. Schedule the cache invalidation with `transaction.on_commit(...)` so it fires only once the DB write has committed (otherwise a concurrent read could repopulate the bulk cache from the pre-write state and we'd be back to square one).

Concrete shape (illustrative, exact diff lives in the implementation commit):

```python
# server/warships/incremental_battles.py — inside record_observation_from_payloads,
# after the `if not events and not ranked_events: return ...` block at line 824-831,
# and BEFORE the BattleEvent creation loop at line 842+.

today_utc = datetime.now(timezone.utc).date()
Player.objects.filter(pk=player.pk).update(
    last_battle_date=today_utc,
    days_since_last_battle=0,
)
player_id_for_cache = player.player_id
player_realm_for_cache = player.realm
def _invalidate_cache():
    from warships.data import invalidate_player_detail_cache
    invalidate_player_detail_cache(player_id_for_cache, realm=player_realm_for_cache)
transaction.on_commit(_invalidate_cache)
```

`Player` is already imported at the top of the function (`server/warships/incremental_battles.py:725`). `from datetime import datetime, timezone` is already at `server/warships/incremental_battles.py:26`, so no new imports are needed for the DB write. The cache-invalidation call requires a deferred import of `invalidate_player_detail_cache` from `warships.data` (lazy because `warships.data` imports from `warships.tasks` which transitively imports this module — verified by `server/warships/data.py:1` importing tasks).

### Test plan

Add focused tests in `server/warships/tests/test_incremental_battles.py`:

1. **Happy path — random battles since prior obs bump `last_battle_date`.**
   - Set `Player.last_battle_date` to 10 days ago, `days_since_last_battle = 10`.
   - Record a baseline observation (`previous is None`).
   - Record a second observation where one ship's `pvp.battles` advanced by 3.
   - Assert `Player.last_battle_date == today_utc`, `days_since_last_battle == 0`.
   - Assert `invalidate_player_detail_cache` was called (mock or check cache key absent).

2. **No-op when no events.**
   - Set baseline. Record second observation with identical ship_data (no deltas).
   - Assert `Player.last_battle_date` is unchanged.

3. **No-op on baseline.**
   - Player with `last_battle_date = 10 days ago`.
   - Record a single observation with `previous is None`.
   - Assert no update — `last_battle_date` unchanged.

4. **Ranked-only events also trigger update.**
   - Set baseline with empty `ranked_ships_stats_json`.
   - Record second observation with `ranked_ship_data` showing a season's battles advanced (no random deltas).
   - Assert `Player.last_battle_date` is bumped.

5. **Random-prior-broken guard does not falsely fire.**
   - Construct a `previous` with `pvp_battles > 0` but empty `ships_stats_json`. Per `server/warships/incremental_battles.py:785-796` this forces `events = []`. With no ranked data, `events or ranked_events` is false and no bump occurs.

Existing tests in `test_incremental_battles.py` already exercise the orchestrator (e.g. `test_records_observation_and_emits_events` and the prior-broken guards). The new tests slot alongside without disturbing them.

### Release-gate impact

Touched code is exclusively in `record_observation_from_payloads`. Affected tests:

- `server/warships/tests/test_incremental_battles.py` — direct coverage of the function.
- Indirectly: any test that calls `record_observation_from_payloads` with at least one diff and then reads back `Player.last_battle_date`. Search of existing tests confirms none currently rely on `last_battle_date` being **unchanged** after a non-baseline observation, so no test churn is expected.

Run before merge:
```bash
cd server
python -m pytest warships/tests/test_incremental_battles.py -x --tb=short
python -m pytest warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py -x --tb=short
```

### Rollout

No env-flag gate. The fix is a behavior correctness improvement scoped strictly to the path that's already gated by `BATTLE_HISTORY_CAPTURE_ENABLED=1` (currently on in production per `agents/runbooks/runbook-battle-history-rollout-2026-04-28.md` Phase 2 rollout — verified by the live battle-history endpoint returning today's data).

Single deploy: backend only. Frontend untouched.

### Verification on production

After deploy, hit `https://battlestats.online/api/player/<name>?realm=na` for a player who just played a match (e.g. `lil_boots` after a battle):

- Before the visit triggers a fresh `update_battle_data`: `days_since_last_battle` may still reflect the last `_update_player_personal_data` cadence.
- After `update_battle_data` runs once (visit triggers it; or wait for the daily floor sweep): `days_since_last_battle == 0` and `last_battle_date == today UTC`.
- Reload the profile page in a browser: header should now read "Last played 0 days ago".
- Clan rail will continue to lag for up to 5 minutes (clan-members cache TTL) before converging on the next rebuild.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Race: two concurrent `update_battle_data` calls for the same player both compute deltas. | Low (Celery serializes via `update_battle_data_task` and the in-memory dedupe). Visit + crawl could double-fire briefly. | None — both writes set the same `today_utc`. Idempotent. | n/a |
| `Player.objects.filter(pk=...).update(...)` bypasses `Player.save()` signals. | Low | We don't have signals on `last_battle_date` / `days_since_last_battle` (verified: no `pre_save`/`post_save` handlers on `Player` reference these fields). | n/a |
| Cache invalidation called inside the atomic block could race a concurrent read that re-populates the cache from the old row. | Low | Race window is microseconds; the next periodic warmer or visit will overwrite. | Call invalidation via `transaction.on_commit(...)` so it fires only after the DB write is durable. |
| `days_since_last_battle=0` set when `last_battle_date` was already today (no-op). | Always when player plays multiple batches in the same UTC day. | None — no-op write, two columns. | n/a |
| Edge of UTC midnight: battle played at 23:59:55Z, observed at 00:00:05Z, records `last_battle_date` as the new day. | Rare | Off by one day for a few-second window. | Accepted — the alternative requires propagating `account/info/`'s `last_battle_time` through the snapshot, which is a larger refactor for negligible benefit. |

## Out of scope (follow-ups)

- **PvE-only activity.** `_coerce_ship_snapshot` (`server/warships/incremental_battles.py:99-135`) parses the `pvp` block only. A player who only played co-op / scenarios since the prior observation will produce zero events here, and `last_battle_date` will remain at whatever value `_update_player_personal_data` last wrote. The reported bug is a PvP-active player, so this fix lands the win; broadening to PvE deltas is a separate widening of the snapshot dataclass.
- **Clan-members cache invalidation on player refresh.** The `clan:members:v3:{clan_id}` cache has no targeted invalidator. Fix #3 from the original triage (invalidate from `_update_player_personal_data` when the refreshed player belongs to a clan) is the right home for that — it consolidates the invalidation pattern around `_update_player_personal_data` and benefits any code path that touches `Player.last_battle_date`, not just the observation pipeline.
- **Cache-hit staleness check in `PlayerViewSet.retrieve`.** Fix #2 from the original triage. Once `update_battle_data` updates `last_battle_date`, the bulk cache invalidation here closes most of the gap; the cache-hit refresh trigger becomes a smaller optimization, not a correctness fix.
