# Runbook: Battle History Rollout (Playerbase, Longitudinal)

_Created: 2026-04-28_
_Context: Take the lil_boots incremental-battle PoC (`runbook-incremental-battle-poc-2026-04-27.md`) playerbase-wide as a longitudinal "your last week of battles" feature, surfaced per ship per day for any player on the site. Reuses existing refresh paths so no new WG calls are introduced._
_Status: Implementation complete on local branches; ready to push, open PRs, and execute the production rollout sequence below. All curated release gates pass (backend 268+, frontend 88), with one pre-existing unrelated failure deselected._

## Purpose

Battlestats today shows running totals only. The PoC proved that diffing two consecutive snapshots of WG `account/info/` + `ships/stats/` yields per-ship per-match deltas (battles, wins, frags, damage, xp, planes_killed, survived). This rollout takes that mechanism playerbase-wide as a longitudinal record — "show me my last 7 days of battles, by ship and by day" — for any player on the site. Multi-match collapse between observations is acceptable; what matters is that **daily totals per ship are stable and trend over time**.

The PoC's "poll every 60 s" model does not scale: applied to even a small fraction of the 274 K-player base it would saturate the WG `application_id` rate budget. The rollout instead **piggybacks capture on the WG calls the site already makes** during visit-driven and incremental-crawl refreshes, then layers a denormalized daily roll-up table optimized for longitudinal reads.

## Implementation status

All eight rollout phases are committed locally on independent feature branches and pass their respective release gates. They are deploy-ready in the order below; pushing them to GitHub and merging into `main` is the first action the engineer rolling this out should take.

| Phase | Branch | Commit | Flag landed (default off in prod) | Tests |
|---|---|---|---|---|
| 0 — PoC tree | `feature/incremental-battles-poc` | `d49600c` | `BATTLE_TRACKING_PLAYER_NAMES` (empty in prod) | smoke + lil_boots live capture |
| Runbook reconcile | `runbook/battle-history-rollout-2026-04-28` | `51a86f1` (this commit) | docs only | n/a |
| 1 — Orchestrator refactor | `feature/battle-history-phase1-refactor` | `e949320` | none (refactor) | 18 new pytest cases |
| 2 — Capture hook | `feature/battle-history-phase2-capture-hook` | `28ef5ae` | `BATTLE_HISTORY_CAPTURE_ENABLED` | 22 cumulative |
| 3 — Rollup table + writers | `feature/battle-history-phase3-rollup` | `8a4e60b` | `BATTLE_HISTORY_ROLLUP_ENABLED` | 30 cumulative |
| 4 — Read API | `feature/battle-history-phase4-api` | `e14ee51` | `BATTLE_HISTORY_API_ENABLED` | 36 cumulative |
| 4.6 — Lifetime + delta | `feature/battle-history-phase4.6-vs-lifetime` | `9005d61` | none (extends API) | 39 cumulative |
| 5 — Frontend `BattleHistoryCard` | `feature/battle-history-phase5-frontend` | `b3b0a79` | gated by API flag | 88 frontend |

Migrations 0051 / 0052 / 0053 are additive `CreateModel` / `AddField` only — no `AlterField` on existing tables, no `NOT NULL` columns added. The deploy script's `python manage.py migrate --noinput` (`server/deploy/deploy_to_droplet.sh:437`) applies them on the first deploy. Tables exist but stay empty until the corresponding env flag is flipped on.

## Premise: capture is a side-effect, not a poll

The Wargaming public API has no per-match endpoint. Per-battle deltas are still computed by diffing successive aggregate snapshots, exactly as in the PoC. The change vs. the PoC is **when** snapshots are taken:

- **PoC**: dedicated `poll_tracked_player_battles_task` issues 2 WG calls per tick per tracked player. Stays in place for `lil_boots` and tests.
- **Rollout**: snapshots are recorded as a side effect of `update_battle_data` (`server/warships/data.py:2365`), which already fetches `ships/stats/` and is the chokepoint for both visit-driven refreshes (`update_battle_data_task`) and the incremental crawl path (`refresh_player_detail_payloads` → `update_battle_data`). At the tail of that function the WG payload is already in scope and the `Player` row has the freshest aggregates from the most recent `update_player_data`.

Result: every player whose page is visited or whose tier rotates through the incremental crawl gets a `BattleObservation` with **no incremental WG cost**. Resolution is whatever the existing refresh cadence is (instant on visit, ~3 h via incremental crawl).

## Dependencies

All prerequisite phases (PoC + Phases 1–5 + 4.6) are committed locally on the branches listed in **Implementation status**. The first task in the rollout is pushing those eight branches and opening their PRs in the order shown.

The PoC's 60-second poll loop and `BATTLE_TRACKING_PLAYER_NAMES` env var stay intact through the rollout. The rollout coexists with the PoC; they share the same orchestrator function (`record_observation_from_payloads`, introduced in Phase 1 as a refactor of the PoC's `record_observation_and_diff`).

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

#### Per-ship observation shape (already in tree on the PoC commit)

The PoC commit already writes `BattleObservation.ships_stats_json` as a list of dicts with the full delta vocabulary the rollout needs — `battles`, `wins`, `losses`, `frags`, `damage_dealt`, `xp`, `planes_killed`, `survived_battles` per ship. See `server/warships/incremental_battles.py:_coerce_ship_snapshot` and `record_observation_and_diff`. No further shape widening is required for the rollout phases.

Two open optimizations remain (deferable, not blocking):

1. **Restrict to active ships.** Today every ship returned by `ships/stats/` is captured. Most accounts have touched <100 of the ~548 ships in the game; filtering to `pvp.battles > 0` would cut the JSON 4–8×. Land in Phase 7 or earlier if storage growth in Phase 2 exceeds the envelope.
2. **Compression of stale rows.** `BattleObservation` rows older than the active diff window (only the most recent observation per player is read by `compute_battle_events`) could be compressed or have their `ships_stats_json` nulled out once the corresponding `BattleEvent` rows exist. Phase 7 territory; see "Compression / shape work" at the bottom of this runbook.

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

### Retention model — durable stream, no pruning ever

The conceptual model is a **single unbounded stream of capture events per player**, of which the 7-day card is one bounded window. Daily / weekly / monthly / yearly rollups are progressively coarser windows over the same stream. **Every tier is kept forever.** No data is ever deleted.

Tier 1: **`PlayerDailyShipStats`** — the analytical floor. Source for all coarser rollups. ~10 K rows/day at playerbase scale, ~3.6 M rows/year. Trivial Postgres scale.

Tier 2: **`PlayerWeeklyShipStats` / `PlayerMonthlyShipStats` / `PlayerYearlyShipStats`** — materialized rollups, 1/7, 1/30, 1/365 the row count of tier 1.

Tier 3: **`BattleObservation`** — heavy JSON (~30–60 KB/row), the prior-state input for each diff. **Also kept forever**, even though only the most-recent per player is needed for the next diff. The historical observations are the only path to rebuilding the rollup tables byte-for-byte from scratch if a bug ever invalidates them.

Tier 4: **`BattleEvent`** — small, one row per detected match per ship. **Also kept forever**, for the same reconstructibility reason: events let us rebuild any past day's `PlayerDailyShipStats` without having to re-poll WG (which can't return historical data anyway).

**No pruning task ships.** The earlier draft of this runbook included `cleanup_old_battle_observations_task` with a 14-day forensic window; that task is **explicitly out of scope**. If storage growth ever becomes a real cost, the response is compression (Phase 9 below), not deletion.

Storage envelope (for planning, not for action): at full playerbase capture, ~10 K observations/day per realm × ~50 KB/row × 365 days × N years ≈ ~180 GB/year/realm. Postgres `jsonb` already does row-level compression; if the cost ever justifies it, Phase 9 compresses or columnar-stores the cold tail without losing data.

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

## Production rollout sequence

Five stages over ~2 weeks. Every flag flip is an edit to the droplet's `/etc/battlestats-server.env` (or `/etc/battlestats-celery.env`) followed by `systemctl restart battlestats-server battlestats-celery battlestats-celery-beat`. **No code change is required between stages** — `os.getenv` is read at runtime, so a worker restart is enough to pick up the new flag value.

### Day 0 — deploy code with all flags off

1. Push the eight branches in the order listed in **Implementation status**, open PRs, merge each in order.
2. Run `./server/deploy/deploy_to_droplet.sh battlestats.online`. Migrations 0051 / 0052 / 0053 apply. Tables exist but stay empty.
3. Verify zero log noise on the droplet: `journalctl -u battlestats-server -u battlestats-celery --since "5 minutes ago"`.

### Day 1 — `BATTLE_HISTORY_CAPTURE_ENABLED=1`

Restart server + workers. Watch for 24–48 h:

```sql
-- Should see hundreds per hour at steady state.
SELECT count(*) FROM warships_battleobservation WHERE observed_at > now() - interval '1 hour';

-- Storage envelope check.
SELECT pg_size_pretty(pg_total_relation_size('warships_battleobservation'));
```

Expected envelope: ~10 K rows/day per realm at steady state, ~30–60 KB/row, so **≤ 7 GB per realm before pruning kicks in**. If growth exceeds the envelope, drop or extend the active-ships filter in `incremental_battles.py:_serialize_ships_payload` (only ships with `pvp.battles > 0`) before stage 2.

### Day 3 — `BATTLE_HISTORY_ROLLUP_ENABLED=1`

On-write incremental + nightly sweeper (04:30 UTC) start filling `PlayerDailyShipStats`. Spot-check after one beat tick:

```sql
SELECT date, COUNT(*) FROM warships_playerdailyshipstats GROUP BY date ORDER BY date DESC LIMIT 5;
```

Cross-validate aggregates for any sample player:

```sql
-- Period totals from BattleEvent and PlayerDailyShipStats must match for any (player, day).
SELECT SUM(battles_delta), SUM(damage_delta) FROM warships_battleevent
  WHERE player_id=? AND detected_at::date = '2026-MM-DD';
SELECT SUM(battles), SUM(damage) FROM warships_playerdailyshipstats
  WHERE player_id=? AND date = '2026-MM-DD';
```

### Day 5 — `BATTLE_HISTORY_API_ENABLED=1` + frontend ship

Flip the API flag. `GET /api/player/<name>/battle-history?days=N` goes live. Curl-verify against a known-active player; expect p95 < 50 ms warm, < 200 ms cold. Frontend deploy ships `BattleHistoryCard` in the same window — the card silently no-ops when `totals.battles=0`, so cold-start players see nothing extra on their detail page.

### Day 14+ — no pruning (data is durable)

There is no pruning step. All four tiers (`PlayerDailyShipStats`, `Player{Weekly,Monthly,Yearly}ShipStats`, `BattleObservation`, `BattleEvent`) are retained forever per the retention model above. The only operational concern past day 14 is monitoring storage growth and considering Phase 9 (compression) if real measurements justify it.

Reversibility: un-flip any flag at any stage and the system returns to its pre-rollout behavior. Tables remain — and that's the point. Captured data is durable.

## Scaling to all active players

Capture is automatic — it's a side-effect of the WG calls the site already makes:

- `incremental_player_refresh_task` (`server/warships/tasks.py:1138`) walks every NA / EU / Asia player every ~3 h and calls `update_player_data` → `update_battle_data`. Phase 2's hook captures every one of those refreshes for free. **No new WG API budget consumed.**
- Visit-driven `/api/player/<name>/` hits add real-time coverage for the active-fan slice.

**Optional accelerator for seeding.** If you want every active player observed within 24 h of flipping the capture flag, temporarily lower `PLAYER_REFRESH_INTERVAL_MINUTES` from `180` to `60` for one cycle. Triples observation rate, then revert. Existing infrastructure does the work — no new code, no new WG calls beyond what the existing crawl already issues.

## Cadence guarantees (load contract)

Two contracts the rollout is designed around. They're enforced by **existing** code, not a new throttle — call them out explicitly so a future engineer doesn't add a faster path that violates them.

### Per-player capture: at most one observation every 15 minutes

The capture hook lives at the **tail** of `update_battle_data` (`server/warships/data.py:~2450`), past the existing 15-min freshness early-bail at line 2382:

```python
if player.battles_json and player.battles_updated_at and datetime.now() - player.battles_updated_at < timedelta(minutes=15):
    return player.battles_json   # capture hook NOT reached
```

Threshold is `PLAYER_BATTLE_DATA_STALE_AFTER = timedelta(minutes=15)` (`data.py:114`). Any visit-driven or crawl-driven call to `update_battle_data` within 15 min of the last refresh returns early without issuing a WG fetch and without firing the capture hook. **Visit storms on hot players are naturally throttled.**

The user-facing manual refresh control on `PlayerDetail` mirrors this server-side throttle visually: red until 15 min has elapsed since the player's last fetch, green and clickable thereafter. Clicking issues a single forced refresh that passes through `update_battle_data`'s normal flow (still gated by the 15-min window if multiple users hit it at once on the same player).

If a future change ever wants sub-15-min freshness for a specific user surface, the safe path is a separate code path that does **not** trigger `update_battle_data` — never lower the global `PLAYER_BATTLE_DATA_STALE_AFTER`.

### Per-player coverage: daily differentials on every active player

`incremental_player_refresh_task` walks the entire playerbase per realm in graduated tiers (`hot` / `active` / `warm`) on a ~3 h cycle. Hot players (visited in the last 12 h) cycle every ~10 min within the task; active players every ~1 h; warm players every cycle. Any player who's been seen by the site in the last 30 days is touched by the crawl at least 4–8 times per day, which means **at least 4–8 `BattleObservation` rows per active player per day** once capture is on — enough to compute meaningful daily differentials.

The PoC's 60 s tracked-player loop continues to run for `lil_boots` and any other names in `BATTLE_TRACKING_PLAYER_NAMES` — that's a deliberate exception, scoped to a 1-row whitelist. Production droplet leaves `BATTLE_TRACKING_PLAYER_NAMES` empty, so the 15-min throttle is universal in prod.

## Operational watchpoints

- **Storage growth** in `warships_battleobservation`. Data is kept forever; growth is unbounded by design. Watch `pg_total_relation_size` weekly. If growth tracks above the planning envelope (~180 GB/year/realm), narrow the per-ship JSON shape (`incremental_battles.py:_serialize_ships_payload`) or open Phase 9 (compression / columnar). Never disable capture and never delete rows.
- **Worker queue depth.** Each `update_battle_data` now does an extra DB write + diff (negligible per call). Watch `celery -A battlestats inspect active` after the capture flip; if depth climbs unexpectedly the diff is hot-pathing somewhere it shouldn't.
- **API p95 latency** on `/api/player/.../battle-history`. Should be sub-50 ms warm, sub-200 ms cold. The cold case for a player with thousands of `PlayerDailyShipStats` rows is the worst-case shape — load-test before flipping.
- **Cache invalidation.** The 5-min Redis TTL is forgiving. If immediate freshness on the card after a player refresh is wanted, hook the existing `update_player_data` callsite to `cache.delete_pattern("...battle-history*")`. Optional, costs a few % of refresh latency.

## Freshness reference

[wows-numbers.com](https://wows-numbers.com), the canonical community Personal-Rating source, updates its expected-values dataset no more than every 15 minutes. That's a useful baseline for what counts as "fresh enough" in this domain:

- The **PoC's 60 s poll** for `lil_boots` is 15× tighter than the community baseline — appropriate for the dev loop where we want sub-minute observation; overkill for population-averages math.
- **Visit-driven captures match** the 15-min benchmark whenever a player's page is loaded.
- The **3 h incremental-crawl cadence** is the lagging edge — coarse for population-averages staleness but plenty for the "your last week of battles" feature this rollout is delivering.

When the future first-party expected-values phase lands (see **Out of scope** below), the nightly aggregator at 04:30 UTC is the right cadence: our underlying samples already match wows-numbers' 15-min freshness for active players, and recomputing rolling averages once a day from a fresh pile of samples is the correct cadence for *aggregates*, not individual samples.

## Kill switch

Unset any of `BATTLE_HISTORY_CAPTURE_ENABLED`, `BATTLE_HISTORY_ROLLUP_ENABLED`, or `BATTLE_HISTORY_API_ENABLED` and `systemctl restart` the workers + server. The corresponding behavior is dormant on the next tick. Tables and any captured data remain in place (harmless).

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
6. **No pruning, ever.** There is no cleanup task in this rollout. All four tiers are durable. Never write a query or task that deletes rows from `BattleObservation`, `BattleEvent`, or any rollup table.

## WG API budget

No new calls. Capture is a pure side-effect of fetches that already happen for `update_player_data` / `update_battle_data`. Rate budget is unchanged from the current site.

The one cost is **storage and write amplification**: every refresh writes a `BattleObservation` (~30–60 KB JSON). Per the retention model, none of this is ever pruned, so the responses are pure-shrink (not delete):

- `ships_stats_json` only includes ships where `pvp.battles > 0` (4–8× shrink already in place).
- Postgres `jsonb` does row-level compression by default.
- Phase 9 (compression / columnar / cold-archive) opens if real storage measurements justify it. Until then, the cost of keeping everything is bounded and acceptable on Postgres.

## Out of scope (filed for future phases)

Filed here so a future engineer knows what got deliberately deferred and where the design notes live.

- **Per-match-level resolution.** The WG API has no per-match endpoint; multi-match collapse between observations is acceptable. Out forever.
- **Co-op / scenario / operations battles.** Only `pvp.battles` is tracked; non-PvP modes are silent in the current shape.
- **Authenticated "log in to see _your_ history."** Battlestats has no auth surface today; the read path is `/api/player/<name>/battle-history`, addressable to anyone who knows a name. Privacy is identical to the existing player-detail page.
- **Weekly / monthly / yearly rollup tiers + period API (Phase 6).** Three new materialized tables — `PlayerWeeklyShipStats(player, week_start, ship_id, ...)`, `PlayerMonthlyShipStats(player, month_start, ship_id, ...)`, `PlayerYearlyShipStats(player, year, ship_id, ...)` — each kept forever. Built nightly from `PlayerDailyShipStats` by extending `roll_up_player_daily_ship_stats_task` (or three siblings). Read API extends to `/api/player/<name>/battle-history?period=daily|weekly|monthly|yearly&windows=N`; default stays `daily, windows=7` for back-compat. Frontend adds a small period switcher above the per-ship table. Storage cost is trivial (1/7, 1/30, 1/365 the daily row count). The daily layer remains the source of truth — coarser tiers are derived, never written-to directly.
- **Ranked battles (Phase 7).** WG exposes per-season per-ship ranked stats at `seasons/shipstats/`; the codebase already wraps it as `_fetch_ranked_ship_stats_for_player` (`server/warships/api/ships.py`). Same diff-and-aggregate pattern as randoms, but each event needs a `mode='ranked'` tag and `BattleObservation` needs a parallel `ranked_ships_stats_json` to hold the prior totals. Land once randoms is stable in production.
- **First-party expected values (Phase 8).** Once population coverage is meaningful (~2 weeks post-capture-on), aggregate `BattleObservation.ships_stats_json` across all players to compute per-ship population averages — our own equivalent of wows-numbers' expected-values dataset. Surfaces "vs field" badges on the `BattleHistoryCard`. Cold-start gate: suppress the comparison until `sample_battles >= 50` per ship. Nightly aggregator at 04:30 UTC matches the freshness reference above.
- **Compression (Phase 9).** Move `ships_stats_json` to `bytea` + zstd, or null it out on rows older than the active diff window (only the most recent observation per player is consulted by `compute_battle_events`; everything else is cold history). Open only if real storage measurements during the rollout justify it.
- **Replacing the PoC's 60 s loop for `lil_boots`** — the rollout coexists with the PoC indefinitely. The PoC stays valuable as a high-frequency reference signal to spot-check the lower-cadence rollout against.

## File map (touch list for the implementation tranche)

| File                                                                     | Change                                                                                                                                                                                                              |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `server/warships/incremental_battles.py`                                 | Add `record_observation_from_payloads`; refactor `record_observation_and_diff` to wrap it; add `_apply_event_to_daily_summary`. Widen `ships_stats_json` shape to include damage/xp/planes_killed/survived_battles. |
| `server/warships/models.py`                                              | Add `PlayerDailyShipStats`.                                                                                                                                                                                         |
| `server/warships/migrations/00XX_player_daily_ship_stats.py`             | Generated.                                                                                                                                                                                                          |
| `server/warships/data.py:2365`                                           | Call `record_observation_from_payloads` at tail of `update_battle_data`, gated by `BATTLE_HISTORY_CAPTURE_ENABLED`. Failures logged and swallowed.                                                                  |
| `server/warships/tasks.py`                                               | Add `roll_up_player_daily_ship_stats_task`. (No cleanup task — data is kept forever.)                                                                                                                                 |
| `server/warships/signals.py`                                             | Register nightly Beat schedules for both new tasks. Gate via env flags.                                                                                                                                             |
| `server/warships/views.py`                                               | Add `@api_view` `battle_history` endpoint.                                                                                                                                                                          |
| `server/warships/management/commands/rebuild_player_daily_ship_stats.py` | New.                                                                                                                                                                                                                |
| `client/app/components/BattleHistoryCard.tsx`                            | New (deferrable).                                                                                                                                                                                                   |
| `client/app/components/PlayerDetail.tsx`                                 | Mount `BattleHistoryCard` when totals.battles > 0 (deferrable).                                                                                                                                                     |
| `client/app/lib/chartTheme.ts`, `client/app/lib/wrColor.ts`              | Reused as-is.                                                                                                                                                                                                       |
| `CLAUDE.md` (env section)                                                | Document `BATTLE_HISTORY_CAPTURE_ENABLED`, `BATTLE_HISTORY_ROLLUP_ENABLED`, `BATTLE_HISTORY_API_ENABLED`. No retention env var — data is durable.                                                                      |

## References

- PoC runbook: `agents/runbooks/runbook-incremental-battle-poc-2026-04-27.md`.
- Snapshot precedent for daily aggregates: `server/warships/data.py:2518` (`update_snapshot_data`) — same delta-from-previous-row pattern, but at player level.
- Refresh path entry points: `server/warships/data.py:4696` (`update_player_data`), `:2365` (`update_battle_data`), `:199` (`refresh_player_detail_payloads`).
- Incremental crawl path: `server/warships/management/commands/incremental_player_refresh.py:180` (calls `fetch_players_bulk` + `save_player`, then `refresh_player_detail_payloads`).
- Visit-driven dispatch sites: `server/warships/views.py:143,254,261,268,295`.
- Lock helper precedent: `server/warships/tasks.py:329` (`_run_locked_task`).

## Next step

User reviews this runbook. On approval, the implementation tranche begins — gated by the four `BATTLE_HISTORY_*` env flags so the deploy is a no-op until each stage is flipped on.
