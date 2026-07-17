# Runbook — Landing treemap ↔ ship-filter correlation

**Date:** 2026-07-01
**Status:** active (shipped)
**Owner:** frontend
**Area:** landing page (`PlayerSearch.tsx`, `RealmTopShipsTreemapSVG.tsx`, `ShipLeaderboard.tsx`); rides the existing `compute_realm_ships_by_tier_type` warm chain (no backend change)

## What shipped

The landing treemap now **mirrors the ship-filter selection directly below it**
instead of showing a fixed "25 most-played ships, all tiers/types" view.

- Pick **T10 Cruiser** in the filter bar and the treemap redraws to just T10
  cruisers; change Tier / Type / WR% and the treemap follows in lockstep with the
  table.
- **Tile size** = battles (popularity within the bucket). **Tile color** = win
  rate, via the same `wrColor` scale the table uses (`app/lib/wrColor.ts`), so a
  tile's color matches the WR number on its row inches below.
- The treemap **tracks the WR filter** (All / top-50% / top-25%): its battles and
  colors re-pool with the table's selection.
- The treemap's **Random / Ranked toggle was removed** — it is Random-only now,
  matching the leaderboard (see "Why no ranked" below).
- Tile click still drills into that ship's player board in place (unchanged
  `shipLeaderboardRef.selectShip()` handoff).

## Why this needed no new backend warming

The treemap is now fed by the **same** endpoint the leaderboard already calls —
`GET /api/realm/<realm>/ships?tier=&type=&wr_pct=` (`realm_ships_by_tier_type`,
`views.py`). That payload already carries everything a per-bucket treemap needs:
`ship_id, ship_name, ship_type, tier, battles, win_rate`, plus `total_battles`,
`window_start/end`, `captured_on`.

The nightly warm chain **already pre-warms every tier×type bucket** at all three
WR settings, `mode=random`:

- `warm_realm_top_ships_task` (`tasks.py:1360-1434`) walks every
  `tier ∈ _badge_tiers()` × `ship_type ∈ SHIP_LEADERBOARD_TYPES` all-view bucket,
  then chains `queue_realm_ships_pct_warm`.
- `warm_realm_ships_pct_task` (`tasks.py:1480-1554`) walks the same grid at
  `wr_pct=50` (one query materializes **both** 50 & 25), skip-if-warm.

So the "warm a bunch of treemaps" work was already done: the correlated treemap
simply reads the buckets the leaderboard fetches. **This shipped as a frontend-only
change.**

### Why no ranked

Only the treemap's random-mode top-ships payload and the **random** all/pct
tier×type buckets are warmed. Ranked-per-`(tier,type)` is **not** warmed. Keeping a
ranked toggle correlated to the filters would have made every ranked bucket a cold
~10-28s per-`(ship,player)` compute on the request thread. The leaderboard is
already Random-only, so the toggle was dropped rather than adding a new nightly
ranked-per-bucket warm task. If ranked-per-bucket is ever wanted, it requires a new
warm leg mirroring the random one — do not re-add the toggle without it.

## Architecture — single source of truth for the bucket

`ShipLeaderboard` remains the sole owner of the bucket fetch (its
restore/persist, WR-percentile poll, easter-egg, and board-drill logic are
untouched). It emits the resolved bucket upward; `PlayerSearch` holds it and feeds
the treemap. The treemap became a **presentational** component (no fetch of its
own), so the two surfaces can never disagree and never double-fetch.

Data flow (bidirectional, both reuse existing plumbing):

- **leaderboard → treemap** — new `onBucket({tier, type, wrPct, ships,
  totalBattles, windowStart, windowEnd, loading, pending, empty})` callback,
  fired from a state-keyed effect in `ShipLeaderboard`.
- **treemap → leaderboard** — the existing `shipLeaderboardRef.selectShip()`
  imperative handle (tile drill-down), unchanged.

While a ship board is open, `ShipLeaderboard`'s list effect is gated off; the
last-resolved bucket stays in state, so the treemap keeps showing the correct
tier+type (the bucket does not change when you drill into one of its ships).

Easter-egg buckets (T9 Submarine, T9 AirCarrier — no such ships exist in WoWS)
emit `empty: true`; the treemap renders a **plain empty box** (no tiles, no
caption) sized to the exact treemap height so the ship leaderboard below never
shifts. The svg's height attr is explicitly zeroed on an empty render so a prior
populated render's height can't stack on top of the box. (The ShipLeaderboard's
own ASCII sub/CV easter-egg animation below is unchanged.)

## Files

- `client/app/components/RealmTopShipsTreemapSVG.tsx` — presentational rewrite:
  props-driven (`ships`, `tier`, `type`, `wrPct`, window bounds, `loading`,
  `pending`, `empty`, `onSelect`); WR-color tiles; removed self-fetch + mode
  toggle; bucket-aware heading + tooltip copy. Tile/tooltip refresh 2026-07-17
  (4.1.4): tile sub-line is the WR% alone (battles count dropped), names at
  12px; the hover tooltip (shared by Map and Plot views) is a bold title over
  value/label pairs on a two-column grid — battles (pluralized), `wrColor`-tinted
  WR, and a bold class+tier row — matching the player-page treemap tooltips
  (see runbook-battle-history-treemaps-2026-07-13.md).
- `client/app/components/ShipLeaderboard.tsx` — added `onBucket` prop + emit
  effect; exported `ListShip`. No other behavior change.
- `client/app/components/PlayerSearch.tsx` — holds bucket state, passes it to the
  treemap alongside `onSelect`.

## QA / verification

Local caveat: `SHIP_BADGE_TIERS='10'` locally, so **only T10 works**; T8/T9 pills
already 400 today (pre-existing, not a regression). Demo on T10.

1. `docker compose up -d` (Django :8888); `cd client && npm run dev` (:3000).
2. Land on T10 Battleship → treemap shows T10 BBs colored by WR.
3. Switch Type BB→CA→DD → treemap redraws each time; tile colors match the table
   WR column.
4. Toggle WR All→50%→25% → treemap battles shrink and colors shift in lockstep
   with the table.
5. Click a treemap tile → drills into that ship's board; Clear returns to the
   list.
6. `cd client && npm run lint && npm test`; backend `pytest -k ship` stays green
   (no backend change, sqlite harness).

## Known limitations

- **Filter-switch transition.** On a tier/type/WR switch, `ShipLeaderboard.list`
  lags one render (the refetch runs in a post-commit effect). The emit tags the
  stale bucket via `listBucketKey`, so the treemap **dims the old map and waits**
  rather than painting the prior bucket's ships under the new heading. For a warm
  bucket the swap is ~1 frame.
- **Realm switch while a ship board is open.** The emit effect is not keyed on
  realm, and the list refetch is gated off while a ship is drilled in, so the
  treemap keeps showing the previous realm's bucket until the user clears the ship
  board (which re-runs the list fetch). Narrow (drill-in + realm-switch on the
  landing) and self-correcting; not fixed to avoid an extra fetch on every drill.

## Optional follow-up (not done — deferred to keep the slice minimal)

`/api/realm/<realm>/top-ships` + `compute_realm_top_ships` + the `top-ships` legs
of `warm_realm_top_ships_task` are now **frontend-dead** (the treemap was the sole
consumer — confirmed via grep). Retiring them removes a small nightly waste and a
now-misleading "treemap" docstring in `warm_realm_top_ships_task`. Left in place
for now; retire when convenient.

## Rollout

Frontend-only. Ships as a **minor** (UX change). Per `CLAUDE.md`, after any
version bump the client must be rebuilt/redeployed for the footer version to
update. No backend deploy, migration, or env change required.
