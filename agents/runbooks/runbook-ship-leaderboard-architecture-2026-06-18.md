# Runbook — Ship Leaderboard Architecture (2026-06-18)

**Status:** active reference · **Kind:** architecture/reference · **Section:** backend+frontend
**Reconciled 2026-06-29:** window default 14 → 30 (v2.16.0); line anchors refreshed against current code.

How the ship-standings feature works end to end: a precompute writes a
snapshot, a thin Redis read-cache serves it, and the same snapshot feeds two
surfaces — the `/ship/<id>` leaderboard page and the gold/silver/bronze ship
badges on player profiles. **Nothing is aggregated live at request time.**

Related docs: `runbook-ship-top-player-badges-2026-06-05.md` (original badge
build), `runbook-ship-badges-rolling-2026-06-14.md` (rolling-nightly decision +
Ship Honors removal), `runbook-ship-leaderboard-window-30d-2026-06-29.md`
(14→30-day window widen), `runbook-ship-banner-ux-pass-2026-06-05.md`,
`runbook-ship-leaderboard-submarine-easter-egg-2026-06-11.md` (empty-ship UI),
`agents/work-items/ship-leaderboard-ux-refresh-spec.md` (`/ship` page UX).

## Pipeline at a glance

```
snapshot task ──> compute (BattleEvent aggregate + shrinkage + z-score)
   │                └─> ShipTopPlayerSnapshot rows (rewrite-in-place, pruned)
   │
   ├─> GET /api/realm/<realm>/ship/<id>/leaderboard  (15-min Redis cache)
   │       └─> ShipRouteView.tsx  (/ship/<id> page) + ShipLeaderboard.tsx (landing drill-down)
   │
   └─> player payload `ship_badges`  (top-3 only)
           └─> ShipTopPlayerBanner.tsx  (profile badge cards)
```

## 1. Precompute — the source of truth

- **Task:** `snapshot_ship_top_players_task` — `server/warships/tasks.py:1215`.
  Runs **per realm, twice daily** (every 12h: each realm fires at `realm_hour`
  and `realm_hour+12`, striped via `REALM_INTERVAL_OFFSETS*4` in
  `signals.py:243`), gated by `SHIP_BADGE_SNAPSHOT_ENABLED`. Delegates to
  `data.compute_ship_top_player_snapshot()`; on success enqueues
  `materialize_landing_player_best_snapshots_task` (`tasks.py:1579`, called at
  `tasks.py:1250`) to refresh landing caches.
- **Compute:** `data.compute_ship_top_player_snapshot()` — `data.py:6091`.
  1. **Aggregate `BattleEvent`** over a trailing `SHIP_LEADERBOARD_WINDOW_DAYS`
     window (default **30**, `data.py:6015`) — filtered `mode='random'`,
     `player__is_hidden=False`, realm, window; grouped by `(ship_id, player_id)`;
     sums battles/wins/damage/frags/survived. Scope = `SHIP_BADGE_TIERS`
     (**prod = 8,9,10; local default = 10** — see the local-default memory).
  2. **Empirical-Bayes shrinkage** of each metric toward a prior
     (`SHIP_BADGE_PRIOR_WR=0.5`, `SHIP_BADGE_PRIOR_BATTLES=50`) so short hot
     streaks regress and high-volume records stay near true rate.
  3. **Within-pool z-score** of three signals — win rate, avg damage/battle,
     kills/battle — blended by weight `SHIP_BADGE_WEIGHT_WINS=0.5` /
     `_DAMAGE=0.35` / `_KILLS=0.15` into one composite.
  4. **Rank + persist** top `SHIP_BADGE_LIST_SIZE` (15) per qualifying ship as
     `ShipTopPlayerSnapshot`; ranks ≤ `SHIP_BADGE_TOP_N` (3) become profile
     badges. Per-ship gates: `SHIP_BADGE_MIN_BATTLES` (15),
     `SHIP_BADGE_MIN_SHIP_POPULATION` (20; CV=10, sub=12).
  5. **Invalidate** caches for both the new top-3 *and* the previous run's top-3
     (dropped players lose badges immediately), then **prune** snapshots older
     than `SHIP_BADGE_RETENTION_DAYS` (5).

**Design note:** the board is ephemeral and rewritten in place — "badges worn
while held, no durable ledger." A rolling ledger would inflate ~30× (one row set
per window-day; rolling decision 2026-06-14), which is why nothing accumulates.

## 2. Model — `ShipTopPlayerSnapshot` (`models.py:815`)

`captured_on` (date, snapshot identity), `realm`, `ship_id`/`ship_name`, `rank`,
`player` FK, raw window stats (`win_rate`, `battles`, `damage`, `frags`,
`survived`). Unique `(captured_on, realm, ship_id, rank)`; indexed
`(player, -captured_on)` for badge reads.

## 3. API — `GET /api/realm/<realm>/ship/<ship_id>/leaderboard`

- View `ship_leaderboard` — `views.py:2241`. Validates realm → Redis read-cache
  `{realm}:ship-lb:{ship_id}`, TTL `SHIP_LEADERBOARD_CACHE_TTL=900` (15 min,
  `data.py:6016`) → on miss `data.get_ship_leaderboard()` (`data.py:6508`).
- Read function fetches the **latest `captured_on`** rows for realm+ship,
  re-applies `is_hidden=False` at read time (an account gone private since the
  snapshot drops out), and shapes `avg_damage = damage/battles`,
  `kills_per_battle = frags/battles`. **No live aggregation.**
- Payload: `realm`, `window_days`, `captured_on`, `window_start`, ship identity
  (`tier`/`ship_type`/`nation`/`is_premium`), ranked `players[]`. Unknown ship →
  404; ranked-but-empty → `players: []`.

## 4. Profile badges — `data.get_player_ship_badges` (`data.py:6377`)

Reads the player's latest snapshot rows where `rank ≤ SHIP_BADGE_TOP_N` (3),
keeps badge-eligible tiers only, sorts tier-desc then rank (most prestigious T10
leads). Bulk variant `get_players_ship_badges_bulk` (`data.py:6440`) does it in
2 queries (latest `captured_on` per player + all badge rows) to avoid N+1 across
rosters/search. The `ship_badges` array is embedded in the player payload — **no
extra fetch.**

## 5. Frontend

- **Routing:** `/ship/[shipSlug]/page.tsx`; slug = `<ship_id>-<name>`,
  `parseShipIdFromRouteSegment` (`app/lib/entityRoutes.ts`) takes the leading
  digits as the ID.
- **`ShipRouteView.tsx`** — fetches the leaderboard endpoint (client cache
  mirrors the 15-min TTL), renders masthead identity (class glyph + tier/class/
  nation chips + premium marker via `app/lib/shipIdentity.ts`), a champion/podium
  desktop table, and a mobile-card split. Rows link to
  `/player/<name>?realm=<realm>`.
- **`ShipLeaderboard.tsx`** — landing drill-down: ship list (`/api/realm/.../ships`,
  1h cache) → reuses the same `/ship/<id>/leaderboard` endpoint (15-min cache).
- **`ShipTopPlayerBanner.tsx`** — top-3 cards above Battle History from
  `ship_badges`, each linking to `/ship/<id>` via `buildShipPath`.

## Mental model / one-liner

Twice a day, per realm, the system rewrites a trailing-30-day **random-battles**
T8–T10 board using a shrinkage + z-score composite (WR-weighted), stores top-15
per ship in `ShipTopPlayerSnapshot`, and serves it via a 15-min Redis read-cache.
Top-3 finishes become profile badges **worn only while held** — fall out of the
window, lose the badge on the next snapshot.

## Gotchas

- **Coverage is observation-density bound.** A ship needs ≥
  `SHIP_BADGE_MIN_SHIP_POPULATION` qualifying players in the window to produce a
  board; thinly-played ships render empty (hence the submarine easter-egg UI).
  Sparse `BattleObservation`/`BattleEvent` density caps how complete boards are.
- **Local vs prod tier scope differs** (`SHIP_BADGE_TIERS` local default `10`,
  prod `8,9,10`) — T8/T9 ship features 400 locally until set.
- **`captured_on` is UTC** (backend buckets by UTC); the page shows "Captured …
  UTC".

## Env knobs (defaults)

`SHIP_BADGE_SNAPSHOT_ENABLED` (kill switch) · `SHIP_LEADERBOARD_WINDOW_DAYS=30` ·
`SHIP_LEADERBOARD_CACHE_TTL=900` · `SHIP_BADGE_TIERS` (prod 8,9,10) ·
`SHIP_BADGE_TOP_N=3` · `SHIP_BADGE_LIST_SIZE=15` · `SHIP_BADGE_RETENTION_DAYS=5` ·
`SHIP_BADGE_MIN_BATTLES=15` · `SHIP_BADGE_MIN_SHIP_POPULATION=20` (CV 10 / sub 12) ·
`SHIP_BADGE_PRIOR_WR=0.5` · `SHIP_BADGE_PRIOR_BATTLES=50` ·
`SHIP_BADGE_WEIGHT_WINS=0.5` / `_DAMAGE=0.35` / `_KILLS=0.15` ·
`SHIP_BADGE_SNAPSHOT_HOUR=2`.
