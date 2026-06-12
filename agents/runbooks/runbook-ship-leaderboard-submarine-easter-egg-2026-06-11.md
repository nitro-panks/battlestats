# Runbook — Ship Leaderboard "Tier 9 Submarine" ASCII-Sub Easter Egg (2026-06-11)

**Status:** IMPLEMENTED (2026-06-12). Built per this plan: a single new client component
(`SubmarineEasterEgg.tsx`) + the parent short-circuit wiring change in `ShipLeaderboard.tsx`, with a
wiring test. Scope: a single new client component + a four-line wiring change.

## Why

The `ShipLeaderboard` inline explorer (`client/app/components/ShipLeaderboard.tsx`) lets a user pick
any **tier ∈ {8, 9, 10}** × **type ∈ {Battleship, Cruiser, Destroyer, AirCarrier, Submarine}**
combination from hardcoded pill rows. Several of those combinations have **no ships in World of
Warships** — most notably **Tier 9 Submarines** and **Tier 9 Aircraft Carriers** (subs and CVs skip
odd tiers). Today, picking T9 + Submarine shows a flat dead-end:

> No ranked ships for T9 Submarine.

That dead-end is an opportunity. This plan replaces the T9-Submarine message with a small,
theme-aware **D3 animation of an ASCII submarine** — a delight/easter-egg moment
for the curious player who pokes at impossible combinations.

> **Update (2026-06-12, v1.25.1+):** the sub's ASCII art has its bow on the **left**, so it now
> cruises **right→left** to face its heading (the original left→right swam it backwards). A **kraken**
> rises from the bottom edge behind it — mantle partly offscreen, tendrils raking up at ~60° toward the
> fleeing tail. See `SubmarineEasterEgg.tsx`.

**Goal:** code a D3 SVG animation (900 × 300px, transparent background, theme-aware) and render it in
place of the "No ranked ships for T9 Submarine." text for the **T9 + Submarine** combination only.

## Scope decisions (read before building)

- **T9 Submarine only.** T9 Carrier is the *exact same empty-bucket pattern* and a natural follow-up,
  but it is **out of scope** here — this is a deliberate cut, not an oversight. Extending to CV later
  is a one-line predicate change (see "Future extension").
- **No new telemetry event.** Discovery is already measurable: selecting the Submarine pill fires the
  existing `trackEvent('ship-leaderboard-filter', { realm, control: 'type', tier: 9, type: 'Submarine' })`
  (`ShipLeaderboard.tsx:201-206`). Do **not** add a redundant event.
  > **Superseded (2026-06-12, v1.25.2):** a dedicated, edge-triggered
  > `trackEvent('ship-leaderboard-easter-egg', { realm, egg: 't9-submarine' })` now fires once each time
  > the animation surfaces (fired off the `isSubEasterEgg` render predicate, not the pill click, so it
  > counts the *view* regardless of click order and isn't tangled into the high-volume filter event).
  > Reset-on-exit so re-entering counts again; a realm flip while it's on screen doesn't double-count.
- **Minor version bump** on ship (new user-facing surface/UX per `CLAUDE.md` Versioning). Remember the
  mandatory client rebuild after any bump.

## Where it plugs in (verified against code)

The tier/type picker and the empty-state branches live in **`ShipLeaderboard.tsx`** (the inline
explorer under the landing treemap) — **not** `ShipRouteView.tsx` (that is the single-ship
`/ship/[slug]` page and has no tier/type picker). The render switch is at
`ShipLeaderboard.tsx:321-346`:

```tsx
<div className="mt-4">
    {!bothSelected ? (
        <p className="py-6 text-sm text-[var(--text-muted)]">
            Pick a tier and a type to rank ships by win rate.
        </p>
    ) : selectedShip ? (
        <ShipBoard … />
    ) : (
        <ShipList … />     // ← renders "No ranked ships for T9 Submarine." when the list is empty
    )}
</div>
```

The "No ranked ships for {tierTypeLabel}." text itself is in `ShipList` at `ShipLeaderboard.tsx:376-378`.

## Approach: parent short-circuit (no fetch) — THE decision

Render the easter egg directly from the parent render switch, **before** any fetch, when
`tier === 9 && type === 'Submarine'`:

```tsx
    ) : tier === 9 && type === 'Submarine' ? (
        <SubmarineEasterEgg />
    ) : selectedShip ? (
```

Insert this branch **between** the `!bothSelected` check and the `selectedShip` check. This is safe:
`chooseTier`/`chooseType` both `setSelectedShip(null)` (`ShipLeaderboard.tsx:195-206`), so the T9+SS
combo can never carry a stale drilldown into this branch.

### Why short-circuit, not "gate on the empty list" — REJECTED ALTERNATIVE

Gating the easter egg on `sortedShips.length === 0` inside `ShipList` was considered and **rejected**:

1. **It 400s in local dev and any env where `SHIP_BADGE_TIERS` excludes 9.** `data._badge_tiers()`
   (`data.py:6187-6193`) defaults to `'10'` when `SHIP_BADGE_TIERS` is unset, and `local server/.env`
   ships that default. The `/api/realm/<realm>/ships?tier=9&type=Submarine` endpoint validates `tier`
   against `_badge_tiers()` and returns **400** for T9 locally (`views.py` ship-list validation,
   ~2085-2131) → the list lands in the *error* branch ("Couldn't load ships."), never the *empty*
   branch. The easter egg would silently fail to appear in dev, and QA would require an env edit.
2. The short-circuit makes **backend behavior irrelevant** (no fetch at all), so it works in **every**
   environment with **no env change**, avoids a pointless round-trip, and removes the brief
   "Loading ships…" flash before the empty state resolves.

Trade-off accepted: the short-circuit would *mask a real T9 submarine* if World of Warships ever added
one. For a whimsical easter egg on a tier that has never had a sub, that is the correct trade. (For the
record: in **prod**, `SHIP_BADGE_TIERS='8,9,10'`, so T9 is valid and the endpoint would return an empty
`200` — `'Submarine' ∈ SHIP_LEADERBOARD_TYPES`, `data.py:6557` — but we still don't fetch it.)

## New component: `client/app/components/SubmarineEasterEgg.tsx`

A self-contained client component following the established D3 chart pattern (`useRef` + `useEffect` +
`d3.select` direct DOM manipulation; D3 `^7.9.0`, `client/package.json:18`) used by `TierSVG.tsx`,
`TypeSVG.tsx`, etc.

### Requirements

| Requirement | Decision |
|---|---|
| Dimensions | **900 × 300** px (`viewBox="0 0 900 300"`, `max-width: 900px`, `width: 100%` for responsive scale-down) |
| Background | **Transparent** — inherits the host page background (`--bg-page`), which is exactly "the background of the component and site" and is theme-aware *for free*. Do not paint a fill rect. |
| Theme awareness | Read `chartColors[theme]` via `useTheme()` (`app/context/ThemeContext.tsx`), exactly like `TierSVG.tsx:256`. Sub body uses **`colors.shipSS`** — the submarine-class purple (`#7c3aed` light / `#a78bfa` dark, `chartTheme.ts:102,157`), thematically perfect. Re-render on theme toggle via `theme` in the effect deps. |
| Motion | ASCII sub translates **left→right** across the full 900px, looping forever, with a gentle vertical bob (sine). Linear ease, ~12s per crossing. |
| Geometry | **No `getBBox()`** — see "Tested assumptions". All bounds are fixed constants computed from the known monospace art. |
| Facing | Art faces its **natural direction**; animate left→right. **No runtime `scale(-1,1)` flip** (it inverts the translate coordinate system and runs the animation backwards). If bow-forward is ever wanted, hand-mirror the literal strings in `SUB_ART` — do not transform. |
| Accessibility | `role="img"` + `aria-label`; honor `prefers-reduced-motion: reduce` by rendering the sub **static, centered** (no transition). |
| Cleanup | On unmount / theme change, `.interrupt()` the transition and clear the SVG to avoid leaked rAF/transition loops. |

### The ASCII art

Inspired by https://ascii.co.uk/art/submarine (artist "dr"). Compact (4 lines), clearly a sub with
sail + periscope. Signature trimmed. **Render each line as its own left-anchored `<tspan>`** so
per-line alignment (the only alignment that matters) is preserved; keep leading spaces verbatim.

```
         |\_
   _____|~ |____
  (  --         ~~~~--_,
   ~~~~~~~~~~~~~~~~~~~'`
```

As a JS string array (note the escaped backslash and the single backtick on the last line):

```tsx
const SUB_ART = [
    '         |\\_',
    '   _____|~ |____',
    '  (  --         ~~~~--_,',
    "   ~~~~~~~~~~~~~~~~~~~'`",
];
```

### Reference implementation (copy-paste starting point)

```tsx
'use client';

import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { useTheme } from '../context/ThemeContext';
import { chartColors } from '../lib/chartTheme';

const SUB_ART = [
    '         |\\_',
    '   _____|~ |____',
    '  (  --         ~~~~--_,',
    "   ~~~~~~~~~~~~~~~~~~~'`",
];

const WIDTH = 900;
const HEIGHT = 300;
const FONT_SIZE = 18;
const LINE_H = 22;
const CROSS_MS = 12000;
// Fixed off-screen bounds (no getBBox). At ~0.6em/char monospace, the widest
// line (~23 chars) is ~250px; -360 → WIDTH+60 fully clears both edges.
const X_START = -360;
const X_END = WIDTH + 60;
const BLOCK_H = SUB_ART.length * LINE_H;
const Y_MID = (HEIGHT - BLOCK_H) / 2;

const SubmarineEasterEgg: React.FC = () => {
    const ref = useRef<HTMLDivElement | null>(null);
    const { theme } = useTheme();

    useEffect(() => {
        const host = ref.current;
        if (!host) return;
        const colors = chartColors[theme];

        d3.select(host).selectAll('*').remove();

        const svg = d3
            .select(host)
            .append('svg')
            .attr('viewBox', `0 0 ${WIDTH} ${HEIGHT}`)
            .attr('width', '100%')
            .attr('role', 'img')
            .attr('aria-label', 'There are no Tier 9 submarines — but here is one anyway.')
            .style('display', 'block')
            .style('max-width', `${WIDTH}px`)
            .style('background', 'transparent');

        const g = svg.append('g');
        const text = g
            .append('text')
            .attr('font-family', 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace')
            .attr('font-size', FONT_SIZE)
            .attr('fill', colors.shipSS)
            .style('white-space', 'pre');

        SUB_ART.forEach((line, i) => {
            text
                .append('tspan')
                .attr('x', 0)
                .attr('y', i * LINE_H + FONT_SIZE)
                .attr('xml:space', 'preserve')
                .text(line);
        });

        const reduce =
            typeof window !== 'undefined' &&
            window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        if (reduce) {
            // Static, roughly centered (no animation).
            g.attr('transform', `translate(${(WIDTH - 250) / 2}, ${Y_MID})`);
            return () => {
                d3.select(host).selectAll('*').remove();
            };
        }

        let stopped = false;
        const swim = () => {
            if (stopped) return;
            g.attr('transform', `translate(${X_START}, ${Y_MID})`)
                .transition()
                .duration(CROSS_MS)
                .ease(d3.easeLinear)
                .attrTween('transform', () => {
                    const ix = d3.interpolateNumber(X_START, X_END);
                    return (t: number) => {
                        const x = ix(t);
                        const bob = Math.sin(t * Math.PI * 4) * 8; // gentle vertical bob
                        return `translate(${x}, ${Y_MID + bob})`;
                    };
                })
                .on('end', swim);
        };
        swim();

        return () => {
            stopped = true;
            d3.select(host).selectAll('*').interrupt();
            d3.select(host).selectAll('*').remove();
        };
    }, [theme]);

    return <div ref={ref} className="w-full" style={{ maxWidth: WIDTH }} />;
};

export default SubmarineEasterEgg;
```

### Optional polish (not required for v1)

- Bubble trail: a few small `<circle>` elements parented to `g`, drifting up with their own
  per-bubble tween. Use `colors.wrNull` / `colors.accentMid` so they stay theme-aware.
- A faint waterline: a single horizontal `<line>` at `y ≈ HEIGHT*0.5` in `colors.gridLine`.

Keep v1 to the sub itself; add polish only if cheap.

## Wiring change in `ShipLeaderboard.tsx`

1. Add the import near the other component imports at the top of the file:
   ```tsx
   import SubmarineEasterEgg from './SubmarineEasterEgg';
   ```
2. Add the branch in the render switch (`~line 326`), between `!bothSelected` and `selectedShip`:
   ```tsx
       ) : tier === 9 && type === 'Submarine' ? (
           <SubmarineEasterEgg />
       ) : selectedShip ? (
   ```
   `tier` is typed `Tier | null` (`8 | 9 | 10`) and `type` is `ShipType | null` (includes
   `'Submarine'`), so the predicate type-checks with no casts.

No backend, API, or model changes. No new env vars or kill switches.

## Tested technical assumptions (QA of this plan)

| Assumption | Verified? | Evidence |
|---|---|---|
| Picker + empty message live in `ShipLeaderboard.tsx`, not `ShipRouteView.tsx` | ✅ | Tier/type pills `ShipLeaderboard.tsx:286-318`; message `:376-378`; render switch `:321-346` |
| `'Submarine'` is an accepted type (so the combo is reachable, message currently renders) | ✅ | `SHIP_LEADERBOARD_TYPES` includes `'Submarine'` (`data.py:6557`); pill set includes it (`ShipLeaderboard.tsx:27`) |
| **Gate-on-empty would 400 in local/dev** (the decisive reason for short-circuit) | ✅ | `_badge_tiers()` defaults to `'10'` (`data.py:6187-6193`); local `.env` ships that default (memory: *local SHIP_BADGE_TIERS default*); T9 → 400 → error branch, not empty branch |
| Combo can't carry a stale drilldown into the new branch | ✅ | `chooseTier`/`chooseType` both `setSelectedShip(null)` (`:195-206`) |
| Theme pattern: `useTheme()` → `chartColors[theme]`, re-render on `theme` dep | ✅ | `TierSVG.tsx:248-272`; `chartTheme.ts` exports `shipSS` etc. |
| D3 version supports `attrTween`/`interpolateNumber`/`easeLinear` | ✅ | D3 `^7.9.0` (`package.json:18`) |
| **`getBBox()` is unsafe and unnecessary** — eliminated | ✅ | jsdom returns zeros (breaks tests); returns 0 under any `display:none` ancestor; conflicts with flip transforms. Geometry is fixed arithmetic from known monospace art — no measurement needed. |
| Transparent SVG bg == "component and site background", theme-aware | ✅ | Host section sits on `--bg-page` (`globals.css:6,35`); transparent inherits it in both themes |

## Test coverage (doctrine requirement #3)

The animation internals are not jsdom-testable (no real layout/rAF), and we removed `getBBox`, so the
worthwhile test is at the **wiring/branch** level, not the D3 internals:

- New `client/app/components/__tests__/ShipLeaderboard.test.tsx` (or add to an existing one):
  render `<ShipLeaderboard />`, click the **T9** pill then the **Submarine (SS)** pill, and assert:
  1. the easter-egg container renders — query by its `aria-label`
     (`There are no Tier 9 submarines…`) or add a `data-testid`;
  2. the text `No ranked ships` is **absent**;
  3. no `/api/.../ships?tier=9&type=Submarine` fetch is issued (assert the `fetchSharedJson`/fetch mock
     was not called for that combo) — proves the short-circuit.
- Mock `useRealm`/`trackEvent`/`fetchSharedJson` as the existing component tests do. The test exercises
  the **branch**, not the animation. If `SubmarineEasterEgg`'s D3 effect is noisy in jsdom, mock the
  module (`jest.mock('../SubmarineEasterEgg', …)`) to a stub that renders the `aria-label` container.

## Validation checklist before shipping

1. `cd client && npx tsc --noEmit` and `npm run lint` clean (local FE validation per project memory:
   the Docker release gate is authoritative; local `run_release_gate.sh` has a pre-existing d3-ESM
   failure unrelated to this change).
2. `npm run build` succeeds.
3. `npm test -- ShipLeaderboard` passes the new wiring test.
4. **Manual visual QA** (mandatory — lint/build/CI don't catch visual regressions; memory: *verify
   UX/layout changes visually before deploy*):
   - `npm run dev`, open the surface hosting `ShipLeaderboard`, pick **T9 → SS**.
   - Confirm: sub cruises left→right and loops; background matches the page (no box/edge); toggle
     theme — sub recolors (purple light↔dark) with no leaked/duplicated animation; 900×300 footprint;
     scales down gracefully on a narrow viewport. No console errors.
   - Set OS "reduce motion" → confirm a static centered sub, no animation.
   - **No env change needed** to reproduce locally — that's the payoff of the short-circuit.
5. Doctrine pre-commit: this runbook documents the behavior; the wiring test covers it. On
   implementation, mark this runbook **IMPLEMENTED** with the date.

## Future extension (deliberately deferred)

T9 **AirCarrier** is the identical empty bucket. To cover it, broaden the predicate — e.g.
`tier === 9 && (type === 'Submarine' || type === 'AirCarrier')` — and either reuse the sub (whimsy
over realism) or add a parallel ASCII-CV component. Out of scope for this runbook.
