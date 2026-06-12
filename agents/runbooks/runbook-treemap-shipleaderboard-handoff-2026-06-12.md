# Runbook: Treemap → ShipLeaderboard In-Place Drill-Down

## Summary

On the landing page, clicking a tile in the realm most-played-ships treemap
(`RealmTopShipsTreemapSVG`) now drills **straight to that ship's best-player board inside the inline
`ShipLeaderboard`** that sits directly below it — in place, no navigation. The treemap sets the
leaderboard's tier + type to the clicked ship and opens its player board, scrolling the leaderboard
into view. The standalone `/ship/<id>` route is unchanged and remains the fallback for tiles the
leaderboard can't represent (sub-T8, null tier, unknown type).

This is a **frontend-only** change. No backend, API, payload, or DB work — both surfaces already
consume `/api/realm/<realm>/top-ships` and `/api/realm/<realm>/ship/<id>/leaderboard`.

## UI Behavior

- **Supported tile** (tier 8/9/10 + one of BB/CA/DD/CV/SS): click sets the leaderboard tier+type
  pills to match, opens that ship's player board in place, and smooth-scrolls the leaderboard
  section into view. Clicking **Clear** in the board returns to the ship list for that same
  tier+type (proving the filters were set, not just the board).
- **Unsupported tile** (sub-T8, null tier, or a type outside the five canonical classes): falls back
  to the existing full-page `router.push('/ship/<id>')` navigation. No tile is a dead click.
- **Both treemap modes** (Random / Ranked) drill to the same season-snapshot board the leaderboard
  already fetches — identical to the prior `/ship/<id>` navigation, so no new data inconsistency.

## Handoff Contract

The bridge is a one-shot **imperative command**, not lifted state. `ShipLeaderboard` exposes a ref
handle; the treemap calls it through the shared parent `PlayerSearch`. This keeps the leaderboard's
large internal state cluster (list / board / loading / error / sort) encapsulated and avoids
prop↔state sync races (e.g. a prop re-applying after the user hits Clear).

```
RealmTopShipsTreemapSVG  --onSelect(sel)-->  PlayerSearch  --ref.selectShip(sel)-->  ShipLeaderboard
```

### `ShipLeaderboardHandle`

```ts
export interface ShipLeaderboardHandle {
    selectShip(sel: { id: number; name: string; tier: Tier; type: ShipType }): void;
}
```

`selectShip` sets `tier`, `type`, and `selectedShip` directly (not via the `chooseTier`/`chooseType`
pill handlers, which no-op on unchanged values and clear `selectedShip`), then scrolls the section
into view. Setting `selectedShip` fires the board fetch effect (straight to the board); the list
effect stays dormant while `selectedShip` is truthy. Setting tier/type directly means a later
**Clear** lands on the correct tier/type ship list.

The three `setState` calls run inside a native D3 click listener; React 18 automatic batching
collapses them into one render, so no intermediate render with the new tier/type + null
`selectedShip` fires a wasted list fetch.

`Tier`, `ShipType`, and `SHIP_TYPES` are exported from `ShipLeaderboard.tsx` and reused by the
treemap to gate which tiles drill in place vs. fall back to the route.

### Stale-closure guard (treemap)

The treemap's click handler is bound inside its D3 render `useEffect`. To pick up the latest
`onSelect` without rebuilding the D3 tree, the callback is mirrored into a ref
(`onSelectRef`) updated every render, and the handler reads `onSelectRef.current`. The render
effect's dependency array is unchanged.

## Analytics

- `treemap-ship` now carries `target: 'leaderboard' | 'route'` distinguishing the in-place drill
  from the fallback navigation.
- `ship-leaderboard-drilldown` carries `source: 'treemap' | 'row'` distinguishing a treemap handoff
  from a ship-list row click.

## Files

- `client/app/components/ShipLeaderboard.tsx` — `forwardRef` + `useImperativeHandle` handle, exports.
- `client/app/components/RealmTopShipsTreemapSVG.tsx` — `onSelect` prop, `onSelectRef`, click branch.
- `client/app/components/PlayerSearch.tsx` — ref bridge between the two siblings.
- `client/app/components/__tests__/ShipLeaderboard.test.tsx` — handle test.

## Verification

1. `cd client && npx tsc --noEmit && npm run lint && npm run build`.
2. `cd client && npm test -- app/components/__tests__/ShipLeaderboard.test.tsx`.
3. Manual (`npm run dev`, port 3000): click a T10 DD tile → board in place; Clear → T10 DD list;
   T10 CV tile → CV board; sub-T8 tile (if present) → `/ship/<id>` navigation; Ranked mode → still
   in place; repeat on EU/ASIA for realm consistency. DevTools: one
   `/api/realm/<realm>/ship/<id>/leaderboard` request, no full document load.

## Out of Scope

- Any change to the `/ship/<id>` route, its payload, or backend endpoints.
- Highlighting the originating tile or syncing treemap mode into the leaderboard.
- Lifting leaderboard state into the parent (rejected in favor of the imperative handle).
