# Runbook: Ship Leaderboard Data Freshness & Update Cadence

_Last updated: 2026-06-11_

_Status: Active operational reference_

_Context: Captures how "live" the ship leaderboard data actually is — the full freshness chain from the `ShipLeaderboard` / `ShipRouteView` components down to the snapshot writer — so future operators don't misread the standings as real-time._

## Purpose

Answer the recurring question: **how live are the ship stats shown on `/ship/<id>` and the landing tier/type drill-down board?** The short answer is **not live** — they are a precomputed **fortnightly batch snapshot**, not a real-time query. This runbook documents the dominant cadence (the 2-week season boundary), the two 15-minute caches layered on top, the kill switch that gates the writer, and how to confirm the current state in prod.

Read this before "fixing" a leaderboard that looks stale — within a season, static is **expected**, not a bug.

## Freshness chain (dominant factor first)

### 1. The real cadence — once every 2 weeks at a season boundary

The read path does **no live aggregation**. `get_ship_leaderboard()` (`server/warships/data.py`) does a pure snapshot read of the most recent `captured_on`'s `ShipTopPlayerSnapshot` rows for the ship+realm, joins `Ship` for the header, and shapes the payload. The endpoint is `ship_leaderboard` (`server/warships/views.py:2136`).

Those snapshot rows are written by `snapshot_ship_top_players_task` (`server/warships/tasks.py:898`):

- Runs on a **weekly Monday** Beat tick, but **self-gates on `is_season_boundary()`** (`data.py`, `delta % SHIP_SEASON_LENGTH_DAYS == 0`). So it is effectively **bi-weekly** — only the Monday a 14-day season closes — and it finalizes that *just-completed* season exactly once.
- `SHIP_SEASON_LENGTH_DAYS = SHIP_LEADERBOARD_WINDOW_DAYS = 14` (`data.py:5855`, `:5872`).
- The displayed board is the latest `captured_on` (a **season-start** date); its window is `[captured_on, captured_on + 14d)`. `next_window_open` (the in-progress season's close) is the authoritative value the frontend countdown reads.

**Implication:** within a season the numbers are **completely static**. A player grinding the ship *today* will not appear/update until the season closes and the next snapshot recomputes. The displayed stats (`win_rate`, `battles`, `avg_damage`, `kills_per_battle`) are delta-sums over the closed 14-day window — the same basis as the profile ship badges. (Survival% / KDR are intentionally omitted: per-battle survival isn't available for a multi-battle window, so it would undercount.)

### 2. Two 15-minute caches on top (negligible vs the fortnight)

These only matter for the few minutes right after a new season snapshot lands; against a 2-week refresh they are noise.

- **Backend Redis read-cache** — `SHIP_LEADERBOARD_CACHE_TTL = 900s` (`data.py:5856`), applied in the view (`cache_key = f"{realm}:ship-lb:{ship_id}"`, `views.py:2156`).
- **Client fetch cache** — `BOARD_FETCH_TTL_MS = 900_000` in `client/app/components/ShipLeaderboard.tsx`; the same 15 min as `SHIP_LEADERBOARD_FETCH_TTL_MS` in `client/app/components/ShipRouteView.tsx`. Both fetch via `fetchSharedJson`. Column **sorting is client-side** over the already-fetched rows — sorting never refetches.

Note: `ShipLeaderboard.tsx` (landing tier/type drill-down) and `ShipRouteView.tsx` (the `/ship/<id>` page) hit the **same** `/api/realm/<realm>/ship/<ship_id>/leaderboard` endpoint, so the freshness story is identical for both.

### 3. Kill switch gating the writer

`SHIP_BADGE_SNAPSHOT_ENABLED` (default `0`) gates `snapshot_ship_top_players_task`. If `0`, **no new snapshots are written** and the board freezes at the last-written season indefinitely. Prod is `=1` (verified 2026-06-11); tiers pinned `SHIP_BADGE_TIERS=8,9,10`.

## Verify current state in prod

```bash
# Writer enabled? Which tiers?
ssh root@battlestats.online 'grep -E "SHIP_BADGE_SNAPSHOT_ENABLED|SHIP_BADGE_TIERS" /etc/battlestats-server.env'

# What season is currently published, and how many rows?
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server;
  set -a; . /etc/battlestats-server.env; . /etc/battlestats-server.secrets.env; set +a;
  /opt/battlestats-server/venv/bin/python manage.py shell -c "
from warships.models import ShipTopPlayerSnapshot as S
print(\"distinct captured_on:\", list(S.objects.values_list(\"captured_on\", flat=True).distinct().order_by(\"-captured_on\")[:4]))
print(\"rows total:\", S.objects.count())
"'
```

**Reference reading (2026-06-11):** single distinct `captured_on = 2026-05-25` (season `05-25 → 06-08`, finalized at the 06-08 boundary), `6135` rows. The board has been frozen since ~06-08 and next drops **2026-06-22**. Older seasons are pruned — `ShipTopPlayerSnapshot` is ephemeral, so seeing only one (or a couple of) `captured_on` values is normal.

## The landing tier/type list is a *different*, live path (switch-lag source)

The freshness story above is the snapshot **board** read (`/ship/<id>/leaderboard`). The landing drill-down (`ShipLeaderboard.tsx`) layers a second surface on top with a very different cost profile:

- **Click a ship** → `/ship/<id>/leaderboard` → `get_ship_leaderboard` → cheap `ShipTopPlayerSnapshot` row read, Redis-cached 15 min. Fast.
- **Change a tier/type pill** → `/api/realm/<realm>/ships?tier=&type=` → `compute_realm_ships_by_tier_type` (`data.py:6553`) → a **live `BattleEvent` GROUP-BY** (`Sum` of battles/wins/damage/frags over the 14-day window, joined to `Player` for the realm). Expensive.

This asymmetry is why **switching tier/type lags but drilling in is instant.** The list result is cached per `(realm, tier, type)` bucket under a season-tagged key (immutable for the whole season) — but there are **3 tiers × 5 types = 15 buckets per realm**, so before warming, the *first* click of any new combination paid the full cold aggregation on the request path (subsequent clicks of the same combo were warm). Redis `allkeys-lru` eviction could also re-cold a bucket mid-season.

**Fix (shipped):** `warm_realm_top_ships_task` (`tasks.py`) — the existing daily per-realm landing warmer (Beat `top-ships-warmer-{realm}`, ~00:05/00:10/00:15 striped) — now also force-recomputes all populated tier/type buckets (`mode="random"` only; the frontend never passes a mode) right after it warms the treemap. One daily pass makes every filter click a guaranteed Redis hit for the season. Buckets with no candidate ships short-circuit before the aggregation and aren't cached — that cheap early-return path was never the lag source. Tests: `test_realm_ships_by_tier_type.py::RealmShipsByTierTypeWarmTests`.

## Diagnosing "the leaderboard looks stale"

1. **Is it within a season?** If `today` is between the current `captured_on` and `captured_on + 14d`, static is expected. Check `next_window_open` for the next drop. **Not a bug.**
2. **Did the boundary pass but the board didn't update?** Confirm `SHIP_BADGE_SNAPSHOT_ENABLED=1`, then check whether `snapshot_ship_top_players_task` ran on the boundary Monday (Beat health, task logs). If `captured_on` is two+ seasons behind, the writer didn't fire — investigate the `background` worker / Beat, not the read path.
3. **Stale only for ~15 min after a known boundary?** That's the Redis + client caches draining. Harmless.
4. **A specific strong player is missing a badge/standing** — this is usually sparse `BattleObservation` mis-bucketing by `detected_at`, a separate concern (see `runbook-ship-top-player-badges-2026-06-05.md`), not a leaderboard-freshness issue.

## If real-time-ish ship stats are ever required

This is a deliberate snapshot-backed design (cheap reads, no population-wide live aggregation on request). Closing the staleness gap is a **non-trivial** change, not a config tweak:

- **Shorten the window** (e.g. weekly season) — more frequent drops, but smaller per-window battle samples (noisier standings) and 2× the snapshot churn/pruning.
- **Add a live-aggregation path** — on-request or short-TTL recompute of `BattleEvent` deltas per ship; far more expensive (the reason the snapshot model exists), would need its own caching/work-mem budget.

Neither should be undertaken as a quick fix; size it as a feature.

## Related runbooks

- `agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md` — the snapshot/badge engine, season-boundary semantics, `detected_at` bucketing caveats.
- `agents/runbooks/runbook-ship-award-ledger-2026-06-05.md` — the durable `ShipAward` career ledger (distinct from the ephemeral leaderboard snapshot).
- `agents/runbooks/runbook-cache-audit.md` — cache families, TTLs, and invalidation across the app.
