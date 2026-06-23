# Runbook: Player clan-rail soft-navigation (in-place player swap)

_Created: 2026-06-23_
_Status: IMPLEMENTED on `feat/player-rail-soft-nav` (2026-06-23). Spike → parent-layout split shipped; lean gate + production build green. Pending: live visual/interaction verify + deploy._
_Context: On a player page, clicking another clan member in the left rail does a full route navigation (everything remounts + re-skeletons). The goal is to redraw only the main content well while the left rail (clan name, clan chart, member list) stays mounted, moving only the "current player" marker. This runbook captures the architecture, the decisive spike that picked the approach, and the implementation plan._
_QA: empirical Playwright spike on Next 16.2.7 dev (Strict Mode disabled to de-noise mount counts) — two route structures compared; see Spike findings._

## Purpose

Durable design reference for converting clicks on a clan-member row from a full
remount-the-page navigation into an in-place swap of the main well only. Read this before
implementing the split, and when reasoning about why the rail lives in a parent layout rather
than at the dynamic segment. Supersedes nothing; complements the client-request-layer design in
[runbook-player-fetch-orchestration-2026-06-21.md](runbook-player-fetch-orchestration-2026-06-21.md).

## Desired behavior

- Click a member row in the left rail → only the main content well (`order-2`/right column) redraws
  for the newly selected player.
- The left rail — clan name link, `ClanSVG` chart, `ClanMembers` list — stays mounted and visually
  stable; only the "current player" marker moves to the clicked row.
- URL is rewritten to `/player/<newName>` (real URL, deep-linkable, SSR-correct on hard reload,
  native browser back/forward). No reliance on the share button.

## Key architectural facts (pre-spike)

These are why the change is cheaper than it looks:

1. **The rail is already decoupled from the per-player request scope.** Only main-well components
   consume `usePlayerRequestSignal` (`BattleHistoryCard`, `RandomsSVG`, `RankedSeasons`,
   `PlayerClanBattleSeasons`, `PlayerDetailInsightsTabs`). The rail's two data sources fetch on their
   own controllers keyed by **clan**, not player:
   - `ClanSVG` → `/api/fetch/clan_data/<clanId>:active` (`useEffect` dep `clanId`)
   - `ClanMembers` via `useClanMembers(clanId)` → `/api/fetch/clan_members/<clanId>` (resets only on `clanId` change)
   So switching to another member **of the same clan** triggers zero rail refetches today — the rail only
   *appears* to redraw because the whole route remounts.
2. **The marker is prop-driven.** `ClanMembers` computes `isCurrentPlayer` by case-insensitively comparing
   each `member.name` to the `highlightedPlayerName` prop (currently `player.name`). Moving the marker is a
   prop change, not a refetch.
3. **Navigation today** is `onSelectMember(name) → router.push(buildPlayerPath(name, realm))` in
   `PlayerRouteView.tsx`, which changes the `[playerName]` route segment and remounts everything.
4. **Realm is already soft-nav** via `?realm=` query param + `RealmContext` — it does not remount the route.

## Spike findings (decisive)

Tested two throwaway route structures with a client child that logs mount/unmount and renders a
module-scoped mount counter; navigated A→B→C plus browser back/forward via Playwright. Strict Mode was
disabled so mount counts are not doubled.

### Structure 1 — rail in `app/<seg>/[id]/layout.tsx` (layout AT the dynamic segment)

**Remounts on every navigation.** +1 mount per nav, render count resets to 1 each time, an UNMOUNT precedes
each MOUNT. The "preserved layout" guarantee does **not** apply to a layout sitting *at* the dynamic segment
whose param is changing. (This was the naive first guess and it fails — the rail would still flicker.)

### Structure 2 — rail in the PARENT `app/<seg>/layout.tsx`, well at `app/<seg>/[id]/page.tsx`

**Stays mounted across all navigation.** The rail reads the active child segment via
`useSelectedLayoutSegment()`:

| step             | rail mounts | rail renders | marker  | well id |
|------------------|:-----------:|:------------:|---------|---------|
| initial (alpha)  | 1           | 1            | alpha   | alpha   |
| click bravo      | 1           | 2            | bravo   | bravo   |
| click charlie    | 1           | 3            | charlie | charlie |
| browser back     | 1           | 4            | bravo   | bravo   |
| browser forward  | 1           | 5            | charlie | charlie |

One mount event total; the rail only re-renders (marker moves); the well swaps; back/forward are native.

**Conclusion:** the rail must live in a layout **above** the changing segment (the `player` segment, which is
invariant), not at `[playerName]`. The active player is read from below via `useSelectedLayoutSegment()`.

## Decision: parent-layout structure

```
app/player/
  layout.tsx              ← NEW. Renders the left rail (clan name + ClanSVG + ClanMembers).
                            Reads active player via useSelectedLayoutSegment() (URL-decode it),
                            fetches /api/player/<name> for clan_id / clan_name / clan_tag
                            (DEDUPS against the page's identical fetch via fetchSharedJson),
                            feeds the clanId-keyed rail. Holds the prior clan_id during the
                            inter-player fetch so the rail never blanks.
  [playerName]/page.tsx   ← unchanged route; now renders ONLY the main well (PlayerRouteView trimmed
                            to the right column). key={playerName} on the well so per-player sub-state
                            (selected tab, scroll, sort) does not leak across a swap.
```

- Member click stays `router.push(buildPlayerPath(name, realm))` — a normal navigation. Because the rail
  is in the preserved parent layout, only `page.tsx` re-renders; the rail re-renders (marker moves) without
  remounting. No `pushState`/`popstate` hand-rolling; URLs, deep-links, and back/forward are native.
- Same clan ⇒ `clan_id` unchanged ⇒ rail's clanId-keyed children do not refetch or re-animate.
- Different clan (a roster member who has since departed has a different `clan_id`) ⇒ rail correctly redraws.

## Implementation (as shipped)

1. **Split `PlayerDetail.tsx`'s two-column grid** (`lg:grid-cols-[350px_1fr]`): the left rail
   (clan name link, `#clan_plot_container` → `ClanSVG`, `#clan_members_container` → `ClanMembers`)
   moved into `PlayerRailLayout` (rendered by the new `app/player/layout.tsx`). The grid, the outer
   `bg-[var(--bg-page)] p-6` wrapper, and the page-level **Back** button (`router.push('/')`) now live in
   `PlayerRailLayout`; `{children}` (the page well) is the right cell (`order-1 lg:order-2 lg:pl-4`).
   `PlayerDetail` is now just the right-well content (player header, summary cards, ship banner, tabs) — it
   no longer takes `onBack`/`onSelectMember` and no longer loads clan members. The legacy
   `warmupSettled`/`shouldLoadClanMembers` gating was dropped (the rail loads on `clan_id`, the prod
   de-waterfall behavior); `PlayerDetailInsightsTabs`' `onWarmupSettled` is now unused (prop kept optional).
2. **`app/player/layout.tsx`** (server component) renders `'use client'` `PlayerRailLayout`, which:
   - reads `useSelectedLayoutSegment()` → `decodeURIComponent` → active player name. This drives the
     **marker** (`highlightedPlayerName` on `ClanSVG` + `ClanMembers`) so it moves synchronously on click,
     before any fetch. (NOT the fetched payload name — that would lag a click during the loading gap.)
   - fetches `/api/player/<name>/` via `fetchSharedJson` for `clan_id`/`clan_name`/`clan_tag`/`player_id`.
     This **dedups** onto `PlayerRouteView`'s identical critical fetch (same URL ⇒ same cacheKey). It
     requests the same response headers + `PLAYER_ROUTE_FETCH_TTL_MS` so the dedup holds regardless of which
     subscriber wins the in-flight race (child-before-parent effect order means the page wins on mount).
   - runs `useClanMembers(clanId)` and renders `ClanSVG` + `ClanMembers` as before.
   - **retention:** keeps the last-resolved `clanIdentity` across the inter-player fetch gap and across a
     failed/404 new-player fetch, so a same-clan swap never blanks. `clanIdentity === null` (first load only)
     shows a `LoadingPanel` instead of a "No Clan" flash.
3. **`page.tsx`**: `<PlayerRouteView key={playerName} …>` remounts the well on a player swap (resets tab/
   scroll/sort). `PlayerRouteView` dropped the `onBack`/`onSelectMember` wiring (and `useRouter`).
4. **Member click** stays `router.push(buildPlayerPath(name, realm))` — now owned by `PlayerRailLayout`.

### Second consumer found during implementation — landing `?q=` redirect (behavior change)

`PlayerSearch` (the landing `/` view) was a **second `PlayerDetail` consumer**: it rendered the player view
**inline** for the SEO `SearchAction` deep-link (`/?q=<name>`) and a now-dead `navSearch` event. Since
`PlayerDetail` no longer renders standalone (no rail/back), and the visible header search already navigates
to `/player/<name>`, `PlayerSearch` now **`router.replace(buildPlayerPath(q, realm))`** on a `?q=` param
instead of rendering inline — unifying on the canonical route (deep-linkable, gets the rail). Realm is read
from `useRealm()` (client) so a bare `/?q=` keeps the stored preference; `replace` (not `push`) so Back
doesn't bounce. The orphaned `useClanHydrationPoll` hook + the `navSearch` listener were removed. The SEO
`SearchAction` JSON-LD target (`/?q=`) is unchanged — the redirect satisfies it.

## Risks / edge cases

- **Loading-gap blanking** — the rail must hold the prior `clan_id` during the inter-player fetch; verify it
  never flashes empty on a same-clan swap.
- **Main-well state bleed** — `key={playerName}` on the well so old tab/scroll/sort state does not carry over.
- **Departed-member / different clan** — do not assume the rail's clan is invariant; a clicked member whose
  `clan_id` differs must redraw the rail.
- **Realm composition** — realm is query-param soft-nav and the rail fetches are `withRealm`-keyed; confirm a
  realm switch still refetches the rail without remounting the layout.
- **Segment decode** — `useSelectedLayoutSegment()` returns the raw URL segment; decode it before comparing to
  member names (the `ClanMembers` match is already case-insensitive + trimmed).
- **Dev Strict-Mode double-mount** — known dev-only interaction (see the dev "Player not found" note); prod is fine.

## Validation

- Spike evidence above (Playwright, Next 16.2.7 dev, Strict Mode off) is the basis for the parent-layout
  decision. Spike routes were throwaway and removed; the worktree branch is otherwise clean.
- **Regression coverage added** — `app/components/__tests__/PlayerRailLayout.test.tsx`: rail does NOT remount
  on a same-clan member swap (module-scoped `ClanSVG` mount counter stays at 1, marker moves, `clanId`
  unchanged); rail redraws (`clanId` 100→200) on a cross-clan swap without remounting; marker follows the
  active segment; member click → `router.push('/player/<name>?realm=na')`. `PlayerDetail.test.tsx` lost the
  clan-rail tests (moved here); `PlayerSearch.test.tsx` now asserts the `?q=` redirect; `PlayerRouteView`'s
  nav-wiring assertion removed.
- **Lean gate + build green** — `npm test` 42 suites / 245 tests pass; `next build` compiles + typechecks +
  generates all routes.
- **Live interaction verify PASSED (production build, real prod data)** — `next start` against
  `https://battlestats.online`, Playwright on `/player/jinxns` (clan CMAR). Method: tag the rail's
  `#clan_plot_container` + `#clan_members_container` with a `data-verify` attribute React doesn't manage, then
  navigate; surviving tags ⇒ the same DOM nodes ⇒ no remount. Results:
  - Same-clan member click (jinxns→El4Guapo): well h1 swaps, `aria-current` marker moves, **rail nodes
    survive** (no remount/re-skeleton). PASS.
  - Browser **Back** (→jinxns) and **Forward** (→El4Guapo): URL + well + marker all track, rail nodes survive
    every hop. PASS.
  - Visual: `nav_final.png` / `softnav_after.png` confirm the two-column layout, clan chart + member list, and
    the gold "you are here" marker on the active row.
  - (The dev-server `next dev` run hit the documented dev-only Strict-Mode AbortController artifact — NOT a
    regression; the production build is clean. See the dev "Player not found" note.)
- **Realm switch on a player page** — correct by construction: `PlayerRailLayout`'s clan fetch effect keys on
  `[activePlayerName, realm]` and `useClanMembers` on `[clanId, …, realm]`, so a `?realm=` change refetches the
  rail without remounting the layout. (Not live-verified — a clean test is muddied by the test player being
  NA-only; switching to a realm where the player is absent 404s the well while the rail retains the prior
  realm's clan, consistent with the accepted 404-member-click retention behavior above.)
- **Still owed before deploy:** spot-check mobile ordering (player-info on top, rail below) + the `/?q=<name>`
  redirect on a real device/browser; then deploy frontend (mandatory client rebuild).

## Follow-ups

- Live visual/interaction verify, then deploy frontend (mandatory client rebuild — `NEXT_PUBLIC_APP_VERSION`
  is build-time).
- Reconcile the player-page section of `CLAUDE.md` and cross-link from
  [runbook-player-fetch-orchestration-2026-06-21.md](runbook-player-fetch-orchestration-2026-06-21.md).
- Verify native browser back/forward restore the correct player + marker (spike showed the mechanism; confirm
  end-to-end with the real components).

## Related runbooks

- [runbook-player-fetch-orchestration-2026-06-21.md](runbook-player-fetch-orchestration-2026-06-21.md) — the
  client request layer (request scope, dedup cache, priority queue) this rides on.
- [runbook-player-refresh-pill-clobber-2026-06-21.md](runbook-player-refresh-pill-clobber-2026-06-21.md) — adjacent player-page refresh behavior.
