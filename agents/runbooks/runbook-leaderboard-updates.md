# Runbook: Ship Leaderboard Data Freshness & Update Cadence

_Last updated: 2026-06-15_

_Status: Active operational reference_

_Context: Captures how "live" the ship leaderboard data actually is — the full freshness chain from the `ShipLeaderboard` / `ShipRouteView` / `RealmTopShipsTreemapSVG` components down to the snapshot writer — so future operators don't misread the standings as real-time._

> **Cadence updated 2026-06-14/15 — now a NIGHTLY ROLLING recompute, not a fortnightly season.** Earlier revisions of this runbook described a fixed 2-week season boundary; that model is gone. The `/ship/<id>` board + profile badges moved to a rolling trailing 14-day recompute on 2026-06-14 ([runbook-ship-badges-rolling-2026-06-14.md](runbook-ship-badges-rolling-2026-06-14.md)), and the landing treemap + tier/type drill-down list followed on 2026-06-15 so all three share one rolling window 1:1.

## Purpose

Answer the recurring question: **how live are the ship stats shown on `/ship/<id>`, the landing tier/type drill-down board, and the realm treemap?** The short answer is **not live, but daily** — they are a precomputed snapshot recomputed **every night** over a **rolling trailing 14-day window**, not a real-time query. This runbook documents the dominant cadence (the nightly snapshot), the 15-minute caches layered on top, the kill switch that gates the writer, and how to confirm the current state in prod.

Read this before "fixing" a leaderboard that looks stale — within a day, static is **expected**, not a bug.

## Freshness chain (dominant factor first)

### 1. The real cadence — once a night

The read path does **no live aggregation**. `get_ship_leaderboard()` (`server/warships/data.py`) does a pure snapshot read of the most recent `captured_on`'s `ShipTopPlayerSnapshot` rows for the ship+realm, joins `Ship` for the header, and shapes the payload. The endpoint is `ship_leaderboard` (`server/warships/views.py`).

Those snapshot rows are written by `snapshot_ship_top_players_task` (`server/warships/tasks.py`):

- Runs **nightly** per realm (Beat `ship-top-player-snapshot-{realm}`, striped by `REALM_CRAWL_CRON_HOURS`, ~02:30 + realm offset UTC). Each run recomputes the whole trailing window and overwrites that night's rows — there is no season boundary and no "finalize once" gate.
- `SHIP_LEADERBOARD_WINDOW_DAYS = 14` (`data.py`) is the lookback span (operator-tunable). The fixed `SHIP_SEASON_*` epoch/length and `is_season_boundary()` are gone.
- The displayed board is the latest `captured_on` (a **run date**); its window is `[captured_on - 14d, captured_on)`. Badges are worn **only while held** — a player who drops out of the top 3 loses the badge on the next nightly run.

**Implication:** the numbers advance **every day**. A trailing 14-day window shares ~93% of its data night to night, so turnover is gradual (a few ships/night), not churn — but it is no longer static. The displayed stats (`win_rate`, `battles`, `avg_damage`, `kills_per_battle`) are delta-sums over the trailing 14-day window — the same basis as the profile ship badges. (Survival% / KDR are intentionally omitted: per-battle survival isn't available for a multi-battle window, so it would undercount.)

### 2. 15-minute caches on top (negligible vs the nightly recompute)

These only matter for the few minutes right after a new nightly snapshot lands; against a daily refresh they are noise.

- **Backend Redis read-cache** — `SHIP_LEADERBOARD_CACHE_TTL = 900s` (`data.py`), applied in the view (`cache_key = f"{realm}:ship-lb:{ship_id}"`).
- **Client fetch cache** — `BOARD_FETCH_TTL_MS = 900_000` in `client/app/components/ShipLeaderboard.tsx`; the same 15 min as `SHIP_LEADERBOARD_FETCH_TTL_MS` in `client/app/components/ShipRouteView.tsx`. Both fetch via `fetchSharedJson`. Column **sorting is client-side** over the already-fetched rows — sorting never refetches.

Note: `ShipLeaderboard.tsx` (landing tier/type drill-down) and `ShipRouteView.tsx` (the `/ship/<id>` page) hit the **same** `/api/realm/<realm>/ship/<ship_id>/leaderboard` endpoint, so the freshness story is identical for both.

### 3. Kill switch gating the writer

`SHIP_BADGE_SNAPSHOT_ENABLED` (default `0`) gates `snapshot_ship_top_players_task`. If `0`, **no new snapshots are written** and the board freezes at the last-written night indefinitely. Prod is `=1`; tiers pinned `SHIP_BADGE_TIERS=8,9,10`.

## Verify current state in prod

```bash
# Writer enabled? Which tiers?
ssh root@battlestats.online 'grep -E "SHIP_BADGE_SNAPSHOT_ENABLED|SHIP_BADGE_TIERS" /etc/battlestats-server.env'

# What night is currently published, and how many rows?
ssh root@battlestats.online 'cd /opt/battlestats-server/current/server;
  set -a; . /etc/battlestats-server.env; . /etc/battlestats-server.secrets.env; set +a;
  /opt/battlestats-server/venv/bin/python manage.py shell -c "
from warships.models import ShipTopPlayerSnapshot as S
print(\"distinct captured_on:\", list(S.objects.values_list(\"captured_on\", flat=True).distinct().order_by(\"-captured_on\")[:4]))
print(\"rows total:\", S.objects.count())
"'
```

**Expected:** the newest `captured_on` should be **today (or yesterday)** per realm, with a few nights of history retained (`SHIP_BADGE_RETENTION_DAYS`, default 5) before pruning. If the newest `captured_on` is several days old, the nightly writer isn't firing — investigate the `background` worker / Beat, not the read path.

## The landing tier/type list + treemap share the rolling window (1:1 with the board)

The freshness story above is the snapshot **board** read (`/ship/<id>/leaderboard`). The landing page layers two more surfaces, both now aggregating over the **same rolling window** the board reads:

- **Treemap** (`RealmTopShipsTreemapSVG.tsx`) → `/api/realm/<realm>/top-ships` → `compute_realm_top_ships` (`data.py`) — most-played ships, a `BattleEvent` GROUP-BY over the trailing window.
- **Tier/type pill** (`ShipLeaderboard.tsx`) → `/api/realm/<realm>/ships?tier=&type=` → `compute_realm_ships_by_tier_type` (`data.py`) — a `BattleEvent` GROUP-BY (`Sum` of battles/wins/damage/frags over the window, joined to `Player` for the realm), candidate set restricted to ships ranked in the latest snapshot. The payload also carries **`total_battles`** — battles over **every** ship of that tier+type in the window (a second `Sum` over the full Ship-table tier+type set, *not* the snapshot-ranked candidates, and dropping the min-battles floor — same window/realm/mode/`is_hidden` basis as the per-ship rows so the fractions are consistent). The client renders each ship's Battles cell as `count (share%)` where `share = ship.battles / total_battles`; because the denominator includes unlisted low-population ships, the listed shares sum to **<100%** by design. Old `:published` payloads predating the field render battles-only (client guards `total_battles ≤ 0`).
- **Drill in** (click a ship) → `/ship/<id>/leaderboard` → `get_ship_leaderboard` → cheap `ShipTopPlayerSnapshot` row read, Redis-cached 15 min. Fast.

Both GROUP-BY surfaces resolve their window via `latest_ship_snapshot_window(realm)` — anchored on the realm's latest `ShipTopPlayerSnapshot.captured_on`, so a clicked treemap tile and its drill-down board cover the **identical date span** (no off-by-a-window mismatch). Results are cached per bucket under a **window-end-tagged** key (`top-ships:<mode>:win<YYYY-MM-DD>:<limit>` and `ships-by:<mode>:win<YYYY-MM-DD>:t<tier>:<type>`), so when a new nightly snapshot lands the key changes and the next request recomputes over the matching window — **alignment self-heals regardless of beat order.**

**Switch-lag warming:** there are **3 tiers × 5 types = 15 buckets per realm**; cold, the first click of a new combination would pay the full aggregation on the request path. `warm_realm_top_ships_task` (Beat `top-ships-warmer-{realm}`) force-recomputes both treemap modes + every populated tier/type bucket once a day. It is scheduled **~1h after that realm's nightly snapshot** (snapshot ~02:30, warm ~03:xx striped) so it warms the *current* window rather than the previous day's. Buckets with no candidate ships short-circuit before the aggregation and aren't cached — that cheap early-return path was never the lag source. Tests: `test_realm_ships_by_tier_type.py::RealmShipsByTierTypeWarmTests`.

## Diagnosing "the leaderboard looks stale"

1. **Is the newest `captured_on` today/yesterday?** If so, static *within the day* is expected — it advances on the next nightly run. **Not a bug.**
2. **Is `captured_on` several days behind?** Confirm `SHIP_BADGE_SNAPSHOT_ENABLED=1`, then check whether `snapshot_ship_top_players_task` ran (Beat health, task logs). If it's stuck, investigate the `background` worker / Beat, not the read path.
3. **Treemap/list disagree with a ship's drill-down board?** They share the rolling window via `latest_ship_snapshot_window`, but a ship not ranked on the latest night keeps an older per-ship `captured_on`, so its `/ship/<id>` window can lag the realm-wide treemap by a snapshot. Pre-existing and minor.
4. **Stale only for ~15 min after a known nightly run?** That's the Redis + client caches draining. Harmless.
5. **A specific strong player is missing a badge/standing** — this is usually sparse `BattleObservation` mis-bucketing by `detected_at`, a separate concern (see `runbook-ship-top-player-badges-2026-06-05.md`), not a leaderboard-freshness issue.

## If even-fresher ship stats are ever required

This is a deliberate snapshot-backed design (cheap reads, no population-wide live aggregation on request). The window now advances nightly; closing the remaining ≤1-day gap is a **non-trivial** change, not a config tweak:

- **Shorten the window** — smaller per-window battle samples (noisier standings); tunable via `SHIP_LEADERBOARD_WINDOW_DAYS` but it trades sample size for recency.
- **Add a live-aggregation path** — on-request or short-TTL recompute of `BattleEvent` deltas per ship; far more expensive (the reason the snapshot model exists), would need its own caching/work-mem budget.

Neither should be undertaken as a quick fix; size it as a feature.

## Related runbooks

- `agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md` — the canonical rolling-nightly snapshot/badge engine + Ship Honors removal.
- `agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md` — the original snapshot/badge engine design (ranking, population guards, `detected_at` bucketing caveats); cadence sections superseded by the rolling runbook.
- `agents/runbooks/runbook-cache-audit.md` — cache families, TTLs, and invalidation across the app.
