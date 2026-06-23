# Runbook — Inline ship-list win-rate-percentile filter (top 50% / 25%)

**Date:** 2026-06-23
**Status:** active (shipped)
**Owner:** data
**Area:** landing inline ship leaderboard (`ShipLeaderboard.tsx`), `compute_realm_ships_by_tier_type`, `warm_ships_by_pct_task`

## What shipped

The inline ship list (landing page, under the treemap — pick a Tier + Type, ships
ranked by win rate) gained a **WR filter** in the filter bar, right of the SS type
pill: `WR  [All] [50%] [25%]`.

- **All** is the prior behavior — each ship's stats (battles, avg damage,
  kills/battle, win rate) are realm-wide aggregates over the rolling trailing
  `SHIP_LEADERBOARD_WINDOW_DAYS` (14) window.
- **50% / 25%** re-pool each ship's stats over only the **top N% of that ship's
  players by window win rate**, answering "how are good/great players doing with
  these ships?".
- **The list defaults to 50%** (not All) — the landing leads with the good-player
  view. The default landing bucket is pre-warmed (below) so this is instant.

**Load-bearing constraint:** the filter NEVER changes *which* ships are listed —
only the displayed numbers narrow. Ship membership still gates on the ship's
**full-population** battles ≥ `SHIP_LIST_MIN_BATTLES` (50), identical to the
all-view. The list re-orders by the subset win rate (desc) and every column stays
click-sortable on the new numbers.

The filter applies to the **ship list only**, not the drilled-in per-player board;
the WR pills are hidden while a ship board is open.

## Backend

`compute_realm_ships_by_tier_type(realm, tier, ship_type, mode, wr_pct=None, player_min_battles=None, use_cache=True)`
(`server/warships/data.py`):

- `wr_pct=None` → unchanged cheap path (one `BattleEvent` GROUP BY ship → ~31
  rows). **Hot path is untouched** — landing visitors and the daily warmer never
  pay the heavier query.
- `wr_pct in (50, 25)` → heavy path: one per-`(ship, player)` `BattleEvent`
  aggregation over the window (47k rows for the busiest NA T10 bucket), then for
  each ship rank its players by win rate (tie-break battles, then player id),
  keep the top `ceil(n·pct/100)` (≥1), and re-pool. `100` is an internal
  equivalence hatch (ignores the floor, pools everyone → reproduces the cheap
  all-path exactly; pinned by `test_pct_100_no_floor_matches_the_all_path_exactly`).
- **Two distinct floors** (kept separate in code + docs):
  - `SHIP_LIST_MIN_BATTLES` (50) — gates whether a *ship* is listed, on
    full-population battles. Unchanged by this filter.
  - `SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES` (env, default 15) — a *player* needs
    this many window battles to enter the ranking, so "top 25% by WR" reflects
    players with a real sample, not tiny-sample 100%-WR tourists. If the floor
    leaves a ship's ranked population empty, it **falls back to full-population
    stats** rather than dropping the ship (never violate the constraint).

Endpoint: `GET /api/realm/<realm>/ships?tier=&type=&wr_pct=50|25`. Unsupported
`wr_pct` values fall through to the all-view. Payload gains a top-level `wr_pct`
field (`null` for all).

### Cold-compute handling — lazy + async poll (NOT synchronous, NOT daily-warmed)

The percentile recompute is **~15–28s** for popular T10 buckets (PDSS is no
faster — same 14-day scan), which is **over the client's 15s fetch timeout**.
Warming every pct bucket daily would add ~2–3× the existing ship-list warm load
onto the 2-vCPU Postgres (the binding constraint). So neither synchronous-on-cold
nor daily-warm was chosen. Instead (decision: August, 2026-06-23):

- The read path **never computes the heavy query**. On a cold percentile fresh
  key it queues `warm_ships_by_pct_task` (background queue, per-bucket lock +
  dispatch dedup → a burst of pollers enqueues at most one warm) and returns a
  `pending: True` payload (`ships: []`) + sets the `X-Ships-WR-Pending: true`
  response header.
- The background task runs the heavy compute with `use_cache=False`. **One run
  materializes BOTH 50 and 25** from the single per-player query and writes both
  window-keyed fresh keys (26h TTL). So a 50↔25 toggle never re-runs the query.
- Percentile keys carry **NO durable `:published` fallback** (unlike the all/treemap
  keys): the all-only daily warmer can't refresh a pct published key, so it would
  serve a frozen window forever. A cold pct key simply re-queues + polls.
- Client (`ShipLeaderboard.tsx`): the percentile fetch uses `ttlMs:0` (bypasses
  the settled client cache, so a `pending` stub never poisons it and each poll
  hits the server) and polls every ~3s up to ~16× (~48s budget) while
  `data.pending`, showing a one-time "Crunching stats for the top N%…" message.
  Once a non-pending payload lands it renders + stops. The default all-view keeps
  its normal client cache.

### Default-bucket warm (because the list now defaults to 50%)

Since the landing list defaults to the top-50% view, the *default* bucket can't be
lazy — the primary landing view would crunch for ~28s on the first load after each
nightly window rotation. So `warm_realm_top_ships_task` (the existing daily ship
warmer, chained after the snapshot) now also pre-computes the **single default
bucket** per realm: `SHIP_LIST_DEFAULT_TIER`/`SHIP_LIST_DEFAULT_TYPE` (= T10
Battleship, mirroring `ShipLeaderboard.tsx`), one heavy query that caches both 50 &
25. **Only that one bucket** is warmed — every other tier/type at 50%/25% stays
lazy (queue + poll on first view), so the added daily DB cost is ~1 heavy query per
realm, not the full 15-bucket grid.

## Cost / ops notes

- Added daily DB load: **~1 heavy query per realm** (the default bucket warm).
  Every other pct bucket computes only when a user opens the WR filter on it,
  cached 26h, recomputed at most once/day/bucket on the first cold view after the
  nightly window rotation.
- `warm_ships_by_pct_task` runs on the `background` worker (`battlestats-celery-background`),
  off the user-facing lanes. Per-bucket lock TTL 300s.
- Knob: `SHIP_LIST_WR_PCT_PLAYER_MIN_BATTLES` (default 15) — raise to demand a
  larger per-player sample for "good players"; does not affect the listed set.

## Tests

- Backend: `server/warships/tests/test_realm_ships_by_tier_type.py`
  (`RealmShipsByTierTypeWrPctTests`) — top-25/50 re-pooling math, **ship-set
  equivalence to the all-view**, player-floor fallback, the 100% equivalence
  hatch, the cold→pending+queue serve, warm-then-ready, and the view honoring /
  ignoring `wr_pct` + the pending header.
- Frontend: `client/app/components/__tests__/ShipLeaderboard.test.tsx` (WR-percentile
  describe) — the `&wr_pct` fetch with `ttlMs:0`, the pending→poll→render flow with
  the crunching message, All issuing no param, and the pills hidden in board view.

## Validation (local, 2026-06-23, cloud DB + dockerized redis/rabbitmq)

End-to-end verified: cold 25% → `pending` + header → `warm_ships_by_pct_task`
computed na/T10/Cruiser in ~31s → re-request ready (31 ships, same set), sibling
50% instant from the same warm. Screenshots confirmed All / crunching / top-50 /
top-25 render with the same ship set, WR + damage climbing as the percentile
narrows (Sicilia 56.6%→ Aki 70.3%, Slava 118k→140k avg dmg at top-25%).
