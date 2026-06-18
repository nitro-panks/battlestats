# Runbook — Landing treemap + ShipLeaderboard "blank on refresh" (2026-06-18)

**Status:** DIAGNOSIS (no code change yet). Documents the current data flow of the landing-page
**most-played-ships treemap** (`RealmTopShipsTreemapSVG`) and the **inline ship leaderboard**
(`ShipLeaderboard`), and explains exactly when and why "the whole treemap and list of ships go
offline when it refreshes." Written from a code read; a repro recipe is included so the precise
trigger can be confirmed before any fix is built.

## TL;DR

The two surfaces blank **together only on a mount event** — a hard browser reload, or returning to
the landing pane from a profile (which *unmounts and remounts* the whole subtree). They are
client-only components with **no SSR and no loading skeleton**, so on mount the treemap is an empty
`<svg>` and the list reads "Loading ships…" until the first client fetch resolves. The backend
computes both payloads **synchronously on a cold Redis cache** (heavy `BattleEvent` aggregation), so
that blank window can last **seconds** — long enough to read as "offline."

What does **not** blank the treemap: an in-place refetch (realm toggle, mode toggle, TTL expiry).
The D3 tiles are imperative DOM outside React's control, so a failed or empty refetch leaves the
**previous tiles on screen as stale** — it never wipes them. (The list behaves differently — see
"The two surfaces diverge.")

## The components

| Surface | Component | Endpoint | Client TTL | Effect deps (refetch triggers) |
|---|---|---|---|---|
| Treemap | `client/app/components/RealmTopShipsTreemapSVG.tsx` | `/api/realm/<realm>/top-ships?mode=&limit=25` | 1 h | `[realm, mode]` |
| Ship list | `client/app/components/ShipLeaderboard.tsx` (`ShipList`) | `/api/realm/<realm>/ships?tier=&type=` | 1 h | `[realm, tier, type, bothSelected, selectedShip, isEasterEgg]` |
| Ship board (drill-down) | `ShipLeaderboard.tsx` (`ShipBoard`) | `/api/realm/<realm>/ship/<id>/leaderboard` | 15 min | `[realm, selectedShip]` |

Both live in the landing pane, mounted side-by-side in `PlayerSearch.tsx:465-474`:

```tsx
<RealmTopShipsTreemapSVG onSelect={(sel) => shipLeaderboardRef.current?.selectShip(sel)} />
<ShipLeaderboard ref={shipLeaderboardRef} />
```

Both read `realm` from `RealmContext`; `realm` is the **only shared refetch trigger**, so a realm
switch is the one in-place action that refetches *both* at once.

## The fetch layer (`app/lib/sharedJsonFetch.ts`)

- **Cache-then-network with a hard TTL gate** — *not* stale-while-revalidate. While a key is cached
  and unexpired it is returned with no network call. After TTL it is a plain cache miss → a full
  network round-trip (nothing stale is served *during* that trip at the fetch layer).
- **No retry** is configured by these callers (the `retry` option exists but neither the treemap nor
  the list/board passes it). A single transient 5xx / timeout / network error is therefore terminal.
- **No auto-refresh timer / no SWR.** There is no `setInterval`/`requestIdleCallback`/focus-refetch
  for these two surfaces. (`PlayerSearch`'s focus/visibility/`pageshow` handler at lines 285-309
  refreshes only the landing **players + clans** lists — it does **not** touch the treemap or ship
  list.) A refetch happens **only** on an effect-dep change or a remount.

## Why it blanks — the actual mechanism

### Treemap: blanks only before its first successful draw

The render effect (`RealmTopShipsTreemapSVG.tsx`):

```ts
175  if (!svgRef.current || !data || width <= 0 || data.ships.length === 0) return;  // guard
...
185  svg.selectAll('*').remove();                                                     // clear+rebuild
```

The `return` guard is **upstream** of `remove()`. The tiles are D3-appended `<g>`/`<rect>` nodes —
**imperative DOM that React's vdom doesn't manage**. Consequences:

- On a refetch **after a first successful draw**, if it errors (`.catch` → `setData(null)`,
  line 145) or returns empty: React re-renders only the empty `<svg ref=…/>` element; it never
  removes the D3 children. The render effect re-runs, hits the guard, and **returns before
  `remove()`**. → **The old tiles stay on screen as stale.** No blank.
- The treemap is blank **only when `data` has never been set to a non-empty payload** — i.e. before
  the *first* successful render: fresh mount, remount, or hard reload. There is **no loading
  skeleton and no error branch** — just an empty `<svg>` — so a cold/slow/failed *first* fetch shows
  literally nothing.

So `setData(null)`-on-error is **not** what blanks an already-drawn treemap. (This corrects an
earlier theory — preserved here so it isn't re-derived.)

### The two surfaces diverge on a failed *in-place* refetch

`ShipList`'s render branches (`ShipLeaderboard.tsx:429-437`) are **not** all gated on stale data:

```ts
if (loading && !ships) return <p>…Loading ships…</p>;        // only blanks when no prior list
if (error)            return <p>…Couldn't load ships…</p>;   // <-- NOT gated on !ships
if (!sortedShips || sortedShips.length === 0) return <p>…No ranked ships…</p>;
```

So on a **failed** in-place refetch (e.g. realm toggle that errors): the treemap keeps **stale
tiles**, but the list **swaps its populated table for "Couldn't load ships."** They do **not** go
offline together on a failed refetch — the list degrades, the treemap doesn't.

### They blank *together* only on a mount event

By elimination (no in-place auto-refresh; failed refetch diverges), "the whole treemap **and** list
offline together" is a **fresh mount** of the subtree, where both start at `data=null` / `list=null`:

1. **Hard browser reload** of `/`. Both are `'use client'` with no SSR, so first paint is an empty
   `<svg>` + "Loading ships…" until the client fetches resolve.
2. **Back-from-profile remount.** `PlayerSearch.tsx:448` is
   `playerData ? <PlayerDetail …/> : <landing pane>`. Opening a profile **unmounts the entire
   treemap + ShipLeaderboard subtree**; navigating back **remounts** both with fresh `null` state →
   same cold-start blank.

The **ship board** (`ShipBoard`) is a third, separate case: it sets `setBoard(null)` *before every*
drill-down fetch (line 288), so it always shows "Loading leaderboard…" briefly — but that is a
user-initiated drill-down, not "the whole treemap and list."

## Why the blank window is *long* (backend)

The blank lasts only as long as the first client fetch. That fetch is slow whenever it lands on a
**cold Redis key**, because the backend computes in-request (no serve-empty-and-queue):

- `compute_realm_top_ships` (`data.py:6530`) and `compute_realm_ships_by_tier_type` (`data.py:6623`)
  run the heavy `BattleEvent … Sum("battles_delta")` aggregation **synchronously on cache miss**,
  then `cache.set(…, timeout=26*3600)`. On a warm key the response is instant; on a cold key it is a
  multi-second analytical query.
- The cache key embeds the **window-end date**:
  `realm_cache_key(realm, f"top-ships:{mode}:win{window_end_d.isoformat()}:{limit}")`. At the
  **nightly window rollover** the entire warm set goes cold at once until `warm_realm_top_ships_task`
  (`tasks.py:1187`) repopulates it. Redis is `allkeys-lru` at a 3 GB cap, so keys can also be
  **evicted** between warms.
- Net: a mount (reload / back-from-profile) that coincides with a cold key — post-rollover, after
  eviction, or any uncached `tier×type×mode×realm` combination — pays the full query latency with
  **no skeleton to cover it and no retry to recover a timeout**, which is exactly the "offline"
  perception.

## Trigger matrix (what the code predicts)

| Action | Treemap | Ship list | Together "offline"? |
|---|---|---|---|
| Hard reload `/` | **blank** (empty `<svg>`, no skeleton) until 1st fetch | "Loading ships…" until 1st fetch | **Yes** |
| Back from a profile | **blank** (remount → `data=null`) | "Loading ships…" (remount) | **Yes** |
| Realm toggle (in place), success | stale tiles → swap | stale table → swap | No (stays populated) |
| Realm toggle (in place), **fails** | **stale tiles kept** | **"Couldn't load ships"** | No (diverge) |
| Mode toggle (treemap only) | stale tiles → swap | unaffected | No |
| Drill into a ship | unaffected | board: "Loading leaderboard…" (`setBoard(null)`) | No |

**Default reading of "when it refreshes" = a page reload / back-from-profile remount** (rows 1–2).
That is the only path the code supports for *both* surfaces blanking simultaneously.

## Confirm the trigger before fixing (repro)

Per the FE visual-verify recipe (`memory/reference_frontend_visual_verify_recipe.md`): run the client
against live prod data and watch which action blanks the treemap.

```bash
cd client
BATTLESTATS_API_ORIGIN=https://battlestats.online npm run dev   # http://localhost:3000
```

On `/`, test in order and note what the treemap does:
- **(a) toggle realm in place** — code predicts tiles stay (stale), then swap. *Not* a blank.
- **(b) hard reload** — code predicts blank `<svg>` + "Loading ships…" until fetch resolves.
- **(c) open a player, hit back** — code predicts remount blank, same as (b).

To make the cold-cache latency visible (so the blank is long enough to see), pick an
**uncached bucket**: a non-default `tier×type` (anything but T10 Battleship) or a realm/mode you
haven't loaded this session.

## Implementation status (2026-06-18)

**Option 4 (backend) is implemented** — see
`runbook-shipleaderboard-warm-before-evict-2026-06-18.md`. The treemap +
tier-type list now warm-before-evict: a window-independent durable `:published`
key serves the previous numbers on a cold (rotated/evicted) fresh key while a
queued/chained warm recomputes the new window, so the rotation gap no longer
pays a synchronous aggregation. The **`/ship/<id>` board** was excluded (fast
DB read on miss). Options **1–2 (FE skeleton / seed-last-good for the mount
blank) remain open** — the backend fix does not address the hard-reload /
back-from-profile *mount* blank.

## If/when we fix it (options, not yet decided — 1–2 still open; see status above)

Smallest safe slices, roughly in order of impact:

1. **Treemap loading + error states.** Give the empty `<svg>` a skeleton/shimmer while `data===null`
   and an explicit "Couldn't load — retry" on `.catch` (today the catch only does `setData(null)`
   with no visible affordance). Kills the "looks offline" perception on mount.
2. **Retain last-good across remount.** The blank is a *mount* problem, so component-local state
   can't help. Lift the last successful treemap/list payload into a parent or module-level cache
   (the `settledRequests` map in `sharedJsonFetch` already survives remounts within a session — a
   synchronous "seed from settled cache on first render" would paint stale-instantly instead of
   blank). Note the back-from-profile case at `PlayerSearch.tsx:448` unmounts the subtree entirely.
3. **Add `retry` to the treemap/list fetches.** `fetchSharedJson` supports it; these callers don't
   pass it. One retry on 5xx/timeout turns a transient cold-query failure into a recovered draw.
4. **Backend: serve-stale-and-queue on cold keys** (mirror the app's cache-first/lazy-refresh
   doctrine) so a window-rollover/eviction mount never pays the synchronous aggregation in-request.
   Larger change; weigh against the 26h TTL + nightly warmer already covering the steady state.

Do **not** pursue an in-place auto-refresh timer — there isn't one today and the symptom isn't an
in-place refetch.

## Key source references

- `client/app/components/RealmTopShipsTreemapSVG.tsx` — fetch effect L141-147 (`setData(null)` on
  catch L145); render-effect guard L175 vs `remove()` L185; empty `<svg>` L317.
- `client/app/components/ShipLeaderboard.tsx` — list fetch L256-279; board fetch L283-307
  (`setBoard(null)` L288); `ShipList` branches L429-437; `ShipBoard` branches L564-569.
- `client/app/lib/sharedJsonFetch.ts` — TTL gate L139-144; no SWR; `retry` opt-in L155-177.
- `client/app/components/PlayerSearch.tsx` — mount/unmount gate L448; landing-only focus refresh
  L285-309 (does not touch these surfaces).
- `server/warships/views.py` — `realm_top_ships` L2134; `realm_ships_by_tier_type` L2167;
  `ship_leaderboard` L2219.
- `server/warships/data.py` — `compute_realm_top_ships` L6530 (synchronous cold compute + 26h key
  L6564-6609); `compute_realm_ships_by_tier_type` L6623.
- `server/warships/tasks.py` — `warm_realm_top_ships_task` L1187 (nightly cache warmer).
